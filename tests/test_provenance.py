"""A shot's history follows its files: forks for copies, provenance beside outputs."""

from __future__ import annotations

import copy
import json
import os

import pytest

from comfyaudit.core import history as H
from comfyaudit.core import history_cli
from comfyaudit.server import history_service
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def keys():
    return H.Keys.from_environment()


@pytest.fixture
def studio(monkeypatch, tmp_path):
    cfg = {"user": "Ana Ruiz", "role": "compositor", "ledger": str(tmp_path / "ledger.jsonl"),
           "key_file": "", "confidentiality": "none", "sidecars": True}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    return cfg


def test_a_fork_is_a_new_record_that_carries_its_parent(studio):
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys(), name="SH0120",
                 external_ids={"shotgrid": "4411"}, path="workflows/sh0120.json").workflow
    wf = H.stamp(wf, event="promoted", actor=ANA, keys=keys(),
                 data_payload={"stage": "delivered"}).workflow
    parent = H.read(wf)
    out = H.fork(wf, reason="saved as a copy", actor=ANA, keys=keys(),
                 path="workflows/sh0120_v002.json", parent_file="workflows/sh0120.json")
    hist = H.read(out.workflow)
    assert hist["entries"][0]["event"] == "forked" and len(hist["entries"]) == 1
    assert hist["shot"]["id"] != parent["shot"]["id"]
    assert hist["shot"]["name"] == "SH0120" and hist["shot"]["external_ids"] == {"shotgrid": "4411"}
    # A copy of a delivered shot is new work.
    assert hist["shot"]["stage"] == "production"
    assert hist["parent"]["history"] == parent
    lin = H.lineage(hist)
    assert lin["shot"]["parent"]["head"] == parent["entries"][-1]["hash"]
    v = H.verify(out.workflow, keys=keys())
    assert v.assurance == "signed", v.problems
    assert any("forked from SH0120" in n for n in v.notes)
    # The parent travels frozen: altering it is caught.
    bad = copy.deepcopy(out.workflow)
    bad["extra"]["comfyaudit_history"]["parent"]["history"]["entries"][0]["note"] = "rewritten"
    v = H.verify(bad, keys=keys())
    assert v.assurance == "none" and any(p.startswith("the parent record") for p in v.problems)
    # Swapping in a different parent is caught too.
    other = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    swapped = copy.deepcopy(out.workflow)
    swapped["extra"]["comfyaudit_history"]["parent"]["history"] = H.read(other)
    assert H.verify(swapped, keys=keys()).assurance == "none"


def test_an_old_copy_stamped_after_the_shot_moved_on_becomes_a_fork(studio):
    ledger = studio["ledger"]
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys(), ledger=ledger).workflow
    old_render = copy.deepcopy(wf)          # the history as an earlier PNG carried it
    wf = H.stamp(wf, event="reviewed", actor=ANA, keys=keys(), ledger=ledger, note="ok").workflow
    assert not H.ledger_diverged(ledger, H.read(wf))
    assert H.ledger_diverged(ledger, H.read(old_render))
    out = history_service.stamp({"workflow": old_render, "event": "note", "note": "back in"})
    assert out["forked"] and out["history"]["entries"][0]["event"] == "forked"
    assert out["history"]["entries"][-1]["event"] == "note"
    assert H.verify({}, keys=keys(), ledger=ledger, history=out["history"]).assurance != "none"


def test_saving_as_a_copy_forks_and_moving_the_file_does_not(studio, monkeypatch, tmp_path):
    files = tmp_path / "user"
    monkeypatch.setattr(history_service, "_userdata", lambda p: str(files / p))
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys(),
                 path="workflows/a.json").workflow
    (files / "workflows").mkdir(parents=True)
    (files / "workflows" / "a.json").write_text(json.dumps(wf))
    # The original is still there: this is a copy.
    out = history_service.relocate({"workflow": wf, "path": "workflows/b.json"})
    assert out["forked"]
    first = out["history"]["entries"][0]
    assert first["event"] == "forked"
    assert first["data"]["reason"] == "saved as a copy of workflows/a.json"
    assert out["history"]["parent"]["file"] == "workflows/a.json"
    # The original is gone: it was renamed.
    (files / "workflows" / "a.json").unlink()
    out = history_service.relocate({"workflow": wf, "path": "workflows/c.json"})
    assert not out.get("forked")
    last = out["history"]["entries"][-1]
    assert last["event"] == "moved" and last["data"] == {"from": "workflows/a.json", "to": "workflows/c.json"}
    assert H.file_of(out["history"]) == "workflows/c.json"


def test_a_copy_of_a_shot_named_after_an_unsaved_tab_takes_the_copys_name(studio, monkeypatch, tmp_path):
    files = tmp_path / "user"
    monkeypatch.setattr(history_service, "_userdata", lambda p: str(files / p))
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys(), name="Unsaved Workflow (3)",
                 path="workflows/a.json").workflow
    (files / "workflows").mkdir(parents=True)
    (files / "workflows" / "a.json").write_text(json.dumps(wf))
    out = history_service.relocate({"workflow": wf, "path": "workflows/sh0120_v002.json",
                                    "name": "sh0120_v002"})
    assert out["history"]["shot"]["name"] == "sh0120_v002"
    # A shot someone actually named keeps its name in every copy.
    named = H.stamp(load(), event="created", actor=ANA, keys=keys(), name="SH0120",
                    path="workflows/a.json").workflow
    (files / "workflows" / "a.json").write_text(json.dumps(named))
    out = history_service.relocate({"workflow": named, "path": "workflows/v3.json", "name": "v3"})
    assert out["history"]["shot"]["name"] == "SH0120"


def test_every_render_gets_a_provenance_file_that_verifies_on_its_own(studio, monkeypatch, tmp_path):
    frame = tmp_path / "beauty_0001.exr"
    frame.write_bytes(b"half-float pixels")
    monkeypatch.setattr(history_service, "output_path", lambda item: str(frame))
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys(), name="SH0120").workflow
    out = history_service.stamp({"workflow": wf, "event": "rendered",
                                 "outputs": [{"filename": "beauty_0001.exr", "type": "output"}]})
    side = str(frame) + ".provenance.json"
    doc = json.load(open(side, encoding="utf-8"))
    assert doc["entry"] == 2 and doc["history"]["entries"][-1]["event"] == "rendered"
    assert doc["shot"]["name"] == "SH0120"
    assert history_cli.verify_file(str(frame), keys=keys(), ledger=studio["ledger"]) == 0
    # A changed file no longer matches what its entry recorded.
    frame.write_bytes(b"retouched pixels")
    assert history_cli.verify_file(str(frame), keys=keys()) == history_cli.EXIT_ALTERED
    # Nor does a tampered sidecar.
    frame.write_bytes(b"half-float pixels")
    doc["history"]["entries"][0]["note"] = "rewritten"
    json.dump(doc, open(side, "w", encoding="utf-8"))
    assert history_cli.verify_file(str(frame), keys=keys()) == history_cli.EXIT_ALTERED


def test_sidecars_can_be_switched_off(studio, monkeypatch, tmp_path):
    studio["sidecars"] = False
    frame = tmp_path / "f.png"
    frame.write_bytes(b"x")
    monkeypatch.setattr(history_service, "output_path", lambda item: str(frame))
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    history_service.stamp({"workflow": wf, "event": "rendered", "outputs": [{"filename": "f.png"}]})
    assert not os.path.exists(str(frame) + ".provenance.json")
