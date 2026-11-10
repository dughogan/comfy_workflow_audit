"""Studio governance fields: what ran (job ID, model hashes, runtime, installed
packs) and what the studio tracks per shot (client, QC, asset IDs)."""

from __future__ import annotations

import base64
import copy
import io
import json
import os
import zipfile

import pytest

from comfyaudit.core import history as H
from comfyaudit.core.report import history_html
from comfyaudit.server import delivery, live
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
DOUG = H.Actor(name="Doug Hogan", role="VFX supervisor")


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def keys():
    k = H.Keys()
    k.sign_key = H._derive("studio-secret", b"comfyaudit-sign")
    k.key_id = "test"
    return k


def checkpoint(wf):
    return next(n for n in wf["nodes"] if n["type"] == "CheckpointLoaderSimple")


def with_custom_pack():
    """The fixture with one node swapped for a custom pack's node type."""
    wf = load()
    node = next(n for n in wf["nodes"] if n["type"] not in ("CheckpointLoaderSimple",))
    node["type"] = "ImpactWildcardProcessor"
    return wf


@pytest.fixture
def providers():
    """Runtime and pack providers as the running ComfyUI would set them."""
    H.set_runtime_provider(lambda: {"comfyui": "0.9.1", "python": "3.12.4",
                                    "torch": "2.9.0", "gpu": "RTX PRO 6000"})
    H.set_pack_provider(lambda nodes: {"installed": "1.2.0", "commit": "a" * 40})
    yield
    H.set_runtime_provider(None)
    H.set_pack_provider(None)


# -- what ran -------------------------------------------------------------------

def test_a_render_records_the_comfyui_job_it_was(providers):
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0120").workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k,
                 outputs=[{"file": "beauty_0001.png", "sha256": "ab" * 32}],
                 data_payload={"prompt_id": "4f1c-job"}).workflow
    lin = H.lineage(H.read(wf))
    assert lin["final_job_id"] == "4f1c-job"
    assert H.verify(wf, keys=k).assurance == "signed"


def test_the_runtime_a_render_ran_on_is_recorded_and_a_change_is_flagged(providers):
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k).workflow
    lin = H.lineage(H.read(wf))
    assert lin["ran_on"] == {"comfyui": "0.9.1", "python": "3.12.4", "torch": "2.9.0",
                             "gpu": "RTX PRO 6000"}
    H.set_runtime_provider(lambda: {"comfyui": "0.9.2", "python": "3.12.4",
                                    "torch": "2.9.0", "gpu": "RTX PRO 6000"})
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k).workflow
    assert H.read(wf)["entries"][-1]["changes"]["runtime_changed"] == ["comfyui: 0.9.1 -> 0.9.2"]


def test_without_a_running_comfyui_no_runtime_is_claimed():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    entries = H.read(wf)["entries"]
    assert "runtime" not in H.snapshot_of(entries, entries[-1])


def test_model_hashes_show_a_file_swapped_under_the_same_name():
    k = keys()
    digest = {"v": "11" * 32}

    def hasher(folder, name):
        return digest["v"]

    wf = H.stamp(load(), event="created", actor=ANA, keys=k, model_hash=hasher).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k, model_hash=hasher).workflow
    digest["v"] = "22" * 32  # retrained, same filename
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k, model_hash=hasher).workflow
    hist = H.read(wf)
    name = checkpoint(load())["widgets_values"][0]
    assert name in hist["entries"][-1]["changes"]["model_files_changed"]
    hashed, total = H.lineage(hist)["models_hashed"]
    assert total and hashed == total


def test_a_model_that_cannot_be_hashed_is_recorded_without_one():
    def broken(folder, name):
        raise OSError("gone")

    wf = H.stamp(load(), event="created", actor=ANA, keys=keys(), model_hash=broken).workflow
    entries = H.read(wf)["entries"]
    assert all("sha256" not in m for m in H.snapshot_of(entries, entries[-1])["models"])


