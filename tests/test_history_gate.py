"""The Shot History Gate: nothing is saved from a shot whose history fails the bar."""

from __future__ import annotations

import copy
import json
import os

import pytest

from comfyaudit.core import history as H
from comfyaudit.nodes.audit_nodes import ComfyAuditHistoryGate

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
IMAGES = object()


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def gate(workflow, **opts):
    return ComfyAuditHistoryGate().run(IMAGES, extra_pnginfo={"workflow": workflow}, **opts)


@pytest.fixture(autouse=True)
def no_studio_key(monkeypatch):
    for var in ("COMFYAUDIT_HISTORY_KEY", "COMFYAUDIT_HISTORY_KEY_FILE", "COMFYAUDIT_HISTORY_LEDGER"):
        monkeypatch.delenv(var, raising=False)


def tracked():
    return H.stamp(load(), event="created", actor=ANA, keys=H.Keys(), name="SH0120").workflow


def test_a_tracked_intact_shot_passes_and_hands_the_images_on():
    images, verdict = gate(tracked())
    assert images is IMAGES and verdict.startswith("passed")


def test_a_workflow_with_no_history_is_stopped_unless_allowed():
    with pytest.raises(RuntimeError, match="no shot history"):
        gate(load())
    assert gate(load(), require_history=False)[1].startswith("passed")
    with pytest.raises(RuntimeError, match="no shot history"):
        ComfyAuditHistoryGate().run(IMAGES, extra_pnginfo=None)


def test_an_altered_history_is_stopped():
    wf = tracked()
    forged = copy.deepcopy(wf)
    forged["extra"][H.EXTRA_KEY]["entries"][0]["actor"]["name"] = "Someone Else"
    with pytest.raises(RuntimeError, match="altered"):
        gate(forged)
    assert gate(forged, stop_if_altered=False)[1].startswith("passed")


def test_a_sandbox_shot_is_stopped_when_production_is_required():
    wf = tracked()
    with pytest.raises(RuntimeError, match="in sandbox"):
        gate(wf, minimum_stage="production")
    wf = H.stamp(wf, event="promoted", actor=ANA, keys=H.Keys(),
                 data_payload={"stage": "production"}).workflow
    assert "production" in gate(wf, minimum_stage="production")[1]


def test_a_non_commercial_model_stops_it_only_when_asked():
    wf = load()
    node = next(n for n in wf["nodes"] if n["type"] == "CheckpointLoaderSimple")
    node["widgets_values"][0] = "flux1-dev.safetensors"
    wf = H.stamp(wf, event="created", actor=ANA, keys=H.Keys()).workflow
    assert gate(wf)[1].startswith("passed")
    with pytest.raises(RuntimeError, match="flux1-dev"):
        gate(wf, stop_if_not_commercial=True)


def _legal(wf, decision, keys):
    return H.stamp(wf, event="approved" if decision == "cleared" else "rejected",
                   actor=H.Actor(name="Sam Okafor", role="legal"), keys=keys, note="reason",
                   data_payload={"legal": {"decision": decision}}).workflow


def test_require_legal_clearance_needs_a_signed_current_clearance(monkeypatch):
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    keys = H.Keys.from_environment()
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys, name="SH0120").workflow
    assert gate(wf)[1].startswith("passed")           # off by default
    with pytest.raises(RuntimeError, match="not cleared"):
        gate(wf, require_legal_clearance=True)
    with pytest.raises(RuntimeError, match="sent this shot back"):
        gate(_legal(wf, "sent back", keys), require_legal_clearance=True)
    cleared = _legal(wf, "cleared", keys)
    assert "cleared by legal (Sam Okafor)" in gate(cleared, require_legal_clearance=True)[1]
    changed = copy.deepcopy(cleared)
    next(n for n in changed["nodes"] if n["type"] == "CLIPTextEncode")["widgets_values"][0] += ", warmer"
    changed = H.stamp(changed, actor=ANA, keys=keys).workflow
    with pytest.raises(RuntimeError, match="earlier version"):
        gate(changed, require_legal_clearance=True)


def test_an_unsigned_clearance_does_not_open_the_gate():
    wf = _legal(tracked(), "cleared", H.Keys())
    with pytest.raises(RuntimeError, match="unsigned"):
        gate(wf, require_legal_clearance=True)
