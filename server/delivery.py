"""Package for delivery: check a workflow for commercial use, then seal and package it.

Two steps behind the one button in the sidebar:

* ``check`` runs the audit against the studio profile and turns its verdict
  into a to-do list: what blocks delivery and what would lift it, which inputs
  still need an origin, and which rules want a person to confirm something.
* ``deliver`` re-runs that check on the server - the verdict that gets sealed
  is never one the browser sent - records the sign-off with the clearance
  inside it, moves the shot to *delivered*, and returns the package: the
  workflow with its sealed history, a C2PA manifest definition, the legal
  report and the licence audit, together as a zip, indexed by <shot>.package.json.

A no-go, or a verdict that could not be reached, is only delivered when the
person signing off records why; that exception is sealed with the sign-off.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tempfile
import os
import textwrap
import zipfile
from typing import Any

from ..core import history as H
from ..core.knowledge import alternatives as alternatives_mod
from ..core.knowledge import licences as licences_mod
from ..core.report import compliance, history_html, life_viewer, machine, omc
from ..core.report import html as html_report
from ..core.score import clearance as clearance_mod
from . import c2pa as c2pa_mod
from . import history_service
from . import settings as settings_mod
from . import shotgrid


def _audit(workflow: dict[str, Any], body: dict[str, Any]):
    from ..nodes.audit_nodes import run_audit

    options = body.get("options") or {}
    return run_audit(
        workflow,
        online=bool(options.get("online")),
        check_local_models=bool(options.get("check_local_models", True)),
        hash_models=bool(options.get("hash_models")),
        name=str(body.get("name", "") or ""),
    )


def _installed() -> list[str]:
    """Basenames of the weights this ComfyUI can see, or nothing outside ComfyUI."""
    try:
        from . import live
        index = live.live_model_index()
        return list(index.by_name) if index.available else []
    except Exception:  # noqa: BLE001
        return []


def fixes(result: clearance_mod.ClearanceResult,
          installed: list[str] | None = None) -> list[dict[str, Any]]:
    """What stands between this workflow and a yes, one item per distinct fix.

    Weights that share a licence share its blocking clause, so they are one
    item naming every file it covers rather than one item per file.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for det in result.determinations:
        if det.verdict == "go":
            continue
        for reason in det.reasons:
            if reason.verdict != det.verdict:
                continue
            key = (reason.text, reason.remedy)
            item = grouped.setdefault(key, {
                "verdict": reason.verdict, "kind": det.kind, "licence": det.licence,
                "url": det.url, "problem": reason.text, "remedy": reason.remedy,
                "action": reason.action,
                "action_label": clearance_mod.ACTIONS.get(reason.action, ""),
                "subjects": [], "alternatives": [],
            })
            if det.subject not in item["subjects"]:
                item["subjects"].append(det.subject)
    # A model that cannot be used commercially gets the models that do the
    # same job and can, read against the licence knowledge base.
    matcher = licences_mod.LicenceMatcher()
    for item in grouped.values():
        if item["kind"] == "model" and item["verdict"] == "no-go":
            seen: set[str] = set()
            for subject in item["subjects"]:
                for alt in alternatives_mod.suggest(subject, installed or [], matcher):
                    if alt["name"] not in seen:
                        seen.add(alt["name"])
                        item["alternatives"].append(alt)
    order = {"no-go": 0, "unknown": 1, "conditions": 2}
    return sorted(grouped.values(), key=lambda f: (order.get(f["verdict"], 3), f["problem"]))


def _sources(lin: dict[str, Any]) -> list[dict[str, Any]]:
    """Supplied inputs whose origin nobody has declared yet."""
    return [{"node": s["node"], "origin": s.get("origin", ""), "asset_id": s.get("asset_id", "")}
            for s in lin["sources"] if not s.get("origin") or s["origin"] == "unknown"]


