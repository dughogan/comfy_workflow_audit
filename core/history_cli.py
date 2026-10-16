"""``comfyaudit history`` and ``comfyaudit guide``: shot history from the command line.

The same engine the canvas uses, for pipelines and for people who live in a
terminal: stamp a workflow file, verify it, print its life, or write the
provenance record and C2PA manifest for a delivery.

UI-format workflows carry their history inside the file and are updated in
place - that is the point of keeping it there. API-format prompts have no room
for it, so theirs is kept beside them as ``<name>.history.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import graph
from . import history as H

EXIT_ALTERED = 3


def add_parsers(sub) -> None:
    hist = sub.add_parser("history", help="record, verify and report a workflow's shot history")
    hsub = hist.add_subparsers(dest="history_command", required=True)

    st = hsub.add_parser("stamp", help="add an entry to a workflow's history (updates the file)")
    st.add_argument("workflow")
    st.add_argument("--event", default="edited", choices=sorted(H.EVENTS))
    st.add_argument("--by", default="", metavar="NAME", help="who is acting (default: COMFYAUDIT_USER or the OS account)")
    st.add_argument("--role", default="")
    st.add_argument("--note", default="")
    st.add_argument("--name", default="", help="shot name, set on the first entry")
    st.add_argument("--project", default="")
    st.add_argument("--id", action="append", default=[], metavar="SYSTEM=ID",
                    help="studio asset id, e.g. shotgrid=Shot#4411 (repeatable)")
    st.add_argument("--source", default="", metavar="NODE=ORIGIN",
                    help=f"with --event sourced: input node and origin ({', '.join(H.ORIGINS)})")
    st.add_argument("--asset-id", default="", help="with --event sourced: the source's own asset id")
    st.add_argument("--owner", default="", help="with --event sourced: rights holder")
    st.add_argument("--stage", default="", choices=[""] + list(H.STAGES), help="with --event promoted")
    st.add_argument("--evidence", default="", metavar="RULE[:N]",
                    help="with --event note: attest evidence for a compliance rule")
    st.add_argument("--output-file", action="append", default=[], metavar="PATH",
                    help="with --event rendered: an output file to fingerprint (repeatable)")
    st.add_argument("--ledger", default=os.environ.get("COMFYAUDIT_HISTORY_LEDGER", ""))
    st.add_argument("--confidentiality", default="", choices=["", "none", "redact", "encrypt"])
    st.add_argument("--force", action="store_true", help="record an edit even if nothing changed")
    st.add_argument("-o", "--output", default="", help="write here instead of updating in place")

    ve = hsub.add_parser("verify", help="check a history is intact (exit 3 if altered)")
    ve.add_argument("workflow")
    ve.add_argument("--ledger", default=os.environ.get("COMFYAUDIT_HISTORY_LEDGER", ""))

    vf = hsub.add_parser("verify-file", help="check an output against the provenance file "
                         "beside it (exit 3 if it does not match)")
    vf.add_argument("workflow", metavar="output",
                    help="a rendered file, or its .provenance.json")
    vf.add_argument("--ledger", default=os.environ.get("COMFYAUDIT_HISTORY_LEDGER", ""))

    lg = hsub.add_parser("log", help="print the history as a timeline")
    lg.add_argument("workflow")
    lg.add_argument("--reveal", action="store_true", help="decrypt protected prompts (needs the key)")

    rc = hsub.add_parser("record", help="write the provenance record (HTML)")
    rc.add_argument("workflow")
    rc.add_argument("-o", "--output", default="")
    rc.add_argument("--ledger", default=os.environ.get("COMFYAUDIT_HISTORY_LEDGER", ""))
    rc.add_argument("--reveal", action="store_true")
    rc.add_argument("--territory", default="", help="where the studio operates (US, EU, GB...)")
    rc.add_argument("--distribute", default="", help="where the work is distributed, comma separated")
    rc.add_argument("--likeness", action="store_true", help="real performers' likenesses are involved")
    rc.add_argument("--union", action="store_true", help="union performers are involved")
    rc.add_argument("--advertising", action="store_true", help="the work is advertising")
    rc.add_argument("--feature", action="store_true", help="a feature film (awards eligibility rules)")
    rc.add_argument("--youtube", action="store_true", help="published on YouTube")
    rc.add_argument("--union-crew", action="store_true", help="crew under IATSE / Animation Guild terms")
    rc.add_argument("--animation", action="store_true", help="an animated production")
    rc.add_argument("--tpn", action="store_true", help="the studio is a TPN-assessed vendor (MPA content security)")
    rc.add_argument("--eo-insurance", action="store_true", help="the production carries E&O insurance")
    rc.add_argument("--copyright-claim", action="store_true", help="copyright will be registered or enforced")
    rc.add_argument("--client", action="append", default=[], help="who commissioned the work (repeatable)")
    rc.add_argument("--third-party-characters", action="store_true",
                    help="the shot depicts or evokes characters or IP owned by someone else")
    rc.add_argument("--ai-music", action="store_true", help="the shot includes AI-generated music or sound")

    mf = hsub.add_parser("manifest", help="write a C2PA manifest definition for signing")
    mf.add_argument("workflow")
    mf.add_argument("-o", "--output", default="")
    mf.add_argument("--title", default="")

    gd = sub.add_parser("guide", help="write the commercial model licensing guide (HTML)")
    gd.add_argument("-o", "--output", default="licensing-guide.html")
    gd.add_argument("--licences", "--licenses", dest="licences", default="")


def _load(path: str) -> tuple[dict, dict | None]:
    data = graph.extract_embedded(path) if not path.lower().endswith(".json") else None
    if data is None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    if graph._looks_like_api(data):  # noqa: SLF001
        side = H.sidecar_path(path)
        if os.path.exists(side):
            with open(side, encoding="utf-8") as fh:
                return data, json.load(fh)
        return data, None
    return data, H.read(data)


SIDECAR_SUFFIX = ".provenance.json"


def verify_file(path: str, *, keys: H.Keys, ledger: str = "") -> int:
    """Is this output the one its provenance file says, from an intact history?

    Three things have to hold: the history in the sidecar is intact (and
    signed, when the key is set), the entry the sidecar names is in that
    history unchanged, and this file's contents match the fingerprint that
    entry recorded. The ledger, when given, confirms that entry was witnessed;
    it is expected to hold later entries, because the shot went on.
    """
    output = path[: -len(SIDECAR_SUFFIX)] if path.endswith(SIDECAR_SUFFIX) else path
    side = output + SIDECAR_SUFFIX
    if not os.path.isfile(side):
        print(f"no provenance file beside {output} (looked for {os.path.basename(side)})")
        return 2
    with open(side, encoding="utf-8") as fh:
        doc = json.load(fh)
    history = doc.get("history") or {}
    v = H.verify({}, keys=keys, history=history)
    failures = list(v.problems)
    entries = history.get("entries") or []
    seq = doc.get("entry")
    entry = entries[seq - 1] if isinstance(seq, int) and 0 < seq <= len(entries) else None
    if not entry or entry.get("hash") != doc.get("entry_hash"):
        failures.append("the entry this provenance file names is not in its history as recorded")
    actual = H.file_sha256(output) if os.path.isfile(output) else ""
    recorded = {o.get("sha256") for o in (entry or {}).get("outputs", []) if o.get("sha256")}
    if not actual:
        failures.append(f"{os.path.basename(output)} is not there to check")
    elif actual not in recorded:
        failures.append(f"{os.path.basename(output)} is not the file entry {seq} recorded - "
                        "its contents have changed since it was rendered")
    shot = doc.get("shot") or {}
    label = H.ASSURANCE_LABELS[v.assurance]
    if failures:
        print(f"DOES NOT MATCH: {os.path.basename(output)}")
        for f in failures:
            print(f"  - {f}")
        return EXIT_ALTERED
    print(f"MATCHES: {os.path.basename(output)} is the output of entry {seq} "
          f"({str(doc.get('at', ''))[:16].replace('T', ' ')}) of {shot.get('name') or 'the shot'}"
          + "".join(f", {k} {val}" for k, val in (shot.get("external_ids") or {}).items()) + ".")
    print(f"History: {label}. {v.assurance_detail}")
    if ledger and os.path.exists(ledger):
        rows = H._ledger_rows(ledger, H.chain_shot_id(history))  # noqa: SLF001
        if any(r.get("seq") == seq and r.get("hash") == entry["hash"] for r in rows):
            print(f"Ledger: entry {seq} was witnessed by the studio ledger.")
        else:
            print(f"Ledger: entry {seq} is not in the studio ledger.")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.command == "guide":
        from .report import licensing_guide
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(licensing_guide.render(args.licences or None))
        print(f"wrote {args.output}")
        return 0

    cmd = args.history_command
    path = args.workflow
    if cmd == "verify-file":
        return verify_file(path, keys=H.Keys.from_environment(), ledger=args.ledger)
    data, history = _load(path)
    keys = H.Keys.from_environment(confidentiality=getattr(args, "confidentiality", "") or "")

    if cmd == "stamp":
        if not path.lower().endswith(".json"):
            print("stamp works on .json workflow files; images keep the history they were rendered with",
                  file=sys.stderr)
            return 2
        payload: dict = {}
        if args.event == "sourced":
            node, _, origin = args.source.partition("=")
            payload = {"node": node, "origin": origin or "unknown",
                       "asset_id": args.asset_id, "owner": args.owner}
        if args.event == "promoted":
            payload = {"stage": args.stage}
        if args.evidence:
            payload["evidence"] = args.evidence
        ids = dict(item.split("=", 1) for item in args.id if "=" in item)
        actor = H.Actor.current(name=args.by, role=args.role, tool="comfyaudit cli")
        try:
            result = H.stamp(data, event=args.event, actor=actor, note=args.note,
                             outputs=args.output_file, keys=keys, name=args.name,
                             project=args.project, ledger=args.ledger, external_ids=ids,
                             data_payload=payload, history=history, force=args.force)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if result.entry is None:
            print("nothing changed since the last entry; not recorded (use --force to record anyway)")
            return 0
        if result.sidecar:
            target = H.sidecar_path(args.output or path)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(result.history, fh, indent=1)
        else:
            target = args.output or path
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(result.workflow, fh, indent=1)
        e = result.entry
        print(f"#{e['seq']} {H.EVENTS[e['event']]} by {e['actor'].get('name')} "
              f"-> {target}" + (f"  [{', '.join(e['breakpoints'])}]" if e["breakpoints"] else ""))
        return 0

    if history is None:
        print(f"{path} carries no shot history", file=sys.stderr)
        return 1

    if cmd == "verify":
        v = H.verify(data, keys=keys, ledger=args.ledger, history=history)
        print(f"{H.ASSURANCE_LABELS[v.assurance].upper()}: {v.assurance_detail}")
        for p in v.problems:
            print(f"  - {p}")
        return EXIT_ALTERED if v.assurance == "none" else 0

    if cmd == "log":
        lin = H.lineage(history)
        shot = lin["shot"]
        print(f"{shot.get('name') or 'shot'}  [{shot.get('stage')}]  "
              + " ".join(f"{k}={v}" for k, v in (shot.get("external_ids") or {}).items()))
        for r in H.timeline(history, keys=keys, reveal=args.reveal):
            who = r["actor"].get("name", "?")
            bp = f"  [{', '.join(r['breakpoints'])}]" if r["breakpoints"] else ""
            print(f"{r['seq']:>4}  {r['at'][:16].replace('T', ' ')}  {who:<18} {r['label']}{bp}")
            if r["note"]:
                print(f"{'':>6}“{r['note']}”")
            for key, val in r["changes"].items():
                if key == "initial":
                    continue
                print(f"{'':>6}{key}: {val}")
        for g in lin["gaps"]:
            print(f"gap: {g}")
        return 0

    if cmd == "record":
        from .report import history_html
        context = {"territory": args.territory.upper(),
                   "distribute_in": [t.strip().upper() for t in args.distribute.split(",") if t.strip()],
                   "likeness": args.likeness, "union_performers": args.union,
                   "advertising": args.advertising, "feature": args.feature,
                   "youtube": args.youtube, "union_crew": args.union_crew,
                   "animation": args.animation, "tpn_vendor": args.tpn,
                   "eo_insurance": args.eo_insurance, "copyright_claim": args.copyright_claim,
                   "clients": args.client,
                   "third_party_characters": args.third_party_characters,
                   "ai_music": args.ai_music}
        html = history_html.render(history, data=None if graph._looks_like_api(data) else data,  # noqa: SLF001
                                   keys=keys, ledger=args.ledger, reveal=args.reveal,
                                   context=context)
        out = args.output or os.path.splitext(path)[0] + ".provenance.html"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"wrote {out}")
        return 0

    if cmd == "manifest":
        out = args.output or os.path.splitext(path)[0] + ".c2pa.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(H.c2pa_manifest(history, title=args.title), fh, indent=2)
        print(f"wrote {out}  (unsigned definition; sign with c2patool or your signing service)")
        return 0
    return 2
