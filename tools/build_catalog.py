#!/usr/bin/env python3
"""Build the bundled catalog data shipped inside ``comfyaudit.core.knowledge.data``.

Three sources are consumed:

1. A checkout of ComfyUI itself -> core node schemas.  Node definitions come in
   two flavours in modern ComfyUI: the legacy ``INPUT_TYPES`` classmethod (V1)
   and the newer ``define_schema`` / ``io.Schema`` form (V3).  Both are parsed
   statically with :mod:`ast` so no ComfyUI dependency is needed at runtime.

   The important product is the *ordered widget list* for every node.  A UI
   format workflow stores widget values positionally in ``widgets_values``, so
   without this table you cannot tell a prompt from a filename from a seed.

2. ComfyUI-Manager's ``custom-node-list.json`` / ``extension-node-map.json`` /
   ``github-stats.json`` -> which pack owns a node class, who wrote it, how
   many stars it has and when it was last touched.

3. ComfyUI-Manager's ``model-list.json`` -> filename to upstream URL, giving
   offline provenance for the most commonly used weights.

Usage::

    python tools/build_catalog.py --comfyui /path/to/ComfyUI --manager /path/to/json-dir
"""

from __future__ import annotations

import argparse
import ast
import gzip
import json
import os
import re
import sys
from typing import Any

# --------------------------------------------------------------------------
# Type classification
# --------------------------------------------------------------------------

# V1 type strings that render as a widget rather than an input socket.
V1_WIDGET_PRIMITIVES = {"INT": "int", "FLOAT": "float", "STRING": "string", "BOOLEAN": "bool"}

# V3 io.<X>.Input classes that render as widgets.
V3_WIDGET_CLASSES = {
    "Int": "int",
    "Float": "float",
    "String": "string",
    "Boolean": "bool",
    "Combo": "combo",
    "MultiCombo": "combo",
    "MultiSelect": "combo",
}

# Widget names that ComfyUI's frontend augments with a hidden
# ``control_after_generate`` widget, consuming an extra slot in widgets_values.
SEED_WIDGET_NAMES = {"seed", "noise_seed", "rand_seed"}


def _lit(node: ast.AST) -> Any:
    """Best effort literal evaluation; returns ``None`` when not a literal."""
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _dotted(node: ast.AST) -> str | None:
    """Render ``a.b.c`` attribute chains and bare names as a dotted string."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _io_const(node: ast.AST) -> str | None:
    """Resolve ``IO.STRING`` / ``IO.CLIP`` style constants to their type name.

    ComfyUI's own nodes mix bare string types with the ``IO`` enum, so a parser
    that only understands string literals mis-reads core nodes such as
    ``CLIPTextEncode`` (whose ``clip`` socket would look like a widget).
    """
    ref = _dotted(node)
    if ref and ref.split(".")[0] in ("IO", "io_types"):
        return ref.split(".")[-1].upper()
    return None


def _resolve_type_node(node: ast.AST) -> str | None:
    """Return a ComfyUI type name for a literal or ``IO.X`` reference."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return _io_const(node)


# Core loaders that build their combo list from a helper instead of
# folder_paths.get_filename_list, mapped to the folder they really read.
LIST_FN_FOLDERS = {
    "vae_list": "vae",
    "get_filename_list_with_downloadable": "*",
}


def _folder_from_call(node: ast.AST) -> str | None:
    """Return the folder key of a ``folder_paths.get_filename_list("x")`` call.

    This is the single most valuable signal in the whole catalog: it marks a
    widget as naming a *model file on disk* and says which model directory it
    lives in, which is exactly what an audit needs to enumerate weights.
    """
    if not isinstance(node, ast.Call):
        return None
    fn = _dotted(node.func) or ""
    short = fn.split(".")[-1]
    if short in LIST_FN_FOLDERS:
        return LIST_FN_FOLDERS[short]
    if not fn.endswith("get_filename_list"):
        return None
    if node.args:
        val = _lit(node.args[0])
        if isinstance(val, str):
            return val
        # e.g. get_filename_list(folder_name) - unknown at parse time
        return "*"
    return "*"


# --------------------------------------------------------------------------
# V1 parsing: INPUT_TYPES classmethod
# --------------------------------------------------------------------------


