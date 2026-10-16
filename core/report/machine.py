"""Machine-readable data inside the human reports.

Every HTML document ComfyAudit packages is read by people, and may also be
read by software: a studio's asset system, or a "legal agent" checking the
package. Scraping prose and tables for that is fragile, so each page carries
the data it was drawn from, exactly, in one JSON block:

    <script type="application/json" id="comfyaudit-data">{"schema": ..., ...}</script>

Browsers do not run or show it. ``schema`` names the document type and its
version (``comfyaudit/<kind>/<n>``); a reader should check it before trusting
field names. The delivery package's ``<shot>.package.json`` indexes every file.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from .. import __version__

ELEMENT_ID = "comfyaudit-data"
DISCLAIMER = ("Produced by an automated tool from the workflow's recorded history and "
              "ComfyAudit's knowledge bases. It is evidence to take to counsel, not legal advice.")


def stamp(kind: str, data: dict[str, Any], version: int = 1) -> dict[str, Any]:
    """``data`` with the header every machine-readable document carries, first."""
    head = {"schema": f"comfyaudit/{kind}/{version}", "comfyaudit_version": __version__,
            "generated_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "disclaimer": DISCLAIMER}
    # The header always wins: a reader keys on ``schema`` before anything else.
    return {**head, **{k: v for k, v in data.items() if k not in head}}


def dumps(data: Any) -> str:
    """JSON that is safe inside a <script> element: no '<' survives to close it or open a comment.

    ``\\u003c`` is the same character to any JSON parser."""
    return json.dumps(data, default=str, ensure_ascii=False).replace("<", "\\u003c")


def block(kind: str, data: dict[str, Any], version: int = 1) -> str:
    return (f"<script type='application/json' id='{ELEMENT_ID}'>"
            f"{dumps(stamp(kind, data, version))}</script>")


def read(html: str) -> dict[str, Any]:
    """The data block of a page this module wrote (for tests and for tools)."""
    start = html.index(f"id='{ELEMENT_ID}'>") + len(f"id='{ELEMENT_ID}'>")
    end = html.index("</script>", start)
    return json.loads(html[start:end])
