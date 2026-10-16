"""Autodesk Flow Production Tracking (ShotGrid): look shots up, post deliveries back.

A small client for the REST API (developers.shotgridsoftware.com/rest-api,
checked 2026-10-16), standard library only. It does three things:

* **look up** a shot by id or code, so the ID typed into Shot details is
  confirmed to exist and the shot's code and project come from ShotGrid;
* **post a delivery** as a Note on that shot: the verdict, who signed off,
  and the history's head hash, with the delivery package attached;
* optionally **write the head hash** into a text field on the shot, so
  ShotGrid itself holds a witness of the record.

Configured from the server's environment only:

* ``COMFYAUDIT_SHOTGRID_URL`` - the site, e.g. ``https://studio.shotgrid.autodesk.com``
* ``COMFYAUDIT_SHOTGRID_SCRIPT`` and ``COMFYAUDIT_SHOTGRID_KEY`` (or
  ``..._KEY_FILE``) - a script user's name and key
* ``COMFYAUDIT_SHOTGRID_API`` - path prefix, default ``/api/v1.1``
* ``COMFYAUDIT_SHOTGRID_HASH_FIELD`` - optional custom text field on Shot
  (an admin creates it; e.g. ``sg_comfyaudit_head``)

Built and tested against a local stand-in of the documented endpoints; it has
not been run against a live site.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ShotGridError(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("COMFYAUDIT_SHOTGRID_URL") and os.environ.get("COMFYAUDIT_SHOTGRID_SCRIPT")
                and _secret())


def _secret() -> str:
    key = os.environ.get("COMFYAUDIT_SHOTGRID_KEY", "")
    path = os.environ.get("COMFYAUDIT_SHOTGRID_KEY_FILE", "")
    if not key and path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
    return key


class Client:
    def __init__(self, site: str = "", script: str = "", key: str = "", api: str = "") -> None:
        self.site = (site or os.environ.get("COMFYAUDIT_SHOTGRID_URL", "")).rstrip("/")
        self.script = script or os.environ.get("COMFYAUDIT_SHOTGRID_SCRIPT", "")
        self.key = key or _secret()
        self.api = "/" + (api or os.environ.get("COMFYAUDIT_SHOTGRID_API", "/api/v1.1")).strip("/")
        self.token = ""
        if not (self.site and self.script and self.key):
            raise ShotGridError("ShotGrid is not configured on this server")

    # -- transport ---------------------------------------------------------
    def _auth(self) -> None:
        body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                       "client_id": self.script,
                                       "client_secret": self.key}).encode()
        req = urllib.request.Request(f"{self.site}{self.api}/auth/access_token", data=body,
                                     method="POST", headers={
                                         "Content-Type": "application/x-www-form-urlencoded",
                                         "Accept": "application/json"})
        data = self._send(req)
        self.token = f"{data.get('token_type', 'Bearer')} {data['access_token']}"

    def _send(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                errors = json.loads(exc.read() or b"{}").get("errors") or []
                detail = "; ".join(f"{e.get('title', '')} {e.get('detail', '')}".strip() for e in errors)
            except ValueError:
                pass
            raise ShotGridError(f"ShotGrid answered {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise ShotGridError(f"Could not reach ShotGrid: {exc.reason}") from exc

    def call(self, method: str, path: str, body: Any = None, params: dict[str, str] | None = None,
             content_type: str = "application/json", retry: bool = True) -> dict[str, Any]:
        if not self.token:
            self._auth()
        url = f"{self.site}{self.api}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self.token, "Accept": "application/json",
            **({"Content-Type": content_type} if data is not None else {})})
        try:
            return self._send(req)
        except ShotGridError as exc:
            if retry and " 401" in str(exc):
                self.token = ""      # expired: sign in again, once
                return self.call(method, path, body, params, content_type, retry=False)
            raise

    # -- what ComfyAudit needs ---------------------------------------------
    def find_shot(self, value: str) -> dict[str, Any] | None:
        """A shot by numeric id or by code; None if there is no such shot."""
        fields = "code,project,sg_status_list,description"
        value = str(value).strip()
        if value.isdigit():
            try:
                rec = self.call("GET", f"entity/shots/{value}", params={"fields": fields}).get("data")
            except ShotGridError as exc:
                if " 404" in str(exc):
                    return None
                raise
        else:
            rows = self.call("GET", "entity/shots", params={f"filter[code]": value,
                                                            "fields": fields}).get("data") or []
            rec = rows[0] if rows else None
        if not rec:
            return None
        attrs = rec.get("attributes") or {}
        project = ((rec.get("relationships") or {}).get("project") or {}).get("data") or {}
        return {"id": rec.get("id"), "code": attrs.get("code", ""),
                "status": attrs.get("sg_status_list", ""),
                "project": {"id": project.get("id"), "name": project.get("name", "")},
                "url": f"{self.site}/detail/Shot/{rec.get('id')}"}

    def post_delivery(self, shot: dict[str, Any], subject: str, content: str,
                      attachment: tuple[str, bytes] | None = None,
                      head: str = "") -> dict[str, Any]:
        """A Note on the shot, the package attached, and the head hash written back."""
        body = {"subject": subject, "content": content,
                "note_links": [{"type": "Shot", "id": shot["id"]}]}
        if shot.get("project", {}).get("id"):
            body["project"] = {"type": "Project", "id": shot["project"]["id"]}
        note = self.call("POST", "entity/notes", body).get("data") or {}
        out = {"note_id": note.get("id"), "attached": False, "hash_field": ""}
        if attachment and note.get("id"):
            name, data = attachment
            info = self.call("GET", f"entity/notes/{note['id']}/_upload", params={"filename": name})
            upload = (info.get("links") or {}).get("upload", "")
            complete = (info.get("links") or {}).get("complete_upload", "")
            upload_info = dict(info.get("data") or {})
            if upload.startswith("/"):
                upload = self.site + upload
            req = urllib.request.Request(upload, data=data, method="PUT", headers={
                "Content-Type": "application/zip", "Content-Size": str(len(data)),
                # The signed URL is someone else's host unless it is the site's own.
                **({"Authorization": self.token} if upload.startswith(self.site) else {})})
            sent = self._send(req)
            if upload_info.get("storage_service") == "sg" and isinstance(sent.get("data"), dict):
                upload_info.update(sent["data"])
            path = complete[len(self.api):] if complete.startswith(self.api) else complete
            self.call("POST", path, {"upload_info": upload_info,
                                     "upload_data": {"display_name": name}})
            out["attached"] = True
        field = os.environ.get("COMFYAUDIT_SHOTGRID_HASH_FIELD", "")
        if field and head:
            self.call("PUT", f"entity/shots/{shot['id']}", {field: head})
            out["hash_field"] = field
        return out


def shot_ref(history: dict[str, Any]) -> str:
    """The ShotGrid id or code the shot was given in Shot details ("Shot#4411" -> "4411")."""
    value = str(((history.get("shot") or {}).get("external_ids") or {}).get("shotgrid", "")).strip()
    return value.split("#", 1)[1].strip() if value.lower().startswith("shot#") else value


def lookup(body: dict[str, Any]) -> dict[str, Any]:
    """Route: confirm a shot exists before it is written into the history."""
    if not configured():
        return {"configured": False}
    try:
        shot = Client().find_shot(str(body.get("value", "")))
    except ShotGridError as exc:
        return {"configured": True, "error": str(exc)}
    return {"configured": True, "found": bool(shot), "shot": shot}


def push_delivery(history: dict[str, Any], clearance: dict[str, Any], who: str,
                  package: tuple[str, bytes] | None) -> dict[str, Any]:
    """After delivery: a Note on the shot. Never fails the delivery, which is already sealed."""
    ref = shot_ref(history)
    if not configured() or not ref:
        return {}
    entries = history.get("entries") or []
    head = entries[-1].get("hash", "") if entries else ""
    try:
        client = Client()
        shot = client.find_shot(ref)
        if not shot:
            return {"error": f"ShotGrid has no shot {ref}; nothing was posted."}
        lines = [f"Verdict: {clearance.get('verdict_label') or clearance.get('verdict', '')}",
                 clearance.get("headline", ""),
                 f"Signed off by: {who}"]
        if clearance.get("exception"):
            lines.append(f"Delivered by exception: {clearance['exception']}")
        lines += [f"History head: {head}",
                  f"Checked with ComfyAudit {clearance.get('audited_with', {}).get('comfyaudit', '')}"]
        posted = client.post_delivery(shot, f"ComfyAudit delivery: {shot['code'] or ref}",
                                      "\n".join(x for x in lines if x), attachment=package, head=head)
        return {"shot": shot, **posted}
    except ShotGridError as exc:
        return {"error": str(exc)}
