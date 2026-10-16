"""ComfyAudit Review: legal opens a delivery package, answers, and clears or sends back."""

from __future__ import annotations

import base64
import http.client
import io
import json
import os
import re
import threading
import zipfile

import pytest

from comfyaudit.core import answers as A
from comfyaudit.core import history as H
from comfyaudit.core.report import machine
from comfyaudit.review import app as R
from comfyaudit.review import server as S
from comfyaudit.server import delivery, shotgrid
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
TPN = "mpa-tpn-content-security-ai-controls"
SHOW_TAG, SHOT_TAG, STUDIO_TAG = f"{TPN}:0", f"{TPN}:1", f"{TPN}:3"


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def studio(monkeypatch, tmp_path):
    cfg = {"user": "Priya Rao", "role": "VFX supervisor", "auto": True,
           "ledger": str(tmp_path / "ledger.jsonl"),
           "key_file": "", "confidentiality": "none", "distribute_in": [], "territory": "",
           "likeness": False, "clients": [], "tpn_vendor": True}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    monkeypatch.setenv("COMFYAUDIT_ANSWERS_FILE", str(tmp_path / "answers.json"))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_LEDGER", str(tmp_path / "ledger.jsonl"))
    for var in ("COMFYAUDIT_TRUSTED_USER_HEADER", "COMFYAUDIT_HISTORY_KEYRING", "COMFYAUDIT_SHOTGRID_URL"):
        monkeypatch.delenv(var, raising=False)
    return R.Review(R.Config(root=str(tmp_path / "review"), inbox=str(tmp_path / "inbox")))


def package(name="SH0120", project="Pilot") -> bytes:
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys.from_environment(),
                 name=name, project=project, ledger=os.environ["COMFYAUDIT_HISTORY_LEDGER"]).workflow
    out = delivery.deliver({"workflow": wf, "name": name, "exception": "Test delivery."})
    assert "error" not in out, out
    return base64.b64decode(out["zip_b64"])


def items(view):
    return {i["tag"]: i for g in view["open_items"] for i in g["items"]}


def sam():
    return R.person(declared="Sam Okafor")


def test_a_package_opens_checks_out_and_reads_the_decision_from_the_signed_chain(studio):
    pid = studio.add(package(), "SH0120.delivery.zip", "Sam Okafor")
    view = studio.shot(pid)
    assert view["integrity"]["package_files"] and view["writable"]
    assert view["integrity"]["assurance"] in ("signed", "witnessed")
    assert view["decision"]["signed_off_by"] == "Priya Rao"
    assert view["decision"]["exception"] == "Test delivery."
    assert view["legal_state"] == "awaiting" and view["shot"]["stage"] == "delivered"
    # The sign-off seals each open item with its tag, so legal can answer it here.
    got = items(view)
    assert {SHOW_TAG, SHOT_TAG, STUDIO_TAG} <= set(got)
    assert got[SHOW_TAG]["scope"] == "show" and got[SHOT_TAG]["scope"] == "shot"
    assert got[STUDIO_TAG]["scope"] == "studio" and got[SHOW_TAG]["state"] == "open"
    roles = {d["role"] for d in view["documents"]}
    assert roles == {"legal-report", "licence-audit", "life-of-the-shot"}
    # Opening the same package again finds the same review.
    assert studio.add(open(os.path.join(studio.config.root, "packages", pid, "package.zip"), "rb").read()) == pid


