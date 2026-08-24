"""Find every weight file the workflow loads.

Three signals are used, in descending order of confidence:

1. The node is in the core catalog and the widget is declared as reading from a
   ComfyUI model folder (``folder_paths.get_filename_list("loras")``).
2. The widget *name* matches a known naming convention (``lora_name``,
   ``ckpt_name``, ...), which is how custom packs almost always name theirs.
3. The value simply looks like a weights file (``.safetensors`` and friends).

Anything caught only by (3) is reported with lower confidence rather than
dropped, because an unaudited model is exactly what this tool exists to find.
"""

from __future__ import annotations

import re
from typing import Any

from .. import catalog
from ..graph import Node, Workflow
from ..records import ModelRef

FOLDER_ROLES = {
    "checkpoints": "Checkpoint",
    "diffusion_models": "Diffusion model (UNet)",
    "unet": "Diffusion model (UNet)",
    "loras": "LoRA",
    "vae": "VAE",
    "vae_approx": "VAE preview decoder",
    "text_encoders": "Text encoder",
    "clip": "Text encoder",
    "clip_vision": "CLIP vision encoder",
    "controlnet": "ControlNet",
    "upscale_models": "Upscale model",
    "embeddings": "Textual inversion embedding",
    "style_models": "Style model",
    "gligen": "GLIGEN",
    "hypernetworks": "Hypernetwork",
    "photomaker": "PhotoMaker",
    "ipadapter": "IP-Adapter",
    "insightface": "InsightFace face analysis",
    "sams": "Segment Anything",
    "ultralytics": "YOLO detector",
    "animatediff_models": "AnimateDiff motion module",
    "animatediff_motion_lora": "AnimateDiff motion LoRA",
    "audio_encoders": "Audio encoder",
    "model_patches": "Model patch",
    "detection": "Detection model",
    "geometry_estimation": "Geometry estimation model",
    "optical_flow": "Optical flow model",
    "frame_interpolation": "Frame interpolation model",
    "latent_upscale_models": "Latent upscale model",
    "configs": "Model config",
    "hosted-api": "Hosted model (partner API)",
    "unknown": "Model",
}

STRENGTH_WIDGETS = ("strength_model", "strength", "weight", "lora_strength", "strength_clip")

# A HuggingFace-style ``owner/name`` reference typed straight into a widget.
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,60}/[\w.-]{1,80}$")

# Values that are combo *modes*, not filenames - avoids treating "None" or
# "randomize" as a weight file.
NON_MODEL_VALUES = {
    "none", "null", "undefined", "", "disabled", "off", "auto", "default",
    "randomize", "increment", "decrement", "fixed", "enable", "disable",
    "baked vae", "bypass",
}


def extract(wf: Workflow) -> list[ModelRef]:
    refs: list[ModelRef] = []
    for node in wf.nodes.values():
        refs.extend(_from_node(wf, node))
    return refs


def _from_node(wf: Workflow, node: Node) -> list[ModelRef]:
    schema = catalog.get_node_schema(node.type)
    slots = {w["name"]: w for w in (schema.get("widgets") or [])} if schema else {}
    out: list[ModelRef] = []

    for name in node.widget_order:
        value = node.widgets.get(name)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text.lower() in NON_MODEL_VALUES:
            continue
        # Prompts and other long free text are never filenames.
        if len(text) > 200 or "\n" in text:
            continue

        slot = slots.get(name) or {}
        declared = slot.get("model_folder")
        looks_like_file = catalog.looks_like_model_file(text)
        hint = catalog.folder_hint_for_widget(name) or catalog.folder_hint_for_value(text)
        is_repo = bool(REPO_RE.match(text)) and not looks_like_file

        folder: str | None = None
        confidence = "high"

        if declared and declared != "*":
            folder = declared
        elif declared == "*":
            folder = hint or "unknown"
            confidence = "medium"
        elif hint and (looks_like_file or is_repo or _plausible_filename(text)):
            folder = hint if hint != "*" else "unknown"
            confidence = "high" if looks_like_file else "medium"
        elif looks_like_file:
            folder = "unknown"
            confidence = "medium"
        else:
            continue

        if node.unaligned and not looks_like_file:
            # Widget names on this node could not be trusted; only accept
            # values that are self-evidently weights files.
            continue

        hosted = catalog.is_api_node(node.type)
        if hosted:
            # A partner-API node names a hosted model, not a file on disk.
            folder, confidence = "hosted-api", "high"

        ref = ModelRef(
            filename=text,
            folder=folder or "unknown",
            role=FOLDER_ROLES.get(folder or "unknown", "Model"),
            node_id=node.id,
            node_type=node.type,
            node_label=node.label,
            widget=name,
            enabled=node.enabled,
            confidence="low" if node.unaligned else confidence,
            strength=_strength_for(node, name),
        )
        if hosted:
            ref.notes.append("runs on the vendor's servers; weights are never local")
        if is_repo and not hosted:
            ref.repo_id = text
            ref.notes.append("widget names a remote repository, downloaded on first run")
        if not node.enabled:
            ref.notes.append(f"node is {node.mode_name}")
        out.append(ref)

    return out


