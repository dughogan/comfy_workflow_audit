"""ShotGrid, against a local stand-in of the documented REST endpoints.

Not a live site: this proves the client speaks the API as documented
(developers.shotgridsoftware.com/rest-api, checked 2026-10-16), nothing more.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from comfyaudit.core import history as H
from comfyaudit.server import delivery, shotgrid
from comfyaudit.server import settings as settings_mod

import os

ANA = H.Actor(name="Ana Ruiz", role="compositor")


def load(name="beauty-pass.json"):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


class FakeSite:
    def __init__(self):
        self.shots = {4411: {"code": "SPI_0420", "sg_status_list": "ip", "project": 88}}
        self.notes, self.uploads, self.completed, self.updates = {}, [], [], []
        self.tokens = {"tok-1"}
        self.expire_next = False
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body=None):
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def authed(self):
                tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                if site.expire_next:
                    site.expire_next = False
                    site.tokens.clear()
                if tok not in site.tokens:
                    self.reply(401, {"errors": [{"status": 401, "title": "Unauthorized"}]})
                    return False
                return True

            def shot(self, sid):
                s = site.shots[sid]
                return {"id": sid, "type": "Shot",
                        "attributes": {"code": s["code"], "sg_status_list": s["sg_status_list"]},
                        "relationships": {"project": {"data": {"type": "Project", "id": s["project"],
                                                               "name": "Pilot"}}}}

            def do_POST(self):
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/api/v1.1/auth/access_token":
                    form = urllib.parse.parse_qs(self.body().decode())
                    if form.get("client_secret") != ["s3cret"] or form.get("grant_type") != ["client_credentials"]:
                        return self.reply(400, {"errors": [{"title": "Bad credentials"}]})
                    tok = f"tok-{len(site.tokens) + 2}"
                    site.tokens.add(tok)
                    return self.reply(200, {"access_token": tok, "token_type": "Bearer", "expires_in": 600})
                if not self.authed():
                    return
                data = json.loads(self.body() or b"{}")
                if url.path == "/api/v1.1/entity/notes":
                    nid = 900 + len(site.notes)
                    site.notes[nid] = data
                    return self.reply(201, {"data": {"id": nid, "type": "Note"}})
                if url.path.endswith("/_upload"):
                    site.completed.append(data)
                    return self.reply(204)
                self.reply(404, {"errors": [{"title": "Not found"}]})

            def do_GET(self):
                if not self.authed():
                    return
                url = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(url.query)
                parts = url.path.split("/")
                if url.path == "/api/v1.1/entity/shots":
                    code = q.get("filter[code]", [""])[0]
                    rows = [self.shot(i) for i, s in site.shots.items() if s["code"] == code]
                    return self.reply(200, {"data": rows})
                if url.path.startswith("/api/v1.1/entity/shots/"):
                    sid = int(parts[-1])
                    if sid not in site.shots:
                        return self.reply(404, {"errors": [{"title": "Record not found"}]})
                    return self.reply(200, {"data": self.shot(sid)})
                if url.path.endswith("/_upload"):
                    nid = parts[-2]
                    return self.reply(200, {
                        "data": {"timestamp": "t", "upload_type": "Attachment", "upload_id": None,
                                 "storage_service": "sg", "original_filename": q["filename"][0]},
                        "links": {"upload": f"/api/v1.1/entity/notes/{nid}/_upload/bytes",
                                  "complete_upload": f"/api/v1.1/entity/notes/{nid}/_upload"}})
                self.reply(404, {"errors": [{"title": "Not found"}]})

            def do_PUT(self):
                if not self.authed():
                    return
                url = urllib.parse.urlsplit(self.path)
                raw = self.body()
                if url.path.endswith("/_upload/bytes"):
                    site.uploads.append(raw)
                    return self.reply(200, {"data": {"upload_id": "u-1"}})
                site.updates.append((url.path, json.loads(raw)))
                self.reply(200, {"data": {}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def site(monkeypatch):
    fake = FakeSite()
    monkeypatch.setenv("COMFYAUDIT_SHOTGRID_URL", fake.url)
    monkeypatch.setenv("COMFYAUDIT_SHOTGRID_SCRIPT", "comfyaudit")
    monkeypatch.setenv("COMFYAUDIT_SHOTGRID_KEY", "s3cret")
    monkeypatch.setenv("COMFYAUDIT_SHOTGRID_HASH_FIELD", "sg_comfyaudit_head")
    yield fake
    fake.server.shutdown()


def test_a_shot_is_found_by_id_or_code_and_a_missing_one_is_none(site):
    c = shotgrid.Client()
    by_id = c.find_shot("4411")
    assert by_id["code"] == "SPI_0420" and by_id["project"]["id"] == 88
    assert c.find_shot("SPI_0420")["id"] == 4411
    assert c.find_shot("9999") is None and c.find_shot("NOPE") is None
    assert shotgrid.lookup({"value": "4411"})["found"]


def test_an_expired_token_signs_in_again_once(site):
    c = shotgrid.Client()
    c.find_shot("4411")
    site.expire_next = True
    assert c.find_shot("4411")["id"] == 4411


def test_wrong_credentials_are_reported_not_raised_from_the_route(site, monkeypatch):
    monkeypatch.setenv("COMFYAUDIT_SHOTGRID_KEY", "wrong")
    out = shotgrid.lookup({"value": "4411"})
    assert "Bad credentials" in out["error"]


def test_nothing_happens_when_shotgrid_is_not_configured(monkeypatch):
    monkeypatch.delenv("COMFYAUDIT_SHOTGRID_URL", raising=False)
    assert shotgrid.lookup({"value": "1"}) == {"configured": False}
    assert shotgrid.push_delivery({"shot": {"external_ids": {"shotgrid": "1"}}}, {}, "x", None) == {}


def test_shot_ref_accepts_the_shot_hash_form():
    assert shotgrid.shot_ref({"shot": {"external_ids": {"shotgrid": "Shot#4411"}}}) == "4411"
    assert shotgrid.shot_ref({"shot": {"external_ids": {"shotgrid": "SPI_0420"}}}) == "SPI_0420"
    assert shotgrid.shot_ref({"shot": {}}) == ""


def test_delivery_posts_a_note_attaches_the_package_and_writes_the_head(site, monkeypatch):
    cfg = {"user": "Priya Rao", "role": "VFX supervisor", "auto": True, "ledger": "",
           "key_file": "", "confidentiality": "none", "distribute_in": [], "territory": "",
           "likeness": False, "clients": []}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys.from_environment(),
                 name="SH0120", external_ids={"shotgrid": "Shot#4411"}).workflow
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "exception": "Test delivery."})
    assert "error" not in out, out
    sg = out["shotgrid"]
    assert sg["note_id"] and sg["attached"] and sg["hash_field"] == "sg_comfyaudit_head"
    note = site.notes[sg["note_id"]]
    head = out["history"]["entries"][-1]["hash"]
    assert note["note_links"] == [{"type": "Shot", "id": 4411}]
    assert note["project"] == {"type": "Project", "id": 88}
    assert head in note["content"] and "Priya Rao" in note["content"]
    assert "Test delivery." in note["content"]
    assert site.uploads and site.uploads[0][:2] == b"PK"
    assert site.completed[0]["upload_info"]["upload_id"] == "u-1"
    assert site.completed[0]["upload_data"]["display_name"] == "SH0120.delivery.zip"
    assert site.updates == [("/api/v1.1/entity/shots/4411", {"sg_comfyaudit_head": head})]


def test_a_shotgrid_failure_does_not_undo_the_delivery(site, monkeypatch):
    cfg = {"user": "Priya Rao", "role": "", "auto": True, "ledger": "", "key_file": "",
           "confidentiality": "none", "distribute_in": [], "territory": "", "likeness": False,
           "clients": []}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys(),
                 name="SH0121", external_ids={"shotgrid": "7777"}).workflow
    out = delivery.deliver({"workflow": wf, "name": "SH0121", "exception": "Test."})
    assert out["history"]["shot"]["stage"] == "delivered"
    assert "no shot 7777" in out["shotgrid"]["error"]