def _v1_spec_to_entry(name: str, spec: ast.AST, section: str) -> dict[str, Any] | None:
    """Convert one ``"name": (TYPE, {opts})`` entry into a catalog record."""
    type_node: ast.AST
    opts: dict[str, Any] = {}

    if isinstance(spec, (ast.Tuple, ast.List)) and spec.elts:
        type_node = spec.elts[0]
        if len(spec.elts) > 1:
            opts = _lit(spec.elts[1]) or {}
    else:
        type_node = spec

    entry: dict[str, Any] = {"name": name, "optional": section == "optional"}

    folder = _folder_from_call(type_node)
    tname = _resolve_type_node(type_node)
    if folder is not None:
        entry.update(kind="combo", widget=True, model_folder=folder)
    elif tname is not None:
        if tname in V1_WIDGET_PRIMITIVES:
            entry.update(kind=V1_WIDGET_PRIMITIVES[tname], widget=True)
        elif tname == "COMBO":
            entry.update(kind="combo", widget=True)
        else:
            entry.update(kind="link", widget=False, type=tname)
    elif isinstance(type_node, (ast.List, ast.Tuple, ast.ListComp, ast.Call, ast.Name, ast.Attribute)):
        # A list literal, comprehension, or a reference such as
        # comfy.samplers.KSampler.SAMPLERS -> combo widget.
        entry.update(kind="combo", widget=True)
        options = _lit(type_node)
        if isinstance(options, list) and all(isinstance(o, (str, int, float)) for o in options):
            entry["options"] = options[:64]
        else:
            ref = _dotted(type_node)
            if ref:
                entry["options_ref"] = ref
            elif isinstance(type_node, ast.Call):
                fnref = _dotted(type_node.func)
                if fnref:
                    entry["options_fn"] = fnref
    else:
        entry.update(kind="link", widget=False, type="*")

    if isinstance(opts, dict):
        if opts.get("forceInput"):
            # Rendered as a socket, not a widget - consumes no widget slot.
            entry.update(kind="link", widget=False, type=entry.get("type", "*"), force_input=True)
        for flag in ("multiline", "image_upload", "video_upload", "audio_upload",
                     "dynamicPrompts", "control_after_generate"):
            if opts.get(flag):
                entry[flag] = True
        if "default" in opts and isinstance(opts["default"], (str, int, float, bool)):
            entry["default"] = opts["default"]
    return entry


def _inherited_entries(cls: ast.ClassDef, classes: dict[str, ast.ClassDef] | None,
                       section: str) -> list[dict[str, Any]]:
    """Entries a class inherits from a base's INPUT_TYPES, for ``**`` spreads."""
    if not classes:
        return []
    for base in cls.bases:
        bname = base.id if isinstance(base, ast.Name) else (_dotted(base) or "").split(".")[-1]
        base_cls = classes.get(bname)
        if base_cls is None or bname == cls.name:
            continue
        rest = {k: v for k, v in classes.items() if k != cls.name}
        parsed = parse_v1_class(base_cls, rest)
        if not parsed:
            continue
        out: list[dict[str, Any]] = []
        for w in parsed["widgets"]:
            if w.get("synthetic"):
                continue
            rec = dict(w)
            rec.update(widget=True, optional=(section == "optional"))
            out.append(rec)
        for i in parsed["inputs"]:
            out.append({"name": i["name"], "type": i["type"], "kind": "link",
                        "widget": False, "optional": i.get("optional", False)})
        if out:
            return out
    return []


def _find_input_types(cls: ast.ClassDef,
                      classes: dict[str, ast.ClassDef] | None = None) -> ast.FunctionDef | None:
    """Find INPUT_TYPES on the class or, failing that, on a base in this file."""
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "INPUT_TYPES":
            return item  # type: ignore[return-value]
    for base in cls.bases:
        bname = base.id if isinstance(base, ast.Name) else (_dotted(base) or "").split(".")[-1]
        if classes and bname in classes and bname != cls.name:
            found = _find_input_types(classes[bname], {k: v for k, v in classes.items() if k != cls.name})
            if found is not None:
                return found
    return None


