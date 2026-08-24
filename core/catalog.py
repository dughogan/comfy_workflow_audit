"""Access to the bundled catalog data.

Everything here is offline: node schemas scraped from a real ComfyUI release,
the ComfyUI-Manager pack index, and a filename to upstream-URL table.  Loading
is lazy so importing :mod:`comfyaudit` stays cheap.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from functools import lru_cache
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(__file__), "knowledge", "data")

# Widget-name heuristics used when a node is not in the core catalog (i.e. it
# comes from a custom pack).  Maps a regex over the widget name to the ComfyUI
# model folder the value most likely refers to.
NAME_FOLDER_HINTS: list[tuple[str, str]] = [
    (r"^(ckpt|checkpoint)(_name)?$", "checkpoints"),
    (r"lora(_name|_\d+)?$", "loras"),
    (r"^lora_name", "loras"),
    (r"^vae(_name)?$", "vae"),
    (r"^(unet|diffusion_model)(_name)?$", "diffusion_models"),
    (r"^(clip|text_encoder)(_name)?\d*$", "text_encoders"),
    (r"^clip_vision(_name)?$", "clip_vision"),
    (r"^control_?net(_name)?$", "controlnet"),
    (r"^(upscale_model|upscaler)(_name)?$", "upscale_models"),
    (r"^embedding(_name)?$", "embeddings"),
    (r"^style_model(_name)?$", "style_models"),
    (r"^gligen(_name)?$", "gligen"),
    (r"^hypernetwork(_name)?$", "hypernetworks"),
    (r"^ipadapter(_file|_name)?$", "ipadapter"),
    (r"^insightface", "insightface"),
    (r"^sam_model(_name)?$", "sams"),
    (r"^(model_name|model_path|model_file)$", "*"),
    (r"^(bbox|segm)_model(_name)?$", "ultralytics"),
    (r"^animatediff_model|^model_name_mm$", "animatediff_models"),
    (r"^motion_lora", "animatediff_motion_lora"),
]

MODEL_EXTENSIONS = (
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft",
    ".onnx", ".engine", ".pkl", ".npz", ".msgpack",
)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".exr", ".gif", ".tga")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".avi", ".mkv", ".mxf", ".gif")
AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")


def _load(name: str) -> Any:
    path = os.path.join(DATA_DIR, name)
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


@lru_cache(maxsize=1)
def core_nodes() -> dict[str, Any]:
    """Node id -> schema, for the ComfyUI release the catalog was built from."""
    return _load("core_nodes.json.gz")


@lru_cache(maxsize=1)
def node_packs() -> dict[str, Any]:
    """``{"packs": {repo: meta}, "node_index": {class: [repo]}, ...}``."""
    return _load("node_packs.json.gz")


@lru_cache(maxsize=1)
def known_models() -> dict[str, Any]:
    """Lowercased filename -> upstream reference metadata."""
    return _load("known_models.json.gz")


@lru_cache(maxsize=1)
def comfyui_version() -> str:
    return str(core_nodes().get("comfyui_version", "unknown"))


#: Optional callable that resolves a node schema from a *live* ComfyUI process.
#: The bundled catalog only knows core nodes from one ComfyUI release; running
#: inside ComfyUI we can read the real INPUT_TYPES of every installed node,
#: custom packs included, which turns widget names on custom nodes from a guess
#: into a fact.  Injected by the plugin layer so core stays ComfyUI-free.
_live_provider: Any = None


def set_live_provider(provider: Any) -> None:
    """Install (or clear, with ``None``) the live node-schema resolver."""
    global _live_provider
    _live_provider = provider
    get_node_schema.cache_clear()
    is_core_node.cache_clear()


def has_live_provider() -> bool:
    return _live_provider is not None


@lru_cache(maxsize=8192)
def get_node_schema(class_type: str) -> dict[str, Any] | None:
    if _live_provider is not None:
        live = _live_provider(class_type)
        if live is not None:
            return live
    return core_nodes()["nodes"].get(class_type)


#: Node types the ComfyUI frontend provides itself.  They have no Python class
#: and so never appear in a scraped node catalog, but they are still core and
#: must not be reported as missing custom nodes.
FRONTEND_CORE_TYPES = {
    "Note", "MarkdownNote", "Reroute", "PrimitiveNode", "Primitive",
    "SubgraphInputNode", "SubgraphOutputNode", "SubgraphNode",
}


@lru_cache(maxsize=8192)
def is_core_node(class_type: str) -> bool:
    """Whether the node ships with ComfyUI itself rather than a custom pack.

    A live schema does not imply core - custom packs are live too - so this
    stays anchored to the bundled catalog and the frontend's own node types.
    """
    return class_type in core_nodes()["nodes"] or class_type in FRONTEND_CORE_TYPES


def is_api_node(class_type: str) -> bool:
    schema = get_node_schema(class_type)
    return bool(schema and schema.get("api_node"))


@lru_cache(maxsize=4096)
def find_pack(class_type: str) -> dict[str, Any] | None:
    """Locate the custom node pack that provides ``class_type``.

    Returns the pack record with an extra ``repo`` key and, when the class name
    is claimed by more than one pack, a ``collisions`` list.  Name collisions
    matter in production: two installed packs exporting the same class silently
    shadow one another depending on load order.
    """
    data = node_packs()
    repos = data["node_index"].get(class_type)
    if not repos:
        # Fall back to the nodename_pattern hints ComfyUI-Manager publishes.
        for hint in data.get("nodename_patterns", []):
            try:
                if hint["repo"] and re.search(hint["pattern"], class_type):
                    repos = [hint["repo"]]
                    break
            except re.error:
                continue
    if not repos:
        return None

    primary = data["packs"].get(repos[0])
    if primary is None:
        return {"repo": repos[0], "title": repos[0].split("/")[-1], "author": "", "reference": "https://" + repos[0]}
    out = dict(primary)
    out["repo"] = repos[0]
    if len(repos) > 1:
        out["collisions"] = repos[1:]
    return out


def pack_by_repo(repo_url: str) -> dict[str, Any] | None:
    """Find a pack by repository, however the reference is written.

    The frontend stamps ``aux_id`` as a bare ``owner/repo`` while the index is
    keyed by host, so a lookup that does not try both silently misses - which
    reads as "unidentified node" for a pack that is sitting right there.
    """
    key = re.sub(r"\.git$", "", (repo_url or "").strip().rstrip("/"))
    key = re.sub(r"^https?://(www\.)?", "", key).lower()
    if not key:
        return None

    packs = node_packs()["packs"]
    candidates = [key]
    if key.count("/") == 1 and "." not in key.split("/")[0]:
        candidates += [f"github.com/{key}", f"gitlab.com/{key}"]
    for candidate in candidates:
        rec = packs.get(candidate)
        if rec:
            out = dict(rec)
            out["repo"] = candidate
            return out
    return None


def pack_by_id(pack_id: str) -> dict[str, Any] | None:
    """Look a pack up by its Comfy Registry id (``properties.cnr_id``)."""
    for key, rec in node_packs()["packs"].items():
        if rec.get("id") == pack_id:
            out = dict(rec)
            out["repo"] = key
            return out
    # Registry ids are often the repo name lowercased.
    needle = pack_id.lower()
    for key, rec in node_packs()["packs"].items():
        if key.split("/")[-1] == needle:
            out = dict(rec)
            out["repo"] = key
            return out
    return None


def known_model(filename: str) -> dict[str, Any] | None:
    return known_models().get(os.path.basename(filename or "").lower())


#: Some packs encode the model folder into the value: "bbox/face_yolov8m.pt".
VALUE_FOLDER_PREFIXES = {
    "bbox": "ultralytics", "segm": "ultralytics",
    "sams": "sams", "insightface": "insightface", "ipadapter": "ipadapter",
}


def folder_hint_for_widget(name: str) -> str | None:
    """Guess the model folder for a widget on a node we have no schema for."""
    low = (name or "").lower()
    for pattern, folder in NAME_FOLDER_HINTS:
        if re.search(pattern, low):
            return folder
    return None


def folder_hint_for_value(value: str) -> str | None:
    """Guess the folder from a value that carries its own subdirectory."""
    head = (value or "").replace("\\", "/").split("/", 1)[0].strip().lower()
    return VALUE_FOLDER_PREFIXES.get(head)


def looks_like_model_file(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return value.lower().rstrip().endswith(MODEL_EXTENSIONS)


def asset_kind(value: Any) -> str | None:
    """Classify a string that looks like a media filename."""
    if not isinstance(value, str) or not value.strip():
        return None
    low = value.lower().split("?")[0].strip()
    # LoadImage values can carry a subfolder annotation like "img.png [input]".
    low = re.sub(r"\s*\[(input|output|temp)\]\s*$", "", low)
    if low.endswith(IMAGE_EXTENSIONS):
        return "image"
    if low.endswith(VIDEO_EXTENSIONS):
        return "video"
    if low.endswith(AUDIO_EXTENSIONS):
        return "audio"
    return None