def test_hashing_on_only_one_side_is_not_called_a_swap():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k,
                 model_hash=lambda f, n: "33" * 32).workflow
    assert "model_files_changed" not in H.read(wf)["entries"][-1]["changes"]


def test_installed_pack_versions_come_from_the_install_and_changes_are_flagged(providers):
    k = keys()
    wf = H.stamp(with_custom_pack(), event="created", actor=ANA, keys=k).workflow
    entries = H.read(wf)["entries"]
    custom = H.snapshot_of(entries, entries[-1])["nodes"]["custom"]
    assert custom, "the fixture should carry one custom node pack"
    assert custom[0]["installed"] == "1.2.0" and custom[0]["commit"] == "a" * 40
    H.set_pack_provider(lambda nodes: {"installed": "1.3.0", "commit": "b" * 40})
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k).workflow
    changed = H.read(wf)["entries"][-1]["changes"]["pack_versions_changed"]
    assert any("installed: 1.2.0 -> 1.3.0" in c for c in changed)


# -- the model hasher -------------------------------------------------------------

def test_the_hasher_reads_a_file_once_and_again_only_when_it_changes(tmp_path):
    f = tmp_path / "m.safetensors"
    f.write_bytes(b"weights-v1")
    cache = str(tmp_path / "cache" / "hashes.json")
    hasher = live.ModelHasher(cache)
    first = hasher.hash_path(str(f), blocking=True)
    assert len(first) == 64
    assert hasher.cached(str(f)) == first
    # A fresh process reads the cache instead of the file.
    again = live.ModelHasher(cache)
    assert again.cached(str(f)) == first
    f.write_bytes(b"weights-v2, longer")
    assert again.cached(str(f)) == ""
    assert again.hash_path(str(f), blocking=True) not in ("", first)


def test_an_edit_never_waits_on_a_hash(tmp_path):
    f = tmp_path / "m.safetensors"
    f.write_bytes(b"x" * 1024)
    hasher = live.ModelHasher(str(tmp_path / "hashes.json"))
    assert hasher.hash_path(str(f), blocking=False) == ""


# -- per shot: client, QC, asset IDs ----------------------------------------------

def test_the_client_is_sealed_and_a_quiet_change_is_caught():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0120",
                 project="Atlas", client="Northlight Pictures").workflow
    hist = H.read(wf)
    assert hist["shot"]["client"] == "Northlight Pictures"
    assert H.lineage(hist)["shot"]["client"] == "Northlight Pictures"
    assert H.verify(wf, keys=k).assurance == "signed"
    bad = copy.deepcopy(wf)
    H.read(bad)["shot"]["client"] = "Someone Else"
    assert H.verify(bad, keys=k).problems


def test_a_record_made_without_a_client_hashes_as_it_always_did():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0120").workflow
    entry = H.read(wf)["entries"][0]
    assert "client" not in entry["shot"]
    assert H.verify(wf, keys=k).assurance == "signed"


def test_a_qc_result_must_be_pass_or_fail():
    with pytest.raises(ValueError):
        H.stamp(load(), event="qc", actor=DOUG, keys=keys(), data_payload={"result": "maybe"})


def test_a_production_shot_without_qc_on_its_final_render_shows_a_gap():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k,
                 outputs=[{"file": "a.png", "sha256": "ab" * 32}]).workflow
    wf = H.stamp(wf, event="promoted", actor=DOUG, keys=k,
                 data_payload={"stage": "production"}).workflow
    assert any("quality check" in g for g in H.lineage(H.read(wf))["gaps"])
    wf = H.stamp(wf, event="qc", actor=DOUG, keys=k,
                 data_payload={"result": "pass", "checks": "4K, no artefacts"}).workflow
    lin = H.lineage(H.read(wf))
    assert lin["qc"]["result"] == "pass" and lin["qc"]["covers_final"]
    assert not any("quality check" in g for g in lin["gaps"])
    # A new render after QC is not covered by it.
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k,
                 outputs=[{"file": "b.png", "sha256": "cd" * 32}]).workflow
    lin = H.lineage(H.read(wf))
    assert not lin["qc"]["covers_final"]
    assert any("quality check" in g for g in lin["gaps"])


