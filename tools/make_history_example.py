#!/usr/bin/env python3
"""Build a tracked example shot with a realistic life, for demos and the README.

Starts from the beauty-pass fixture and plays out a few days of production:
an artist builds it, a supervisor redirects it, a model with a non-commercial
licence is swapped out, a take is rejected, the plate's origin is declared,
the shot is promoted, and legal signs it off.

    python tools/make_history_example.py --out examples/
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from comfyaudit.core import history as H  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "examples"))
    ap.add_argument("--key", default="example-studio-key")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    with open(os.path.join(HERE, "..", "tests", "fixtures", "beauty-pass.json")) as fh:
        wf = json.load(fh)

    keys = H.Keys(sign_key=H._derive(args.key, b"comfyaudit-sign"), key_id="example")
    ledger = os.path.join(args.out, "example-ledger.jsonl")
    if os.path.exists(ledger):
        os.remove(ledger)
    clock = dt.datetime(2026, 10, 12, 9, 40).astimezone()

    ana = H.Actor(name="Ana Ruiz", role="compositor", machine="ws-comp-14")
    doug = H.Actor(name="Doug Hogan", role="VFX supervisor", machine="ws-sup-02")
    mei = H.Actor(name="Mei Tanaka", role="lighting TD", machine="ws-lt-07")
    lee = H.Actor(name="Lee Park", role="production legal", machine="ws-legal-01")

    def step(event, actor, minutes, **kw):
        nonlocal wf, clock
        clock += dt.timedelta(minutes=minutes)
        H.now = lambda: clock.isoformat(timespec="seconds")
        r = H.stamp(wf, event=event, actor=actor, keys=keys, ledger=ledger, **kw)
        wf = r.workflow

    def node(t):
        return next(n for n in wf["nodes"] if n["type"] == t)

    def prompt(text):
        node("CLIPTextEncode")["widgets_values"][0] = text

    step("created", ana, 0, name="SH0120 beauty pass", project="Atlas",
         external_ids={"shotgrid": "Shot#4411"}, note="from the brief: warm, intimate, no retouch look")
    prompt(node("CLIPTextEncode")["widgets_values"][0] + ", soft rim light from camera left")
    step("edited", ana, 25)
    srcs = H.lineage(H.read(wf))["sources"]
    step("sourced", ana, 4, data_payload={"node": srcs[0]["node"], "origin": "photographed",
                                          "asset_id": "PLATE-0120-A", "owner": "Atlas production"})
    step("rendered", ana, 6, outputs=[{"file": "SH0120_beauty_v001.exr", "sha256": "1f" * 32}])
    step("reviewed", doug, 50, note="skin reads waxy - lose the face restore, keep the grain")

    # Doug's note: the face-restore model goes; the upscaler goes non-commercial -> permissive.
    for n in wf["nodes"]:
        for i, v in enumerate(n.get("widgets_values") or []):
            if v == "4x-UltraSharp.pth":
                n["widgets_values"][i] = "RealESRGAN_x4plus.pth"
            if isinstance(v, str) and "codeformer" in v.lower():
                n["mode"] = 4
    step("edited", mei, 70, note="swapped upscaler to Real-ESRGAN (BSD), bypassed CodeFormer")
    step("rendered", mei, 8, outputs=[{"file": "SH0120_beauty_v002.exr", "sha256": "2e" * 32}])
    step("rejected", doug, 35, note="rim too hot on the cheek")
    prompt(node("CLIPTextEncode")["widgets_values"][0].replace("soft rim light", "subtle rim light"))
    step("edited", ana, 40)
    step("rendered", ana, 7, outputs=[{"file": "SH0120_beauty_v003.exr", "sha256": "3d" * 32}])
    step("reviewed", doug, 22, note="that's the one")
    if len(srcs) > 1:
        step("sourced", ana, 3, data_payload={"node": srcs[1]["node"], "origin": "client",
                                              "asset_id": "CLIENT-REF-0042", "owner": "client"})
    step("promoted", doug, 5, data_payload={"stage": "production"}, note="v003 to client review")
    step("approved", lee, 60 * 20, note="cleared for Atlas broadcast spot, ref LEGAL-2231")

    out = os.path.join(args.out, "example-tracked-shot.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, indent=1)
    print(f"wrote {out} ({len(H.read(wf)['entries'])} entries) and {ledger}")
    print(f"verify with: COMFYAUDIT_HISTORY_KEY={args.key} comfyaudit history verify {out} --ledger {ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
