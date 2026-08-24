"""Normalise the two (really three) shapes a ComfyUI workflow arrives in.

* **UI format** - what "Save" / "Export" writes and what people share.  Nodes
  carry ``widgets_values``, a *positional* array with no field names, plus a
  separate ``links`` table.
* **API format** - what "Export (API)" writes and what the ``/prompt`` endpoint
  eats.  Named ``inputs`` per node, links expressed as ``[node_id, slot]``.
* **Embedded** - either of the above hidden in the metadata of a PNG/WebP that
  ComfyUI rendered.

All three collapse into :class:`Workflow`, where every widget value has a name
whenever the node's schema is known.
"""

from __future__ import annotations

import json
import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from . import catalog

# LiteGraph node modes.
MODE_ALWAYS = 0
MODE_ON_EVENT = 1
MODE_NEVER = 2      # "muted" in the UI - node and everything downstream is skipped
MODE_ON_TRIGGER = 3
MODE_BYPASS = 4     # node passes its inputs straight through

MODE_NAMES = {
    MODE_ALWAYS: "active",
    MODE_ON_EVENT: "on-event",
    MODE_NEVER: "muted",
    MODE_ON_TRIGGER: "on-trigger",
    MODE_BYPASS: "bypassed",
}


@dataclass
class InputSlot:
    """One input socket on a node."""

    name: str
    type: str = "*"
    source_node: str | None = None
    source_slot: int = 0
    #: True when the value normally shown as a widget has been converted to a
    #: socket, which means it is driven by upstream logic rather than typed in.
    from_widget: bool = False


@dataclass
class Node:
    id: str
    type: str
    title: str | None = None
    mode: int = MODE_ALWAYS
    widgets: dict[str, Any] = field(default_factory=dict)
    widget_order: list[str] = field(default_factory=list)
    inputs: dict[str, InputSlot] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    #: Names of subgraphs this node lives inside, outermost first.
    path: tuple[str, ...] = ()
    #: Widget names whose value we could not confidently align (UI format only).
    unaligned: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        base = self.title or self.type
        if self.path:
            return f"{'/'.join(self.path)}/{base}"
        return base

    @property
    def enabled(self) -> bool:
        return self.mode == MODE_ALWAYS

    @property
    def mode_name(self) -> str:
        return MODE_NAMES.get(self.mode, f"mode-{self.mode}")

    def widget(self, name: str, default: Any = None) -> Any:
        return self.widgets.get(name, default)

    def driven_input(self, name: str, known_nodes: dict[str, "Node"]) -> bool:
        """True when this input is genuinely fed by another node in the graph.

        A link whose origin is not a real node is a subgraph boundary: the value
        is promoted to the parent's widget and is still typed by a person, so it
        must not be mistaken for an automated feed.
        """
        slot = self.inputs.get(name)
        return bool(slot and slot.source_node and slot.source_node in known_nodes)

    def linked_inputs(self) -> Iterator[InputSlot]:
        for slot in self.inputs.values():
            if slot.source_node is not None:
                yield slot


@dataclass
class Group:
    title: str
    color: str | None = None


@dataclass
class Workflow:
    """A parsed workflow plus everything we learned while parsing it."""

    nodes: dict[str, Node] = field(default_factory=dict)
    groups: list[Group] = field(default_factory=list)
    source_format: str = "unknown"      # "ui" | "api"
    source_path: str | None = None
    #: What to call this workflow when there is no file behind it. The canvas
    #: knows the open workflow's name but there is no path to derive it from,
    #: and reporting every unsaved graph as "workflow" makes a folder of
    #: exported reports unreadable.
    source_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    subgraph_count: int = 0
    #: Node types that are really subgraph instances, not installable nodes.
    subgraph_ids: set[str] = field(default_factory=set)
    raw: dict[str, Any] = field(default_factory=dict)

    # -- traversal helpers -------------------------------------------------

    def __iter__(self) -> Iterator[Node]:
        return iter(self.nodes.values())

    def __len__(self) -> int:
        return len(self.nodes)

    def by_type(self, *types: str) -> list[Node]:
        wanted = set(types)
        return [n for n in self.nodes.values() if n.type in wanted]

    def active(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.enabled]

    def upstream(self, node: Node, input_name: str | None = None) -> list[Node]:
        """Nodes feeding ``node``, optionally only through one named input."""
        out: list[Node] = []
        for name, slot in node.inputs.items():
            if input_name is not None and name != input_name:
                continue
            if slot.source_node and slot.source_node in self.nodes:
                out.append(self.nodes[slot.source_node])
        return out

    def trace(self, node: Node, input_name: str | None = None,
              *, through: Iterable[str] = (), _seen: set[str] | None = None) -> Iterator[Node]:
        """Walk upstream, transparently passing through pass-through nodes.

        ``through`` names node types (Reroute, bypassed nodes, primitives) that
        should be stepped over rather than reported.
        """
        seen = _seen if _seen is not None else set()
        for parent in self.upstream(node, input_name):
            if parent.id in seen:
                continue
            seen.add(parent.id)
            transparent = parent.type in set(through) or parent.mode == MODE_BYPASS
            if transparent:
                yield from self.trace(parent, None, through=through, _seen=seen)
            else:
                yield parent

    def find_producer(self, node: Node, input_name: str,
                      accept: Iterable[str]) -> Node | None:
        """First upstream node of an accepted type reachable from an input."""
        want = set(accept)
        stack = list(self.upstream(node, input_name))
        seen: set[str] = set()
        while stack:
            cur = stack.pop(0)
            if cur.id in seen:
                continue
            seen.add(cur.id)
            if cur.type in want:
                return cur
            stack.extend(self.upstream(cur))
        return None


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

