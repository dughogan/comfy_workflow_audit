"""Shot-history operations behind the web routes, kept free of aiohttp so they test.

The browser owns the open workflow; the server owns the secrets. So the canvas
posts its graph here, the server stamps it with the studio key and the
operator's declared identity from settings, and hands the graph back for the
canvas to adopt. The key never travels to the browser.
"""

from __future__ import annotations

import json
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


def input_path(value: str) -> str:
    """Resolve an input widget's value to a file in ComfyUI's own folders, or "".

    ``"x.png [output]"`` points at a render, which is how the history sees a
    result being picked and brought back in. Nothing outside ComfyUI's input,
    output and temp folders is ever read, whatever the value says.
    """
    try:
        import folder_paths
        path = folder_paths.get_annotated_filepath(str(value))
    except Exception:  # noqa: BLE001
        return ""
    if not path:
        return ""
    real = os.path.realpath(path)
    roots = []
    for getter in ("get_input_directory", "get_output_directory", "get_temp_directory"):
        try:
            roots.append(os.path.realpath(getattr(folder_paths, getter)()))
        except Exception:  # noqa: BLE001
            pass
    return real if any(real.startswith(r + os.sep) for r in roots) else ""


def _path(body: dict[str, Any]) -> str | None:
    """The workflow file the canvas says it is editing; None when it did not say."""
    if "path" not in body:
        return None
    return str(body.get("path") or "") if body.get("persisted", True) else ""


def stamp(body: dict[str, Any]) -> dict[str, Any]:
    cfg = settings_mod.history_settings()
    workflow = body["workflow"]
    outputs, files = [], []
    for item in body.get("outputs") or []:
        if isinstance(item, dict) and item.get("filename"):
            path = output_path(item)
            row = H._output_row(path) if path else {"file": item["filename"]}  # noqa: SLF001
            if item.get("type") and item["type"] != "output":
                row["kind"] = item["type"]
            outputs.append(row)
            files.append((path, row))
    k, ledger, who = keys(cfg), cfg.get("ledger", ""), actor(cfg)
    forked = None
    existing = H.read(workflow)
    if existing and ledger and H.ledger_diverged(ledger, existing):
        # Another copy of this shot has carried on past this one - an old
        # render opened again, a file restored from backup. Stamping on top
        # would put two chains under one shot id, so this copy becomes its own
        # record, with the chain it came from carried along.
        made = H.fork(workflow, reason="the studio ledger shows this shot carried on in "
                      "another copy", actor=who, keys=k, ledger=ledger, path=_path(body),
                      resolve=input_path)
        workflow, forked = made.workflow, _brief(made.entry)
    result = H.stamp(
        workflow, event=str(body.get("event", "edited")), actor=who,
        note=str(body.get("note", "") or ""), outputs=outputs, keys=k,
        name=str(body.get("name", "") or ""), project=str(body.get("project", "") or ""),
        ledger=ledger,
        external_ids={kk: str(v) for kk, v in (body.get("external_ids") or {}).items() if v},
        data_payload=body.get("data") or None,
        force=bool(body.get("force")), rename=bool(body.get("rename")),
        resolve=input_path, path=_path(body))
    if result.entry and result.entry["event"] == "rendered" and cfg.get("sidecars", True):
        for path, row in files:
            if path:
                write_sidecar(path, row, result.history, result.entry)
    return {
        "workflow": result.workflow if not result.sidecar else None,
        "history": result.history,
        "recorded": result.entry is not None,
        "entry": _brief(result.entry),
        "forked": forked,
        "summary": summary(result.history, workflow=result.workflow, cfg=cfg),
    }


SIDECAR_SUFFIX = ".provenance.json"


def write_sidecar(path: str, row: dict[str, Any], history: dict[str, Any],
                  entry: dict[str, Any]) -> str:
    """Put the shot's history next to an output, so the file carries its own life.

    PNGs embed the workflow, but EXR, video and 3D outputs mostly do not, and
    even a PNG stops at the entry before its own render. The sidecar holds the
    history through the entry that produced this file, and the file's
    fingerprint, so ``comfyaudit history verify-file`` can show this exact
    file came from that entry.
    """
    shot = entry.get("shot") or {}
    doc = {
        "comfyaudit_provenance": 1,
        "file": os.path.basename(path),
        "sha256": row.get("sha256", ""),
        "bytes": row.get("bytes"),
        "entry": entry["seq"],
        "entry_hash": entry["hash"],
        "at": entry["at"],
        "shot": {k: shot.get(k) for k in ("id", "name", "project", "external_ids")},
        "verify": f"comfyaudit history verify-file {os.path.basename(path)}",
        "history": history,
    }
    side = path + SIDECAR_SUFFIX
    tmp = side + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"), default=str)
    os.replace(tmp, side)
    return side


