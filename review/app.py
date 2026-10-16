"""What ComfyAudit Review does, kept free of HTTP so it tests.

A delivery package (``<shot>.delivery.zip``, made by Package for delivery in
ComfyUI) is opened here, checked, and reviewed:

* **checked** - every file against the SHA-256 in ``<shot>.package.json``, and
  the shot history inside the workflow with ``history.verify`` (studio key,
  personal keys and ledger, as configured for this server);
* **read** - the decision is read from the signed chain (the sign-off entry),
  never from the unsigned index; what is open for legal is what the sign-off
  sealed, less what has been answered since;
* **answered** - show and studio items go to the shared answer store
  (``core/answers.py``), signed the same way; shot items are signed ``note``
  entries in the shot's own history, tagged ``data.evidence``;
* **decided** - clearing or sending a shot back is a signed entry in the
  history (``approved`` / ``rejected`` with ``data.legal``, read back by
  ``history.legal_decision``), and the
  updated workflow is offered for download and noted on ShotGrid.

Every write is signed. A server without a signing key is read-only: an
unsigned decision on a signed history is exactly what a forger would add.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import re
import threading
import zipfile
from dataclasses import dataclass
from typing import Any

from ..core import __version__
from ..core import answers as answers_mod
from ..core import history as H
from ..core.report import compliance
from ..server import history_service, shotgrid
from ..server import settings as settings_mod

TOOL = f"ComfyAudit Review {__version__}"
PACKAGE_SCHEMA = "comfyaudit/delivery-package/"
#: The role a decision is recorded under when the keyring gives none.
REVIEW_ROLE = "legal"
DECISIONS = {"clear": "Cleared by legal", "send-back": "Sent back by legal"}
#: How the shot stands with legal, for people.
STATES = {"cleared": "Cleared by legal", "sent-back": "Sent back",
          "awaiting": "Waiting for legal review"}
#: A single file inside a package bigger than this is refused rather than read.
MAX_MEMBER = 512 * 1024 * 1024
_PID = re.compile(r"^[0-9a-f]{16}$")


class ReviewError(Exception):
    """Something went wrong that the person can act on, said plainly."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class Config:
    """Where the app keeps things and how it listens. Environment only:

    * ``COMFYAUDIT_REVIEW_DIR`` - where opened packages and their reviewed
      workflows are kept (default ``~/.comfyaudit/review``);
    * ``COMFYAUDIT_REVIEW_INBOX`` - optional folder of packages to pick from;
    * ``COMFYAUDIT_REVIEW_HOST`` / ``COMFYAUDIT_REVIEW_PORT`` (default
      127.0.0.1:8095);
    * ``COMFYAUDIT_REVIEW_MAX_MB`` - largest upload (default 1024).

    Signing, the ledger, answers and ShotGrid use the same variables as the
    ComfyUI side (``COMFYAUDIT_HISTORY_KEY_FILE``, ``COMFYAUDIT_HISTORY_KEYRING``,
    ``COMFYAUDIT_TRUSTED_USER_HEADER``, ``COMFYAUDIT_HISTORY_LEDGER``,
    ``COMFYAUDIT_ANSWERS_FILE``, ``COMFYAUDIT_SHOTGRID_*``), so both sides
    sign and check with the same keys and share one set of answers.
    """

    root: str
    inbox: str = ""
    host: str = "127.0.0.1"
    port: int = 8095
    max_bytes: int = 1024 * 1024 * 1024

    @classmethod
    def from_environment(cls) -> "Config":
        env = os.environ
        return cls(
            root=env.get("COMFYAUDIT_REVIEW_DIR", "").strip()
            or os.path.join(os.path.expanduser("~"), ".comfyaudit", "review"),
            inbox=env.get("COMFYAUDIT_REVIEW_INBOX", "").strip(),
            host=env.get("COMFYAUDIT_REVIEW_HOST", "").strip() or "127.0.0.1",
            port=int(env.get("COMFYAUDIT_REVIEW_PORT", "") or 8095),
            max_bytes=int(float(env.get("COMFYAUDIT_REVIEW_MAX_MB", "") or 1024) * 1024 * 1024))


