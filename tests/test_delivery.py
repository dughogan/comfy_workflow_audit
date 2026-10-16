"""Package for delivery, and the commercial standing a history carries over time."""

from __future__ import annotations

import base64
import copy
import io
import json
import os
import zipfile

import pytest

from comfyaudit.core import history as H
from comfyaudit.server import delivery
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def set_checkpoint(wf, filename):
    node = next(n for n in wf["nodes"] if n["type"] == "CheckpointLoaderSimple")
    node["widgets_values"][0] = filename
    return wf


def test_a_change_that_ends_commercial_use_is_flagged_dated_and_explained():
    k = H.Keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    first = H.commercial_timeline(H.read(wf))[-1]
    blockers_before = {m["filename"] for m in first["models"]} if first["status"] == "blocked" else set()
    wf = H.stamp(set_checkpoint(copy.deepcopy(wf), "flux1-dev.safetensors"),
                 actor=ANA, keys=k).workflow
    change = H.commercial_timeline(H.read(wf))[-1]
    # Flagged whether this is what blocks the shot or one more blocker on top.
    assert change["status"] == "blocked" and change["seq"] == 2
    assert change["by"] == "Ana Ruiz" and change["at"]
    assert "flux1-dev.safetensors" in change["added"]
    assert {m["filename"] for m in change["models"]} == blockers_before | {"flux1-dev.safetensors"}
    # Taking it out again is a change too, and it is recorded as one.
    wf = H.stamp(set_checkpoint(copy.deepcopy(wf), "juggernautXL_v9Rundiffusion.safetensors"),
                 actor=ANA, keys=k).workflow
    last = H.commercial_timeline(H.read(wf))[-1]
    assert last["seq"] == 3 and "flux1-dev.safetensors" in last["removed"]
    assert "flux1-dev.safetensors" not in {m["filename"] for m in last["models"]}


def test_a_shot_that_stays_the_same_adds_no_flags():
    k = H.Keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="reviewed", actor=ANA, keys=k, note="fine").workflow
    assert len(H.commercial_timeline(H.read(wf))) == 1


def test_a_blocked_model_is_offered_alternatives_whose_licences_allow_commercial_use():
    from comfyaudit.core.knowledge import alternatives as A
    alts = A.suggest("flux1-dev.safetensors", installed=["flux1-schnell-fp8.safetensors"])
    assert alts[0]["name"] == "FLUX.1 [schnell]" and alts[0]["installed"]
    assert alts[0]["filename"] == "flux1-schnell-fp8.safetensors"
    assert all(a["commercial_use"] in ("yes", "conditional") for a in alts)
    # The alternatives file can never vouch for a licence: an option whose
    # licence the knowledge base marks non-commercial is not offered.
    rigged = {"alternatives": [{"for": ["codeformer"], "task": "x",
                                "options": [{"name": "Also NC", "match": ["4x-ultrasharp"]}]}]}
    assert A.suggest("codeformer.pth", data=rigged) == []


def test_bringing_a_render_back_in_is_recorded_as_a_selection_on_its_own(tmp_path):
    # The artist picks render 2 and loads it as the plate for the next pass.
    # Nobody clicks anything: the history matches the file to the render by
    # contents, records the choice, and knows where that input came from.
    frame = tmp_path / "beauty_0002.png"
    frame.write_bytes(b"pixels of the take the artist liked")
    k = H.Keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k, outputs=[str(frame)]).workflow
    node = next(n for n in wf["nodes"] if n["type"] == "LoadImage")
    node["widgets_values"][0] = "beauty_0002.png [output]"
    resolve = lambda v: str(frame) if v.startswith("beauty_0002.png") else ""  # noqa: E731
    wf = H.stamp(wf, actor=ANA, keys=k, resolve=resolve).workflow
    entry = H.read(wf)["entries"][-1]
    assert entry["changes"]["selected"] == [{"node": str(node["id"]), "from_entry": 2}]
    assert "selection" in entry["breakpoints"]
    src = next(s for s in H.lineage(H.read(wf))["sources"] if s["node"] == str(node["id"]))
    assert src["origin"] == "generated" and src["declared_by"] == "recorded automatically"
    # A file that is not one of this history's renders is not a selection.
    other = tmp_path / "plate.png"
    other.write_bytes(b"a photographed plate")
    node = next(n for n in wf["nodes"] if n["type"] == "LoadImage")
    node["widgets_values"][0] = "plate.png"
    wf = H.stamp(wf, actor=ANA, keys=k, resolve=lambda v: str(other)).workflow
    assert "selected" not in H.read(wf)["entries"][-1]["changes"]


