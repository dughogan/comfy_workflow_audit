"""Per-person signing: each entry signed with the signed-in person's own key."""

from __future__ import annotations

import copy
import json
import os

import pytest

from comfyaudit.core import history as H
from comfyaudit.server import history_service
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def keyring(monkeypatch, tmp_path):
    secret = tmp_path / "doug.key"
    secret.write_text("doug-secret")
    ring = tmp_path / "keyring.json"
    ring.write_text(json.dumps({"people": {
        "ana": {"name": "Ana Ruiz", "role": "compositor", "secret": "ana-secret"},
        "doug": {"name": "Doug Hogan", "role": "VFX supervisor", "secret_file": str(secret)},
    }}))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEYRING", str(ring))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    return ring


def actor(keys, user):
    info = keys.people_names[keys.for_person(user).key_id]
    return H.Actor(name=info["name"], role=info["role"], declared_by="studio sign-in")


def test_each_entry_is_signed_with_its_persons_key(keyring):
    k = H.Keys.from_environment()
    ana, doug = k.for_person("ana"), k.for_person("doug")
    assert ana.key_id.startswith("person:ana:") and ana.sign_key != doug.sign_key
    wf = H.stamp(load(), event="created", actor=actor(k, "ana"), keys=ana).workflow
    wf = H.stamp(wf, event="approved", actor=actor(k, "doug"), keys=doug, note="ok").workflow
    v = H.verify(wf, keys=k)
    assert v.assurance == "signed", v.problems
    assert v.signers == {"Ana Ruiz": [1], "Doug Hogan": [2]}
    assert "personal key" in v.assurance_detail


def test_a_name_that_does_not_match_the_signing_key_is_caught(keyring):
    k = H.Keys.from_environment()
    # Ana's key, but the entry claims Doug approved it.
    wf = H.stamp(load(), event="approved", actor=actor(k, "doug"), keys=k.for_person("ana")).workflow
    v = H.verify(wf, keys=k)
    assert v.assurance == "none"
    assert any("signed with Ana Ruiz's key" in p for p in v.problems)


def test_without_the_keyring_personal_signatures_are_not_vouched_for(keyring, monkeypatch):
    k = H.Keys.from_environment()
    wf = H.stamp(load(), event="created", actor=actor(k, "ana"), keys=k.for_person("ana")).workflow
    monkeypatch.delenv("COMFYAUDIT_HISTORY_KEYRING")
    v = H.verify(wf, keys=H.Keys.from_environment())
    assert v.assurance == "consistency" and v.signatures_valid is False
    # And another person's forged key does not verify as theirs.
    forged = copy.deepcopy(wf)
    entry = forged["extra"]["comfyaudit_history"]["entries"][0]
    entry["sig"] = "0" * 64
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEYRING", str(keyring))
    assert H.verify(forged, keys=H.Keys.from_environment()).assurance == "none"


def test_the_server_signs_as_the_signed_in_person_and_refuses_anyone_else(keyring, monkeypatch):
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": {
        "user": "whatever I typed", "role": "", "ledger": "", "key_file": "",
        "confidentiality": "none", "sidecars": False})
    monkeypatch.setenv("COMFYAUDIT_TRUSTED_USER_HEADER", "X-Forwarded-User")
    out = history_service.stamp({"workflow": load(), "event": "created", "_signed_in_user": "doug"})
    entry = out["history"]["entries"][0]
    assert entry["actor"]["name"] == "Doug Hogan" and entry["actor"]["declared_by"] == "studio sign-in"
    assert entry["key_id"].startswith("person:doug:")
    with pytest.raises(ValueError, match="No signed-in user"):
        history_service.stamp({"workflow": load(), "event": "created"})
    with pytest.raises(ValueError, match="no key in the studio keyring"):
        history_service.stamp({"workflow": load(), "event": "created", "_signed_in_user": "mallory"})