def parse_v1_class(cls: ast.ClassDef, classes: dict[str, ast.ClassDef] | None = None) -> dict[str, Any] | None:
    fn = _find_input_types(cls, classes)
    if fn is None:
        return None

    entries: list[dict[str, Any]] = []
    for ret in ast.walk(fn):
        if not isinstance(ret, ast.Return) or not isinstance(ret.value, ast.Dict):
            continue
        for sect_key, sect_val in zip(ret.value.keys, ret.value.values):
            section = _lit(sect_key) if sect_key is not None else None
            if section not in ("required", "optional"):
                continue
            if not isinstance(sect_val, ast.Dict):
                continue
            for k, v in zip(sect_val.keys, sect_val.values):
                if k is None:
                    # ``{**super().INPUT_TYPES()["required"], "channel": ...}``
                    # - splice the base class's entries in at this position.
                    entries.extend(_inherited_entries(cls, classes, section))
                    continue
                nm = _lit(k)
                if not isinstance(nm, str):
                    continue
                rec = _v1_spec_to_entry(nm, v, section)
                if rec:
                    entries.append(rec)
        break  # only the first fully-formed return statement

    # Drop duplicates introduced by a spread that repeats an explicit entry,
    # keeping the last definition (which is what the dict literal does).
    deduped: list[dict[str, Any]] = []
    for e in entries:
        for i, prev in enumerate(deduped):
            if prev["name"] == e["name"]:
                deduped[i] = e
                break
        else:
            deduped.append(e)
    entries = deduped

    return _finalise(cls, entries, classes)


# --------------------------------------------------------------------------
# V3 parsing: define_schema -> io.Schema(...)
# --------------------------------------------------------------------------


def parse_v3_schema(call: ast.Call) -> dict[str, Any] | None:
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    node_id = _lit(kw.get("node_id")) if "node_id" in kw else None
    if not isinstance(node_id, str):
        return None

    entries: list[dict[str, Any]] = []
    inputs = kw.get("inputs")
    if isinstance(inputs, (ast.List, ast.Tuple)):
        for elt in inputs.elts:
            rec = _v3_input(elt)
            if rec:
                entries.append(rec)

    out_types: list[str] = []
    outputs = kw.get("outputs")
    if isinstance(outputs, (ast.List, ast.Tuple)):
        for elt in outputs.elts:
            if isinstance(elt, ast.Call):
                ref = _dotted(elt.func) or ""
                m = re.search(r"io\.(\w+)\.Output", "io." + ref if not ref.startswith("io.") else ref)
                out_types.append((m.group(1) if m else ref.split(".")[-2] if "." in ref else "*").upper())

    meta: dict[str, Any] = {
        "node_id": node_id,
        "category": _lit(kw.get("category")) or "",
        "description": (_lit(kw.get("description")) or "")[:400],
        "output_node": bool(_lit(kw.get("is_output_node"))),
        "deprecated": bool(_lit(kw.get("is_deprecated"))),
        "experimental": bool(_lit(kw.get("is_experimental"))),
        "outputs": out_types,
        "schema": "v3",
    }
    return _assemble(meta, entries)


def _v3_input(elt: ast.AST) -> dict[str, Any] | None:
    if not isinstance(elt, ast.Call):
        return None
    ref = _dotted(elt.func)
    if not ref or ".Input" not in ref:
        return None
    io_cls = ref.split(".")[-2]

    name = None
    if elt.args:
        name = _lit(elt.args[0])
    kwargs = {k.arg: k.value for k in elt.keywords if k.arg}
    if name is None and "id" in kwargs:
        name = _lit(kwargs["id"])
    if not isinstance(name, str):
        return None

    entry: dict[str, Any] = {"name": name, "optional": bool(_lit(kwargs.get("optional")))}
    kind = V3_WIDGET_CLASSES.get(io_cls)
    if kind:
        entry.update(kind=kind, widget=True)
        if io_cls in ("Combo", "MultiCombo", "MultiSelect"):
            opt_node = kwargs.get("options")
            folder = _folder_from_call(opt_node) if opt_node is not None else None
            if folder is not None:
                entry["model_folder"] = folder
            else:
                options = _lit(opt_node) if opt_node is not None else None
                if isinstance(options, list) and all(isinstance(o, (str, int, float)) for o in options):
                    entry["options"] = options[:64]
                elif opt_node is not None:
                    ref2 = _dotted(opt_node)
                    if ref2:
                        entry["options_ref"] = ref2
    else:
        entry.update(kind="link", widget=False, type=io_cls.upper())

    if _lit(kwargs.get("force_input")):
        entry.update(kind="link", widget=False, force_input=True)
    for flag, key in (("multiline", "multiline"), ("image_upload", "image_upload"),
                      ("video_upload", "video_upload"), ("control_after_generate", "control_after_generate"),
                      ("dynamicPrompts", "dynamic_prompts")):
        if _lit(kwargs.get(key)):
            entry[flag] = True
    if "upload" in kwargs:
        up = _dotted(kwargs["upload"]) or ""
        if "image" in up.lower():
            entry["image_upload"] = True
        elif "video" in up.lower() or "audio" in up.lower():
            entry["video_upload"] = True
    default = _lit(kwargs.get("default")) if "default" in kwargs else None
    if isinstance(default, (str, int, float, bool)):
        entry["default"] = default
    return entry


