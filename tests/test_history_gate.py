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
