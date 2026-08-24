"""Knowledge that knows how old it is.

The licence base is hand-curated and accurate as of a date. Two of the licences
in it changed within a year of being written down. A tool reporting last year's
terms with this year's confidence is worse than one admitting it does not know,
so the age has to be visible and the replacement path has to work.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

import pytest

from comfyaudit.core.knowledge import freshness
from comfyaudit.core.report import markdown as md_report

TODAY = _dt.date(2026, 8, 23)


def _meta(checked, version="1"):
    return {"version": version, "checked": checked}


# -- reading the age --------------------------------------------------------


def test_recent_knowledge_reads_as_current():
    result = freshness.assess(_meta("2026-08-01"), today=TODAY)
    assert result.state == "current"
    assert result.age_days == 22
    assert not result.worth_saying


def test_a_few_months_reads_as_ageing():
    result = freshness.assess(_meta("2026-03-01"), today=TODAY)
    assert result.state == "ageing"
    assert "update-knowledge" in result.message


def test_most_of_a_year_reads_as_old():
    result = freshness.assess(_meta("2025-09-01"), today=TODAY)
    assert result.state == "old"
    assert result.worth_saying
    assert "relicensed mid-flight" in result.message


def test_no_date_at_all_is_said_out_loud_rather_than_assumed_current():
    result = freshness.assess({"version": "1"}, today=TODAY)
    assert result.state == "unknown"
    assert result.age_days is None
    assert result.worth_saying


def test_a_clock_behind_the_file_does_not_produce_a_negative_age():
    result = freshness.assess(_meta("2026-12-01"), today=TODAY)
    assert result.age_days == 0
    assert result.state == "current"


# -- what the report says ---------------------------------------------------


def test_the_report_always_states_how_old_its_knowledge_is(beauty_report):
    text = md_report.render(beauty_report)
    assert "Licence knowledge checked" in text or "last checked" in text


def test_old_knowledge_is_said_near_the_top_not_only_in_the_appendix(beauty_report):
    beauty_report.freshness = freshness.assess(_meta("2024-01-01"), today=TODAY)
    text = md_report.render(beauty_report)
    assert "Check this at source" in text
    assert text.index("Check this at source") < text.index("## 1.")


@pytest.fixture()
def beauty_report():
    from comfyaudit.core.audit import run

    return run(os.path.join(os.path.dirname(__file__), "fixtures",
                            "beauty-pass.json"))


# -- fetching and installing ------------------------------------------------


def test_a_fetched_file_that_is_not_a_knowledge_base_is_refused(monkeypatch):
    """Whatever a URL returns, it is not automatically licence terms."""
    _serve(monkeypatch, json.dumps({"something": "else"}))
    with pytest.raises(ValueError, match="not a comfyaudit licence"):
        freshness.fetch("https://example.invalid/kb.json")


def test_an_empty_licence_table_is_refused(monkeypatch):
    _serve(monkeypatch, json.dumps({"licences": {}}))
    with pytest.raises(ValueError, match="no licence definitions"):
        freshness.fetch("https://example.invalid/kb.json")


def test_a_valid_file_comes_back_parsed(monkeypatch):
    _serve(monkeypatch, json.dumps({"version": "2", "licences": {"mit": {}}}))
    assert freshness.fetch("https://example.invalid/kb.json")["version"] == "2"


def test_installing_keeps_the_previous_file(tmp_path):
    """"The licence changed" and "the knowledge base changed" look identical."""
    path = tmp_path / "licences.json"
    path.write_text(json.dumps({"version": "1", "licences": {"mit": {}}}))

    freshness.install({"version": "2", "licences": {"mit": {}}}, str(path))
    assert json.loads(path.read_text())["version"] == "2"
    assert json.loads((tmp_path / "licences.json.previous").read_text())["version"] == "1"


def test_installing_into_a_new_location_needs_no_previous_file(tmp_path):
    target = tmp_path / "nested" / "licences.json"
    freshness.install({"version": "1", "licences": {"mit": {}}}, str(target))
    assert target.is_file()


# -- what changed -----------------------------------------------------------


def test_a_relicensed_entry_shows_up_as_a_field_that_moved():
    diff = freshness.compare(
        {"version": "1", "licences": {"x": {"name": "X", "commercial_use": "yes"}}},
        {"version": "2", "licences": {"x": {"name": "X", "commercial_use": "no"}}})
    assert diff["changed"][0]["fields"] == ["commercial_use"]
    assert diff["changed"][0]["was"]["commercial_use"] == "yes"
    assert diff["changed"][0]["now"]["commercial_use"] == "no"


def test_additions_and_removals_are_named():
    diff = freshness.compare({"licences": {"a": {}, "b": {}}},
                             {"licences": {"b": {}, "c": {}}})
    assert diff["added"] == ["c"]
    assert diff["removed"] == ["a"]


def test_an_unchanged_base_reports_nothing_moved():
    kb = {"version": "1", "licences": {"x": {"commercial_use": "yes"}}}
    diff = freshness.compare(kb, kb)
    assert not (diff["added"] or diff["removed"] or diff["changed"])


def _serve(monkeypatch, payload: str):
    """Stand in for urlopen without going near the network."""
    import contextlib
    import urllib.request

    class Response:
        def read(self):
            return payload.encode("utf-8")

    @contextlib.contextmanager
    def fake(request, timeout=None):
        yield Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