@dataclass
class Person:
    """Who is making a change, and the keys it is signed with."""

    actor: H.Actor
    keys: H.Keys
    #: "sign-in" when the studio's sign-in named them and their own key signs;
    #: "declared" when they typed their name and the studio key signs.
    mode: str


def _studio_keys() -> H.Keys:
    return history_service.keys(settings_mod.history_settings())


def _ledger() -> str:
    return os.environ.get("COMFYAUDIT_HISTORY_LEDGER", "").strip()


def per_person() -> bool:
    return bool(os.environ.get(history_service.TRUSTED_USER_HEADER))


def person(signed_in: str = "", declared: str = "") -> Person:
    """The person behind a request. Raises ReviewError when nothing they do could be signed."""
    machine = H.Actor.current().machine
    if per_person():
        try:
            keys = history_service.signer(settings_mod.history_settings(),
                                          {"_signed_in_user": signed_in})
        except ValueError as exc:
            raise ReviewError(str(exc)) from exc
        info = keys.people_names.get(keys.key_id, {})
        actor = H.Actor(name=info.get("name") or signed_in, id=info.get("user") or signed_in,
                        role=info.get("role") or REVIEW_ROLE, machine=machine,
                        tool=TOOL, declared_by="studio sign-in")
        return Person(actor, keys, "sign-in")
    keys = _studio_keys()
    if not keys.sign_key:
        raise ReviewError("This server has no signing key (COMFYAUDIT_HISTORY_KEY_FILE), so it "
                          "can show packages but not record decisions: an unsigned decision on "
                          "a signed history would read as tampering.")
    name = " ".join(str(declared or "").split())
    if not name:
        raise ReviewError("Give your name first. It is recorded with what you decide.")
    actor = H.Actor(name=name, role=REVIEW_ROLE, machine=machine, tool=TOOL,
                    declared_by="declared in ComfyAudit Review",
                    id=os.environ.get("COMFYAUDIT_USER_ID", ""))
    return Person(actor, keys, "declared")


def identity(signed_in: str = "", declared: str = "") -> dict[str, Any]:
    """What the page says about who is reviewing, without raising."""
    try:
        p = person(signed_in, declared)
        return {"mode": p.mode, "name": p.actor.name, "role": p.actor.role, "can_sign": True,
                "key": p.keys.key_id, "problem": ""}
    except ReviewError as exc:
        return {"mode": "sign-in" if per_person() else "declared", "name": declared,
                "role": "", "can_sign": False, "key": "", "problem": str(exc)}


def status(me: dict[str, Any]) -> dict[str, Any]:
    """What a front end needs to know about this server before it shows anything.

    No paths and no key material: only how decisions here are signed and who
    the caller is."""
    keys = _studio_keys()
    return {"app": "ComfyAudit Review API", "version": __version__,
            "signing": "per-person" if per_person() else ("studio-key" if keys.sign_key else "none"),
            "ledger": bool(_ledger()), "shotgrid": shotgrid.configured(),
            "you": me}


# ---------------------------------------------------------------------------
# reading a package
# ---------------------------------------------------------------------------

