"""The facility's decision record, and the question it lets a report answer.

The point of the registry is that the second audit of a workflow is a different
question from the first. These check that difference is real: that a settled
decision goes quiet, and that anything which moved underneath one does not.
"""

from __future__ import annotations

import json
import os

import pytest

from comfyaudit.core import registry as reg
from comfyaudit.core.audit import AuditOptions, run
from comfyaudit.core.records import LicenseInfo, ModelRef, PackRef
from comfyaudit.core.report import markdown as md_report

EXAMPLES = os.path.join(os.path.dirname(__file__), "fixtures")


def _model(name, licence="MIT", sha="", enabled=True):
    return ModelRef(filename=name, folder="checkpoints", enabled=enabled,
                    license=LicenseInfo(name=licence),
                    notes=[f"sha256:{sha}"] if sha else [])


# -- the file ---------------------------------------------------------------


def test_a_missing_registry_loads_empty_rather_than_failing(tmp_path):
    """A facility that has not started one yet is the normal case."""
    registry = reg.Registry.load(str(tmp_path / "nothing.json"))
    assert len(registry) == 0
    assert registry.check([_model("a.safetensors")]).new


def test_a_registry_survives_a_save_and_load(tmp_path):
    path = str(tmp_path / "reg.json")
    registry = reg.Registry()
    registry.record(reg.Entry(key="a.safetensors", decided_by="D. Hogan",
                              note="fine", reference="SHOW-1"))
    registry.save(path)

    again = reg.Registry.load(path)
    entry = again.get("a.safetensors")
    assert entry.decided_by == "D. Hogan"
    assert entry.reference == "SHOW-1"
    assert entry.decided_on  # stamped on record


def test_an_unknown_status_is_refused():
    """Statuses drive the report's behaviour, so a typo must not pass silently."""
    with pytest.raises(ValueError):
        reg.Registry().record(reg.Entry(key="a", status="probably fine"))