PASSTHROUGH_TYPES = {"Reroute", "Reroute (rgthree)", "ReroutePrimitive|pysssss", "PrimitiveNode"}

#: Nodes that move a value across the canvas by *name* rather than by a link.
#: KJNodes' Set/Get pair is the common one, and heavily-built graphs use dozens
#: of them - a workflow can be almost entirely wired this way. Left unresolved
#: they sever every trace: a prompt goes into a SetNode and reappears out of a
#: GetNode with no edge between them.
VARIABLE_SETTERS = {"SetNode", "Set_", "SetValue", "Anything Everywhere"}
VARIABLE_GETTERS = {"GetNode", "Get_", "GetValue"}


def load(path: str) -> Workflow:
    """Load a workflow from ``.json`` or from PNG/WebP metadata."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".webp", ".jpg", ".jpeg", ".flac"):
        data = extract_embedded(path)
        if data is None:
            raise ValueError(f"no ComfyUI workflow metadata found in {path}")
        wf = from_dict(data)
    else:
        with open(path, "r", encoding="utf-8") as fh:
            wf = from_dict(json.load(fh))
    wf.source_path = path
    return wf


def from_dict(data: dict[str, Any]) -> Workflow:
    """Parse an already-decoded workflow document."""
    if not isinstance(data, dict):
        raise ValueError("workflow JSON must be an object")

    # Some tools wrap the graph, e.g. {"workflow": {...}} or {"prompt": {...}}.
    for key in ("workflow", "prompt"):
        inner = data.get(key)
        if isinstance(inner, dict) and ("nodes" in inner or _looks_like_api(inner)):
            data = inner
            break

    if isinstance(data.get("nodes"), list):
        return _parse_ui(data)
    if _looks_like_api(data):
        return _parse_api(data)
    raise ValueError("unrecognised workflow JSON: expected a 'nodes' list (UI format) "
                     "or node-id keys with 'class_type' (API format)")


def _looks_like_api(data: dict[str, Any]) -> bool:
    values = [v for v in data.values() if isinstance(v, dict)]
    if not values:
        return False
    typed = sum(1 for v in values if "class_type" in v)
    return typed >= max(1, len(values) // 2)


# --------------------------------------------------------------------------
# API format
# --------------------------------------------------------------------------


def _parse_api(data: dict[str, Any]) -> Workflow:
    wf = Workflow(source_format="api", raw=data)
    for node_id, payload in data.items():
        if not isinstance(payload, dict) or "class_type" not in payload:
            continue
        node = Node(
            id=str(node_id),
            type=str(payload.get("class_type", "")),
            title=(payload.get("_meta") or {}).get("title"),
            mode=MODE_ALWAYS,
            raw=payload,
        )
        for name, value in (payload.get("inputs") or {}).items():
            if _is_api_link(value):
                node.inputs[name] = InputSlot(name=name, source_node=str(value[0]),
                                              source_slot=int(value[1]))
            else:
                node.widgets[name] = value
                node.widget_order.append(name)
        wf.nodes[node.id] = node

    _annotate_input_types(wf)
    return wf


def _is_api_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
        and not isinstance(value[0], bool)
    )


def _annotate_input_types(wf: Workflow) -> None:
    """Fill in socket types from the core catalog where we know them."""
    for node in wf.nodes.values():
        schema = catalog.get_node_schema(node.type)
        if not schema:
            continue
        types = {i["name"]: i.get("type", "*") for i in schema.get("inputs", [])}
        for name, slot in node.inputs.items():
            if slot.type == "*" and name in types:
                slot.type = types[name]


# --------------------------------------------------------------------------
# UI format
# --------------------------------------------------------------------------


def _parse_ui(data: dict[str, Any]) -> Workflow:
    wf = Workflow(source_format="ui", raw=data)
    wf.extra = {
        "version": data.get("version"),
        "revision": data.get("revision"),
        "frontend": (data.get("extra") or {}).get("frontendVersion"),
        "comfy_version": (data.get("extra") or {}).get("VHS_latentpreview") and None,
    }
    ds = (data.get("extra") or {}).get("ds")
    if ds:
        wf.extra["viewport"] = ds

    subgraph_defs = _collect_subgraph_defs(data)
    wf.subgraph_count = len(subgraph_defs)
    wf.subgraph_ids = set(subgraph_defs)

    _ingest_ui_graph(wf, data, path=(), subgraph_defs=subgraph_defs, id_prefix="")

    for grp in data.get("groups") or []:
        if isinstance(grp, dict):
            wf.groups.append(Group(title=str(grp.get("title", "")), color=grp.get("color")))

    _resolve_variable_nodes(wf)
    _annotate_input_types(wf)
    return wf


def _resolve_variable_nodes(wf: Workflow) -> None:
    """Rewire Set/Get variable nodes into the real edges they stand for.

    A ``SetNode`` named "width" captures whatever feeds it; every ``GetNode``
    named "width" re-emits it. This walks the names back to the producing node
    and repoints the consumers at it, so upstream tracing sees the graph the
    author actually drew rather than a field of disconnected islands.
    """
    setters: dict[str, tuple[str, int]] = {}
    getters: dict[str, str] = {}

    for node in wf.nodes.values():
        name = _variable_name(node)
        if not name:
            continue
        if node.type in VARIABLE_SETTERS:
            for slot in node.inputs.values():
                if slot.source_node:
                    setters[name] = (slot.source_node, slot.source_slot)
                    break
        elif node.type in VARIABLE_GETTERS:
            getters[node.id] = name

    if not (setters and getters):
        return

    # A setter's own input can come from a getter, so follow the chain - with a
    # bound, because nothing stops an author wiring one in a circle.
    resolved: dict[str, tuple[str, int]] = {}
    for node_id, name in getters.items():
        target = setters.get(name)
        seen = {node_id}
        for _ in range(8):
            if target is None or target[0] not in getters or target[0] in seen:
                break
            seen.add(target[0])
            target = setters.get(getters[target[0]])
        if target is not None:
            resolved[node_id] = target

    rewired = 0
    for node in wf.nodes.values():
        for slot in node.inputs.values():
            if slot.source_node in resolved:
                slot.source_node, slot.source_slot = resolved[slot.source_node]
                rewired += 1
    if rewired:
        wf.extra["variable_links_resolved"] = rewired


def _variable_name(node: Node) -> str:
    """The variable a Set/Get node is named for."""
    if node.type not in VARIABLE_SETTERS and node.type not in VARIABLE_GETTERS:
        return ""
    for key in ("widget_0", "value", "name", "Constant"):
        value = node.widgets.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    values = node.raw.get("widgets_values")
    if isinstance(values, list) and values and isinstance(values[0], str):
        return values[0].strip()
    return str((node.properties or {}).get("previousName") or "").strip()


def _collect_subgraph_defs(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Gather subgraph definitions, which may nest inside one another."""
    out: dict[str, dict[str, Any]] = {}

    def walk(container: dict[str, Any]) -> None:
        defs = (container.get("definitions") or {}).get("subgraphs") or []
        for sg in defs:
            if isinstance(sg, dict) and sg.get("id"):
                out[str(sg["id"])] = sg
                walk(sg)

    walk(data)
    return out


