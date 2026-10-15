"""Read the studio profile out of ComfyUI's own settings.

A studio profile describes the facility, not the workflow. Territory, revenue
band and what ships are the same on Monday as they were on Friday, and the same
for every graph on the machine - so making someone wire a node into each one
would be asking them to restate a constant.

ComfyUI keeps front-end settings in ``<user dir>/<user>/comfy.settings.json``,
written by the settings dialog. The web extension registers this pack's entries
there; this module reads them back on the Python side, where the audit runs.

Everything here is optional. No ComfyUI, no settings file, or an empty profile
all end the same way: nothing is determined, and the report stays descriptive.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..core.score import clearance

#: Setting ids, as registered by ``web/comfyaudit.js``. ComfyUI namespaces its
#: settings by dotted id, and the prefix is what groups them into one panel.
PREFIX = "ComfyAudit"

TERRITORY = f"{PREFIX}.Studio.Territory"
REVENUE = f"{PREFIX}.Studio.Revenue"
SHIPS = f"{PREFIX}.Studio.Ships"
TRAINS = f"{PREFIX}.Studio.TrainsModels"
LIKENESS = f"{PREFIX}.Studio.Likeness"
LABEL = f"{PREFIX}.Studio.Label"

#: Not part of the profile - it describes what the facility has decided rather
#: than what it is - but it lives in the same settings panel for the same
#: reason: it is a property of the installation, not of any one workflow.
REGISTRY = f"{PREFIX}.Registry.Path"

#: What the settings dialog shows, and the key each choice maps to. The dialog
#: is read by people, the engine is not.
TERRITORY_OPTIONS = {
    "not set": "",
    "United States": "US",
    "European Union": "EU",
    "United Kingdom": "GB",
    "South Korea": "KR",
    "Canada": "CA",
    "Australia": "AU",
    "Japan": "JP",
    "China": "CN",
    "India": "IN",
    "elsewhere": "OTHER",
}

REVENUE_OPTIONS = {
    "not set": "unknown",
    "under $1M": "under-1m",
    "$1M - $10M": "1m-10m",
    "$10M - $20M": "10m-20m",
    "$20M - $100M": "20m-100m",
    "over $100M": "over-100m",
}

SHIP_OPTIONS = {
    "not set": "unknown",
    "finished frames to a client": "deliverable-only",
    "nothing leaves the building": "internal-only",
    "software containing this workflow": "software",
    "a network service": "service",
}


def settings_path(user: str = "default") -> str:
    """Where ComfyUI keeps the settings this pack writes into.

    Returns an empty string outside ComfyUI, which is the normal case for the
    CLI and the test suite.
    """
    try:
        import folder_paths
    except ImportError:
        return ""
    root = getattr(folder_paths, "get_user_directory", None)
    if root is None:
        return ""
    try:
        return os.path.join(root(), user, "comfy.settings.json")
    except Exception:  # noqa: BLE001 - a broken install must not break the audit
        return ""


def read_settings(user: str = "default") -> dict[str, Any]:
    """The raw settings dict, or an empty one for any reason at all."""
    path = settings_path(user)
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        # A corrupt settings file is ComfyUI's problem to report, not ours to
        # crash a queued render over.
        return {}
    return data if isinstance(data, dict) else {}


def studio_profile(user: str = "default") -> clearance.StudioProfile | None:
    """Build a profile from the settings, or None when none was set.

    None rather than an empty profile, so the caller can tell "the operator
    chose not to say" from "the operator said nothing applies".
    """
    settings = read_settings(user)
    if not settings:
        return None

    profile = clearance.StudioProfile(
        territory=TERRITORY_OPTIONS.get(settings.get(TERRITORY, "not set"), ""),
        revenue_band=REVENUE_OPTIONS.get(settings.get(REVENUE, "not set"), "unknown"),
        ships=SHIP_OPTIONS.get(settings.get(SHIPS, "not set"), "unknown"),
        trains_models=bool(settings.get(TRAINS)),
        likeness_involved=bool(settings.get(LIKENESS)),
        label=str(settings.get(LABEL, "") or "").strip(),
    )
    return profile if profile.is_set else None


def registry_path(user: str = "default") -> str:
    """Where the facility keeps its decision record, if it keeps one."""
    return str(read_settings(user).get(REGISTRY, "") or "").strip()


def describe() -> dict[str, Any]:
    """What the settings currently say, for the diagnostics block."""
    profile = studio_profile()
    return {
        "settings_file": settings_path(),
        "profile_set": profile is not None,
        "profile": profile.describe() if profile else "",
        "registry_path": registry_path(),
    }


# -- shot history ------------------------------------------------------------

#: Who is at this workstation. Recorded on every history entry; without it the
#: entry falls back to the operating-system account, which is rarely a name a
#: lawyer recognises.
HISTORY_USER = f"{PREFIX}.History.UserName"
HISTORY_ROLE = f"{PREFIX}.History.Role"
#: Stamp the open workflow automatically on every queue and every render.
HISTORY_AUTO = f"{PREFIX}.History.Auto"
#: The ledger path and the signing-key file are deliberately NOT settings.
#: ComfyUI settings are writable by any browser that can reach the server, so a
#: path there would let anyone redirect where the server writes, or swap in a
#: signing key of their own. Both come from the server's environment instead:
#: COMFYAUDIT_HISTORY_LEDGER and COMFYAUDIT_HISTORY_KEY_FILE (or the key itself
#: in COMFYAUDIT_HISTORY_KEY).
HISTORY_CONFIDENTIALITY = f"{PREFIX}.History.Confidentiality"
#: Where finished work is distributed, which is what decides several rules
#: (the EU marking obligations reach content made available in the EU, wherever
#: it was rendered).
HISTORY_DISTRIBUTE = f"{PREFIX}.History.DistributeIn"

#: Production facts that decide which union and disclosure rules apply.
HISTORY_UNION = f"{PREFIX}.History.UnionProduction"
HISTORY_ADVERTISING = f"{PREFIX}.History.Advertising"
HISTORY_FEATURE = f"{PREFIX}.History.FeatureFilm"

CONFIDENTIALITY_OPTIONS = {
    "prompts readable": "none",
    "prompts as fingerprints only": "redact",
    "prompts encrypted": "encrypt",
}


def history_settings(user: str = "default") -> dict[str, Any]:
    s = read_settings(user)
    distribute = [t.strip().upper() for t in
                  str(s.get(HISTORY_DISTRIBUTE, "") or "").replace(";", ",").split(",")
                  if t.strip()]
    return {
        "user": str(s.get(HISTORY_USER, "") or "").strip(),
        "role": str(s.get(HISTORY_ROLE, "") or "").strip(),
        "auto": bool(s.get(HISTORY_AUTO, True)),
        "ledger": os.environ.get("COMFYAUDIT_HISTORY_LEDGER", "").strip(),
        "key_file": os.environ.get("COMFYAUDIT_HISTORY_KEY_FILE", "").strip(),
        "confidentiality": CONFIDENTIALITY_OPTIONS.get(
            s.get(HISTORY_CONFIDENTIALITY, "prompts readable"), "none"),
        "distribute_in": distribute,
        "territory": TERRITORY_OPTIONS.get(s.get(TERRITORY, "not set"), ""),
        "likeness": bool(s.get(LIKENESS)),
        "union_performers": bool(s.get(HISTORY_UNION)),
        "advertising": bool(s.get(HISTORY_ADVERTISING)),
        "feature": bool(s.get(HISTORY_FEATURE)),
    }
