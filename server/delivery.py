"""Package for delivery: check a workflow for commercial use, then seal and package it.

Two steps behind the one button in the sidebar:

* ``check`` runs the audit against the studio profile and turns its verdict
  into a to-do list: what blocks delivery and what would lift it, which inputs
  still need an origin, and which rules want a person to confirm something.
* ``deliver`` re-runs that check on the server - the verdict that gets sealed
  is never one the browser sent - records the sign-off with the clearance
  inside it, moves the shot to *delivered*, and returns the package: the
  workflow with its sealed history, a C2PA manifest definition, the legal
  report and the audit's clearance sheet, together as a zip.

A no-go, or a verdict that could not be reached, is only delivered when the
person signing off records why; that exception is sealed with the sign-off.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from typing import Any

from ..core import history as H
from ..core.knowledge import alternatives as alternatives_mod
from ..core.knowledge import licences as licences_mod
from ..core.report import compliance, history_html
from ..core.report import html as html_report
from ..core.score import clearance as clearance_mod
from . import history_service
from . import settings as settings_mod


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


def _confirmations(lin, history, cfg) -> list[dict[str, Any]]:
    """Rules that apply and still want a person to confirm something."""
    checks = compliance.assess(lin, history, history_service.context(cfg))
    out = []
    for rule in checks["rules"]:
        items = [{"text": e["text"], "tag": e["tag"]} for e in rule["evidence"]
                 if e["state"] == "need" and e.get("tag")]
        if items:
            out.append({"rule": rule["id"], "title": rule["title"],
                        "jurisdiction": rule["jurisdiction"], "items": items})
    return out


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
        "confirmations": _confirmations(lin, history, cfg) if lin else [],
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

    keys = history_service.keys(cfg)
    ledger = cfg.get("ledger", "")
    who = history_service.actor(cfg)
    name = str(body.get("name", "") or "")
    signed = H.stamp(body["workflow"], event="approved", actor=who, keys=keys, ledger=ledger,
                     name=name, note=str(body.get("note", "") or "Approved for delivery"),
                     data_payload={"clearance": clearance})
    done = H.stamp(signed.workflow, event="promoted", actor=who, keys=keys, ledger=ledger,
                   note="Packaged for delivery", data_payload={"stage": "delivered"})
    workflow, history = done.workflow, done.history

    shot = history["shot"].get("name") or name or "shot"
    slug = _slug(shot)
    manifest = H.c2pa_manifest(history, title=shot)
    record = history_html.render(history, data=workflow, keys=keys, ledger=ledger,
                                 context=history_service.context(cfg))
    sheet = html_report.render(report)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{slug}.json", json.dumps(workflow, indent=1, default=str))
        zf.writestr(f"{slug}.c2pa.json", json.dumps(manifest, indent=1, default=str))
        zf.writestr(f"{slug}.legal-report.html", record)
        zf.writestr(f"{slug}.clearance-sheet.html", sheet)
        zf.writestr("README.txt", _readme(shot, clearance))

    return {
        "workflow": workflow,
        "history": history,
        "summary": history_service.summary(history, workflow=workflow, cfg=cfg),
        "manifest": manifest,
        "report_html": record,
        "zip_name": f"{slug}.delivery.zip",
        "zip_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }


def _readme(shot: str, clearance: dict[str, Any]) -> str:
    lines = [
        f"Delivery package: {shot}",
        "",
        f"Verdict at sign-off: {clearance.get('verdict_label') or clearance.get('verdict')}",
        clearance.get("headline", ""),
        "",
        "What is in here",
        f"- {_slug(shot)}.json: the ComfyUI workflow. Its shot history (extra.comfyaudit_history)",
        "  is hash-chained and, when the studio key is set, signed. The sign-off entry carries",
        "  the clearance above, sealed.",
        f"- {_slug(shot)}.legal-report.html: the provenance record for legal - clearance, who did",
        "  what and when, models over time, prompt authorship, sources, the rules checklist.",
        f"- {_slug(shot)}.clearance-sheet.html: the full licence and risk audit behind the verdict.",
        f"- {_slug(shot)}.c2pa.json: a C2PA manifest definition for the delivered file. It is",
        "  unsigned; sign and embed it with c2patool or the studio's signing service.",
        "",
        "Check the history: comfyaudit history verify <workflow.json> (with the studio key and",
        "ledger set). This package was produced by an automated tool; it is evidence to take",
        "to counsel, not legal advice.",
    ]
    if clearance.get("exception"):
        lines[4:4] = [f"Delivered as an exception: {clearance['exception']}", ""]
    return "\n".join(lines) + "\n"