# --------------------------------------------------------------------------
# Shared assembly
# --------------------------------------------------------------------------


def _class_attrs(cls: ast.ClassDef) -> dict[str, Any]:
    """Read the class-level constants ComfyUI uses to describe a node."""
    got: dict[str, Any] = {}
    for item in cls.body:
        if not isinstance(item, ast.Assign):
            continue
        for tgt in item.targets:
            if not isinstance(tgt, ast.Name):
                continue
            name = tgt.id
            if name == "RETURN_TYPES":
                if isinstance(item.value, (ast.Tuple, ast.List)):
                    got[name] = [(_resolve_type_node(e) or "*") for e in item.value.elts]
            else:
                val = _lit(item.value)
                if val is not None:
                    got[name] = val
    return got


def _class_meta(cls: ast.ClassDef, classes: dict[str, ast.ClassDef] | None = None) -> dict[str, Any]:
    """Collect node metadata, following base classes declared in the same file.

    ``PreviewImage(SaveImage)`` and friends inherit CATEGORY/RETURN_TYPES, so a
    flat read of the class body loses them.
    """
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    stack = [cls]
    while stack:
        cur = stack.pop(0)
        if cur.name in seen:
            continue
        seen.add(cur.name)
        chain.append(_class_attrs(cur))
        for base in cur.bases:
            bname = base.id if isinstance(base, ast.Name) else (_dotted(base) or "").split(".")[-1]
            if classes and bname in classes:
                stack.append(classes[bname])

    def pick(key: str, default: Any) -> Any:
        for attrs in chain:
            if key in attrs:
                return attrs[key]
        return default

    return {
        "category": pick("CATEGORY", "") or "",
        "description": str(pick("DESCRIPTION", ""))[:400],
        "output_node": bool(pick("OUTPUT_NODE", False)),
        "deprecated": bool(pick("DEPRECATED", False)),
        "experimental": bool(pick("EXPERIMENTAL", False)),
        "outputs": [str(v) for v in pick("RETURN_TYPES", [])],
        "schema": "v1",
    }


def _finalise(cls: ast.ClassDef, entries: list[dict[str, Any]],
              classes: dict[str, ast.ClassDef] | None = None) -> dict[str, Any]:
    return _assemble(_class_meta(cls, classes), entries)