def _userdata(path: str) -> str:
    """A workflow path as the frontend names it, resolved inside the user folder."""
    try:
        import folder_paths
        root = os.path.realpath(os.path.join(folder_paths.get_user_directory(), "default"))
    except Exception:  # noqa: BLE001
        return ""
    real = os.path.realpath(os.path.join(root, path))
    return real if real.startswith(root + os.sep) else ""


def relocate(body: dict[str, Any]) -> dict[str, Any]:
    """The open workflow's history was last written in another file. Moved, or copied?

    If the file the history last named still holds this shot, two files now
    claim it: this one is a copy (Save As, a duplicated tab, a render opened
    again) and becomes its own record, with the original carried along. If
    that file is gone or holds something else, the workflow was moved or
    renamed, which is recorded and nothing more.
    """
    cfg = settings_mod.history_settings()
    workflow = body["workflow"]
    history = H.read(workflow)
    if not history:
        return {"error": "This workflow has no history."}
    before = H.file_of(history) or ""
    now = _path(body) or ""
    k, ledger, who = keys(cfg), cfg.get("ledger", ""), actor(cfg)
    copied = False
    if body.get("duplicate_of"):
        copied, before = True, str(body["duplicate_of"])
    elif before and before != now:
        old = _userdata(before)
        if old and os.path.isfile(old):
            try:
                with open(old, encoding="utf-8") as fh:
                    other = H.read(json.load(fh))
                copied = bool(other) and H.chain_shot_id(other) == H.chain_shot_id(history)
            except (OSError, ValueError):
                copied = False
    if copied:
        reason = (f"duplicated from {before}" if body.get("duplicate_of") else
                  f"saved as a copy of {before}" if now else f"opened again outside {before}")
        # A shot only ever named after an unsaved tab takes the copy's name.
        temporary = (history["shot"].get("name") or "").lower().startswith("unsaved workflow")
        made = H.fork(workflow, reason=reason, actor=who, keys=k, ledger=ledger,
                      path=_path(body), parent_file=before, resolve=input_path,
                      name=str(body.get("name", "") or "") if temporary else "")
        return {"history": made.history, "forked": _brief(made.entry), "recorded": True,
                "summary": summary(made.history, workflow=made.workflow, cfg=cfg)}
    if before == now:
        return {"history": history, "recorded": False,
                "summary": summary(history, workflow=workflow, cfg=cfg)}
    made = H.stamp(workflow, event="moved", actor=who, keys=k, ledger=ledger,
                   data_payload={"from": before, "to": now}, path=_path(body),
                   resolve=input_path)
    return {"history": made.history, "recorded": made.entry is not None,
            "entry": _brief(made.entry),
            "summary": summary(made.history, workflow=made.workflow, cfg=cfg)}


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
        "commercial": _commercial(history),
        "file": H.file_of(history),
        "shot_id": H.chain_shot_id(history),
        "clearance": H.delivery_clearance(history),
    }


def _commercial(history: dict[str, Any]) -> dict[str, Any]:
    """Where the shot stands on commercial use now, and when that last changed."""
    timeline = H.commercial_timeline(history)
    if not timeline:
        return {"status": "none", "since": None, "changes": []}
    return {"status": timeline[-1]["status"], "since": timeline[-1], "changes": timeline}


def record(body: dict[str, Any]) -> dict[str, Any]:
    cfg = settings_mod.history_settings()
    workflow = body["workflow"]
    history = H.read(workflow)
    if not history:
        return {"error": "This workflow has no shot history yet. Start tracking it first."}
    html = history_html.render(history, data=workflow, keys=keys(cfg),
                               ledger=cfg.get("ledger", ""),
                               reveal=bool(body.get("reveal")), context=context(cfg, body))
    return {"html": html, "summary": summary(history, workflow=workflow, cfg=cfg)}


def context(cfg: dict[str, Any], body: dict[str, Any] | None = None) -> dict[str, Any]:
    """The facts the rules checklist turns on: studio settings, plus per-request ones."""
    body = body or {}
    ctx = {"territory": cfg.get("territory", ""),
           "distribute_in": cfg.get("distribute_in", []),
           "likeness": cfg.get("likeness", False),
           "youtube": bool(body.get("youtube")),
           "clients": list(cfg.get("clients", [])) + list(body.get("clients", []) or [])}
    for flag in ("union_performers", "advertising", "feature", "union_crew", "animation",
                 "tpn_vendor", "eo_insurance", "copyright_claim",
                 "third_party_characters", "ai_music", "intimate_imagery"):
        ctx[flag] = bool(body.get(flag) or cfg.get(flag))
    return ctx


def manifest(body: dict[str, Any]) -> dict[str, Any]:
    history = H.read(body["workflow"])
    if not history:
        return {"error": "This workflow has no shot history yet."}
    return {"manifest": H.c2pa_manifest(history, title=str(body.get("title", "") or ""))}


def guide() -> str:
    return licensing_guide.render()
