#!/usr/bin/env python3
"""Build the base-model licence table from Civitai's own source.

Civitai maintains ``baseLicenses`` and ``baseModelLicenses`` in
``src/server/common/constants.ts``: a mapping from every base model they know
about to the licence that governs it. That is the missing half of auditing a
community model - the uploader's own permission flags tell you what *they*
claim to allow, but a LoRA cannot grant more than the model it was trained on,
and this table is what says which model that is.

Their ``nonCommercial`` flag is sparse (it is a display/attribution table, not a
commercial-use classifier), so this tool takes only the licence *identity* from
them and maps it onto comfyaudit's own licence definitions, which do carry a
commercial position. Anything that cannot be mapped is emitted with a null
licence id and shows up in the report as "base model known, licence not
classified" rather than being silently guessed at.

Usage::

    python tools/build_base_models.py --constants /path/to/constants.ts
    python tools/build_base_models.py --url      # fetch from GitHub
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

CONSTANTS_URL = ("https://raw.githubusercontent.com/civitai/civitai/main/"
                 "src/server/common/constants.ts")

#: Civitai licence key -> comfyaudit licence id. Their keys are stable and few,
#: so an explicit table is clearer than fuzzy name matching, and an unmapped key
#: is reported rather than guessed.
LICENCE_ID_MAP = {
    "openrail": "creativeml-openrail-m",
    "openrail++": "creativeml-openrail-plus-plus-m",
    "sdxl 0.9": "stability-nc-research",
    "sdxl turbo": "stability-nc-research",
    "svd": "stability-nc-research",
    "SAI NC RC": "stability-nc-research",
    "SAI CLA": "stability-community",
    "apache 2.0": "apache-2.0",
    "mit": "mit",
    "agpl": "agpl-3.0-ultralytics",
    "flux1D": "flux1-dev-nc",
    "hunyuan community": "tencent-hunyuan-community",
    "hunyuan video": "tencent-hunyuan-community",
    "ltxv license": "ltx-community",
    "ltxv2": "ltx-community",
    "ltxv25": "ltx-community",
    "openai": "proprietary-api",
    "imagen4": "proprietary-api",
    "veo3": "proprietary-api",
    "kling": "proprietary-api",
    "vidu": "proprietary-api",
    "seedream": "proprietary-api",
    "minimax h3": "proprietary-api",
    "ideogram nc": "cc-by-nc-4.0",
    "illustrious license": "faipl-1.0-sd",
    "noobAi": "faipl-1.0-sd",
    "ponyV7": "faipl-1.0-sd",
    "playground v2": "playground-v2-community",
    "kolors license": "kolors-license",
    "cogvideox license": "cogvideox-license",
}


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "comfyaudit-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


# --------------------------------------------------------------------------


def _block(source: str, declaration: str) -> str:
    """Return the braced object literal following a declaration."""
    start = source.index(declaration)
    open_brace = source.index("{", start)
    depth, index = 0, open_brace
    while index < len(source):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace:index + 1]
        index += 1
    raise ValueError(f"unterminated block after {declaration!r}")


def parse_base_licences(source: str) -> dict[str, dict]:
    """Parse ``baseLicenses`` into ``{key: {name, url, flags...}}``."""
    block = _block(source, "const baseLicenses")
    out: dict[str, dict] = {}

    # Each entry is  key: { ... },  where the key may be quoted.
    for match in re.finditer(r"(?m)^\s{2}(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z0-9_]+))\s*:\s*\{",
                             block):
        key = match.group(1) or match.group(2) or match.group(3)
        body = _block(block[match.start():], ":")
        entry: dict[str, object] = {}
        for field in ("url", "name", "notice", "poweredBy", "attribution"):
            found = re.search(rf"{field}:\s*(?:'([^']*)'|\"([^\"]*)\"|`([^`]*)`)", body)
            if found:
                value = found.group(1) or found.group(2) or found.group(3) or ""
                if value:
                    entry[field] = value
        for flag in ("nonCommercial", "requiresSameLicense", "disableMature"):
            if re.search(rf"{flag}:\s*true", body):
                entry[flag] = True
        if entry:
            out[key] = entry
    return out


def parse_base_model_map(source: str) -> dict[str, str | None]:
    """Parse ``baseModelLicenses`` into ``{base model: licence key or None}``."""
    block = _block(source, "export const baseModelLicenses")
    out: dict[str, str | None] = {}

    pattern = re.compile(
        r"(?m)^\s{2}(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z0-9_]+))\s*:\s*"
        r"(?:baseLicenses\[(?:'([^']+)'|\"([^\"]+)\")\]|(undefined))"
    )
    for match in pattern.finditer(block):
        model = match.group(1) or match.group(2) or match.group(3)
        licence = match.group(4) or match.group(5)
        out[model] = licence if licence else None
    return out


# --------------------------------------------------------------------------


def build(source: str, licences_path: str) -> dict:
    base_licences = parse_base_licences(source)
    base_models = parse_base_model_map(source)

    with open(licences_path, "r", encoding="utf-8") as fh:
        known = set(json.load(fh)["licences"])

    unmapped: set[str] = set()
    entries: dict[str, dict] = {}

    for model, licence_key in sorted(base_models.items()):
        record: dict[str, object] = {}
        if licence_key:
            details = base_licences.get(licence_key, {})
            record["civitai_licence"] = licence_key
            if details.get("name"):
                record["licence_name"] = details["name"]
            if details.get("url"):
                record["licence_url"] = details["url"]
            if details.get("nonCommercial"):
                record["non_commercial"] = True
            if details.get("requiresSameLicense"):
                record["share_alike"] = True
            if details.get("poweredBy") or details.get("attribution"):
                record["attribution"] = details.get("attribution") or details["poweredBy"]

            mapped = LICENCE_ID_MAP.get(licence_key)
            if mapped and mapped in known:
                record["licence_id"] = mapped
            else:
                unmapped.add(licence_key)
        entries[model] = record

    return {
        "source": "civitai/civitai src/server/common/constants.ts",
        "note": ("Base model to licence identity, taken from Civitai's own table. "
                 "The commercial position comes from comfyaudit's licence "
                 "definitions via licence_id; a base model with no licence_id is "
                 "known but not classified."),
        "base_models": entries,
        "unmapped_civitai_licences": sorted(unmapped),
    }


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--constants", help="path to a local copy of constants.ts")
    ap.add_argument("--url", action="store_true", help="fetch constants.ts from GitHub")
    ap.add_argument("--out", default=os.path.join(here, "..", "core", "knowledge",
                                                  "data", "base_models.json"))
    args = ap.parse_args()

    if args.url or not args.constants:
        print(f"fetching {CONSTANTS_URL}")
        source = fetch(CONSTANTS_URL)
    else:
        with open(args.constants, "r", encoding="utf-8") as fh:
            source = fh.read()

    licences_path = os.path.join(here, "..", "core", "knowledge", "data", "licences.json")
    payload = build(source, licences_path)

    classified = sum(1 for r in payload["base_models"].values() if r.get("licence_id"))
    total = len(payload["base_models"])
    print(f"  {total} base models, {classified} mapped to a licence definition")
    if payload["unmapped_civitai_licences"]:
        print("  unmapped Civitai licence keys (add to LICENCE_ID_MAP if they matter): "
              + ", ".join(payload["unmapped_civitai_licences"]))

    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False, sort_keys=True)
    print(f"  wrote {out} ({os.path.getsize(out) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