def _member(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > MAX_MEMBER:
        raise ReviewError(f"{name} in the package is larger than this server reads.")
    return zf.read(name)


def _index_of(zf: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    names = [n for n in zf.namelist() if n.endswith(".package.json") and "/" not in n]
    if len(names) != 1:
        raise ReviewError("This is not a ComfyAudit delivery package: it needs exactly one "
                          "<shot>.package.json index (packages are made by Package for "
                          "delivery in ComfyUI).")
    try:
        index = json.loads(_member(zf, names[0]))
    except ValueError as exc:
        raise ReviewError(f"{names[0]} is not readable JSON.") from exc
    if not str(index.get("schema", "")).startswith(PACKAGE_SCHEMA):
        raise ReviewError(f"{names[0]} is not a ComfyAudit delivery package index "
                          f"(schema {index.get('schema')!r}).")
    return names[0], index


def check_files(zf: zipfile.ZipFile, index_name: str, index: dict[str, Any]) -> dict[str, Any]:
    """Every file against the fingerprint the index gives it."""
    present = set(zf.namelist())
    rows, listed = [], set()
    for f in index.get("files") or []:
        path = str(f.get("path", ""))
        listed.add(path)
        row = {"path": path, "role": f.get("role", ""), "media_type": f.get("media_type", ""),
               "description": f.get("description", ""), "bytes": f.get("bytes")}
        if path not in present:
            row["state"] = "missing"
        else:
            got = hashlib.sha256(_member(zf, path)).hexdigest()
            row["state"] = "ok" if got == f.get("sha256") else "changed"
        rows.append(row)
    unlisted = sorted(n for n in present - listed - {index_name} if not n.endswith("/"))
    bad = [r for r in rows if r["state"] != "ok"]
    problems = [f"{r['path']} {'is missing' if r['state'] == 'missing' else 'has changed since it was packaged'}"
                for r in bad] + [f"{n} is in the package but not in its index" for n in unlisted]
    return {"files": rows, "unlisted": unlisted, "ok": not problems, "problems": problems,
            "index": index_name}


def root_shot(history: dict[str, Any]) -> str:
    """The id of the first record in a line of copies: forks of one shot group together."""
    h, depth = history, 0
    while isinstance(h.get("parent"), dict) and isinstance(h["parent"].get("history"), dict) \
            and depth < H.MAX_FORK_DEPTH:
        h, depth = h["parent"]["history"], depth + 1
    return H.chain_shot_id(h)


def _rules() -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in compliance.load().get("rules", [])}


def _item(tag: str) -> dict[str, Any] | None:
    """A rule's evidence item by tag, if it is one a person answers."""
    rule_id, _, idx = str(tag).partition(":")
    rule = _rules().get(rule_id)
    items = (rule or {}).get("evidence", [])
    if not idx.isdigit() or int(idx) >= len(items) or items[int(idx)].get("check") != "manual":
        return None
    return {**items[int(idx)], "rule": rule, "index": int(idx)}


def legal_reviews(history: dict[str, Any]) -> list[dict[str, Any]]:
    """Every legal decision in the chain ("cleared" / "sent back"), oldest first."""
    out = []
    for e in history.get("entries", []):
        review = (e.get("data") or {}).get("legal")
        if isinstance(review, dict) and H.LEGAL_DECISIONS.get(review.get("decision")) == e.get("event"):
            out.append({**review, "seq": e["seq"], "at": e.get("at", ""), "reason": e.get("note", ""),
                        "by": (e.get("actor") or {}).get("name", ""),
                        "role": (e.get("actor") or {}).get("role", ""),
                        "declared_by": (e.get("actor") or {}).get("declared_by", ""),
                        "signed_with": e.get("key_id", "")})
    return out


class Review:
    """Opened packages, kept under ``Config.root``:

    ``packages/<id>/package.zip`` (as received, never changed),
    ``packages/<id>/workflow.json`` (the workflow with every review entry
    since), ``packages/<id>/meta.json``. ``<id>`` is the first 16 hex digits
    of the zip's SHA-256, so opening the same package twice finds it again.
    """

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_environment()
        self.lock = threading.RLock()
        os.makedirs(self._packages(), exist_ok=True)

    # -- storage ---------------------------------------------------------------
    def _packages(self) -> str:
        return os.path.join(self.config.root, "packages")

    def _dir(self, pid: str) -> str:
        if not _PID.match(str(pid)):
            raise ReviewError("There is no such package here.")
        d = os.path.join(self._packages(), pid)
        if not os.path.isfile(os.path.join(d, "meta.json")):
            raise ReviewError("There is no such package here.")
        return d

    @staticmethod
    def _write_json(path: str, data: Any) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, default=str)
        os.replace(tmp, path)

    def meta(self, pid: str) -> dict[str, Any]:
        with open(os.path.join(self._dir(pid), "meta.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def workflow(self, pid: str) -> dict[str, Any]:
        with open(os.path.join(self._dir(pid), "workflow.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def _zip(self, pid: str) -> zipfile.ZipFile:
        return zipfile.ZipFile(os.path.join(self._dir(pid), "package.zip"))

    def package_ids(self) -> list[str]:
        out = []
        for name in os.listdir(self._packages()):
            if _PID.match(name) and os.path.isfile(os.path.join(self._packages(), name, "meta.json")):
                out.append(name)
        return out

    # -- opening ---------------------------------------------------------------
    def add(self, data: bytes, filename: str = "", by: str = "") -> str:
        """Open a package; returns its id. The same package opened again keeps its review."""
        if len(data) > self.config.max_bytes:
            raise ReviewError("That package is larger than this server accepts "
                              "(COMFYAUDIT_REVIEW_MAX_MB).")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ReviewError("That file is not a zip. Open the .delivery.zip that Package for "
                              "delivery made.") from exc
        with zf:
            index_name, index = _index_of(zf)
            wf_path = next((f.get("path") for f in index.get("files") or []
                            if f.get("role") == "workflow"), "")
            if not wf_path or wf_path not in zf.namelist():
                raise ReviewError("The package index names no workflow, or the workflow is missing.")
            try:
                workflow = json.loads(_member(zf, wf_path))
            except ValueError as exc:
                raise ReviewError(f"{wf_path} is not readable JSON.") from exc
        history = H.read(workflow) if isinstance(workflow, dict) else None
        if not history or not history.get("entries"):
            raise ReviewError("The workflow in this package carries no shot history.")
        pid = hashlib.sha256(data).hexdigest()[:16]
        d = os.path.join(self._packages(), pid)
        with self.lock:
            if os.path.isfile(os.path.join(d, "meta.json")):
                return pid
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "package.zip"), "wb") as fh:
                fh.write(data)
            self._write_json(os.path.join(d, "workflow.json"), workflow)
            shot = history["entries"][-1].get("shot") or {}
            self._write_json(os.path.join(d, "meta.json"), {
                "id": pid, "sha256": hashlib.sha256(data).hexdigest(),
                "filename": os.path.basename(str(filename or "")) or f"{pid}.delivery.zip",
                "opened_at": _now(), "opened_by": by,
                "index": index_name, "workflow_file": wf_path,
                "shot_id": H.chain_shot_id(history), "root_shot": root_shot(history),
                "shot_name": shot.get("name", ""), "project": shot.get("project", ""),
                "production": answers_mod.production_key(shot.get("project", "")),
                "entries_at_packaging": len(history["entries"]),
                "head_at_packaging": history["entries"][-1].get("hash", ""),
                "shotgrid": [],
            })
        return pid

    def inbox(self) -> list[dict[str, Any]]:
        """Packages waiting in the inbox folder, newest first."""
        folder = self.config.inbox
        if not folder or not os.path.isdir(folder):
            return []
        rows = []
        opened = {}
        for pid in self.package_ids():
            try:
                opened[self.meta(pid)["sha256"]] = pid
            except (OSError, ValueError, KeyError):
                pass
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.lower().endswith(".zip") and os.path.isfile(path):
                st = os.stat(path)
                digest = hashlib.sha256()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
                rows.append({"name": name, "bytes": st.st_size,
                             "opened": opened.get(digest.hexdigest(), ""),
                             "modified": _dt.datetime.fromtimestamp(st.st_mtime).astimezone()
                             .isoformat(timespec="seconds")})
        return sorted(rows, key=lambda r: r["modified"], reverse=True)

    def add_from_inbox(self, name: str, by: str = "") -> str:
        folder = self.config.inbox
        if not folder or name not in {r["name"] for r in self.inbox()}:
            raise ReviewError("That package is not in the inbox.")
        root = os.path.realpath(folder)
        path = os.path.realpath(os.path.join(root, name))
        if not path.startswith(root + os.sep):
            raise ReviewError("That package is not in the inbox.")
        with open(path, "rb") as fh:
            return self.add(fh.read(), name, by)

    def file(self, pid: str, path: str) -> tuple[bytes, str]:
        """One file from a package, as received, with its media type from the index."""
        meta = self.meta(pid)
        with self._zip(pid) as zf:
            _, index = _index_of(zf)
            known = {f.get("path"): f.get("media_type", "") for f in index.get("files") or []}
            known[meta["index"]] = "application/json"
            if path not in known or path not in zf.namelist():
                raise ReviewError("That file is not part of this package.")
            return _member(zf, path), known[path] or "application/octet-stream"

    # -- the shot ----------------------------------------------------------------
    def _open_items(self, history: dict[str, Any], sealed: dict[str, Any] | None,
                    inherited: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """What the sign-off left for legal, each with where it stands now."""
        rules = _rules()
        out = []
        for group in (sealed or {}).get("for_legal") or []:
            rule = rules.get(group.get("rule", ""), {})
            evidence = rule.get("evidence", [])
            texts = list(group.get("items") or [])
            tags = list(group.get("tags") or [])
            if len(tags) != len(texts):
                # Packages made before tags were sealed: find each item by its text.
                tags = [next((f"{rule.get('id')}:{i}" for i, ev in enumerate(evidence)
                              if ev.get("text") == t), "") for t in texts]
            attested = compliance._attested(group.get("rule", ""), history)  # noqa: SLF001
            items = []
            for text, tag in zip(texts, tags):
                idx = int(tag.partition(":")[2]) if tag.partition(":")[2].isdigit() else -1
                scope = evidence[idx].get("scope", "shot") if 0 <= idx < len(evidence) else "shot"
                row = {"text": text, "tag": tag, "scope": scope, "state": "open", "answer": None}
                if idx in attested:
                    row["state"], row["answer"] = "answered", {"scope": "shot", "by": attested[idx]}
                elif tag in inherited:
                    a = inherited[tag]
                    row["state"] = "answered"
                    row["answer"] = {"scope": a["scope"], "note": a.get("note", ""),
                                     "by": (a.get("by") or {}).get("name", ""), "at": a.get("at", ""),
                                     "signature": a.get("state", ""),
                                     "production_name": a.get("production_name", "")}
                elif not tag:
                    row["state"] = "unknown"
                items.append(row)
            out.append({"rule": group.get("rule", ""), "title": group.get("title", ""),
                        "jurisdiction": group.get("jurisdiction", ""),
                        "why": rule.get("summary", ""), "source": rule.get("source", ""),
                        "items": items})
        return out

    def shot(self, pid: str) -> dict[str, Any]:
        """Everything the shot page shows, as data. The page is drawn from this."""
        meta = self.meta(pid)
        workflow = self.workflow(pid)
        history = H.read(workflow) or {}
        keys, ledger = _studio_keys(), _ledger()
        with self._zip(pid) as zf:
            index_name, index = _index_of(zf)
            files = check_files(zf, index_name, index)
        v = H.verify(workflow, keys=keys, ledger=ledger)
        # The studio ledger shows the shot carried on past this copy (the artist kept
        # working, or another review recorded decisions): an old copy, not a damaged one.
        # It is checked as one, and kept read-only: writing here would fork the shot.
        old_copy = bool(ledger) and H.ledger_diverged(ledger, history)
        if old_copy:
            v = H.verify(workflow, keys=keys, ledger=ledger, _as_parent=True)  # noqa: SLF001
            v.notes.insert(0, "the studio ledger shows this shot carried on past this copy: "
                              "it has later entries elsewhere")
        lin = H.lineage(history)
        sealed = H.delivery_clearance(history)
        project = lin["shot"].get("project", "")
        inherited = history_service.answers_store().for_shot(project, keys)
        items = self._open_items(history, sealed, inherited)
        open_count = sum(1 for g in items for i in g["items"] if i["state"] != "answered")
        reviews = legal_reviews(history)
        last = reviews[-1] if reviews else None
        state = ("cleared" if last and last["decision"] == "cleared" else
                 "sent-back" if last and last["decision"] == "sent back" else "awaiting")
        relied = []
        for a in (sealed or {}).get("answers_relied_on") or []:
            relied.append({k: a.get(k) for k in ("tag", "scope", "production_name", "note", "at")}
                          | {"by": (a.get("by") or {}).get("name", ""),
                             "signature": answers_mod.state(a, keys), "when": "sealed at sign-off"})
        relied_tags = {a["tag"] for a in relied}
        for g in items:
            for i in g["items"]:
                ans = i["answer"]
                if ans and ans["scope"] in ("show", "studio") and i["tag"] not in relied_tags:
                    relied.append({"tag": i["tag"], "scope": ans["scope"],
                                   "production_name": ans.get("production_name", ""),
                                   "note": ans.get("note", ""), "at": ans.get("at", ""),
                                   "by": ans.get("by", ""), "signature": ans.get("signature", ""),
                                   "when": "answered since the sign-off"})
        rules = _rules()
        for a in relied:
            rid, _, idx = str(a["tag"]).partition(":")
            ev = rules.get(rid, {}).get("evidence", [])
            a["text"] = ev[int(idx)]["text"] if idx.isdigit() and int(idx) < len(ev) else a["tag"]
            a["rule_title"] = rules.get(rid, {}).get("title", rid)
        since = [{"seq": r["seq"], "at": r["at"], "event": r["event"], "label": r["label"],
                  "who": r["actor"].get("name", ""), "role": r["actor"].get("role", ""),
                  "note": r["note"]}
                 for r in H.timeline(history)[meta.get("entries_at_packaging", 0):]]
        docs = [f for f in files["files"] if f["role"] in (
            "legal-report", "licence-audit", "life-of-the-shot")]
        other = [f for f in files["files"] if f not in docs]
        writable = v.assurance != "none" and files["ok"] and not old_copy
        return {
            "package": {"id": pid, "filename": meta["filename"], "sha256": meta["sha256"],
                        "opened_at": meta["opened_at"], "opened_by": meta.get("opened_by", ""),
                        "index": index_name, "made_with": index.get("comfyaudit_version", ""),
                        "made_at": index.get("generated_at", "")},
            "shot": {**lin["shot"], "root": root_shot(history),
                     "production": answers_mod.production_key(project),
                     "shotgrid": shotgrid.shot_ref(history)},
            "legal_state": state, "legal_state_label": STATES[state],
            "legal": H.legal_decision(history),
            "decision": None if not sealed else {
                "verdict": sealed.get("verdict", ""), "verdict_label": sealed.get("verdict_label", ""),
                "headline": sealed.get("headline", ""), "checked_as": sealed.get("profile", ""),
                "signed_off_by": sealed.get("by", ""), "role": sealed.get("role", ""),
                "signed_off_at": sealed.get("at", ""), "note": sealed.get("note", ""),
                "exception": sealed.get("exception", ""),
                "blockers": sealed.get("blockers", []), "conditions": sealed.get("conditions", []),
                "outstanding": sealed.get("outstanding", []), "entry": sealed.get("entry"),
                "index_agrees": (index.get("decision") or {}).get("verdict") == sealed.get("verdict"),
            },
            "integrity": {
                "assurance": v.assurance, "label": H.ASSURANCE_LABELS.get(v.assurance, v.assurance),
                "detail": v.assurance_detail, "problems": v.problems, "notes": v.notes,
                "ledger": v.ledger, "entries": v.entries, "signers": v.signers,
                "package_files": files["ok"], "package_problems": files["problems"],
                "head_at_packaging": meta.get("head_at_packaging", ""),
                "head_in_index": (index.get("integrity") or {}).get("head_hash", ""),
            },
            "open_items": items, "open_count": open_count,
            "inherited": relied,
            "reviews": reviews, "last_review": last,
            "since_packaging": since,
            "documents": docs, "other_files": other,
            "content_credentials": index.get("content_credentials", []),
            "shotgrid": {"configured": shotgrid.configured(), "posts": meta.get("shotgrid", [])},
            "writable": writable,
            "old_copy": old_copy,
            "why_not_writable": "" if writable else (
                "This package is an older copy of the shot: the studio ledger shows it carried on "
                "after this package was made (the artist kept working, or it was reviewed from "
                "another copy). Open the latest package of the shot to record anything."
                if old_copy and v.assurance != "none" and files["ok"] else
                "The package or its history does not check out, so nothing can be recorded "
                "against it: " + "; ".join((files["problems"] + v.problems)[:3])),
        }

    # -- changes -------------------------------------------------------------------
    def _guard(self, pid: str) -> tuple[dict[str, Any], dict[str, Any]]:
        view = self.shot(pid)
        if not view["writable"]:
            raise ReviewError(view["why_not_writable"])
        return view, self.workflow(pid)

    def _save(self, pid: str, workflow: dict[str, Any]) -> None:
        self._write_json(os.path.join(self._dir(pid), "workflow.json"), workflow)

    def answer(self, pid: str, *, tag: str, note: str, who: Person, scope: str = "") -> dict[str, Any]:
        """Answer one item at the level it belongs to: the shot, its show, or the studio."""
        item = _item(tag)
        if not item:
            raise ReviewError(f"{tag} is not an item a person answers.")
        level = item.get("scope", "shot")
        if scope and scope != level:
            raise ReviewError(f"{tag} is answered per {level}, not per {scope}.")
        note = str(note or "").strip()
        if not note:
            raise ReviewError("Say what confirms it; a blank answer proves nothing.")
        with self.lock:
            view, workflow = self._guard(pid)
            if level in answers_mod.SCOPES:
                try:
                    saved = history_service.answers_store().add(
                        tag=tag, scope=level, project=view["shot"].get("project", ""), note=note,
                        actor=who.actor, keys=who.keys)
                except ValueError as exc:
                    raise ReviewError(str(exc)) from exc
                return {"recorded": "answer", "scope": level, "answer": saved}
            try:
                made = H.stamp(workflow, event="note", actor=who.actor, keys=who.keys,
                               ledger=_ledger(), note=note, data_payload={"evidence": tag})
            except ValueError as exc:
                raise ReviewError(str(exc)) from exc
            self._save(pid, made.workflow)
            return {"recorded": "history", "scope": "shot", "entry": history_service._brief(made.entry)}  # noqa: SLF001

    def decide(self, pid: str, *, decision: str, reason: str, who: Person,
               accept_open: bool = False) -> dict[str, Any]:
        """Clear the shot, or send it back, with a reason; signed into its history."""
        if decision not in DECISIONS:
            raise ReviewError("Choose to clear the shot or send it back.")
        reason = str(reason or "").strip()
        if not reason:
            raise ReviewError("Give a reason. It is sealed with the decision and is what the "
                              "artist and the record will show.")
        with self.lock:
            view, workflow = self._guard(pid)
            open_items = [i["tag"] or i["text"] for g in view["open_items"] for i in g["items"]
                          if i["state"] != "answered"]
            if decision == "clear" and open_items and not accept_open:
                raise ReviewError(f"{len(open_items)} item(s) are still open. Answer them, or "
                                  "confirm you are clearing the shot with them open; they are "
                                  "recorded with the decision.")
            review = {"decision": "cleared" if decision == "clear" else "sent back",
                      "by": who.actor.name, "at": _now(), "reason": reason,
                      "package": view["package"]["sha256"],
                      "verdict_at_sign_off": (view["decision"] or {}).get("verdict", ""),
                      "open_items": open_items, "reviewed_with": TOOL}
            ledger = _ledger()
            stage = view["shot"].get("stage", "sandbox")
            try:
                made = H.stamp(workflow, event="approved" if decision == "clear" else "rejected",
                               actor=who.actor, keys=who.keys, ledger=ledger, note=reason,
                               data_payload={"legal": review})
                entry = made.entry
                # Clearing moves a shot to "cleared" if it is not that far yet; a
                # delivered shot stays delivered with the clearance on top. Sent
                # back, it returns to production, where the artist picks it up.
                target = ("cleared" if decision == "clear" and stage in ("sandbox", "production")
                          else "production" if decision == "send-back" and stage != "production"
                          else "")
                if target:
                    made = H.stamp(made.workflow, event="promoted", actor=who.actor, keys=who.keys,
                                   ledger=ledger, note=DECISIONS[decision],
                                   data_payload={"stage": target})
            except ValueError as exc:
                raise ReviewError(str(exc)) from exc
            self._save(pid, made.workflow)
            posted = shotgrid.push_review(made.history, DECISIONS[decision], reason, who.actor.name)
            if posted:
                meta = self.meta(pid)
                meta.setdefault("shotgrid", []).append({"at": _now(), "decision": decision, **posted})
                self._write_json(os.path.join(self._dir(pid), "meta.json"), meta)
            return {"recorded": decision, "label": DECISIONS[decision],
                    "entry": history_service._brief(entry),  # noqa: SLF001
                    "stage": made.history["shot"].get("stage", ""), "shotgrid": posted}

    # -- overviews -------------------------------------------------------------------
    def shots(self) -> list[dict[str, Any]]:
        """One row per opened package, newest first."""
        rows = []
        for pid in self.package_ids():
            try:
                view = self.shot(pid)
            except (ReviewError, OSError, ValueError, KeyError):
                continue
            rows.append({"id": pid, "name": view["shot"].get("name", ""),
                         "project": view["shot"].get("project", ""),
                         "production": view["shot"]["production"],
                         "shot_id": view["shot"].get("id", ""), "root": view["shot"]["root"],
                         "stage": view["shot"].get("stage", ""),
                         "verdict": (view["decision"] or {}).get("verdict", ""),
                         "verdict_label": (view["decision"] or {}).get("verdict_label", ""),
                         "legal_state": view["legal_state"],
                         "legal_state_label": view["legal_state_label"],
                         "open_count": view["open_count"],
                         "assurance": view["integrity"]["assurance"],
                         "assurance_label": view["integrity"]["label"],
                         "package_ok": view["integrity"]["package_files"],
                         "opened_at": view["package"]["opened_at"],
                         "filename": view["package"]["filename"]})
        return sorted(rows, key=lambda r: r["opened_at"], reverse=True)

    def home(self) -> dict[str, Any]:
        rows = self.shots()
        shows: dict[str, dict[str, Any]] = {}
        for r in rows:
            s = shows.setdefault(r["production"], {"production": r["production"],
                                                   "name": r["project"], "packages": 0,
                                                   "awaiting": 0})
            s["packages"] += 1
            s["awaiting"] += r["legal_state"] == "awaiting"
        return {"packages": rows, "shows": sorted(shows.values(), key=lambda s: s["name"].lower()),
                "inbox": self.inbox(), "inbox_folder": bool(self.config.inbox)}

    def _answers(self, production: str) -> list[dict[str, Any]]:
        keys, rules = _studio_keys(), _rules()
        latest: dict[str, dict[str, Any]] = {}
        count: dict[str, int] = {}
        for a in history_service.answers_store().log():
            if a.get("production") != production:
                continue
            count[a.get("tag", "")] = count.get(a.get("tag", ""), 0) + 1
            latest[a.get("tag", "")] = a
        out = []
        for tag, a in latest.items():
            rid, _, idx = tag.partition(":")
            ev = rules.get(rid, {}).get("evidence", [])
            out.append({"tag": tag, "rule_title": rules.get(rid, {}).get("title", rid),
                        "text": ev[int(idx)]["text"] if idx.isdigit() and int(idx) < len(ev) else tag,
                        "note": a.get("note", ""), "by": (a.get("by") or {}).get("name", ""),
                        "role": (a.get("by") or {}).get("role", ""), "at": a.get("at", ""),
                        "signature": answers_mod.state(a, keys), "superseded": count[tag] - 1})
        return sorted(out, key=lambda a: (a["rule_title"], a["tag"]))

    def show(self, production: str) -> dict[str, Any]:
        """A show's shots, newest package of each line of copies, and the show's answers."""
        rows = [r for r in self.shots() if r["production"] == production]
        lines: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            lines.setdefault(r["root"] or r["shot_id"], []).append(r)
        shots = []
        for packages in lines.values():
            packages.sort(key=lambda r: r["opened_at"], reverse=True)
            shots.append({**packages[0], "earlier_packages": [
                {"id": p["id"], "opened_at": p["opened_at"], "legal_state_label": p["legal_state_label"]}
                for p in packages[1:]]})
        shots.sort(key=lambda r: r["name"].lower())
        name = next((r["project"] for r in rows if r["project"]), production)
        counts = {k: sum(1 for s in shots if s["legal_state"] == k) for k in STATES}
        return {"production": production, "name": name, "shots": shots, "counts": counts,
                "answers": self._answers(production) if production else [],
                "studio_answers": self._answers(answers_mod.STUDIO)}
