"""Recover the prompts, and work out which ones are positive and which negative.

Polarity is not stored anywhere in a workflow - it is implied by which sampler
input the conditioning eventually reaches.  So we walk backwards from every
sampler's ``positive`` and ``negative`` sockets and label whatever text nodes we
find on the way.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .. import catalog
from ..graph import MODE_BYPASS, Node, Workflow
from ..records import PromptRef

NOTE_TYPES = {"Note", "MarkdownNote", "Note Plus (mtb)", "NoteNode"}

# Prompt-embedded syntax we want to surface, because each one is a dependency:
# an embedding is a weight file, a wildcard needs a text corpus on disk, and
# ``{a|b}`` means the prompt is not the same on every run.
EMBEDDING_RE = re.compile(r"embedding:\s*([\w./\\-]+)", re.IGNORECASE)
INLINE_LORA_RE = re.compile(r"<lora:([^>]+)>", re.IGNORECASE)
WILDCARD_RE = re.compile(r"__([\w./-]+)__")
DYNAMIC_RE = re.compile(r"\{[^{}]*\|[^{}]*\}")

TEXTISH_TYPE_RE = re.compile(r"text|prompt|encode|clip|string|caption|llm|gpt|instruct", re.IGNORECASE)

# Widget names that hold free text but are not creative prompts.
NON_PROMPT_WIDGETS = {"filename_prefix", "filename", "path", "folder", "output_path",
                      "url", "format", "font", "regex", "delimiter", "separator",
                      "find", "replace", "expression", "sigmas", "api_key", "headers",
                      "model", "model_name", "mode", "device", "dtype"}

# ...and names that mean the value is code or data rather than language.
NON_PROMPT_NAME_RE = re.compile(
    r"regex|expression|shader|sigmas|_code$|^code$|script|json|schema|filename|filepath|"
    r"_path$|^path|url|token|key$|glsl|css|html",
    re.IGNORECASE,
)

# Widget names that state their own polarity, used when graph wiring cannot
# (API nodes take a prompt string directly rather than conditioning).
NEGATIVE_NAME_RE = re.compile(r"negative", re.IGNORECASE)
POSITIVE_NAME_RE = re.compile(r"^(prompt|positive|positive_prompt|text_positive)$", re.IGNORECASE)
SYSTEM_NAME_RE = re.compile(r"system|instruction", re.IGNORECASE)


def extract(wf: Workflow) -> tuple[list[PromptRef], list[PromptRef]]:
    """Return ``(prompts, notes)``."""
    prompts: list[PromptRef] = []
    notes: list[PromptRef] = []

    for node in wf.nodes.values():
        for ref in _texts_from_node(node, wf.nodes):
            (notes if node.type in NOTE_TYPES else prompts).append(ref)

    _assign_polarity(wf, prompts)
    return prompts, notes


def _texts_from_node(node: Node, known_nodes: dict[str, Node]) -> list[PromptRef]:
    schema = catalog.get_node_schema(node.type)
    slots = {w["name"]: w for w in (schema.get("widgets") or [])} if schema else {}
    out: list[PromptRef] = []

    for name in node.widget_order:
        value = node.widgets.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        if name in NON_PROMPT_WIDGETS or NON_PROMPT_NAME_RE.search(name):
            continue

        slot = slots.get(name)
        if slot is not None:
            if not (slot.get("multiline") or (slot.get("kind") == "string" and _texty(value))):
                continue
        elif not _unschemad_looks_like_prompt(node, value):
            continue

        ref = PromptRef(
            text=value,
            node_id=node.id,
            node_type=node.type,
            node_label=node.label,
            widget=name,
            enabled=node.enabled,
            char_count=len(value),
            token_estimate=_estimate_tokens(value),
        )
        _annotate_syntax(ref)
        # A text socket wired to a real upstream node means the value shown here
        # is dead - the text arrives at run time and can be scripted.
        if node.driven_input(name, known_nodes):
            ref.driven_by_link = True
        out.append(ref)

    return out


def _texty(value: str) -> bool:
    return len(value) > 24 or " " in value.strip()


def _unschemad_looks_like_prompt(node: Node, value: str) -> bool:
    """Heuristic for custom nodes we have no schema for."""
    if catalog.looks_like_model_file(value) or catalog.asset_kind(value):
        return False
    if re.match(r"^[\w./\\:-]+$", value.strip()):
        return False        # a bare path or identifier
    if TEXTISH_TYPE_RE.search(node.type):
        return len(value.strip()) >= 8
    return len(value.strip()) >= 60 and " " in value


def _estimate_tokens(text: str) -> int:
    """Rough CLIP token count - enough to flag prompts over the 77-token window."""
    words = re.findall(r"[A-Za-z0-9']+|[^\sA-Za-z0-9]", text)
    return int(round(len(words) * 1.3))


def _annotate_syntax(ref: PromptRef) -> None:
    ref.embeddings = sorted({m.group(1) for m in EMBEDDING_RE.finditer(ref.text)})
    ref.inline_loras = sorted({m.group(1) for m in INLINE_LORA_RE.finditer(ref.text)})
    ref.wildcards = sorted({m.group(1) for m in WILDCARD_RE.finditer(ref.text)})
    ref.dynamic_syntax = bool(DYNAMIC_RE.search(ref.text))


# --------------------------------------------------------------------------
# Polarity
# --------------------------------------------------------------------------


def sampler_nodes(wf: Workflow) -> list[Node]:
    """Nodes that consume both a positive and a negative conditioning input."""
    out = []
    for node in wf.nodes.values():
        names = set(node.inputs) | set(node.widgets)
        if "positive" in names and "negative" in names:
            out.append(node)
    return out


def _assign_polarity(wf: Workflow, prompts: list[PromptRef]) -> None:
    by_node: dict[str, list[PromptRef]] = {}
    for ref in prompts:
        by_node.setdefault(ref.node_id, []).append(ref)

    positive: dict[str, set[str]] = {}
    negative: dict[str, set[str]] = {}

    for sampler in sampler_nodes(wf):
        for polarity, bucket in (("positive", positive), ("negative", negative)):
            for node_id in _ancestors(wf, sampler, polarity):
                bucket.setdefault(node_id, set()).add(sampler.label)

    for node_id, refs in by_node.items():
        in_pos, in_neg = node_id in positive, node_id in negative
        for ref in refs:
            if in_pos and in_neg:
                ref.polarity = "both"
            elif in_pos:
                ref.polarity = "positive"
            elif in_neg:
                ref.polarity = "negative"
            else:
                ref.polarity = _polarity_from_name(ref)
            consumers = sorted(positive.get(node_id, set()) | negative.get(node_id, set()))
            ref.consumers = consumers


def _polarity_from_name(ref: PromptRef) -> str:
    """Fall back to the widget's own name when no sampler wiring explains it."""
    if SYSTEM_NAME_RE.search(ref.widget):
        return "system"
    if NEGATIVE_NAME_RE.search(ref.widget) or NEGATIVE_NAME_RE.search(ref.node_label):
        return "negative"
    if POSITIVE_NAME_RE.match(ref.widget):
        return "positive"
    return "unknown"


def _ancestors(wf: Workflow, node: Node, input_name: str) -> set[str]:
    """Every node id reachable upstream from one named input."""
    seen: set[str] = set()
    stack = [p.id for p in wf.upstream(node, input_name)]
    while stack:
        current = stack.pop()
        if current in seen or current not in wf.nodes:
            continue
        seen.add(current)
        stack.extend(p.id for p in wf.upstream(wf.nodes[current]))
    return seen


def collect_prompt_dependencies(prompts: Iterable[PromptRef]) -> dict[str, list[Any]]:
    """Aggregate the embedded dependencies found across all prompts."""
    embeddings: set[str] = set()
    loras: set[str] = set()
    wildcards: set[str] = set()
    dynamic = False
    for ref in prompts:
        embeddings.update(ref.embeddings)
        loras.update(ref.inline_loras)
        wildcards.update(ref.wildcards)
        dynamic = dynamic or ref.dynamic_syntax
    return {
        "embeddings": sorted(embeddings),
        "inline_loras": sorted(loras),
        "wildcards": sorted(wildcards),
        "dynamic_syntax": dynamic,
    }