def _ingest_ui_graph(wf: Workflow, graph: dict[str, Any], path: tuple[str, ...],
                     subgraph_defs: dict[str, dict[str, Any]], id_prefix: str,
                     depth: int = 0) -> None:
    if depth > 8:
        wf.warnings.append("subgraph nesting deeper than 8 levels was not expanded")
        return

    link_map = _build_link_map(graph, wf, id_prefix)

    for raw in graph.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        node_id = f"{id_prefix}{raw.get('id')}"
        node_type = str(raw.get("type", ""))

        node = Node(
            id=node_id,
            type=node_type,
            title=raw.get("title"),
            mode=int(raw.get("mode", MODE_ALWAYS) or 0),
            properties=dict(raw.get("properties") or {}),
            path=path,
            raw=raw,
        )

        linked_widget_names: set[str] = set()
        for idx, slot in enumerate(raw.get("inputs") or []):
            if not isinstance(slot, dict):
                continue
            name = str(slot.get("name", f"input_{idx}"))
            widget_meta = slot.get("widget")
            is_widget_input = isinstance(widget_meta, dict) and bool(widget_meta.get("name"))
            if is_widget_input:
                linked_widget_names.add(str(widget_meta.get("name")))
            link_id = slot.get("link")
            src = link_map.get(link_id) if link_id is not None else None
            node.inputs[name] = InputSlot(
                name=name,
                type=str(slot.get("type", "*") or "*"),
                source_node=src[0] if src else None,
                source_slot=src[1] if src else 0,
                from_widget=is_widget_input,
            )

        _assign_widgets(node, raw.get("widgets_values"), linked_widget_names, wf)
        wf.nodes[node_id] = node

        # A node whose type is a subgraph id expands into its definition.
        sg = subgraph_defs.get(node_type)
        if sg is not None:
            sg_name = str(sg.get("name") or raw.get("title") or node_type[:8])
            _ingest_ui_graph(wf, sg, path=path + (sg_name,), subgraph_defs=subgraph_defs,
                             id_prefix=f"{node_id}:", depth=depth + 1)