def _plausible_filename(text: str) -> bool:
    """A bare name with no extension can still be a model (diffusers dirs)."""
    if " " in text and "/" not in text and "\\" not in text:
        return False
    return bool(re.match(r"^[\w./\\-]{2,120}$", text))


def _strength_for(node: Node, widget_name: str) -> float | None:
    """Pair a LoRA-ish widget with the strength that applies to it.

    Stacker nodes use suffixed names (``lora_name_2`` / ``strength_2``), so the
    suffix is matched first before falling back to the node-wide strength.
    """
    suffix_match = re.search(r"(_\d+)$", widget_name)
    candidates: list[str] = []
    if suffix_match:
        idx = suffix_match.group(1)
        candidates.extend(f"{base}{idx}" for base in STRENGTH_WIDGETS)
    candidates.extend(STRENGTH_WIDGETS)

    for cand in candidates:
        value = node.widgets.get(cand)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def from_prompt_embeddings(embeddings: list[str], prompt: Any) -> list[ModelRef]:
    """Turn ``embedding:name`` references inside prompt text into model refs."""
    out: list[ModelRef] = []
    for name in embeddings:
        out.append(ModelRef(
            filename=name,
            folder="embeddings",
            role=FOLDER_ROLES["embeddings"],
            node_id=getattr(prompt, "node_id", ""),
            node_type=getattr(prompt, "node_type", ""),
            node_label=getattr(prompt, "node_label", ""),
            widget=getattr(prompt, "widget", ""),
            enabled=getattr(prompt, "enabled", True),
            confidence="high",
            notes=["referenced from prompt text via embedding: syntax"],
        ))
    return out


def from_prompt_loras(loras: list[str], prompt: Any) -> list[ModelRef]:
    """Turn inline ``<lora:name:0.8>`` references into model refs."""
    out: list[ModelRef] = []
    for entry in loras:
        parts = entry.split(":")
        name = parts[0]
        strength: float | None = None
        if len(parts) > 1:
            try:
                strength = float(parts[1])
            except ValueError:
                strength = None
        out.append(ModelRef(
            filename=name,
            folder="loras",
            role=FOLDER_ROLES["loras"],
            node_id=getattr(prompt, "node_id", ""),
            node_type=getattr(prompt, "node_type", ""),
            node_label=getattr(prompt, "node_label", ""),
            widget=getattr(prompt, "widget", ""),
            enabled=getattr(prompt, "enabled", True),
            strength=strength,
            confidence="medium",
            notes=["referenced inline from prompt text; requires a LoRA-parsing custom node"],
        ))
    return out


def deduplicate(refs: list[ModelRef]) -> list[ModelRef]:
    """Collapse repeated references to the same file, keeping usage counts."""
    merged: dict[str, ModelRef] = {}
    for ref in refs:
        key = ref.key
        existing = merged.get(key)
        if existing is None:
            merged[key] = ref
            continue
        if ref.enabled and not existing.enabled:
            # Prefer the enabled occurrence as the representative record.
            ref.notes = list(dict.fromkeys(existing.notes + ref.notes))
            merged[key] = ref
            existing = ref
        note = f"also used by {ref.node_label} ({ref.widget})"
        if note not in existing.notes:
            existing.notes.append(note)
    return list(merged.values())
