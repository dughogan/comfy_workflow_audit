"""Shot-history operations behind the web routes, kept free of aiohttp so they test.

The browser owns the open workflow; the server owns the secrets. So the canvas
posts its graph here, the server stamps it with the studio key and the
operator's declared identity from settings, and hands the graph back for the
canvas to adopt. The key never travels to the browser.
"""

from __future__ import annotations

import os
from typing import Any

from ..core import history as H
from ..core.report import history_html, licensing_guide
from . import settings as settings_mod


def keys(cfg: dict[str, Any] | None = None) -> H.Keys:
    """Studio keys: the environment wins, then the key file named in settings."""
    cfg = cfg if cfg is not None else settings_mod.history_settings()
    k = H.Keys.from_environment(confidentiality=cfg.get("confidentiality", ""))
    if not k.sign_key and cfg.get("key_file") and os.path.isfile(cfg["key_file"]):
        with open(cfg["key_file"], encoding="utf-8") as fh:
            secret = fh.read().strip()
        if secret:
            k.sign_key = H._derive(secret, b"comfyaudit-sign")  # noqa: SLF001
            k.key_id = os.environ.get("COMFYAUDIT_HISTORY_KEY_ID", "") \
                or H.sha256(k.sign_key)[:12]
            if k.confidentiality == "encrypt" and not k.enc_key:
                # One studio secret can cover both, derived apart, so a team
                # that set up signing gets encryption without a second file.
                k.enc_key = H._derive(secret, b"comfyaudit-enc")  # noqa: SLF001
    return k


def actor(cfg: dict[str, Any]) -> H.Actor:
    try:
        import comfy  # noqa: F401
        tool = "ComfyUI"
    except Exception:  # noqa: BLE001
        tool = ""
    return H.Actor.current(name=cfg.get("user", ""), role=cfg.get("role", ""),
                           tool=f"{tool} + comfyaudit {H.__version__}".strip(" +"))


def output_path(item: dict[str, Any]) -> str:
    """Resolve an output reference from the ``executed`` event to a file on disk."""
    try:
        import folder_paths
    except ImportError:
        return ""
    kind = item.get("type", "output")
    base = {"output": getattr(folder_paths, "get_output_directory", lambda: "")(),
            "temp": getattr(folder_paths, "get_temp_directory", lambda: "")(),
            "input": getattr(folder_paths, "get_input_directory", lambda: "")()}.get(kind, "")
    if not base:
        return ""
    root = os.path.realpath(base)
    path = os.path.realpath(os.path.join(root, item.get("subfolder", ""), item.get("filename", "")))
    # Never let a posted name - or a symlink inside the folder - walk out of
    # ComfyUI's own folders.
    if not path.startswith(root + os.sep):
        return ""
    return path


def stamp(body: dict[str, Any]) -> dict[str, Any]:
    cfg = settings_mod.history_settings()
    workflow = body["workflow"]
    outputs = []
    for item in body.get("outputs") or []:
        if isinstance(item, dict) and item.get("filename"):
            path = output_path(item)
            row = H._output_row(path) if path else {"file": item["filename"]}  # noqa: SLF001
            if item.get("type") and item["type"] != "output":
                row["kind"] = item["type"]
            outputs.append(row)
    result = H.stamp(
        workflow, event=str(body.get("event", "edited")), actor=actor(cfg),
        note=str(body.get("note", "") or ""), outputs=outputs, keys=keys(cfg),
        name=str(body.get("name", "") or ""), project=str(body.get("project", "") or ""),
        ledger=cfg.get("ledger", ""),
        external_ids={k: str(v) for k, v in (body.get("external_ids") or {}).items() if v},
        data_payload=body.get("data") or None,
        force=bool(body.get("force")))
    return {
        "workflow": result.workflow if not result.sidecar else None,
        "history": result.history,
        "recorded": result.entry is not None,
        "entry": _brief(result.entry),
        "summary": summary(result.history, workflow=result.workflow, cfg=cfg),
    }


def _brief(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    return {k: entry.get(k) for k in ("seq", "at", "event", "actor", "note",
                                      "breakpoints", "changes", "hash")}


def summary(history: dict[str, Any] | None, *, workflow: dict[str, Any] | None = None,
            cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the sidebar shows at a glance."""
    cfg = cfg if cfg is not None else settings_mod.history_settings()
    if not history:
        return {"present": False, "user": cfg.get("user", ""),
                "auto": cfg.get("auto", True)}
    lin = H.lineage(history)
    v = H.verify(workflow or {}, keys=keys(cfg), ledger=cfg.get("ledger", ""),
                 history=history)
    rows = H.timeline(history)
    return {
        "present": True,
        "user": cfg.get("user", ""),
        "auto": cfg.get("auto", True),
        "shot": lin["shot"],
        "entries": lin["entries"],
        "people": [p["name"] for p in lin["people"]],
        "renders": lin["renders"],
        "human_steps": lin["human_steps"],
        "gaps": lin["gaps"],
        "sources": [{"node": s["node"], "origin": s.get("origin", ""),
                     "asset_id": s.get("asset_id", "")} for s in lin["sources"]],
        "assurance": v.assurance,
        "assurance_detail": v.assurance_detail,
        "problems": v.problems,
        "notes": v.notes,
        "recent": [{"seq": r["seq"], "at": r["at"], "label": r["label"],
                    "event": r["event"], "who": r["actor"].get("name", ""),
                    "note": r["note"]} for r in rows[-6:]][::-1],
        "stages": list(H.STAGES),
        "origins": H.ORIGINS,
    }


def record(body: dict[str, Any]) -> dict[str, Any]:
    cfg = settings_mod.history_settings()
    workflow = body["workflow"]
    history = H.read(workflow)
    if not history:
        return {"error": "This workflow has no shot history yet. Start tracking it first."}
    context = {"territory": cfg.get("territory", ""),
               "distribute_in": cfg.get("distribute_in", []),
               "likeness": cfg.get("likeness", False),
               "union_performers": bool(body.get("union_performers") or cfg.get("union_performers")),
               "advertising": bool(body.get("advertising") or cfg.get("advertising")),
               "feature": bool(body.get("feature") or cfg.get("feature")),
               "youtube": bool(body.get("youtube")),
               "clients": list(cfg.get("clients", [])) + list(body.get("clients", []) or [])}
    for flag in ("union_crew", "animation", "tpn_vendor", "eo_insurance", "copyright_claim",
                 "third_party_characters", "ai_music", "intimate_imagery"):
        context[flag] = bool(body.get(flag) or cfg.get(flag))
    html = history_html.render(history, data=workflow, keys=keys(cfg),
                               ledger=cfg.get("ledger", ""),
                               reveal=bool(body.get("reveal")), context=context)
    return {"html": html, "summary": summary(history, workflow=workflow, cfg=cfg)}


def manifest(body: dict[str, Any]) -> dict[str, Any]:
    history = H.read(body["workflow"])
    if not history:
        return {"error": "This workflow has no shot history yet."}
    return {"manifest": H.c2pa_manifest(history, title=str(body.get("title", "") or ""))}


def guide() -> str:
    return licensing_guide.render()
