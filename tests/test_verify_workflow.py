"""Checking a render against a saved workflow, for when its provenance file is lost."""

from __future__ import annotations

import copy
import json
import os

import pytest

from comfyaudit.core import history as H
from comfyaudit.core import history_cli

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
NOKEY = H.Keys()
KEY = H.Keys(sign_key=b"studio-secret", key_id="studio")


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def save(tmp_path, wf, name="shot.json"):
    path = tmp_path / name
    path.write_text(json.dumps(wf), encoding="utf-8")
    return str(path)


@pytest.fixture
def shot(tmp_path):
    frame = tmp_path / "beauty_0001.png"
    frame.write_bytes(b"the pixels that were rendered")
    wf = H.stamp(load(), event="created", actor=ANA, keys=KEY, name="SH0120",
                 external_ids={"shotgrid": "4411"}).workflow
    before_render = copy.deepcopy(wf)
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=KEY, outputs=[str(frame)]).workflow
    return frame, wf, before_render


def test_a_render_matches_the_saved_workflow_without_its_provenance_file(shot, tmp_path, capsys):
    frame, wf, _ = shot
    assert not os.path.exists(str(frame) + ".provenance.json")
    assert history_cli.verify_file(str(frame), keys=KEY, workflow=save(tmp_path, wf)) == 0
    out = capsys.readouterr().out
    assert "MATCHES: beauty_0001.png is the output of entry 2" in out and "shotgrid 4411" in out


def test_it_matches_by_contents_not_by_name(shot, tmp_path):
    frame, wf, _ = shot
    renamed = tmp_path / "final_delivery.png"
    renamed.write_bytes(frame.read_bytes())
    assert history_cli.verify_file(str(renamed), keys=KEY, workflow=save(tmp_path, wf)) == 0
    frame.write_bytes(b"retouched afterwards")
    assert history_cli.verify_file(str(frame), keys=KEY, workflow=save(tmp_path, wf)) == history_cli.EXIT_ALTERED


def test_a_workflow_saved_before_the_render_says_so(shot, tmp_path, capsys):
    frame, _, before_render = shot
    code = history_cli.verify_file(str(frame), keys=KEY, workflow=save(tmp_path, before_render))
    assert code == history_cli.EXIT_ALTERED
    assert "not saved after this render" in capsys.readouterr().out


def test_an_altered_history_does_not_vouch_for_anything(shot, tmp_path):
    frame, wf, _ = shot
    forged = copy.deepcopy(wf)
    forged["extra"][H.EXTRA_KEY]["entries"][0]["actor"]["name"] = "Someone Else"
    assert history_cli.verify_file(str(frame), keys=KEY, workflow=save(tmp_path, forged)) == history_cli.EXIT_ALTERED


def test_an_unsigned_record_does_not_count_when_the_studio_signs(tmp_path):
    frame = tmp_path / "beauty_0001.png"
    frame.write_bytes(b"pixels")
    wf = H.stamp(load(), event="created", actor=ANA, keys=NOKEY).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=NOKEY, outputs=[str(frame)]).workflow
    assert history_cli.verify_file(str(frame), keys=NOKEY, workflow=save(tmp_path, wf)) == 0
    assert history_cli.verify_file(str(frame), keys=KEY, workflow=save(tmp_path, wf)) == history_cli.EXIT_ALTERED


def test_a_render_made_before_a_save_as_is_found_in_the_parent(shot, tmp_path, capsys):
    frame, wf, _ = shot
    child = H.fork(wf, reason="Save As", actor=ANA, keys=KEY, path="v002.json",
                   parent_file="v001.json").workflow
    assert history_cli.verify_file(str(frame), keys=KEY, workflow=save(tmp_path, child)) == 0
    assert "forked from" in capsys.readouterr().out


def test_the_command_line_takes_the_workflow(tmp_path, monkeypatch, capsys):
    from comfyaudit.core.cli import main
    for var in ("COMFYAUDIT_HISTORY_KEY", "COMFYAUDIT_HISTORY_KEY_FILE", "COMFYAUDIT_HISTORY_KEYRING"):
        monkeypatch.delenv(var, raising=False)
    frame = tmp_path / "beauty_0001.png"
    frame.write_bytes(b"pixels")
    wf = H.stamp(load(), event="created", actor=ANA, keys=NOKEY).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=NOKEY, outputs=[str(frame)]).workflow
    path = save(tmp_path, wf)
    assert main(["history", "verify-file", str(frame), "--workflow", path]) == 0
    assert "MATCHES" in capsys.readouterr().out
    # Without --workflow it still looks for the provenance file, and says it is missing.
    assert main(["history", "verify-file", str(frame)]) == 2