def test_an_altered_package_is_flagged_and_nothing_is_recorded_against_it(studio):
    src = zipfile.ZipFile(io.BytesIO(package()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename.endswith(".licence-audit.html"):
                data = data.replace(b"</body>", b"<p>All clear.</p></body>")
            out.writestr(info.filename, data)
        out.writestr("extra.txt", "slipped in")
    pid = studio.add(buf.getvalue(), "tampered.zip")
    view = studio.shot(pid)
    assert not view["integrity"]["package_files"] and not view["writable"]
    assert any("licence-audit.html has changed" in p for p in view["integrity"]["package_problems"])
    assert any("extra.txt is in the package but not in its index" in p
               for p in view["integrity"]["package_problems"])
    with pytest.raises(R.ReviewError, match="does not check out"):
        studio.decide(pid, decision="clear", reason="Fine", who=sam(), accept_open=True)


def test_a_show_answer_reaches_this_shot_and_every_later_package_of_the_show(studio):
    first = studio.add(package("SH0120", "Pilot"))
    before_answer = studio.add(package("SH0130", "pilot"))
    out = studio.answer(first, tag=SHOW_TAG, note="Client approval email, 14 Oct, filed in Legal/Pilot",
                        who=sam())
    assert out["recorded"] == "answer" and out["answer"]["production"] == "pilot"
    assert out["answer"]["by"] == {"name": "Sam Okafor", "role": "legal", "id": ""}
    assert A.state(out["answer"], H.Keys.from_environment()) == "signed"
    assert items(studio.shot(first))[SHOW_TAG]["state"] == "answered"
    # A package made before the answer inherits it here, live.
    other = studio.shot(before_answer)
    assert items(other)[SHOW_TAG]["state"] == "answered"
    assert [a["tag"] for a in other["inherited"]] == [SHOW_TAG]
    assert other["inherited"][0]["when"] == "answered since the sign-off"
    assert other["inherited"][0]["signature"] == "signed"
    # One made after it has it sealed in its sign-off, and no longer lists it for legal.
    later = studio.shot(studio.add(package("SH0140", "Pilot")))
    assert SHOW_TAG not in items(later)
    sealed = [a for a in later["inherited"] if a["tag"] == SHOW_TAG]
    assert sealed and sealed[0]["when"] == "sealed at sign-off" and sealed[0]["signature"] == "signed"
    # Another show does not.
    assert items(studio.shot(studio.add(package("SH0010", "Other Show"))))[SHOW_TAG]["state"] == "open"
    dash = studio.show("pilot")
    assert {s["name"] for s in dash["shots"]} == {"SH0120", "SH0130", "SH0140"}
    assert [a["tag"] for a in dash["answers"]] == [SHOW_TAG] and dash["answers"][0]["signature"] == "signed"


def test_items_are_answered_only_at_their_own_level(studio):
    pid = studio.add(package())
    with pytest.raises(R.ReviewError, match="answered per show"):
        studio.answer(pid, tag=SHOW_TAG, scope="studio", note="x", who=sam())
    with pytest.raises(R.ReviewError, match="not an item a person answers"):
        studio.answer(pid, tag=f"{TPN}:2", note="x", who=sam())
    with pytest.raises(R.ReviewError, match="blank"):
        studio.answer(pid, tag=SHOT_TAG, note="  ", who=sam())


def test_a_shot_answer_is_a_signed_note_in_the_shots_own_history(studio):
    pid = studio.add(package())
    out = studio.answer(pid, tag=SHOT_TAG, note="Inventory entry INV-77 covers every pack", who=sam())
    assert out["recorded"] == "history"
    hist = H.read(studio.workflow(pid))
    last = hist["entries"][-1]
    assert last["event"] == "note" and last["data"] == {"evidence": SHOT_TAG}
    assert last["actor"]["name"] == "Sam Okafor" and last["actor"]["role"] == "legal"
    assert last["actor"]["declared_by"] == "declared in ComfyAudit Review"
    v = H.verify(studio.workflow(pid), keys=H.Keys.from_environment(),
                 ledger=os.environ["COMFYAUDIT_HISTORY_LEDGER"])
    assert v.assurance == "witnessed", v.problems
    view = studio.shot(pid)
    assert items(view)[SHOT_TAG]["state"] == "answered"
    assert [r["event"] for r in view["since_packaging"]] == ["note"]


def test_clearing_needs_a_reason_and_says_so_when_items_are_still_open(studio, monkeypatch):
    posted = []
    monkeypatch.setattr(shotgrid, "push_review", lambda h, label, reason, who: posted.append(
        (label, reason, who)) or {"note_id": 9})
    pid = studio.add(package())
    with pytest.raises(R.ReviewError, match="reason"):
        studio.decide(pid, decision="clear", reason=" ", who=sam())
    with pytest.raises(R.ReviewError, match="still open"):
        studio.decide(pid, decision="clear", reason="Cleared for Reel 3", who=sam())
    out = studio.decide(pid, decision="clear", reason="Cleared for Reel 3", who=sam(), accept_open=True)
    assert out["recorded"] == "clear" and out["shotgrid"] == {"note_id": 9}
    assert posted == [("Cleared by legal", "Cleared for Reel 3", "Sam Okafor")]
    hist = H.read(studio.workflow(pid))
    e = hist["entries"][-1]
    assert e["event"] == "approved" and e["actor"]["role"] == "legal" and e["note"] == "Cleared for Reel 3"
    review = e["data"]["legal_review"]
    assert review["decision"] == "clear" and SHOW_TAG in review["open_items"]
    assert review["package"] == studio.meta(pid)["sha256"]
    # Already delivered: the clearance goes on top, the stage does not go backwards.
    assert hist["shot"]["stage"] == "delivered"
    assert H.lineage(hist)["approved"]["by"] == "Sam Okafor"
    view = studio.shot(pid)
    assert view["legal_state"] == "cleared" and view["shotgrid"]["posts"][0]["note_id"] == 9
    assert H.verify(studio.workflow(pid), keys=H.Keys.from_environment()).assurance == "signed"


def test_a_shot_still_in_production_is_promoted_to_cleared(studio):
    pid = studio.add(package())
    wf = studio.workflow(pid)
    wf = H.stamp(wf, event="promoted", actor=ANA, keys=H.Keys.from_environment(),
                 ledger=os.environ["COMFYAUDIT_HISTORY_LEDGER"], data_payload={"stage": "production"}).workflow
    studio._save(pid, wf)  # noqa: SLF001
    studio.decide(pid, decision="clear", reason="OK", who=sam(), accept_open=True)
    hist = H.read(studio.workflow(pid))
    assert [e["event"] for e in hist["entries"][-2:]] == ["approved", "promoted"]
    assert hist["shot"]["stage"] == "cleared"


def test_sending_back_records_a_rejection_and_returns_the_shot_to_production(studio):
    pid = studio.add(package())
    out = studio.decide(pid, decision="send-back", reason="Need the client email for Pilot first", who=sam())
    assert out["stage"] == "production"
    hist = H.read(studio.workflow(pid))
    assert [e["event"] for e in hist["entries"][-2:]] == ["rejected", "promoted"]
    assert H.lineage(hist)["approved"] is None
    view = studio.shot(pid)
    assert view["legal_state"] == "sent-back" and view["last_review"]["reason"].startswith("Need the client")


def test_without_a_signing_key_the_app_reads_but_records_nothing(studio, monkeypatch):
    monkeypatch.delenv("COMFYAUDIT_HISTORY_KEY")
    with pytest.raises(R.ReviewError, match="no signing key"):
        R.person(declared="Sam Okafor")
    assert R.identity(declared="Sam Okafor")["can_sign"] is False
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    with pytest.raises(R.ReviewError, match="name"):
        R.person(declared="  ")


def test_an_older_package_without_tags_is_matched_to_its_items_by_text(studio):
    pid = studio.add(package())
    wf = studio.workflow(pid)
    hist = H.read(wf)
    approved = next(e for e in hist["entries"] if e["event"] == "approved")
    for g in approved["data"]["clearance"]["for_legal"]:
        g.pop("tags")
    sealed = R.Review._open_items(studio, hist, H.delivery_clearance(hist), {})  # noqa: SLF001
    tags = {i["tag"] for g in sealed for i in g["items"]}
    assert {SHOW_TAG, SHOT_TAG, STUDIO_TAG} <= tags


def test_the_inbox_opens_only_its_own_files(studio):
    os.makedirs(studio.config.inbox)
    with open(os.path.join(studio.config.inbox, "SH0120.delivery.zip"), "wb") as fh:
        fh.write(package())
    assert [r["name"] for r in studio.inbox()] == ["SH0120.delivery.zip"]
    assert studio.inbox()[0]["opened"] == ""
    pid = studio.add_from_inbox("SH0120.delivery.zip")
    assert studio.inbox()[0]["opened"] == pid
    for bad in ("../answers.json", "..\\answers.json", "C:\\Windows\\win.ini"):
        with pytest.raises(R.ReviewError, match="not in the inbox"):
            studio.add_from_inbox(bad)
    with pytest.raises(R.ReviewError, match="not a zip"):
        studio.add(b"hello")


# -- over HTTP ----------------------------------------------------------------------

@pytest.fixture
def web(studio):
    httpd = S.make_server(studio, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data, resp.headers


def multipart(fields, files):
    boundary = "----comfyauditreview"
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (name, data) in files.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                  "Content-Type: application/zip\r\n\r\n".encode() + data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def test_the_web_app_end_to_end_with_forms(web, studio):
    status, h, page, _ = call(web, "GET", "/")
    assert status == 200 and "Shots for legal review" in page.decode()
    # Comfy branded, with nothing loaded from outside the studio network.
    assert "aria-label='Comfy'" in page.decode() and "#F2FF59" in page.decode()
    assert "https://" not in page.decode().split("<footer>")[0].split("</style>")[1]
    assert "script-src" not in h["content-security-policy"]  # the app's own pages run no script
    token = re.search(r"ca_csrf=([^;]+)", h["set-cookie"]).group(1)
    jar = f"ca_csrf={token}; ca_name=Sam%20Okafor"
    body, ctype = multipart({"csrf": token}, {"package": ("SH0120.delivery.zip", package())})
    # Without the form's token, nothing is opened.
    status, _, _, _ = call(web, "POST", "/open", body, {"Content-Type": ctype, "Cookie": f"ca_csrf={token}x"})
    assert status == 403
    status, h, _, _ = call(web, "POST", "/open", body, {"Content-Type": ctype, "Cookie": jar})
    assert status == 303 and h["location"].startswith("/p/")
    pid = h["location"].split("/")[2].split("?")[0]
    status, _, page, _ = call(web, "GET", f"/p/{pid}?done=opened", headers={"Cookie": jar})
    html_text = page.decode()
    assert status == 200 and "Package opened and checked." in html_text and "Clear the shot" in html_text
    data = machine.read(html_text)
    assert data["schema"] == "comfyaudit/review-shot/1" and data["package"]["id"] == pid
    status, _, raw, _ = call(web, "GET", f"/p/{pid}?format=json")
    assert json.loads(raw)["open_count"] == data["open_count"]
    # Answer the show item with the form, then clear the shot.
    form = f"csrf={token}&tag={SHOW_TAG}&scope=show&note=Client+email+14+Oct"
    status, h, _, _ = call(web, "POST", f"/p/{pid}/answer", form,
                           {"Content-Type": "application/x-www-form-urlencoded", "Cookie": jar})
    assert status == 303 and "answered-show" in h["location"]
    form = f"csrf={token}&decision=clear&reason=Cleared+for+Reel+3&accept_open=1"
    status, h, _, _ = call(web, "POST", f"/p/{pid}/decide", form,
                           {"Content-Type": "application/x-www-form-urlencoded", "Cookie": jar})
    assert status == 303 and h["location"].endswith("done=clear")
    status, h, raw, _ = call(web, "GET", f"/p/{pid}/workflow.json")
    assert status == 200 and "attachment" in h["content-disposition"]
    assert H.read(json.loads(raw))["entries"][-1]["data"]["legal_review"]["decision"] == "clear"
    # Package documents open sandboxed, away from the app.
    legal = next(d["path"] for d in data["documents"] if d["role"] == "legal-report")
    status, h, raw, _ = call(web, "GET", f"/p/{pid}/file/{legal}")
    assert status == 200 and h["content-security-policy"].startswith("sandbox")
    assert call(web, "GET", f"/p/{pid}/file/..%2Fmeta.json")[0] == 404
    status, _, page, _ = call(web, "GET", "/show/pilot")
    assert "Client email 14 Oct" in page.decode() and "SH0120" in page.decode()
    # An error is shown on the page, not as a crash.
    status, _, page, _ = call(web, "POST", f"/p/{pid}/decide", f"csrf={token}&decision=clear&reason=",
                              {"Content-Type": "application/x-www-form-urlencoded", "Cookie": jar})
    assert status == 400 and "Give a reason" in page.decode()


def test_another_site_cannot_post_and_json_clients_need_no_form_token(web, studio):
    pid = studio.add(package())
    status, _, _, _ = call(web, "POST", f"/p/{pid}/decide", "decision=clear&reason=x",
                           {"Content-Type": "application/x-www-form-urlencoded",
                            "Origin": "http://evil.example", "Cookie": "ca_name=Sam"})
    assert status == 403
    status, _, raw, _ = call(web, "POST", f"/p/{pid}/answer",
                             json.dumps({"tag": SHOT_TAG, "note": "INV-77"}),
                             {"Content-Type": "application/json", "Cookie": "ca_name=Agent%20Smith"})
    assert status == 200 and json.loads(raw)["recorded"] == "history"
    status, _, raw, _ = call(web, "POST", "/open", package(), {"Content-Type": "application/zip",
                                                              "Accept": "application/json"})
    assert status == 200 and json.loads(raw)["url"].startswith("/p/")


def test_per_person_signing_from_the_sign_in_header(web, studio, monkeypatch, tmp_path):
    secret = tmp_path / "sam.key"
    secret.write_text("sam-personal-secret")
    ring = tmp_path / "keyring.json"
    ring.write_text(json.dumps({"people": {"sokafor": {"name": "Sam Okafor", "role": "production legal",
                                                       "secret_file": str(secret)}}}))
    pid = studio.add(package())   # made in ComfyUI with the studio key, before the switch
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEYRING", str(ring))
    monkeypatch.setenv("COMFYAUDIT_TRUSTED_USER_HEADER", "X-Studio-User")
    body = json.dumps({"decision": "send-back", "reason": "Missing client approval"})
    status, _, raw, _ = call(web, "POST", f"/p/{pid}/decide", body, {"Content-Type": "application/json"})
    assert status == 400 and "No signed-in user" in json.loads(raw)["error"]
    status, _, raw, _ = call(web, "POST", f"/p/{pid}/decide", body,
                             {"Content-Type": "application/json", "X-Studio-User": "nobody"})
    assert status == 400 and "no key in the studio keyring" in json.loads(raw)["error"]
    status, _, raw, _ = call(web, "POST", f"/p/{pid}/decide", body,
                             {"Content-Type": "application/json", "X-Studio-User": "sokafor"})
    assert status == 200, raw
    entry = [e for e in H.read(studio.workflow(pid))["entries"] if e["event"] == "rejected"][-1]
    assert entry["key_id"].startswith("person:sokafor:")
    assert entry["actor"]["name"] == "Sam Okafor" and entry["actor"]["role"] == "production legal"
    assert entry["actor"]["declared_by"] == "studio sign-in"
    v = H.verify(studio.workflow(pid), keys=H.Keys.from_environment())
    assert v.assurance == "signed", v.problems
    # A doubled header could carry a client's own value: refused.
    conn = http.client.HTTPConnection("127.0.0.1", web, timeout=60)
    conn.putrequest("POST", f"/p/{pid}/decide")
    for k, v_ in (("Content-Type", "application/json"), ("X-Studio-User", "sokafor"),
                  ("X-Studio-User", "admin"), ("Content-Length", str(len(body)))):
        conn.putheader(k, v_)
    conn.endheaders(body.encode())
    resp = conn.getresponse()
    assert resp.status == 400 and "more than once" in json.loads(resp.read())["error"]
    conn.close()
    # Names are not typed when the sign-in decides who you are.
    status, _, raw, _ = call(web, "POST", "/me", json.dumps({"name": "Someone"}),
                             {"Content-Type": "application/json"})
    assert status == 400


# -- the studio's own front end (review-web/), on another origin --------------------

FRONT = "https://review.studio.example"


def test_the_front_end_origin_may_call_the_api_and_no_other(web, studio, monkeypatch):
    monkeypatch.setenv("COMFYAUDIT_REVIEW_CORS_ORIGINS", f"{FRONT}, http://localhost:8096")
    status, h, _, _ = call(web, "OPTIONS", "/p/x/decide", headers={
        "Origin": FRONT, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Private-Network": "true"})
    assert status == 204 and h["access-control-allow-origin"] == FRONT
    assert h["access-control-allow-credentials"] == "true"
    assert h["access-control-allow-private-network"] == "true"
    assert call(web, "OPTIONS", "/status", headers={"Origin": "https://evil.example"})[0] == 403
    status, h, raw, _ = call(web, "GET", "/status", headers={"Origin": FRONT})
    data = json.loads(raw)
    assert status == 200 and h["access-control-allow-origin"] == FRONT
    assert data["app"] == "ComfyAudit Review API" and data["signing"] == "studio-key"
    assert "access-control-allow-origin" not in call(web, "GET", "/status",
                                                      headers={"Origin": "https://evil.example"})[1]
    # The front end posts the zip, then JSON with the reviewer's typed name.
    status, h, raw, _ = call(web, "POST", "/open?filename=SH0120.delivery.zip", package(),
                             {"Origin": FRONT, "Content-Type": "application/zip"})
    assert status == 200 and h["access-control-allow-origin"] == FRONT
    pid = json.loads(raw)["id"]
    status, _, raw, _ = call(web, "POST", f"/p/{pid}/answer",
                             json.dumps({"tag": SHOT_TAG, "note": "INV-77", "reviewer": "Sam Okafor"}),
                             {"Origin": FRONT, "Content-Type": "application/json"})
    assert status == 200, raw
    assert H.read(studio.workflow(pid))["entries"][-1]["actor"]["name"] == "Sam Okafor"
    # A form post from that origin is not how the front end talks: refused.
    status, _, _, _ = call(web, "POST", f"/p/{pid}/decide", "decision=clear&reason=x",
                           {"Origin": FRONT, "Content-Type": "application/x-www-form-urlencoded"})
    assert status == 403


def test_a_typed_name_never_overrides_the_sign_in(web, studio, monkeypatch, tmp_path):
    secret = tmp_path / "sam.key"
    secret.write_text("sam-personal-secret")
    ring = tmp_path / "keyring.json"
    ring.write_text(json.dumps({"people": {"sokafor": {"name": "Sam Okafor", "role": "legal",
                                                       "secret_file": str(secret)}}}))
    pid = studio.add(package())
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEYRING", str(ring))
    monkeypatch.setenv("COMFYAUDIT_TRUSTED_USER_HEADER", "X-Studio-User")
    status, _, raw, _ = call(web, "POST", f"/p/{pid}/answer",
                             json.dumps({"tag": SHOT_TAG, "note": "x", "reviewer": "Someone Else"}),
                             {"Content-Type": "application/json", "X-Studio-User": "sokafor"})
    assert status == 200, raw
    assert H.read(studio.workflow(pid))["entries"][-1]["actor"]["name"] == "Sam Okafor"
