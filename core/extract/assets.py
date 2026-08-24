"""External inputs and outputs: media the workflow reads, and files it writes.

Assets are where a workflow stops being self-contained.  An absolute path from
somebody's laptop, a file that only exists in one artist's ``input/`` folder, or
a URL fetched at run time will each break the moment the workflow moves to a
render farm, so all three are surfaced explicitly.
"""

from __future__ import annotations

import re
from typing import Any

from .. import catalog
from ..graph import Node, Workflow
from ..records import AssetRef

URL_RE = re.compile(r"^(https?|ftp|s3|gs)://", re.IGNORECASE)
ABS_PATH_RE = re.compile(r"^(/|~|[A-Za-z]:[\\/]|\\\\)")

# Widgets that name an output destination rather than an input asset.
OUTPUT_WIDGETS = {"filename_prefix", "output_path", "output_dir", "save_path", "output_file"}


def extract(wf: Workflow) -> tuple[list[AssetRef], list[AssetRef]]:
    """Return ``(inputs, outputs)``."""
    inputs: list[AssetRef] = []
    outputs: list[AssetRef] = []

    for node in wf.nodes.values():
        schema = catalog.get_node_schema(node.type)
        slots = {w["name"]: w for w in (schema.get("widgets") or [])} if schema else {}
        is_output_node = bool(schema and schema.get("output_node"))

        for name in node.widget_order:
            value = node.widgets.get(name)
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text or catalog.looks_like_model_file(text):
                continue

            slot = slots.get(name) or {}
            upload = bool(slot.get("image_upload") or slot.get("video_upload")
                          or slot.get("audio_upload"))

            if name in OUTPUT_WIDGETS or (is_output_node and "prefix" in name):
                outputs.append(AssetRef(
                    value=text, kind="output-path", node_id=node.id, node_type=node.type,
                    node_label=node.label, widget=name, enabled=node.enabled,
                    absolute_path=bool(ABS_PATH_RE.match(text)),
                ))
                continue

            kind = catalog.asset_kind(text)
            is_url = bool(URL_RE.match(text))
            is_abs = bool(ABS_PATH_RE.match(text))

            if not (upload or kind or is_url or is_abs):
                continue
            if not kind and not upload and not is_url and is_abs and len(text) > 260:
                continue

            ref = AssetRef(
                value=text,
                kind="url" if is_url else (kind or _kind_from_slot(slot) or "file"),
                node_id=node.id,
                node_type=node.type,
                node_label=node.label,
                widget=name,
                enabled=node.enabled,
                upload_widget=upload,
                absolute_path=is_abs and not is_url,
            )
            if upload:
                ref.notes.append("supplied through an upload widget - a person picks this file")
            if is_url:
                ref.notes.append("fetched over the network at run time")
            if ref.absolute_path:
                ref.notes.append("absolute path - will not resolve on another machine")
            if not node.enabled:
                ref.notes.append(f"node is {node.mode_name}")
            inputs.append(ref)

        # Output nodes with no filename widget still produce files.
        if is_output_node and not any(o.node_id == node.id for o in outputs):
            outputs.append(AssetRef(
                value="(default output location)", kind="output-path", node_id=node.id,
                node_type=node.type, node_label=node.label, enabled=node.enabled,
            ))

    return inputs, outputs


def _kind_from_slot(slot: dict[str, Any]) -> str | None:
    if slot.get("image_upload"):
        return "image"
    if slot.get("video_upload"):
        return "video"
    if slot.get("audio_upload"):
        return "audio"
    return None
