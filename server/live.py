"""Read the running ComfyUI instead of guessing at it.

Offline, the auditor works from a catalog scraped from one ComfyUI release plus
naming heuristics for everything else.  Running *inside* ComfyUI we can do much
better:

* every installed node - custom packs included - can be asked for its real
  ``INPUT_TYPES``, so widget names on custom nodes stop being ``widget_0``;
* ``folder_paths`` knows where the weights actually are, so model presence and
  size are facts rather than an unchecked assumption;
* ``custom_nodes`` on disk carries the installed version of each pack.

Everything here degrades to ``None`` when ComfyUI is not importable, so the same
code runs under pytest and from the CLI.
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from typing import Any

from ..core import catalog
from ..core.resolve import local as local_mod

SEED_WIDGET_NAMES = {"seed", "noise_seed", "rand_seed"}
WIDGET_PRIMITIVES = {"INT": "int", "FLOAT": "float", "STRING": "string", "BOOLEAN": "bool"}


def comfy_available() -> bool:
    try:
        import nodes  # noqa: F401
        import folder_paths  # noqa: F401
    except Exception:
        return False
    return True


# --------------------------------------------------------------------------
# Node schemas
# --------------------------------------------------------------------------


def _node_class(class_type: str) -> Any | None:
    try:
        import nodes
    except Exception:
        return None
    return nodes.NODE_CLASS_MAPPINGS.get(class_type)


@lru_cache(maxsize=1)
def _folder_listings() -> list[tuple[str, frozenset]]:
    """Snapshot of every model folder's file list, for reverse lookups.

    ``INPUT_TYPES`` hands back the *contents* of a model folder, not its name,
    so the only way to recover the folder at run time is to compare the combo's
    options against each folder's listing.
    """
    try:
        import folder_paths
    except Exception:
        return []

    out: list[tuple[str, frozenset]] = []
    for name in list(getattr(folder_paths, "folder_names_and_paths", {}) or {}):
        try:
            files = folder_paths.get_filename_list(name)
        except Exception:
            continue
        if files:
            out.append((name, frozenset(files)))
    # Longest listings first so a specific folder wins over a superset.
    out.sort(key=lambda item: -len(item[1]))
    return out


def _folder_for_options(options: Any) -> str | None:
    if not isinstance(options, (list, tuple)) or not options:
        return None
    sample = {o for o in options if isinstance(o, str)}
    if not sample:
        return None
    for folder, files in _folder_listings():
        # Equality is the common case; subset covers loaders that prepend a
        # "None" entry or filter the list.
        if sample == files or (sample <= files and len(sample) >= max(1, len(files) // 2)):
            return folder
    return None


def _entry(name: str, definition: Any, optional: bool) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if isinstance(definition, (list, tuple)) and definition:
        type_spec = definition[0]
        if len(definition) > 1 and isinstance(definition[1], dict):
            opts = definition[1]
    else:
        type_spec = definition

    entry: dict[str, Any] = {"name": name, "optional": optional}

    if isinstance(type_spec, str):
        kind = WIDGET_PRIMITIVES.get(type_spec)
        if kind:
            entry.update(kind=kind, widget=True)
        elif type_spec == "COMBO":
            entry.update(kind="combo", widget=True)
            folder = _folder_for_options(opts.get("options"))
            if folder:
                entry["model_folder"] = folder
        else:
            entry.update(kind="link", widget=False, type=type_spec)
    elif isinstance(type_spec, (list, tuple)):
        entry.update(kind="combo", widget=True)
        folder = _folder_for_options(type_spec)
        if folder:
            entry["model_folder"] = folder
        elif all(isinstance(o, (str, int, float)) for o in type_spec):
            entry["options"] = list(type_spec)[:64]
    else:
        entry.update(kind="link", widget=False, type="*")

    if opts.get("forceInput"):
        entry.update(kind="link", widget=False, type=entry.get("type", "*"))
    for flag in ("multiline", "image_upload", "video_upload", "audio_upload",
                 "dynamicPrompts", "control_after_generate"):
        if opts.get(flag):
            entry[flag] = True
    if isinstance(opts.get("default"), (str, int, float, bool)):
        entry["default"] = opts["default"]
    return entry


def live_schema(class_type: str) -> dict[str, Any] | None:
    """Build a catalog-shaped schema from a live node class."""
    cls = _node_class(class_type)
    if cls is None:
        return None

    try:
        spec = cls.INPUT_TYPES()
    except Exception:
        return None
    if not isinstance(spec, dict):
        return None

    entries: list[dict[str, Any]] = []
    for section in ("required", "optional"):
        block = spec.get(section)
        if not isinstance(block, dict):
            continue
        for name, definition in block.items():
            try:
                entries.append(_entry(str(name), definition, section == "optional"))
            except Exception:
                continue

    widgets: list[dict[str, Any]] = []
    ordered = [e for e in entries if e.get("widget") and not e["optional"]]
    ordered += [e for e in entries if e.get("widget") and e["optional"]]
    for w in ordered:
        rec = {k: v for k, v in w.items() if k not in ("widget", "optional")}
        widgets.append(rec)
        if w["name"] in SEED_WIDGET_NAMES or w.get("control_after_generate"):
            widgets.append({"name": "control_after_generate", "kind": "combo", "synthetic": True})
        if w.get("image_upload") or w.get("video_upload") or w.get("audio_upload"):
            widgets.append({"name": f"{w['name']}_upload_ui", "kind": "combo", "synthetic": True})

    returns = getattr(cls, "RETURN_TYPES", ()) or ()
    return {
        "category": str(getattr(cls, "CATEGORY", "") or ""),
        "description": str(getattr(cls, "DESCRIPTION", "") or "")[:400],
        "output_node": bool(getattr(cls, "OUTPUT_NODE", False)),
        "deprecated": bool(getattr(cls, "DEPRECATED", False)),
        "experimental": bool(getattr(cls, "EXPERIMENTAL", False)),
        "outputs": [str(r) for r in returns],
        "widgets": widgets,
        "inputs": [{"name": e["name"], "type": e.get("type", "*"), "optional": e["optional"]}
                   for e in entries if not e.get("widget")],
        "schema": "live",
        "api_node": bool(getattr(cls, "API_NODE", False)),
    }


def install() -> bool:
    """Point the core catalog at the running ComfyUI. Returns success."""
    if not comfy_available():
        return False
    catalog.set_live_provider(live_schema)
    return True


def uninstall() -> None:
    catalog.set_live_provider(None)
    _folder_listings.cache_clear()


# --------------------------------------------------------------------------
# Models on disk
# --------------------------------------------------------------------------


def live_model_index() -> local_mod.ModelIndex:
    """Index the weights ComfyUI can actually see, across every configured path."""
    index = local_mod.ModelIndex(root="<comfyui folder_paths>")
    try:
        import folder_paths
    except Exception:
        return index

    index.available = True
    for folder in list(getattr(folder_paths, "folder_names_and_paths", {}) or {}):
        try:
            names = folder_paths.get_filename_list(folder)
        except Exception:
            continue
        for name in names:
            try:
                path = folder_paths.get_full_path(folder, name)
            except Exception:
                path = None
            if not path or not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            index.by_name.setdefault(os.path.basename(name).lower(), []).append(
                local_mod.LocalFile(path=path, size=size, folder=folder)
            )
            index.scanned += 1
    return index


# --------------------------------------------------------------------------
# Installed custom node packs
# --------------------------------------------------------------------------


def installed_packs() -> dict[str, dict[str, Any]]:
    """Map node class name -> the pack directory that registered it.

    ComfyUI does not record which pack a class came from, so this reconstructs
    it from the module each class was defined in, which is how the loader gets
    them in the first place.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        import nodes
        import folder_paths
    except Exception:
        return out

    roots = [os.path.abspath(p) for p in
             (getattr(folder_paths, "get_folder_paths", lambda _: [])("custom_nodes") or [])]
    if not roots:
        base = getattr(folder_paths, "base_path", None)
        if base:
            roots = [os.path.join(os.path.abspath(base), "custom_nodes")]

    for class_type, cls in list(nodes.NODE_CLASS_MAPPINGS.items()):
        # A class only records its module *name*; the file comes from sys.modules.
        module = sys.modules.get(getattr(cls, "__module__", "") or "")
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        path = os.path.abspath(module_file)
        for root in roots:
            if not path.startswith(root + os.sep):
                continue
            rel = path[len(root) + 1:].split(os.sep)[0]
            out[class_type] = {
                "directory": rel,
                "path": os.path.join(root, rel),
                "version": _pack_version(os.path.join(root, rel)),
            }
            break
    return out