def _assemble(meta: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Split entries into ordered widgets and link inputs.

    Widget order matters: ``widgets_values`` in a UI workflow is positional.
    Required widgets come first in declaration order, then optional ones, and a
    seed widget silently adds a ``control_after_generate`` slot behind it.
    """
    widgets: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for e in entries:
        (widgets if e.get("widget") else links).append(e)

    ordered = [w for w in widgets if not w["optional"]] + [w for w in widgets if w["optional"]]

    slots: list[dict[str, Any]] = []
    for w in ordered:
        rec = {k: v for k, v in w.items() if k not in ("widget", "optional")}
        slots.append(rec)
        if w["name"] in SEED_WIDGET_NAMES or w.get("control_after_generate"):
            slots.append({"name": "control_after_generate", "kind": "combo", "synthetic": True})
        # An upload-capable combo is rendered with a companion button whose
        # state occupies its own slot in widgets_values (LoadImage saves
        # ["photo.png", "image"], not just ["photo.png"]).
        if w.get("image_upload") or w.get("video_upload") or w.get("audio_upload"):
            slots.append({"name": f"{w['name']}_upload_ui", "kind": "combo", "synthetic": True})

    # Numeric primitives get the same control_after_generate treatment as seeds.
    node_hint = str(meta.get("node_id") or "")
    if node_hint in ("PrimitiveInt", "PrimitiveFloat") and slots and not any(
        s.get("name") == "control_after_generate" for s in slots
    ):
        slots.insert(1, {"name": "control_after_generate", "kind": "combo", "synthetic": True})

    out = dict(meta)
    out["widgets"] = slots
    out["inputs"] = [{"name": e["name"], "type": e.get("type", "*"), "optional": e["optional"]} for e in links]
    return out


# --------------------------------------------------------------------------
# File walking
# --------------------------------------------------------------------------


def collect_mappings(tree: ast.Module) -> dict[str, str]:
    """Map display node id -> python class name from NODE_CLASS_MAPPINGS."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id == "NODE_CLASS_MAPPINGS" and isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    key = _lit(k)
                    cls = v.id if isinstance(v, ast.Name) else _dotted(v)
                    if isinstance(key, str) and cls:
                        out[key] = cls.split(".")[-1]
        # NODE_CLASS_MAPPINGS.update({...})
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            if _dotted(node.func.value) == "NODE_CLASS_MAPPINGS" and node.args and isinstance(node.args[0], ast.Dict):
                for k, v in zip(node.args[0].keys, node.args[0].values):
                    key = _lit(k)
                    cls = v.id if isinstance(v, ast.Name) else _dotted(v)
                    if isinstance(key, str) and cls:
                        out[key] = cls.split(".")[-1]
    return out


def collect_display_names(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id == "NODE_DISPLAY_NAME_MAPPINGS" and isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    key, val = _lit(k), _lit(v)
                    if isinstance(key, str) and isinstance(val, str):
                        out[key] = val
    return out


def parse_file(path: str, is_api: bool) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:  # pragma: no cover - defensive
        print(f"  ! syntax error in {path}: {exc}", file=sys.stderr)
        return {}, {}

    by_class: dict[str, dict[str, Any]] = {}
    found: dict[str, dict[str, Any]] = {}
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            rec = parse_v1_class(node, classes)
            if rec is not None:
                by_class[node.name] = rec
            # V3 schema lives inside a define_schema method on the class
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    ref = _dotted(sub.func) or ""
                    if ref.endswith("Schema") and any(k.arg == "node_id" for k in sub.keywords):
                        rec3 = parse_v3_schema(sub)
                        if rec3:
                            nid = rec3.pop("node_id")
                            rec3["source"] = os.path.basename(path)
                            rec3["api_node"] = is_api
                            found[nid] = rec3

    mappings = collect_mappings(tree)
    for nid, cls_name in mappings.items():
        rec = by_class.get(cls_name)
        if rec is None:
            continue
        rec = dict(rec)
        rec["source"] = os.path.basename(path)
        rec["api_node"] = is_api
        found.setdefault(nid, rec)

    return found, collect_display_names(tree)


def build_core_nodes(comfy_root: str) -> dict[str, Any]:
    files: list[tuple[str, bool]] = [(os.path.join(comfy_root, "nodes.py"), False)]
    for sub, is_api in (("comfy_extras", False), ("comfy_api_nodes", True)):
        d = os.path.join(comfy_root, sub)
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith(".py") and not name.startswith("__"):
                    files.append((os.path.join(d, name), is_api))

    nodes: dict[str, Any] = {}
    display: dict[str, str] = {}
    for path, is_api in files:
        if not os.path.exists(path):
            continue
        got, disp = parse_file(path, is_api)
        for k, v in got.items():
            nodes.setdefault(k, v)
        display.update(disp)

    for nid, name in display.items():
        if nid in nodes:
            nodes[nid]["display_name"] = name

    version = "unknown"
    vf = os.path.join(comfy_root, "comfyui_version.py")
    if os.path.exists(vf):
        m = re.search(r'__version__\s*=\s*"([^"]+)"', open(vf).read())
        if m:
            version = m.group(1)

    return {"comfyui_version": version, "nodes": nodes}


# --------------------------------------------------------------------------
# ComfyUI-Manager derived indexes
# --------------------------------------------------------------------------


def _norm_repo(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    url = re.sub(r"\.git$", "", url)
    url = re.sub(r"^https?://(www\.)?", "", url)
    return url.lower()


def build_pack_index(manager_dir: str) -> dict[str, Any]:
    def load(name: str) -> Any:
        with open(os.path.join(manager_dir, name), "r", encoding="utf-8") as fh:
            return json.load(fh)

    custom = load("custom-node-list.json")["custom_nodes"]
    node_map = load("extension-node-map.json")
    try:
        stats = load("github-stats.json")
    except FileNotFoundError:
        stats = {}

    stats_by_repo = {_norm_repo(k): v for k, v in stats.items()}

    packs: dict[str, Any] = {}
    for entry in custom:
        ref = entry.get("reference") or (entry.get("files") or [""])[0]
        key = _norm_repo(ref)
        if not key:
            continue
        st = stats_by_repo.get(key, {})
        rec = {
            "title": entry.get("title", ""),
            "author": entry.get("author", ""),
            "reference": ref,
            "install_type": entry.get("install_type", ""),
            "description": (entry.get("description") or "")[:280],
        }
        if entry.get("id"):
            rec["id"] = entry["id"]
        if entry.get("pip"):
            rec["pip"] = entry["pip"]
        if entry.get("apt_dependency"):
            rec["apt"] = entry["apt_dependency"]
        if st.get("stars") is not None:
            rec["stars"] = st["stars"]
        if st.get("last_update"):
            rec["last_update"] = st["last_update"]
        packs[key] = rec

    # class name -> [pack keys]; a class name can be claimed by several packs,
    # which is itself an auditable collision risk.
    index: dict[str, list[str]] = {}
    for url, payload in node_map.items():
        if not isinstance(payload, list) or not payload:
            continue
        classes = payload[0]
        key = _norm_repo(url)
        meta = payload[1] if len(payload) > 1 and isinstance(payload[1], dict) else {}
        if key not in packs:
            packs[key] = {
                "title": meta.get("title_aux", key.split("/")[-1]),
                "author": key.split("/")[1] if key.count("/") >= 1 else "",
                "reference": url,
                "install_type": "git-clone",
                "description": "",
            }
            st = stats_by_repo.get(key, {})
            if st.get("stars") is not None:
                packs[key]["stars"] = st["stars"]
            if st.get("last_update"):
                packs[key]["last_update"] = st["last_update"]
        for cls in classes:
            if isinstance(cls, str):
                index.setdefault(cls, [])
                if key not in index[cls]:
                    index[cls].append(key)

    # Prefix patterns let us guess a pack for classes not present in the map.
    patterns = [
        {"pattern": e["nodename_pattern"], "repo": _norm_repo(e.get("reference", ""))}
        for e in custom
        if e.get("nodename_pattern")
    ]

    return {"packs": packs, "node_index": index, "nodename_patterns": patterns}


def build_known_models(manager_dir: str) -> dict[str, Any]:
    with open(os.path.join(manager_dir, "model-list.json"), "r", encoding="utf-8") as fh:
        models = json.load(fh)["models"]
    out: dict[str, Any] = {}
    for m in models:
        fn = m.get("filename")
        if not fn:
            continue
        out.setdefault(fn.lower(), {
            "name": m.get("name", ""),
            "type": m.get("type", ""),
            "base": m.get("base", ""),
            "reference": m.get("reference", ""),
            "url": m.get("url", ""),
            "save_path": m.get("save_path", ""),
            "size": m.get("size", ""),
        })
    return out


# --------------------------------------------------------------------------


def write_gz(path: str, payload: Any) -> None:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(raw)
    print(f"  wrote {path}  ({len(raw)/1e6:.2f} MB raw -> {os.path.getsize(path)/1e6:.2f} MB gz)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--comfyui", required=True, help="path to a ComfyUI checkout")
    ap.add_argument("--manager", required=True, help="dir holding ComfyUI-Manager json files")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "core", "knowledge", "data"))
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    print("core nodes:")
    core = build_core_nodes(args.comfyui)
    print(f"  ComfyUI {core['comfyui_version']}: {len(core['nodes'])} node classes")
    n_model_widgets = sum(1 for n in core["nodes"].values() for w in n["widgets"] if w.get("model_folder"))
    print(f"  {n_model_widgets} model-file widgets detected")
    write_gz(os.path.join(out_dir, "core_nodes.json.gz"), core)

    print("node packs:")
    packs = build_pack_index(args.manager)
    print(f"  {len(packs['packs'])} packs, {len(packs['node_index'])} node classes indexed")
    write_gz(os.path.join(out_dir, "node_packs.json.gz"), packs)

    print("known models:")
    known = build_known_models(args.manager)
    print(f"  {len(known)} filenames")
    write_gz(os.path.join(out_dir, "known_models.json.gz"), known)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
