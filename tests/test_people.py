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


# -- adversarial review of per-person keys -----------------------------------

def server_settings(monkeypatch):
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": {
        "user": "", "role": "", "ledger": "", "key_file": "",
        "confidentiality": "none", "sidecars": False})


def resign(entry, key):
    """Rewrite an entry as its key holder could: new hash, valid signature."""
    import hashlib
    import hmac
    entry["hash"] = H._entry_hash(entry)  # noqa: SLF001
    entry["sig"] = hmac.new(key, entry["hash"].encode("ascii"), hashlib.sha256).hexdigest()


def test_studio_signatures_are_not_vouched_for_by_a_keyring_alone(keyring, monkeypatch):
    # A per-person server need not hold the studio key. Its signatures must
    # then go unchecked, not be waved through - or junk ones read as Signed.
    wf = H.stamp(load(), event="approved", actor=H.Actor(name="Doug Hogan"),
                 keys=H.Keys.from_environment()).workflow
    monkeypatch.delenv("COMFYAUDIT_HISTORY_KEY")
    k = H.Keys.from_environment()
    assert not k.sign_key and k.people
    v = H.verify(wf, keys=k)
    assert v.assurance == "consistency" and v.signatures_valid is False
    forged = copy.deepcopy(wf)
    forged["extra"]["comfyaudit_history"]["entries"][0]["sig"] = "0" * 64
    assert H.verify(forged, keys=k).assurance == "consistency"


def test_per_person_server_extends_a_history_signed_with_the_studio_key(keyring, monkeypatch):
    # Studios switch to personal keys part way through a show. The studio-key
    # entries before the switch are not damage, and must not be checked
    # against the signed-in person's key.
    wf = H.stamp(load(), event="created", actor=H.Actor(name="Ana Ruiz"),
                 keys=H.Keys.from_environment()).workflow
    server_settings(monkeypatch)
    monkeypatch.setenv("COMFYAUDIT_TRUSTED_USER_HEADER", "X-Forwarded-User")
    out = history_service.stamp({"workflow": wf, "event": "approved", "note": "ok",
                                 "_signed_in_user": "doug"})
    v = H.verify(out["workflow"], keys=H.Keys.from_environment())
    assert v.assurance == "signed", v.problems
    assert v.signers == {"Doug Hogan": [2]}
    # Mixed keys: the badge must not claim every entry used one kind of key.
    assert "signed with the studio key or a person's own key" in v.assurance_detail
    assert "every entry signed with personal keys" not in v.assurance_detail
    assert H.SIGNATURE_SCOPE not in v.assurance_detail


def test_a_malformed_personal_entry_is_damage_not_a_crash(keyring):
    k = H.Keys.from_environment()
    ana = k.for_person("ana")
    wf = H.stamp(load(), event="created", actor=actor(k, "ana"), keys=ana).workflow
    for bad_actor in ("Ana Ruiz", None, ["Ana Ruiz"]):
        forged = copy.deepcopy(wf)
        e = forged["extra"]["comfyaudit_history"]["entries"][0]
        e["actor"] = bad_actor
        resign(e, ana.sign_key)
        v = H.verify(forged, keys=k)
        assert v.assurance == "none"
    for bad_sig in ("é" * 64, 7, None):
        forged = copy.deepcopy(wf)
        forged["extra"]["comfyaudit_history"]["entries"][0]["sig"] = bad_sig
        assert H.verify(forged, keys=k).assurance == "none"


def test_the_signed_in_user_id_must_match_the_key(keyring, monkeypatch):
    # Ana's key, Ana's display name, but the entry says it was Doug's account.
    k = H.Keys.from_environment()
    ana = k.for_person("ana")
    wf = H.stamp(load(), event="approved",
                 actor=H.Actor(name="Ana Ruiz", id="doug", declared_by="studio sign-in"),
                 keys=ana).workflow
    v = H.verify(wf, keys=k)
    assert v.assurance == "none"
    assert any("ana" in p and "doug" in p for p in v.problems)


def test_renaming_a_person_in_the_keyring_does_not_make_their_history_altered(
        keyring, monkeypatch):
    server_settings(monkeypatch)
    monkeypatch.setenv("COMFYAUDIT_TRUSTED_USER_HEADER", "X-Forwarded-User")
    out = history_service.stamp({"workflow": load(), "event": "created",
                                 "_signed_in_user": "ana"})
    ring = json.loads(keyring.read_text())
    ring["people"]["ana"]["name"] = "Ana Ruiz-Ortega"
    keyring.write_text(json.dumps(ring))
    v = H.verify(out["workflow"], keys=H.Keys.from_environment())
    assert v.assurance == "signed", v.problems
    assert v.signers == {"Ana Ruiz-Ortega": [1]}
    assert any("Ana Ruiz" in n and "Ana Ruiz-Ortega" in n for n in v.notes)


def test_a_removed_person_leaves_their_entries_unverified_not_signed(keyring, monkeypatch):
    server_settings(monkeypatch)
    monkeypatch.setenv("COMFYAUDIT_TRUSTED_USER_HEADER", "X-Forwarded-User")
    out = history_service.stamp({"workflow": load(), "event": "created",
                                 "_signed_in_user": "ana"})
    out = history_service.stamp({"workflow": out["workflow"], "event": "approved",
                                 "note": "ok", "_signed_in_user": "doug"})
    ring = json.loads(keyring.read_text())
    del ring["people"]["ana"]
    keyring.write_text(json.dumps(ring))
    v = H.verify(out["workflow"], keys=H.Keys.from_environment())
    assert v.assurance == "consistency" and v.signatures_valid is False
    assert "studio key" not in v.assurance_detail
    # Doug's entry still checks out as his.
    assert v.signers == {"Doug Hogan": [2]}


class Headers:
    def __init__(self, pairs):
        self.pairs = pairs

    def getall(self, name, default=None):
        got = [v for k, v in self.pairs if k.lower() == name.lower()]
        return got or (default if default is not None else [])


def test_the_sign_in_header_is_read_once_and_never_doubled(monkeypatch):
    monkeypatch.setenv("COMFYAUDIT_TRUSTED_USER_HEADER", "X-Forwarded-User")
    assert history_service.signed_in_user(Headers([("X-Forwarded-User", "doug")])) == "doug"
    assert history_service.signed_in_user(Headers([])) == ""
    # A client-sent value ahead of the proxy's would otherwise win.
    with pytest.raises(ValueError, match="more than once"):
        history_service.signed_in_user(Headers([("x-forwarded-user", "doug"),
                                                ("X-Forwarded-User", "ana")]))
    assert history_service.signed_in_user(Headers([("X-Forwarded-User", "  ana ")])) == "ana"