def _confirmations(lin, history, cfg) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rules that apply and still want a person to confirm something, and the show and
    studio answers that already confirm others."""
    checks = compliance.assess(lin, history, history_service.context(cfg, history=history))
    out = []
    for rule in checks["rules"]:
        items = [{"text": e["text"], "tag": e["tag"], "scope": e.get("scope", "shot")}
                 for e in rule["evidence"] if e["state"] == "need" and e.get("tag")]
        if items:
            out.append({"rule": rule["id"], "title": rule["title"],
                        "jurisdiction": rule["jurisdiction"], "items": items})
    return out, checks.get("answers_used", [])


def _versions() -> dict[str, str]:
    meta = licences_mod.kb_metadata()
    return {"comfyaudit": H.__version__, "licences": str(meta.get("version", "")),
            "regulations": str(compliance.load().get("as_of", ""))}


def _assess(body: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    cfg = settings_mod.history_settings()
    workflow = body["workflow"]
    report = _audit(workflow, body)
    result = report.clearance
    history = H.read(workflow)
    lin = H.lineage(history) if history else None
    confirmations, answers_used = _confirmations(lin, history, cfg) if lin else ([], [])
    out = {
        "determined": result.determined,
        "verdict": result.verdict if result.determined else "unknown",
        "verdict_label": clearance_mod.VERDICT_LABELS.get(
            result.verdict if result.determined else "unknown", ""),
        "headline": result.headline,
        "profile": result.profile.describe() if result.determined else "",
        "missing_facts": result.missing_facts,
        "blockers": result.distinct_blockers(),
        "conditions": list(result.conditions),
        "fixes": fixes(result, _installed()),
        "sources": _sources(lin) if lin else [],
        "confirmations": confirmations,
        "answers_used": answers_used,
        "project": (lin or {}).get("shot", {}).get("project", "") if lin else "",
        "models": [{"filename": d.subject, "licence": d.licence, "verdict": d.verdict}
                   for d in result.determinations if d.kind == "model"],
        "risk": {"score": report.risk.score, "band": report.risk.band},
        "versions": _versions(),
    }
    return report, out


def check(body: dict[str, Any]) -> dict[str, Any]:
    report, out = _assess(body)
    out["audit_html"] = html_report.render(report)
    return out


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "shot"


def review_url() -> str:
    """Where the studio's legal team reviews packages: its own deployed copy of ComfyAudit
    Review (``review-web/``). ``COMFYAUDIT_REVIEW_URL``; no default, so nothing points
    anywhere a studio did not choose."""
    url = os.environ.get("COMFYAUDIT_REVIEW_URL", "").strip()
    return url if re.match(r"^https?://", url, re.I) else ""


def deliver(body: dict[str, Any]) -> dict[str, Any]:
    cfg = settings_mod.history_settings()
    report, assessed = _assess(body)
    exception = str(body.get("exception", "") or "").strip()
    if assessed["verdict"] in ("no-go", "unknown") and not exception:
        why = ("The audit says no-go" if assessed["verdict"] == "no-go"
               else "The audit could not reach a verdict")
        return {"error": f"{why}. Fix what it lists, or record why this is being "
                         "delivered anyway; that reason is sealed with the sign-off."}

    clearance = {k: assessed[k] for k in ("verdict", "verdict_label", "headline",
                                          "profile", "blockers", "conditions")}
    clearance["outstanding"] = [{"problem": f["problem"], "remedy": f["remedy"],
                                 "subjects": f["subjects"]} for f in assessed["fixes"]]
    clearance["exception"] = exception
    clearance["audited_with"] = assessed["versions"]
    # What the sign-off leaves for legal: production facts no one in the
    # workflow has attested yet. Sealed, so legal sees exactly what was open.
    clearance["for_legal"] = [{"rule": c["rule"], "title": c["title"],
                               "jurisdiction": c["jurisdiction"],
                               "items": [i["text"] for i in c["items"]],
                               # Parallel to items, so a reviewer (ComfyAudit Review, an
                               # agent) can answer each one by its tag.
                               "tags": [i.get("tag", "") for i in c["items"]]}
                              for c in assessed["confirmations"]]
    # Show and studio answers this sign-off relied on, sealed with their
    # signatures so the package proves them without the studio's answer store.
    clearance["answers_relied_on"] = [{k: a.get(k) for k in (
        "tag", "scope", "production", "production_name", "note", "by", "at", "key_id",
        "hash", "sig") if k in a} for a in assessed["answers_used"]]

    keys = history_service.signer(cfg, body)
    ledger = cfg.get("ledger", "")
    who = history_service.actor(cfg, body)
    name = str(body.get("name", "") or "")
    approved = H.stamp(body["workflow"], event="approved", actor=who, keys=keys, ledger=ledger,
                       name=name, note=str(body.get("note", "") or "Approved for delivery"),
                       data_payload={"clearance": clearance})
    shot = approved.history["shot"].get("name") or name or "shot"
    slug = _slug(shot)
    # The renders are signed against the history as signed off, and what was
    # signed is recorded on the entry that delivers, so the record itself says
    # which deliverables carry Content Credentials and from whom.
    manifest = H.c2pa_manifest(approved.history, title=shot)
    with tempfile.TemporaryDirectory() as tmp:
        signed, signed_files = _sign_renders(approved.history, manifest, tmp)
    done = H.stamp(approved.workflow, event="promoted", actor=who, keys=keys, ledger=ledger,
                   note="Packaged for delivery", data_payload={
                       "stage": "delivered",
                       "content_credentials": [{k: r[k] for k in (
                           "file", "signed", "validation_state", "signer", "trusted", "reason")
                           if k in r} for r in signed]})
    workflow, history = done.workflow, done.history
    record = history_html.render(history, data=workflow, keys=keys, ledger=ledger,
                                 context=history_service.context(cfg, history=history))
    verification = H.verify(workflow, keys=keys, ledger=ledger)
    content = {
        "workflow": json.dumps(workflow, indent=1, default=str),
        "c2pa": json.dumps(manifest, indent=1, default=str),
        "legal": record,
        "audit": html_report.render(report),
        "life": life_viewer.render(history, verification=verification),
        "omc": json.dumps(omc.export(history), indent=1, default=str),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, suffix, *_ in PACKAGE_FILES:
            zf.writestr(f"{slug}{suffix}", content[key])
        for arcname, data in signed_files:
            zf.writestr(arcname, data)
        zf.writestr("README.txt", _readme(shot, clearance, signed, review_url()))
    # The index goes in last, so it can fingerprint everything else.
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as zf:
        index = _index(slug, history, clearance, verification, signed, zf)
        zf.writestr(f"{slug}.package.json", json.dumps(index, indent=1, default=str))

    tracked = {}
    if body.get("shotgrid", True):
        tracked = shotgrid.push_delivery(history, clearance, who.name,
                                         (f"{slug}.delivery.zip", buf.getvalue()))
    return {
        "review_url": review_url(),
        "shotgrid": tracked,
        "workflow": workflow,
        "history": history,
        "summary": history_service.summary(history, workflow=workflow, cfg=cfg),
        "manifest": manifest,
        "report_html": record,
        "zip_name": f"{slug}.delivery.zip",
        "c2pa": signed,
        "zip_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }


#: Every document in a package: content key, file suffix, media type, role, what it is.
#: The README and the machine-readable index are both written from this list.
PACKAGE_FILES = [
    ("workflow", ".json", "application/json", "workflow",
     "The ComfyUI workflow. Its shot history (extra.comfyaudit_history) is hash-chained and, "
     "when the studio key is set, signed. The sign-off entry carries the clearance, sealed."),
    ("legal", ".legal-report.html", "text/html", "legal-report",
     "The provenance record for legal: clearance, who did what and when, models over time, "
     "prompt authorship, sources, the rules checklist."),
    ("audit", ".licence-audit.html", "text/html", "licence-audit",
     "The full licence and risk audit behind the verdict: every model and node pack, its "
     "licence, what the licence allows for this studio, and the risk score."),
    ("life", ".life.html", "text/html", "life-of-the-shot",
     "The shot's life on one timeline: the graph as it stood at each entry, the story and the "
     "log. Opens in any browser, offline."),
    ("omc", ".omc.json", "application/json", "movielabs-omc",
     "The same history as MovieLabs OMC-JSON v2.8 entities, for studio asset systems."),
    ("c2pa", ".c2pa.json", "application/json", "c2pa-manifest-definition",
     "A C2PA manifest definition for the delivered file, unsigned; renders signed with it are "
     "under signed/."),
]


def _index(slug: str, history: dict[str, Any], clearance: dict[str, Any],
           verification: Any, signed: list[dict[str, Any]], zf: zipfile.ZipFile) -> dict[str, Any]:
    """``<shot>.package.json``: what this package is, what was decided, and every file in it.

    The entry point for software and agents reading a package; each HTML file
    also embeds its own data (see core/report/machine.py).
    """
    approved = next((e for e in reversed(history.get("entries", []))
                     if e.get("event") == "approved"), {})
    actor = approved.get("actor") or {}
    roles = {f"{slug}{suffix}": (media, role, about) for _, suffix, media, role, about in PACKAGE_FILES}
    roles["README.txt"] = ("text/plain", "readme", "The package explained for people.")
    files = []
    for info in zf.infolist():
        data = zf.read(info.filename)
        media, role, about = roles.get(info.filename, (
            "application/octet-stream", "signed-render",
            "A final render with the C2PA manifest signed and embedded by the studio's c2patool."))
        files.append({"path": info.filename, "role": role, "media_type": media,
                      "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "description": about})
    v = verification.as_dict()
    entries = history.get("entries", [])
    return machine.stamp("delivery-package", {
        "about": ("Index of a ComfyAudit delivery package; start here. Each HTML file embeds the "
                  "data it shows as JSON in <script id='comfyaudit-data'>."),
        "shot": {k: history.get("shot", {}).get(k) for k in ("id", "name", "project", "stage",
                                                             "external_ids")},
        "decision": {
            "verdict": clearance.get("verdict"), "verdict_label": clearance.get("verdict_label"),
            "headline": clearance.get("headline"), "checked_as": clearance.get("profile"),
            "signed_off_by": {"name": actor.get("name", ""), "role": actor.get("role", "")},
            "signed_off_at": approved.get("at", ""), "note": approved.get("note", ""),
            "delivered_as_exception": bool(clearance.get("exception")),
            "exception_reason": clearance.get("exception", ""),
            "blockers": clearance.get("blockers", []), "conditions": clearance.get("conditions", []),
        },
        "outstanding_fixes": clearance.get("outstanding", []),
        "left_for_legal": clearance.get("for_legal", []),
        "answered_for_show_or_studio": clearance.get("answers_relied_on", []),
        "integrity": {
            "assurance": v.get("assurance"), "problems": v.get("problems", []),
            "entries": len(entries), "head_hash": entries[-1].get("hash", "") if entries else "",
            "verify_with": "comfyaudit history verify <workflow.json>",
        },
        "content_credentials": signed,
        "knowledge_bases": clearance.get("audited_with", {}),
        "review_url": review_url(),
        "files": files,
    })


def _sign_renders(history: dict[str, Any], manifest: dict[str, Any],
                  tmp: str) -> tuple[list[dict[str, Any]], list[tuple[str, bytes]]]:
    """Sign the final render's outputs with c2patool, into the package only.

    Each file is signed only if its contents still match the fingerprint its
    render entry recorded, so the credentials go on the file the history
    vouches for, not on whatever now has that name.
    """
    final = next((e for e in reversed(history.get("entries", []))
                  if e.get("event") == "rendered" and e.get("outputs")), None)
    if not final:
        return [], []
    results, files = [], []
    for row in final["outputs"]:
        path = history_service.output_path({"filename": row.get("file", ""), "type": "output",
                                            "subfolder": row.get("subfolder", "")})
        if not path or not os.path.isfile(path):
            results.append({"file": row.get("file", ""), "signed": False,
                            "reason": "the render is no longer in the output folder"})
            continue
        if row.get("sha256") and H.file_sha256(path) != row["sha256"]:
            results.append({"file": row["file"], "signed": False,
                            "reason": "the file has changed since it was rendered"})
            continue
        out = os.path.join(tmp, os.path.basename(path))
        res = c2pa_mod.sign(path, manifest, out, H.__version__)
        if res.get("signed"):
            with open(out, "rb") as fh:
                files.append((f"signed/{os.path.basename(path)}", fh.read()))
        results.append(res)
    return results, files


def _signing_lines(signed: list[dict[str, Any]]) -> list[str]:
    if not signed:
        return []
    lines = ["Content Credentials (C2PA):"]
    for r in signed:
        if not r.get("signed"):
            lines.append(f"- {r['file']}: not signed - {r['reason']}")
            continue
        lines.append(f"- signed/{r['signed_file']}: signed and embedded ({r['validation_state']})"
                     + (f", by {r['signer']}" if r.get("signer") else ""))
        if r.get("note"):
            lines.append(f"  Note: {r['note']}.")
    return lines + [""]


def _legal_lines(open_items: list[dict[str, Any]]) -> list[str]:
    if not open_items:
        return []
    n = sum(len(r["items"]) for r in open_items)
    lines = [f"Left for legal to confirm ({n}): production facts nobody in the workflow has",
             "attested. Each is also in the legal report under the rule it belongs to; record",
             "each one in the shot's history once it is confirmed.", ""]
    for r in open_items:
        lines.append(f"[ ] {r['title']} ({r['jurisdiction']})")
        lines += [f"    - {t}" for t in r["items"]]
    return lines + [""]


def _answer_lines(answers: list[dict[str, Any]]) -> list[str]:
    if not answers:
        return []
    lines = [f"Answered once for the show or studio, and relied on here ({len(answers)}). Each is",
             "sealed in the sign-off with its signature, so it holds without the studio's records.", ""]
    for a in answers:
        where = "studio" if a.get("scope") == "studio" else f"show {a.get('production_name') or a.get('production')}"
        lines.append(f"[x] {a.get('tag')} - {where}: {a.get('note')} "
                     f"({(a.get('by') or {}).get('name', '')}, {str(a.get('at', ''))[:10]})")
    return lines + [""]


def _readme(shot: str, clearance: dict[str, Any],
            signed: list[dict[str, Any]] | None = None, review: str = "") -> str:
    lines = [
        f"Delivery package: {shot}",
        "",
        f"Verdict at sign-off: {clearance.get('verdict_label') or clearance.get('verdict')}",
        clearance.get("headline", ""),
        "",
        "What is in here",
        *[line for _, suffix, _m, _r, about in PACKAGE_FILES
          for line in textwrap.wrap(f"- {_slug(shot)}{suffix}: {about}", 88, subsequent_indent="  ")],
        f"- {_slug(shot)}.package.json: the index of this package for software and agents: the",
        "  decision, what is outstanding, and every file with its SHA-256. Each HTML file also",
        "  carries its data as JSON in <script id='comfyaudit-data'>.",
        "",
        *_signing_lines(signed or []),
        *([f"Legal: review this package at {review}", f"(open it and drop in {_slug(shot)}.delivery.zip).", ""]
          if review else []),
        *_legal_lines(clearance.get("for_legal") or []),
        *_answer_lines(clearance.get("answers_relied_on") or []),
        "Check the history: comfyaudit history verify <workflow.json> (with the studio key and",
        "ledger set). This package was produced by an automated tool; it is evidence to take",
        "to counsel, not legal advice.",
    ]
    if clearance.get("exception"):
        lines[4:4] = [f"Delivered as an exception: {clearance['exception']}", ""]
    return "\n".join(lines) + "\n"