def test_a_field_from_a_future_version_is_ignored_not_fatal(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text(json.dumps({
        "schema": "comfyaudit/registry/1",
        "entries": [{"key": "a.safetensors", "kind": "model", "status": "approved",
                     "some_field_added_later": True}],
    }))
    assert reg.Registry.load(str(path)).get("a.safetensors") is not None


def test_keys_compare_the_way_a_filesystem_would():
    registry = reg.Registry([reg.Entry(key="BBox/Face_YOLOv8m.pt")])
    assert registry.get("bbox/face_yolov8m.pt") is not None
    assert registry.get("bbox\\face_yolov8m.pt") is not None


# -- what the check says ----------------------------------------------------


def test_a_cleared_model_goes_quiet():
    registry = reg.Registry([reg.Entry(key="a.safetensors", licence="MIT")])
    check = registry.check([_model("a.safetensors")])
    assert check.clean
    assert check.known and not check.new


def test_a_disabled_model_is_not_asked_about():
    registry = reg.Registry()
    assert registry.check([_model("a.safetensors", enabled=False)]).matches == []


def test_a_rejected_model_is_raised_every_time():
    registry = reg.Registry([reg.Entry(key="a.safetensors", status="rejected",
                                       note="non-commercial", decided_by="D. Hogan")])
    match = registry.check([_model("a.safetensors")]).rejected[0]
    assert "not to be used" in match.detail
    assert "non-commercial" in match.detail
    assert "D. Hogan" in match.detail


def test_a_pending_question_stays_open():
    registry = reg.Registry([reg.Entry(key="a.safetensors", status="pending",
                                       note="asked the vendor")])
    assert registry.check([_model("a.safetensors")]).pending


def test_a_relicensed_model_reopens_a_settled_decision():
    """A decision is about a licence, not about a filename."""
    registry = reg.Registry([reg.Entry(key="a.safetensors", licence="Apache-2.0")])
    match = registry.check([_model("a.safetensors", licence="FLUX.1 [dev] NC")]).changed[0]
    assert "Cleared under Apache-2.0" in match.detail


def test_different_weights_under_the_same_name_reopen_it_too():
    registry = reg.Registry([reg.Entry(key="a.safetensors", sha256="a" * 64)])
    match = registry.check([_model("a.safetensors", sha="b" * 64)]).changed[0]
    assert "Same name, different weights" in match.detail


def test_a_renamed_file_is_still_recognised_by_its_hash():
    """Renaming a weight is the fastest way to lose its provenance."""
    registry = reg.Registry([reg.Entry(key="original.safetensors", sha256="a" * 64)])
    check = registry.check([_model("someone_renamed_this.safetensors", sha="a" * 64)])
    assert check.clean, "the hash should have matched it"


def test_a_relicensed_node_pack_reopens_its_decision():
    registry = reg.Registry([reg.Entry(key="github.com/x/y", kind="pack",
                                       licence="MIT")])
    pack = PackRef(repo="github.com/x/y", title="Y", licence="AGPL-3.0",
                   identified=True)
    assert registry.check([], [pack]).changed


def test_things_needing_attention_sort_before_things_that_do_not():
    registry = reg.Registry([
        reg.Entry(key="cleared.safetensors"),
        reg.Entry(key="banned.safetensors", status="rejected"),
    ])
    check = registry.check([_model("cleared.safetensors"),
                            _model("banned.safetensors"),
                            _model("new.safetensors")])
    assert [m.state for m in check.matches] == ["rejected", "new", "known"]


# -- drafting entries from a report -----------------------------------------


def test_entries_are_drafted_from_a_report_but_never_written(tmp_path):
    """A registry that fills itself in is an expensive way of approving all."""
    report = run(os.path.join(EXAMPLES, "beauty-pass.json"))
    entries = reg.entries_from_report(report, decided_by="D. Hogan")
    assert entries
    assert all(e.decided_by == "D. Hogan" for e in entries)
    assert {e.kind for e in entries} == {"model", "pack"}
    # Nothing was saved anywhere.
    assert not list(tmp_path.iterdir())


def test_drafting_skips_what_is_already_settled(tmp_path):
    path = str(tmp_path / "reg.json")
    first = run(os.path.join(EXAMPLES, "beauty-pass.json"),
                AuditOptions(registry_path=path))
    registry = reg.Registry.load(path)
    for entry in reg.entries_from_report(first):
        registry.record(entry)
    registry.save(path)

    second = run(os.path.join(EXAMPLES, "beauty-pass.json"),
                 AuditOptions(registry_path=path))
    assert second.registry.clean
    assert reg.entries_from_report(second) == []
    # ...unless asked for everything.
    assert reg.entries_from_report(second, only_new=False)


# -- the report -------------------------------------------------------------


def test_the_report_leads_with_what_is_new(tmp_path):
    path = str(tmp_path / "reg.json")
    reg.Registry([reg.Entry(key="4x-UltraSharp.pth", status="rejected",
                            note="use RealESRGAN")]).save(path)

    report = run(os.path.join(EXAMPLES, "beauty-pass.json"),
                 AuditOptions(registry_path=path))
    text = md_report.render(report)
    assert "## 1. New since last cleared" in text
    assert "## 2. Licence summary" in text
    assert "use RealESRGAN" in text


def test_without_a_registry_nothing_about_it_appears(tmp_path):
    report = run(os.path.join(EXAMPLES, "beauty-pass.json"))
    assert report.registry.loaded is False
    assert "New since last cleared" not in md_report.render(report)


def test_an_unreadable_registry_costs_a_note_not_the_report(tmp_path):
    """The rest of the audit is still worth reading."""
    path = tmp_path / "reg.json"
    path.write_text("[not, a, registry, object]")

    report = run(os.path.join(EXAMPLES, "beauty-pass.json"),
                 AuditOptions(registry_path=str(path)))
    assert report.diagnostics["registry_error"]
    assert report.models  # the audit itself completed
