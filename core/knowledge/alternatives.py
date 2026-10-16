"""Commercially usable models that do the same job as a blocked one.

``data/alternatives.json`` says only which models are functional equivalents.
Whether an alternative may be used commercially is never stored there: it is
read from the licence knowledge base when the alternative is offered, and an
option whose licence does not permit commercial use is not offered at all. So
the licences stay in one place, and this file cannot contradict them.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from ..records import ModelRef
from . import licences as licences_mod

DATA = os.path.join(os.path.dirname(__file__), "data", "alternatives.json")

#: Licence readings an alternative may have and still be offered.
USABLE = ("yes", "conditional")


def load(path: str = DATA) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"alternatives": []}


def suggest(filename: str, installed: Iterable[str] = (),
            matcher: licences_mod.LicenceMatcher | None = None,
            data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Alternatives for one weight file, installed ones first.

    ``installed`` is the basenames of the weights this ComfyUI can see; an
    installed alternative names the file to switch to.
    """
    name = os.path.basename(filename).lower()
    data = data if data is not None else load()
    matcher = matcher or licences_mod.LicenceMatcher()
    have = [os.path.basename(n) for n in installed]
    out: list[dict[str, Any]] = []
    for group in data.get("alternatives", []):
        if not any(p in name for p in group.get("for", [])):
            continue
        for option in group.get("options", []):
            patterns = [p.lower() for p in option.get("match", [])]
            skip = [p.lower() for p in option.get("exclude", [])]
            local = next((n for n in have if any(p in n.lower() for p in patterns)
                          and not any(x in n.lower() for x in skip)), "")
            probe = local or f"{patterns[0]}.safetensors" if patterns else ""
            if not probe:
                continue
            lic = matcher.for_model(ModelRef(filename=probe))
            if lic.commercial_use not in USABLE:
                continue
            out.append({"name": option.get("name", ""), "task": group.get("task", ""),
                        "licence": lic.name, "commercial_use": lic.commercial_use,
                        "installed": bool(local), "filename": local,
                        "note": option.get("note", "")})
    return sorted(out, key=lambda a: (not a["installed"], a["commercial_use"] != "yes"))
