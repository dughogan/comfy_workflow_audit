"""Answers given once for a whole show, or for the whole studio.

Most of what the rules ask a person to attest is not about one shot: a
client's written approval of the AI tools for the project, a union notice, the
company's AI policy. Asking for those on every shot invites ticking boxes, so
they are answered once, at the level they belong to, and every shot inherits
them:

* **show** answers belong to one production, matched by the shot's project;
* **studio** answers belong to every production.

Each answer says who gave it, when, and what supports it, and is signed with
the same key that signs history entries. The store is append-only: a new
answer for the same item supersedes the old one, which stays in the log. A
shot's own attestation (a note in its history) always wins over an inherited
one, and a delivered package seals a copy of every inherited answer it relied
on, so the package stands on its own.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import re
from typing import Any

from . import history as H

SCOPES = ("show", "studio")
STUDIO = "*studio*"


def production_key(project: str) -> str:
    """'Pilot: Ep 101 ' -> 'pilot-ep-101'; one show, however it is typed."""
    return re.sub(r"[^a-z0-9]+", "-", str(project or "").strip().lower()).strip("-")


def sign(answer: dict[str, Any], keys: H.Keys) -> dict[str, Any]:
    out = {k: v for k, v in answer.items() if k not in ("hash", "sig", "key_id")}
    if keys.sign_key:
        out["key_id"] = keys.key_id
    out["hash"] = H.sha256(H.canonical({k: v for k, v in out.items() if k != "hash"}))
    if keys.sign_key:
        out["sig"] = H._sign(out["hash"], keys)
    return out


def state(answer: dict[str, Any], keys: H.Keys) -> str:
    """``signed``, ``unsigned`` or ``altered`` (changed since it was written, or a bad signature)."""
    body = {k: v for k, v in answer.items() if k not in ("hash", "sig")}
    if H.sha256(H.canonical(body)) != answer.get("hash"):
        return "altered"
    if "sig" not in answer:
        return "unsigned"
    key = keys.key_for(str(answer.get("key_id", "")))
    if not key:
        return "unsigned"
    good = hmac.new(key, answer["hash"].encode("ascii"), hashlib.sha256).hexdigest()
    return "signed" if hmac.compare_digest(good, str(answer["sig"])) else "altered"


class Store:
    """A JSON file of answers: ``{"log": [answer, ...]}``, appended to, never rewritten in place."""

    def __init__(self, path: str) -> None:
        self.path = path

    def log(self) -> list[dict[str, Any]]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return list(json.load(fh).get("log") or [])
        except (OSError, ValueError):
            return []

    def add(self, *, tag: str, scope: str, project: str, note: str, actor: H.Actor,
            keys: H.Keys) -> dict[str, Any]:
        if scope not in SCOPES:
            raise ValueError(f"an answer is for a show or the studio, not {scope!r}")
        if not note.strip():
            raise ValueError("Say what confirms it; a blank answer proves nothing.")
        if scope == "show" and not production_key(project):
            raise ValueError("Give the shot its project or show in Shot details first; "
                             "a show answer is shared by every shot of that show.")
        answer = sign({
            "tag": tag, "scope": scope,
            "production": STUDIO if scope == "studio" else production_key(project),
            "production_name": "" if scope == "studio" else str(project).strip(),
            "note": note.strip(),
            "by": {"name": actor.name, "role": actor.role, "id": actor.id},
            "at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        }, keys)
        log = self.log() + [answer]
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"log": log}, fh, indent=1)
        os.replace(tmp, self.path)
        return answer

    def for_shot(self, project: str, keys: H.Keys) -> dict[str, dict[str, Any]]:
        """The answers a shot of ``project`` inherits, latest per item; altered ones are dropped."""
        want = {STUDIO, production_key(project)} - {""}
        out: dict[str, dict[str, Any]] = {}
        for answer in self.log():
            if answer.get("production") not in want:
                continue
            st = state(answer, keys)
            if st == "altered":
                continue
            # A show's own answer beats a studio-wide one for the same item.
            prev = out.get(answer["tag"])
            if prev and prev["production"] != STUDIO and answer["production"] == STUDIO:
                continue
            out[answer["tag"]] = {**answer, "state": st}
        return out