def test_the_next_person_to_change_the_shot_is_recorded_as_taking_it_over():
    k = H.Keys()
    doug = H.Actor(name="Doug Hogan", role="supervisor")
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    first_text = next(n for n in wf["nodes"] if n["type"] == "CLIPTextEncode")
    first_text["widgets_values"][0] += ", warmer"
    wf = H.stamp(wf, actor=doug, keys=k).workflow
    assert "handoff" in H.read(wf)["entries"][-1]["breakpoints"]
    first_text = next(n for n in wf["nodes"] if n["type"] == "CLIPTextEncode")
    first_text["widgets_values"][0] += ", softer"
    wf = H.stamp(wf, actor=doug, keys=k).workflow
    assert "handoff" not in H.read(wf)["entries"][-1]["breakpoints"]


@pytest.fixture
def studio(monkeypatch, tmp_path):
    cfg = {"user": "Priya Rao", "role": "VFX supervisor", "auto": True, "ledger": "",
           "key_file": "", "confidentiality": "none", "distribute_in": [], "territory": "",
           "likeness": False, "clients": []}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    return cfg


def test_a_verdict_that_is_not_a_yes_needs_a_recorded_reason_to_deliver(studio):
    wf = H.stamp(set_checkpoint(load(), "flux1-dev.safetensors"), event="created",
                 actor=ANA, keys=H.Keys.from_environment()).workflow
    checked = delivery.check({"workflow": wf, "name": "SH0120"})
    assert checked["verdict"] in ("no-go", "unknown")
    assert checked["fixes"] or checked["missing_facts"]
    refused = delivery.deliver({"workflow": wf, "name": "SH0120"})
    assert "error" in refused and "reason" in refused["error"]


def test_delivery_seals_the_clearance_and_returns_the_package(studio):
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys.from_environment(),
                 name="SH0120").workflow
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "note": "Reel 3",
                            "exception": "Test delivery with the profile unset."})
    assert "error" not in out, out
    hist = H.read(out["workflow"])
    assert hist["shot"]["stage"] == "delivered"
    assert [e["event"] for e in hist["entries"]][-2:] == ["approved", "promoted"]
    sealed = H.delivery_clearance(hist)
    assert sealed["by"] == "Priya Rao" and sealed["note"] == "Reel 3"
    assert sealed["exception"] == "Test delivery with the profile unset."
    assert sealed["audited_with"]["licences"]
    # Sealed means signed: the clearance is inside a signed entry.
    v = H.verify(out["workflow"], keys=H.Keys.from_environment())
    assert v.assurance == "signed", v.problems
    labels = [a["label"] for a in out["manifest"]["assertions"]]
    assert "com.comfyaudit.clearance" in labels
    assert "Delivery clearance" in out["report_html"]
    names = zipfile.ZipFile(io.BytesIO(base64.b64decode(out["zip_b64"]))).namelist()
    assert sorted(names) == sorted(["SH0120.json", "SH0120.c2pa.json", "SH0120.legal-report.html",
                                    "SH0120.clearance-sheet.html", "SH0120.life.html", "SH0120.omc.json", "README.txt"])


def test_what_is_left_for_legal_is_sealed_and_listed_not_asked_of_the_artist(studio, monkeypatch):
    open_rule = [{"rule": "tpn-ai", "title": "TPN: written client approval for AI use",
                  "jurisdiction": "TPN", "items": [{"text": "Client's written approval on file",
                                                    "tag": "tpn-ai:0"}]}]
    monkeypatch.setattr(delivery, "_confirmations", lambda lin, history, cfg: open_rule)
    wf = H.stamp(load(), event="created", actor=ANA, keys=H.Keys.from_environment(),
                 name="SH0120").workflow
    # Nothing was confirmed, and delivery still goes ahead: these are legal's to check.
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "exception": "Test."})
    assert "error" not in out, out
    sealed = H.delivery_clearance(H.read(out["workflow"]))
    assert sealed["for_legal"] == [{"rule": "tpn-ai", "title": "TPN: written client approval for AI use",
                                    "jurisdiction": "TPN", "items": ["Client's written approval on file"]}]
    readme = zipfile.ZipFile(io.BytesIO(base64.b64decode(out["zip_b64"]))).read("README.txt").decode()
    assert "Left for legal to confirm (1)" in readme
    assert "[ ] TPN: written client approval for AI use (TPN)" in readme