def test_a_failed_qc_is_a_gap_with_what_failed():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="promoted", actor=DOUG, keys=k,
                 data_payload={"stage": "production"}).workflow
    wf = H.stamp(wf, event="qc", actor=DOUG, keys=k, note="banding in the sky",
                 data_payload={"result": "fail"}).workflow
    assert any("failed QC" in g and "banding" in g for g in H.lineage(H.read(wf))["gaps"])


def test_asset_ids_must_be_text():
    with pytest.raises(ValueError):
        H.stamp(load(), event="approved", actor=DOUG, keys=keys(),
                data_payload={"output_asset_ids": {"a.png": 7}})


def test_sign_off_maps_each_output_to_its_studio_asset_id():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k, data_payload={"prompt_id": "job-9"},
                 outputs=[{"file": "a.exr", "sha256": "ab" * 32},
                          {"file": "b.exr", "sha256": "cd" * 32}]).workflow
    wf = H.stamp(wf, event="approved", actor=DOUG, keys=k, data_payload={
        "final_asset_id": "ATL_0120_comp_v003", "output_asset_ids": {"a.exr": "AST-1"}}).workflow
    f = H.final_render(H.read(wf))
    assert f["job_id"] == "job-9" and f["final_asset_id"] == "ATL_0120_comp_v003"
    assert [o.get("asset_id") for o in f["outputs"]] == ["AST-1", None]
    html = history_html.render(H.read(wf), data=wf, keys=k)
    assert "What shipped" in html and "ATL_0120_comp_v003" in html and "job-9" in html


# -- delivery ---------------------------------------------------------------------

@pytest.fixture
def studio(monkeypatch):
    cfg = {"user": "Priya Rao", "role": "VFX supervisor", "auto": True, "ledger": "",
           "key_file": "", "confidentiality": "none", "distribute_in": [], "territory": "",
           "likeness": False, "clients": []}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    return cfg


def test_delivery_records_qc_and_asset_ids_and_indexes_them(studio):
    k = H.Keys.from_environment()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0120",
                 client="Northlight Pictures").workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k, data_payload={"prompt_id": "job-77"},
                 outputs=[{"file": "a.png", "sha256": "ab" * 32}]).workflow
    checked = delivery.check({"workflow": wf, "name": "SH0120"})
    assert checked["final_outputs"] == [{"file": "a.png", "sha256": "ab" * 32}]
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "exception": "test",
                            "qc": {"result": "pass", "checks": "res, artefacts"},
                            "final_asset_id": "ATL_0120_v1",
                            "output_asset_ids": {"a.png": "AST-1"}})
    assert "error" not in out, out
    hist = H.read(out["workflow"])
    assert [e["event"] for e in hist["entries"]][-3:] == ["qc", "approved", "promoted"]
    assert H.verify(out["workflow"], keys=k).assurance == "signed"
    zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(out["zip_b64"])))
    index = json.loads(zf.read("SH0120.package.json"))
    fr = index["final_render"]
    assert fr["job_id"] == "job-77" and fr["final_asset_id"] == "ATL_0120_v1"
    assert fr["outputs"][0]["asset_id"] == "AST-1" and fr["qc"]["result"] == "pass"
    assert index["shot"]["client"] == "Northlight Pictures"


def test_delivery_without_qc_records_none_and_leaves_the_gap(studio):
    k = H.Keys.from_environment()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0120").workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k).workflow
    out = delivery.deliver({"workflow": wf, "name": "SH0120", "exception": "test"})
    hist = H.read(out["workflow"])
    assert "qc" not in [e["event"] for e in hist["entries"]]
    assert any("quality check" in g for g in H.lineage(hist)["gaps"])
