"""Answers given once for a show or the studio, inherited by every shot."""

from __future__ import annotations

import json
import os

import pytest

from comfyaudit.core import answers as A
from comfyaudit.core import history as H
from comfyaudit.core.report import compliance
from comfyaudit.server import delivery, history_service
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
LEGAL = H.Actor(name="Sam Okafor", role="production legal")
KEYS = H.Keys(sign_key=b"studio", key_id="studio")
# A show-level item and a studio-level item, as scoped in tools/build_regulations.py.
SHOW_TAG = "mpa-tpn-content-security-ai-controls:0"
STUDIO_TAG = "mpa-tpn-content-security-ai-controls:3"


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def test_a_show_answer_reaches_every_shot_of_that_show_and_no_other(tmp_path):
    store = A.Store(str(tmp_path / "answers.json"))
    store.add(tag=SHOW_TAG, scope="show", project="Pilot: Ep 101", note="Client email 14 Oct",
              actor=LEGAL, keys=KEYS)
    assert SHOW_TAG in store.for_shot("pilot ep 101", KEYS)       # however it is typed
    assert store.for_shot("pilot ep 101", KEYS)[SHOW_TAG]["state"] == "signed"
    assert SHOW_TAG not in store.for_shot("Another Show", KEYS)


def test_a_studio_answer_reaches_every_show_and_a_show_answer_beats_it(tmp_path):
    store = A.Store(str(tmp_path / "answers.json"))
    store.add(tag=STUDIO_TAG, scope="studio", project="", note="Scan SOP v3", actor=LEGAL, keys=KEYS)
    assert STUDIO_TAG in store.for_shot("Any Show", KEYS) and STUDIO_TAG in store.for_shot("", KEYS)
    store.add(tag=SHOW_TAG, scope="studio", project="", note="Blanket approval", actor=LEGAL, keys=KEYS)
    store.add(tag=SHOW_TAG, scope="show", project="Pilot", note="Pilot-specific approval",
              actor=LEGAL, keys=KEYS)
    assert store.for_shot("Pilot", KEYS)[SHOW_TAG]["note"] == "Pilot-specific approval"


def test_an_edited_answer_is_not_inherited(tmp_path):
    store = A.Store(str(tmp_path / "answers.json"))
    store.add(tag=SHOW_TAG, scope="show", project="Pilot", note="Client email", actor=LEGAL, keys=KEYS)
    doc = json.loads((tmp_path / "answers.json").read_text())
    doc["log"][0]["note"] = "Something nobody said"
    (tmp_path / "answers.json").write_text(json.dumps(doc))
    assert store.for_shot("Pilot", KEYS) == {}


def test_answers_need_substance_and_a_show(tmp_path):
    store = A.Store(str(tmp_path / "answers.json"))
    with pytest.raises(ValueError, match="blank"):
        store.add(tag=SHOW_TAG, scope="show", project="Pilot", note=" ", actor=LEGAL, keys=KEYS)
    with pytest.raises(ValueError, match="project"):
        store.add(tag=SHOW_TAG, scope="show", project="", note="x", actor=LEGAL, keys=KEYS)


def assess(history, answers):
    lin = H.lineage(history)
    return compliance.assess(lin, history, {"tpn_vendor": True, "answers": answers})


def item(checks, tag):
    rule_id, _, idx = tag.partition(":")
    rule = next(r for r in checks["rules"] if r["id"] == rule_id)
    return rule["evidence"][int(idx)]


def test_an_inherited_answer_ticks_the_item_and_a_shot_note_wins(tmp_path):
    store = A.Store(str(tmp_path / "answers.json"))
    store.add(tag=SHOW_TAG, scope="show", project="Pilot", note="Client email", actor=LEGAL, keys=KEYS)
    wf = H.stamp(load(), event="created", actor=ANA, keys=KEYS, name="SH0120", project="Pilot").workflow
    checks = assess(H.read(wf), store.for_shot("Pilot", KEYS))
    ev = item(checks, SHOW_TAG)
    assert ev["state"] == "have" and ev["answered_by"] == "show" and "the show Pilot" in ev["text"]
    assert [a["tag"] for a in checks["answers_used"]] == [SHOW_TAG]
    assert item(checks, STUDIO_TAG)["state"] == "need" and item(checks, STUDIO_TAG)["scope"] == "studio"
    wf = H.stamp(wf, event="note", actor=ANA, keys=KEYS, note="Approval for this shot",
                 data_payload={"evidence": SHOW_TAG}).workflow
    ev = item(assess(H.read(wf), store.for_shot("Pilot", KEYS)), SHOW_TAG)
    assert ev["answered_by"] == "shot"


@pytest.fixture
def studio(monkeypatch, tmp_path):
    cfg = {"user": "Priya Rao", "role": "VFX supervisor", "auto": True, "ledger": "",
           "key_file": "", "confidentiality": "none", "distribute_in": [], "territory": "",
           "likeness": False, "clients": [], "tpn_vendor": True}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    monkeypatch.setenv("COMFYAUDIT_ANSWERS_FILE", str(tmp_path / "answers.json"))
    return cfg


def test_the_route_records_only_items_a_person_answers_at_their_own_level(studio):
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys.from_environment(),
                 name="SH0120", project="Pilot").workflow
    ok = history_service.answer({"workflow": wf, "tag": SHOW_TAG, "scope": "show", "note": "Client email"})
    assert ok["answer"]["production"] == "pilot" and ok["answer"]["by"]["name"] == "Priya Rao"
    wrong = history_service.answer({"workflow": wf, "tag": SHOW_TAG, "scope": "studio", "note": "x"})
    assert "answered per show" in wrong["error"]
    auto = history_service.answer({"workflow": wf, "tag": "mpa-tpn-content-security-ai-controls:2",
                                   "scope": "show", "note": "x"})
    assert "not an item a person answers" in auto["error"]


def test_delivery_seals_the_answers_it_relied_on_and_records_content_credentials(studio):
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys.from_environment(),
                 name="SH0120", project="Pilot").workflow
    history_service.answer({"workflow": wf, "tag": SHOW_TAG, "scope": "show", "note": "Client email 14 Oct"})
    checked = delivery.check({"workflow": wf, "name": "SH0120"})
    open_tags = {i["tag"] for c in checked["confirmations"] for i in c["items"]}
    assert SHOW_TAG not in open_tags and STUDIO_TAG in open_tags
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "exception": "Test."})
    assert "error" not in out, out
    hist = H.read(out["workflow"])
    sealed = H.delivery_clearance(hist)["answers_relied_on"]
    assert [a["tag"] for a in sealed] == [SHOW_TAG] and sealed[0]["sig"]
    # The sealed copy still verifies on its own, without the answer store.
    assert A.state(sealed[0], H.Keys.from_environment()) == "signed"
    promoted = hist["entries"][-1]
    assert promoted["event"] == "promoted" and "content_credentials" in promoted["data"]


def test_the_package_lists_what_the_show_answered(studio):
    import base64
    import io
    import zipfile
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys.from_environment(),
                 name="SH0120", project="Pilot").workflow
    history_service.answer({"workflow": wf, "tag": SHOW_TAG, "scope": "show", "note": "Client email 14 Oct"})
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "exception": "Test."})
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(out["zip_b64"])))
    readme = z.read("README.txt").decode()
    assert f"[x] {SHOW_TAG} - show Pilot: Client email 14 Oct (Priya Rao" in readme
    index = json.loads(z.read("SH0120.package.json"))
    assert [a["tag"] for a in index["answered_for_show_or_studio"]] == [SHOW_TAG]
