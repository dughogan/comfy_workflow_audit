"""Packages and reports read by software: the index, and the data each page carries."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import zipfile

import pytest

from comfyaudit.core import history as H
from comfyaudit.core.report import machine
from comfyaudit.server import delivery
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
HOSTILE = "warm grade</script><script>alert(1)</script><!-- x"


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def package(monkeypatch):
    cfg = {"user": "Priya Rao", "role": "VFX supervisor", "auto": True, "ledger": "",
           "key_file": "", "confidentiality": "none", "distribute_in": [], "territory": "",
           "likeness": False, "clients": []}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    keys = H.Keys.from_environment()
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys, name="SH0120").workflow
    wf = H.stamp(wf, event="note", actor=ANA, keys=keys, note=HOSTILE).workflow
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "note": "Reel 3",
                            "exception": "Test delivery."})
    assert "error" not in out, out
    return zipfile.ZipFile(io.BytesIO(base64.b64decode(out["zip_b64"])))


def test_the_index_fingerprints_every_other_file_in_the_package(package):
    index = json.loads(package.read("SH0120.package.json"))
    assert index["schema"] == "comfyaudit/delivery-package/1"
    listed = {f["path"]: f for f in index["files"]}
    assert set(listed) == set(package.namelist()) - {"SH0120.package.json"}
    for path, f in listed.items():
        assert f["sha256"] == hashlib.sha256(package.read(path)).hexdigest(), path
        assert f["role"] and f["media_type"] and f["description"]
    d = index["decision"]
    assert d["signed_off_by"]["name"] == "Priya Rao" and d["note"] == "Reel 3"
    assert d["delivered_as_exception"] and d["exception_reason"] == "Test delivery."
    assert index["integrity"]["assurance"] == "signed"
    assert index["integrity"]["head_hash"] == json.loads(package.read("SH0120.json"))[
        "extra"][H.EXTRA_KEY]["entries"][-1]["hash"]


@pytest.mark.parametrize("path,schema", [
    ("SH0120.legal-report.html", "comfyaudit/provenance-record/1"),
    ("SH0120.licence-audit.html", "comfyaudit/licence-audit/1"),
    ("SH0120.life.html", "comfyaudit/life-of-the-shot/1"),
])
def test_each_report_carries_its_data_for_software(package, path, schema):
    page = package.read(path).decode("utf-8")
    data = machine.read(page)
    assert data["schema"] == schema and data["disclaimer"]
    # Nothing in the data can end the block early or start a script of its own.
    for tag in re.findall(r"<script[^>]*>", page):
        assert tag in ("<script type='application/json' id='comfyaudit-data'>", "<script>")
    assert "alert(1)</script>" not in page


def test_hostile_text_round_trips_exactly_through_the_data_block(package):
    data = machine.read(package.read("SH0120.legal-report.html").decode("utf-8"))
    notes = [e.get("note") for e in data["entries"]]
    assert HOSTILE in notes


def test_the_legal_report_data_says_what_was_decided_and_what_is_open(package):
    data = machine.read(package.read("SH0120.legal-report.html").decode("utf-8"))
    assert data["clearance"]["by"] == "Priya Rao"
    assert data["verification"]["assurance"] == "signed"
    assert {"id", "title", "evidence"} <= set(data["rules"][0]) if data["rules"] else True
