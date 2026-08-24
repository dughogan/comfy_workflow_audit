"""How old the licence knowledge is, and how to replace it.

The knowledge base is hand-curated and accurate as of a date. Licences move:
Stability relicensed SD3 mid-flight, Black Forest Labs revised the FLUX dev
terms, and both were the sort of change that turns a cleared model into an
uncleared one overnight. A tool that reports last year's terms with this year's
confidence is worse than one that admits it does not know.

So the report always says how old its knowledge is, the age is loud once it
passes the point where a term is likely to have moved under it, and there is a
one-command path to a newer file that does not involve reinstalling the pack.

The update fetches raw JSON from the project's own repository over HTTPS. It is
never automatic: a licence knowledge base that changes without anyone noticing
is how a delivery gets cleared against terms nobody read.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass
from typing import Any

#: Where a newer knowledge base comes from. The raw endpoint rather than the
#: API, so no token is needed and no rate limit applies in practice.
DEFAULT_SOURCE = ("https://raw.githubusercontent.com/dughogan/comfyaudit"
                  "/main/comfyaudit/core/knowledge/data/licences.json")

#: Past this, say so in passing. Licences do not usually move this fast, but
#: enough has changed in a quarter to be worth a glance.
STALE_DAYS = 120

#: Past this, say so prominently. Two of the licences in this base changed
#: within a year of publication.
OLD_DAYS = 270


@dataclass
class Freshness:
    """The age of the bundled knowledge, and what to say about it."""

    version: str = ""
    checked: str = ""
    age_days: int | None = None
    state: str = "unknown"        # current | ageing | old | unknown
    message: str = ""
    source: str = DEFAULT_SOURCE

    @property
    def worth_saying(self) -> bool:
        return self.state in ("ageing", "old", "unknown")

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "checked": self.checked,
                "age_days": self.age_days, "state": self.state,
                "message": self.message}


def assess(metadata: dict[str, Any], today: _dt.date | None = None) -> Freshness:
    """Read the knowledge base's own dates and say how much to trust them."""
    out = Freshness(version=str(metadata.get("version", "")),
                    checked=str(metadata.get("checked", "")))
    today = today or _dt.date.today()

    try:
        checked = _dt.date.fromisoformat(out.checked)
    except ValueError:
        out.state = "unknown"
        out.message = ("This licence knowledge base carries no check date, so "
                       "there is no way to tell how current it is. Verify "
                       "anything that gates a delivery at its source.")
        return out

    out.age_days = max(0, (today - checked).days)

    if out.age_days >= OLD_DAYS:
        out.state = "old"
        out.message = (
            f"This licence knowledge is {_months(out.age_days)} old, checked "
            f"{out.checked}. Terms have moved in less time than that - Stability "
            "and Black Forest Labs both relicensed mid-flight - so treat every "
            "entry here as needing confirmation at source. "
            "`comfyaudit update-knowledge` fetches a newer file.")
    elif out.age_days >= STALE_DAYS:
        out.state = "ageing"
        out.message = (
            f"This licence knowledge was last checked {out.checked}, "
            f"{_months(out.age_days)} ago. Worth refreshing with "
            "`comfyaudit update-knowledge` before it gates anything.")
    else:
        out.state = "current"
        out.message = (f"Licence knowledge checked {out.checked}, "
                       f"{_days(out.age_days)} ago.")
    return out


def fetch(source: str = DEFAULT_SOURCE, timeout: int = 20) -> dict[str, Any]:
    """Download a knowledge base and check it is one before returning it."""
    import urllib.request

    request = urllib.request.Request(
        source, headers={"User-Agent": "comfyaudit/update-knowledge"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")

    data = json.loads(payload)
    if not isinstance(data, dict) or "licences" not in data:
        raise ValueError(f"{source} is not a comfyaudit licence knowledge base")
    if not isinstance(data.get("licences"), dict) or not data["licences"]:
        raise ValueError(f"{source} contains no licence definitions")
    return data


def install(data: dict[str, Any], path: str) -> str:
    """Write a fetched knowledge base, keeping the previous one alongside.

    The old file is kept rather than replaced, because "the licence changed" and
    "the knowledge base changed" look identical from a report and only one of
    them is the tool's fault.
    """
    if os.path.isfile(path):
        backup = path + ".previous"
        os.replace(path, backup)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return path


def compare(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What actually changed, so an update can be reviewed rather than trusted."""
    old_lic, new_lic = old.get("licences", {}), new.get("licences", {})
    changed = []
    for key in sorted(set(old_lic) & set(new_lic)):
        before, after = old_lic[key], new_lic[key]
        moved = [field for field in ("commercial_use", "fee", "redistribution",
                                     "conditions")
                 if before.get(field) != after.get(field)]
        if moved:
            changed.append({"licence": key,
                            "name": after.get("name", key),
                            "fields": moved,
                            "was": {f: before.get(f) for f in moved},
                            "now": {f: after.get(f) for f in moved}})
    return {
        "from_version": old.get("version", ""),
        "to_version": new.get("version", ""),
        "added": sorted(set(new_lic) - set(old_lic)),
        "removed": sorted(set(old_lic) - set(new_lic)),
        "changed": changed,
        "model_rules": len(new.get("models", [])) - len(old.get("models", [])),
    }


def _months(days: int) -> str:
    if days < 60:
        return _days(days)
    return f"{days // 30} months"


def _days(days: int) -> str:
    if days <= 1:
        return "a day"
    return f"{days} days"
