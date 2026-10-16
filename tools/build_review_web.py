"""Build the data files the Review web front end (review-web/) ships with.

    python tools/build_review_web.py

* ``review-web/data/rules.json`` - the rules knowledge base, trimmed to what the
  page needs to show what a sign-off left for legal: each rule's title,
  jurisdiction, summary, source and its evidence items with their level
  (shot / show / studio). Public research; no studio data.
* ``review-web/demo/*.delivery.zip`` - two delivery packages of a fictional
  show, made by the real Package for delivery code, for the page's demo mode.
  Everything in them is invented: the show, the people, the studio. They are
  signed with a demo key that is published here, so their signatures prove
  nothing; they exist to show the page working without a studio server.

Run it again after the regulations knowledge base changes.
"""

from __future__ import annotations

import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(PACK))

WEB = os.path.join(PACK, "review-web")
DEMO_KEY = "comfyaudit-review-demo-key-not-secret"
SHOW = "Harbor Lights"
STUDIO_SETTINGS = {
    "ComfyAudit.Studio.Territory": "United States",
    "ComfyAudit.Studio.Revenue": "$20M - $100M",
    "ComfyAudit.Studio.Ships": "finished frames to a client",
    "ComfyAudit.Studio.Label": "Northlight Post (demo)",
    "ComfyAudit.History.DistributeIn": "US, EU",
    "ComfyAudit.History.TPNVendor": True,
    "ComfyAudit.History.UnionProduction": True,
}


def rules() -> dict:
    from comfyaudit.core.report import compliance
    kb = compliance.load()
    return {
        "as_of": kb.get("as_of", ""),
        "rules": {r["id"]: {
            "title": r.get("title", ""), "jurisdiction": r.get("jurisdiction", ""),
            "summary": r.get("summary", ""), "source": r.get("source", ""),
            "evidence": [{"text": e.get("text", ""), "check": e.get("check", "manual"),
                          "scope": e.get("scope", "shot") if e.get("check", "manual") == "manual" else "record"}
                         for e in r.get("evidence", [])],
        } for r in kb.get("rules", [])},
    }


def demo_packages(out: str) -> list[str]:
    import tempfile
    from comfyaudit.core import history as H
    from comfyaudit.server import delivery, history_service
    from comfyaudit.server import settings as settings_mod

    tmp = tempfile.mkdtemp(prefix="comfyaudit-demo-")
    os.environ["COMFYAUDIT_HISTORY_KEY"] = DEMO_KEY
    os.environ["COMFYAUDIT_ANSWERS_FILE"] = os.path.join(tmp, "answers.json")
    os.environ["COMFYAUDIT_HISTORY_LEDGER"] = os.path.join(tmp, "ledger.jsonl")
    for var in ("COMFYAUDIT_HISTORY_KEY_FILE", "COMFYAUDIT_HISTORY_KEYRING",
                "COMFYAUDIT_TRUSTED_USER_HEADER", "COMFYAUDIT_SHOTGRID_URL", "COMFYAUDIT_REVIEW_URL"):
        os.environ.pop(var, None)
    # The sign-off is recorded by the real delivery code, which names the
    # machine it runs on; these packages are published, so name a demo one.
    import socket
    socket.gethostname = lambda: "demo-workstation"
    supervisor = {"ComfyAudit.History.UserName": "Theo Grant", "ComfyAudit.History.Role": "VFX supervisor"}
    settings_mod.read_settings = lambda user="default": {**STUDIO_SETTINGS, **supervisor}
    keys, ledger = H.Keys.from_environment(), os.environ["COMFYAUDIT_HISTORY_LEDGER"]
    artist = H.Actor(name="Mara Lin", role="compositor", tool="ComfyUI + comfyaudit (demo)",
                     machine="demo-workstation", declared_by="settings")
    legal = H.Actor(name="Jo Okonkwo", role="production legal", machine="demo", tool="demo")
    # The studio already answered its model intake once, for every show.
    history_service.answers_store().add(
        tag="mpa-tpn-content-security-ai-controls:3", scope="studio", project="",
        note="Model intake log MI-2026: every model scanned and its SHA-256 recorded before it entered the content network", actor=legal, keys=keys)
    with open(os.path.join(PACK, "tests", "fixtures", "beauty-pass.json"), encoding="utf-8") as fh:
        base = json.load(fh)
    made = []
    for shot, note in (("HL_0420", "Hero beauty pass, final for Reel 2"),
                       ("HL_0430", "Reverse angle, final for Reel 2")):
        wf = json.loads(json.dumps(base))
        wf = H.stamp(wf, event="created", actor=artist, keys=keys, ledger=ledger, name=shot,
                     project=SHOW, external_ids={"shotgrid": f"Shot#{shot[-4:]}"}).workflow
        wf = H.stamp(wf, event="rendered", actor=artist, keys=keys, ledger=ledger).workflow
        result = delivery.deliver({"workflow": wf, "name": shot, "note": note, "shotgrid": False,
                                   "exception": "Upscaler swap is booked for the next pass; "
                                                "delivering this version for the client review."})
        if "error" in result:
            raise SystemExit(result["error"])
        path = os.path.join(out, result["zip_name"])
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(result["zip_b64"]))
        made.append(os.path.basename(path))
    return made


def main() -> None:
    os.makedirs(os.path.join(WEB, "data"), exist_ok=True)
    os.makedirs(os.path.join(WEB, "demo"), exist_ok=True)
    with open(os.path.join(WEB, "data", "rules.json"), "w", encoding="utf-8") as fh:
        json.dump(rules(), fh, ensure_ascii=False, separators=(",", ":"))
    made = demo_packages(os.path.join(WEB, "demo"))
    with open(os.path.join(WEB, "demo", "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"show": SHOW, "packages": made,
                   "note": "Fictional show, people and studio. Signed with a published demo key."},
                  fh, indent=1, ensure_ascii=False)
    print("wrote review-web/data/rules.json and", ", ".join(made))


if __name__ == "__main__":
    main()