def _build_link_map(graph: dict[str, Any], wf: Workflow,
                    id_prefix: str = "") -> dict[Any, tuple[str, int]]:
    """link id -> (origin node id, origin slot), for both link encodings.

    Ids are namespaced with the same prefix used for nodes so that links inside
    an expanded subgraph resolve to the expanded node ids rather than colliding
    with the parent graph.
    """
    out: dict[Any, tuple[str, int]] = {}
    links = graph.get("links") or []
    for link in links:
        try:
            if isinstance(link, (list, tuple)) and len(link) >= 5:
                out[link[0]] = (f"{id_prefix}{link[1]}", int(link[2]))
            elif isinstance(link, dict):
                lid = link.get("id")
                origin = link.get("origin_id", link.get("origin"))
                slot = link.get("origin_slot", 0)
                if lid is not None and origin is not None:
                    out[lid] = (f"{id_prefix}{origin}", int(slot or 0))
        except (TypeError, ValueError):
            wf.warnings.append(f"could not decode link entry: {link!r:.80}")
    return out


def _assign_widgets(node: Node, values: Any, linked_widget_names: set[str], wf: Workflow) -> None:
    """Give positional ``widgets_values`` their real names.

    ComfyUI writes widget values as a bare array in graph order.  We rebuild the
    name sequence from the node's schema and try a few alignments, because a
    workflow saved on an older frontend can be missing the synthetic
    ``control_after_generate`` slot, and widgets converted to inputs may or may
    not still occupy a slot depending on the frontend version.
    """
    if values is None:
        return

    if isinstance(values, dict):
        # A few custom nodes persist a mapping instead of an array.
        for k, v in values.items():
            node.widgets[str(k)] = v
            node.widget_order.append(str(k))
        return

    if not isinstance(values, list):
        node.widgets["value"] = values
        node.widget_order.append("value")
        return

    schema = catalog.get_node_schema(node.type)
    if not schema:
        _assign_anonymous(node, values)
        return

    slots = schema.get("widgets") or []
    by_name = {w["name"]: w for w in slots}
    full = [w["name"] for w in slots]
    no_synthetic = [w["name"] for w in slots if not w.get("synthetic")]
    no_linked = [w["name"] for w in slots if w["name"] not in linked_widget_names]
    minimal = [w["name"] for w in slots
               if not w.get("synthetic") and w["name"] not in linked_widget_names]

    # Nodes whose input set varies with a mode widget, plus frontend-only state
    # widgets, mean an exact length match is not always available.  Score each
    # candidate ordering on how well the declared widget kinds match the actual
    # value types and take the best; leading widgets are reliably in order, so a
    # prefix alignment is correct even when trailing values are surplus.
    best_names, best_score = full, -2.0
    for candidate in (full, no_linked, no_synthetic, minimal):
        score = _alignment_score(candidate, by_name, values)
        if score > best_score:
            best_names, best_score = candidate, score

    names = best_names
    # Only a genuine mismatch is worth flagging: with no named widgets, or no
    # stored values, there is nothing that could have been mis-assigned.
    if names and values and min(len(names), len(values)) > 0 and best_score < 0.75:
        node.unaligned = True
        wf.warnings.append(
            f"node {node.id} ({node.type}): {len(values)} widget values do not fit the "
            f"known schema ({len(full)} widgets); values aligned positionally"
        )

    for name, value in zip(names, values):
        node.widgets[name] = value
        node.widget_order.append(name)

    # Keep any surplus values so nothing is lost from the audit.
    for idx in range(len(names), len(values)):
        key = f"widget_{idx}"
        node.widgets[key] = values[idx]
        node.widget_order.append(key)