def _pack_version(pack_dir: str) -> str:
    """Read an installed pack's version from its own metadata."""
    pyproject = os.path.join(pack_dir, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            text = ""
        match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if match:
            return match.group(1)

    head = os.path.join(pack_dir, ".git", "HEAD")
    if os.path.isfile(head):
        try:
            with open(head, "r", encoding="utf-8") as fh:
                ref = fh.read().strip()
        except OSError:
            return ""
        if ref.startswith("ref: "):
            ref_path = os.path.join(pack_dir, ".git", ref[5:])
            if os.path.isfile(ref_path):
                try:
                    with open(ref_path, "r", encoding="utf-8") as fh:
                        return "git:" + fh.read().strip()[:12]
                except OSError:
                    return ""
        elif ref:
            return "git:" + ref[:12]
    return ""


def environment() -> dict[str, Any]:
    """What the audit should record about the machine it ran on."""
    info: dict[str, Any] = {"comfyui": False}
    try:
        import comfyui_version
        info["comfyui_version"] = getattr(comfyui_version, "__version__", "")
    except Exception:
        info["comfyui_version"] = ""
    try:
        import nodes
        info["comfyui"] = True
        info["installed_node_types"] = len(nodes.NODE_CLASS_MAPPINGS)
    except Exception:
        return info
    try:
        import folder_paths
        info["base_path"] = getattr(folder_paths, "base_path", "")
    except Exception:
        pass
    return info
