"""CLI behaviour, including the pipeline gate."""

from __future__ import annotations

import json
import os

import pytest

from comfyaudit.core import cli

EXAMPLES = os.path.join(os.path.dirname(__file__), "fixtures")
BEAUTY = os.path.join(EXAMPLES, "beauty-pass.json")
CLEAN = os.path.join(EXAMPLES, "clean-batch.json")


def test_info_reports_the_bundled_knowledge(capsys):
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert "core node catalog" in out
    assert "licence knowledge" in out


def test_models_command_lists_licence_status(capsys):
    assert cli.main(["models", BEAUTY]) == 0
    out = capsys.readouterr().out
    assert "codeformer-v0.1.0.pth" in out
    assert "non-commercial" in out


def test_json_output_is_written_to_the_requested_file(tmp_path):
    target = tmp_path / "report.json"
    assert cli.main(["audit", CLEAN, "-f", "json", "-o", str(target), "--quiet"]) == 0
    payload = json.loads(target.read_text())
    assert payload["schema"] == "comfyaudit/1"
    assert payload["summary"]["automation_index"] >= 85


def test_html_output_is_written(tmp_path):
    target = tmp_path / "report.html"
    assert cli.main(["audit", BEAUTY, "-f", "html", "-o", str(target), "--quiet"]) == 0
    assert target.read_text().startswith("<!doctype html>")


def test_fail_on_gates_on_operational_findings():
    """The gate fires on findings, which are now operational rather than policy."""
    assert cli.main(["audit", BEAUTY, "--fail-on", "high", "--quiet",
                     "-f", "json", "-o", os.devnull]) == 1


def test_fail_on_critical_passes_a_clean_workflow():
    assert cli.main(["audit", CLEAN, "--fail-on", "critical", "--quiet",
                     "-f", "json", "-o", os.devnull]) == 0


def test_multiple_workflows_write_one_report_each(tmp_path):
    out_dir = tmp_path / "audits"
    assert cli.main(["audit", BEAUTY, CLEAN, "-o", str(out_dir), "--quiet"]) == 0
    written = sorted(p.name for p in out_dir.iterdir())
    assert written == ["beauty-pass.audit.md", "clean-batch.audit.md"]


def test_a_directory_argument_is_expanded(tmp_path):
    workflows = [name for name in os.listdir(EXAMPLES) if name.endswith(".json")]
    out_dir = tmp_path / "audits"
    assert cli.main(["audit", EXAMPLES, "-o", str(out_dir), "--quiet"]) == 0
    assert len(list(out_dir.iterdir())) == len(workflows)


def test_an_unreadable_workflow_reports_and_exits_non_zero(tmp_path, capsys):
    bad = tmp_path / "broken.json"
    bad.write_text('{"totally": "not a workflow"}')
    assert cli.main(["audit", str(bad), "--quiet"]) == 2
    assert "unrecognised workflow" in capsys.readouterr().err


def test_one_bad_file_does_not_stop_the_others(tmp_path, capsys):
    bad = tmp_path / "broken.json"
    bad.write_text("{}")
    out_dir = tmp_path / "audits"
    code = cli.main(["audit", CLEAN, str(bad), "-o", str(out_dir), "--quiet"])
    assert code == 2
    assert (out_dir / "clean-batch.audit.md").exists()