def _value_fits(slot: dict[str, Any] | None, value: Any) -> float:
    """How well a stored value matches a declared widget kind (0..1)."""
    if value is None:
        return 0.5
    kind = (slot or {}).get("kind", "")
    if kind == "int":
        return 1.0 if isinstance(value, int) and not isinstance(value, bool) else 0.0
    if kind == "float":
        return 1.0 if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
    if kind == "bool":
        return 1.0 if isinstance(value, bool) else 0.0
    if kind == "string":
        return 1.0 if isinstance(value, str) else 0.0
    if kind == "combo":
        options = (slot or {}).get("options")
        if options and isinstance(value, str):
            return 1.0 if value in options else 0.6
        return 1.0 if isinstance(value, (str, int, float, bool)) else 0.3
    return 0.5


def _alignment_score(names: list[str], by_name: dict[str, Any], values: list[Any]) -> float:
    pairs = min(len(names), len(values))
    if pairs == 0:
        return 0.0
    hits = sum(_value_fits(by_name.get(n), values[i]) for i, n in enumerate(names[:pairs]))
    return hits / pairs - 0.02 * abs(len(names) - len(values))


def _assign_anonymous(node: Node, values: list[Any]) -> None:
    """Name widgets on a node we have no schema for (i.e. a custom node)."""
    for idx, value in enumerate(values):
        key = f"widget_{idx}"
        node.widgets[key] = value
        node.widget_order.append(key)


# --------------------------------------------------------------------------
# Embedded metadata
# --------------------------------------------------------------------------


def extract_embedded(path: str) -> dict[str, Any] | None:
    """Pull a workflow out of an image ComfyUI generated.

    ComfyUI stashes the UI graph under a ``workflow`` key and the API prompt
    under ``prompt`` in PNG text chunks (and in EXIF for WebP/JPEG).
    """
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        blob = fh.read()

    chunks: dict[str, str] = {}
    if ext == ".png":
        chunks = _png_text_chunks(blob)
    else:
        chunks = _scan_for_json(blob)

    for key in ("workflow", "prompt"):
        raw = chunks.get(key)
        if not raw:
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None


def _png_text_chunks(blob: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return out
    pos = 8
    while pos + 8 <= len(blob):
        try:
            (length,) = struct.unpack(">I", blob[pos:pos + 4])
        except struct.error:
            break
        ctype = blob[pos + 4:pos + 8]
        payload = blob[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IEND":
            break
        if ctype == b"tEXt":
            key, _, val = payload.partition(b"\x00")
            out[key.decode("latin-1", "replace")] = val.decode("utf-8", "replace")
        elif ctype == b"iTXt":
            parts = payload.split(b"\x00", 5)
            if len(parts) >= 6:
                key = parts[0].decode("latin-1", "replace")
                compressed = parts[1] == b"\x01"
                text = parts[5]
                if compressed:
                    try:
                        text = zlib.decompress(text)
                    except zlib.error:
                        continue
                out[key] = text.decode("utf-8", "replace")
        elif ctype == b"zTXt":
            key, _, rest = payload.partition(b"\x00")
            try:
                out[key.decode("latin-1", "replace")] = zlib.decompress(rest[1:]).decode("utf-8", "replace")
            except zlib.error:
                continue
    return out


def _scan_for_json(blob: bytes) -> dict[str, str]:
    """Best-effort recovery of ``Key:{json}`` metadata from non-PNG containers."""
    out: dict[str, str] = {}
    text = blob.decode("latin-1", "replace")
    for key in ("workflow", "prompt"):
        for match in re.finditer(rf"{key}\s*[:=]\s*(\{{)", text, re.IGNORECASE):
            start = match.start(1)
            depth, idx = 0, start
            while idx < len(text):
                if text[idx] == "{":
                    depth += 1
                elif text[idx] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                idx += 1
            candidate = text[start:idx + 1]
            try:
                json.loads(candidate)
            except json.JSONDecodeError:
                continue
            out[key] = candidate
            break
    return out
