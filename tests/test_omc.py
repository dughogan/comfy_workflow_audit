"""OMC-JSON export, validated against MovieLabs' published v2.8 schema."""

from __future__ import annotations

import json
import os

import pytest

from comfyaudit.core import history as H
from comfyaudit.core.report import omc

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
DOUG = H.Actor(name="Doug Hogan", role="VFX supervisor")


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def shot():
    k = H.Keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0120",
                 external_ids={"shotgrid": "4411"}).workflow
    src = H.lineage(H.read(wf))["sources"][0]["node"]
    wf = H.stamp(wf, event="sourced", actor=ANA, keys=k,
                 data_payload={"node": src, "origin": "photographed", "asset_id": "PLATE-1"}).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k,
                 outputs=[{"file": "sh0120_v001.exr", "sha256": "ab" * 32, "bytes": 10}]).workflow
    wf = H.stamp(wf, event="approved", actor=DOUG, keys=k, note="final",
                 data_payload={"clearance": {"verdict": "go", "verdict_label": "Clear as-is"}}).workflow
    return H.read(wf)


def test_the_export_is_valid_omc_json_v2_8():
    jsonschema = pytest.importorskip("jsonschema")
    with open(os.path.join(HERE, "fixtures", "omc", "OMC-JSON-v2.8.schema.json"), encoding="utf-8") as fh:
        schema = json.load(fh)
    doc = omc.export(shot())
    errors = sorted(jsonschema.Draft201909Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message[:200]}" for e in errors[:5]]


def test_the_export_says_who_made_what_from_what():
    doc = omc.export(shot())
    by = {(e["entityType"], e["identifier"][0]["identifierValue"]): e for e in doc}
    people = {e["name"] for e in doc if e["entityType"] == "Participant"}
    assert {"ComfyUI", "Ana Ruiz", "Doug Hogan"} <= people
    out = next(e for e in doc if e["entityType"] == "Asset" and e["name"] == "sh0120_v001.exr")
    assert {"identifierScope": "sha256", "identifierValue": "ab" * 32} in out["identifier"]
    assert out["provenance"]["CreatedBy"]["identifier"][0]["identifierValue"] == "ptc/comfyui"
    src = out["version"]["DerivationOf"]["identifier"][0]["identifierValue"]
    assert by[("Asset", src)]["annotation"][0]["text"].startswith("Origin: Photographed")
    ctx = next(e for e in doc if e["entityType"] == "Context" and e.get("name") == "SH0120")
    assert {"identifierScope": "shotgrid", "identifierValue": "4411"} in ctx["identifier"]
    assert ctx["customData"][0]["value"]["clearance"]["verdict"] == "go"


def test_prompts_never_leave_in_the_export():
    hist = shot()
    prompt = next(p for e in hist["entries"] for p in (e.get("snapshot") or {}).get("prompts", [])
                  if p.get("text"))["text"]
    assert prompt not in json.dumps(omc.export(hist))
