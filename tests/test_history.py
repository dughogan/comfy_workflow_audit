"""Shot history: the chain, its tamper evidence, lineage, and the documents."""

from __future__ import annotations

import copy
import json
import os

import pytest

from comfyaudit.core import audit, history as H
from comfyaudit.core.report import compliance, history_html, licensing_guide
from comfyaudit.core.report import html as html_report
from comfyaudit.core.report import markdown as md_report

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
ANA = H.Actor(name="Ana Ruiz", role="compositor")
DOUG = H.Actor(name="Doug Hogan", role="VFX supervisor")
LEE = H.Actor(name="Lee Park", role="legal")


def load(name="beauty-pass.json"):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def keys(secret="studio-secret", mode="none"):
    k = H.Keys(confidentiality=mode)
    if secret:
        k.sign_key = H._derive(secret, b"comfyaudit-sign")
        k.key_id = "test"
    return k


def first_text_node(wf, node_type="CLIPTextEncode"):
    return next(n for n in wf["nodes"] if n["type"] == node_type)


def lifetime(k=None, ledger=""):
    """A realistic short life: create, revise, declare, render, review, promote, approve."""
    k = k or keys()
    wf = load()
    r = H.stamp(wf, event="created", actor=ANA, keys=k, name="SH0120", project="Atlas",
                external_ids={"shotgrid": "Shot#4411"}, ledger=ledger)
    wf = r.workflow
    first_text_node(wf)["widgets_values"][0] += ", soft rim light"
    wf = H.stamp(wf, actor=ANA, keys=k, ledger=ledger).workflow
    src = H.lineage(H.read(wf))["sources"][0]["node"]
    wf = H.stamp(wf, event="sourced", actor=ANA, keys=k, ledger=ledger,
                 data_payload={"node": src, "origin": "photographed",
                               "asset_id": "PLATE-0120-A"}).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k, ledger=ledger,
                 outputs=[{"file": "beauty_0001.png", "sha256": "ab" * 32}]).workflow
    wf = H.stamp(wf, event="reviewed", actor=DOUG, keys=k, ledger=ledger,
                 note="skin too waxy, keep the grain").workflow
    wf = H.stamp(wf, event="promoted", actor=DOUG, keys=k, ledger=ledger,
                 data_payload={"stage": "production"}).workflow
    wf = H.stamp(wf, event="approved", actor=LEE, keys=k, ledger=ledger,
                 note="cleared SHOW-114").workflow
    return wf


# -- recording ---------------------------------------------------------------

def test_history_lives_inside_the_workflow_and_leaves_the_graph_untouched():
    wf = load()
    out = H.stamp(wf, event="created", actor=ANA, keys=keys()).workflow
    assert H.EXTRA_KEY in out["extra"]
    assert out["nodes"] == wf["nodes"] and out["links"] == wf["links"]
    assert H.EXTRA_KEY not in (wf.get("extra") or {}), "stamp must not mutate its input"


def test_an_unchanged_save_adds_nothing_but_human_events_always_record():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    assert H.stamp(wf, actor=ANA, keys=keys()).entry is None
    assert H.stamp(wf, event="reviewed", actor=DOUG, keys=keys()).entry is not None


def test_moving_nodes_around_is_not_an_edit():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    for n in wf["nodes"]:
        n["pos"] = [n["pos"][0] + 400, n["pos"][1] - 90]
    assert H.stamp(wf, actor=ANA, keys=keys()).entry is None


def test_first_entry_on_an_existing_graph_says_tracking_began_there():
    entry = H.stamp(load(), actor=ANA, keys=keys()).entry
    assert entry["event"] == "imported"
    lin = H.lineage(H.read(H.stamp(load(), actor=ANA, keys=keys()).workflow))
    assert any("Tracking began" in g for g in lin["gaps"])


def test_a_prompt_revision_is_a_human_break_point():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    first_text_node(wf)["widgets_values"][0] = "a different idea entirely"
    entry = H.stamp(wf, actor=ANA, keys=keys()).entry
    assert entry["changes"]["prompts_changed"]
    assert "prompt" in entry["breakpoints"]


def test_model_swaps_are_tracked_across_the_life():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    loader = next(n for n in wf["nodes"] if n["type"] == "CheckpointLoaderSimple")
    old = loader["widgets_values"][0]
    loader["widgets_values"][0] = "sd_xl_base_1.0.safetensors"
    result = H.stamp(wf, actor=DOUG, keys=keys())
    assert result.entry["changes"]["models_added"] == ["sd_xl_base_1.0.safetensors"]
    assert result.entry["changes"]["models_removed"] == [old]
    assert "model-choice" in result.entry["breakpoints"]

    swapped = {m["filename"]: m for m in H.lineage(result.history)["models"]}
    assert swapped[old]["removed"] == 2 and swapped[old]["removed_by"] == "Doug Hogan"
    assert not swapped[old]["in_final"]
    assert swapped["sd_xl_base_1.0.safetensors"]["introduced_by"] == "Doug Hogan"
    assert swapped["sd_xl_base_1.0.safetensors"]["in_final"]


def test_shot_identity_and_studio_ids_are_inside_the_signed_entries():
    wf = lifetime()
    entries = H.read(wf)["entries"]
    assert all(e["shot"]["external_ids"] == {"shotgrid": "Shot#4411"} for e in entries)
    forged = copy.deepcopy(wf)
    forged["extra"][H.EXTRA_KEY]["entries"][0]["shot"]["external_ids"] = {"shotgrid": "Shot#9999"}
    assert H.verify(forged, keys=keys()).assurance == "none"


def test_unchanged_states_are_stored_once():
    hist = H.read(lifetime())
    by_event = {e["event"]: e for e in hist["entries"]}
    assert "snapshot" not in by_event["reviewed"]
    assert by_event["reviewed"]["snapshot_ref"] == by_event["sourced"]["snapshot_ref"] \
        or by_event["reviewed"]["snapshot_ref"] <= by_event["reviewed"]["seq"]
    assert H.snapshot_of(hist["entries"], by_event["approved"])["models"]


# -- verification -----------------------------------------------------------

def test_a_signed_untouched_history_verifies():
    v = H.verify(lifetime(), keys=keys())
    assert v.intact and v.signed == "yes" and v.signatures_valid
    assert v.assurance == "signed" and v.current


def test_editing_an_entry_is_caught_at_that_entry():
    wf = lifetime()
    wf["extra"][H.EXTRA_KEY]["entries"][2]["actor"]["name"] = "Someone Else"
    v = H.verify(wf, keys=keys())
    assert v.assurance == "none"
    assert any(p.startswith("entry 3:") for p in v.problems)


def test_rewriting_the_chain_without_the_key_fails_the_signature():
    wf = lifetime()
    hist = wf["extra"][H.EXTRA_KEY]
    hist["entries"][4]["note"] = "looks great, ship it"
    # A forger who recomputes every hash still cannot produce the signatures.
    prev = hist["entries"][3]["hash"]
    for e in hist["entries"][4:]:
        e["prev"] = prev
        e["hash"] = H._entry_hash(e)
        prev = e["hash"]
    v = H.verify(wf, keys=keys())
    assert v.signatures_valid is False and v.assurance == "none"


def test_removing_or_reordering_entries_breaks_the_chain():
    wf = lifetime()
    del wf["extra"][H.EXTRA_KEY]["entries"][3]
    assert H.verify(wf, keys=keys()).assurance == "none"
    wf = lifetime()
    e = wf["extra"][H.EXTRA_KEY]["entries"]
    e[4], e[5] = e[5], e[4]
    assert H.verify(wf, keys=keys()).assurance == "none"


def test_a_wrong_key_does_not_verify():
    v = H.verify(lifetime(), keys=keys("another-studio"))
    assert v.signatures_valid is False


def test_without_a_key_the_record_says_it_is_only_consistent():
    wf = lifetime(k=keys(secret=""))
    v = H.verify(wf, keys=keys(secret=""))
    assert v.signed == "no" and v.assurance == "consistency"
    assert "not signed" in v.assurance_detail


def test_edits_after_the_last_entry_are_flagged():
    wf = lifetime()
    first_text_node(wf)["widgets_values"][0] = "changed after sign-off"
    v = H.verify(wf, keys=keys())
    assert v.current is False
    # Worth saying, but the record itself is undamaged.
    assert any("changed since the last entry" in n for n in v.notes)
    assert v.assurance == "signed"


def test_the_ledger_catches_a_truncated_or_replaced_history(tmp_path):
    ledger = str(tmp_path / "ledger.jsonl")
    wf = lifetime(ledger=ledger)
    assert H.verify(wf, keys=keys(), ledger=ledger).assurance == "witnessed"

    cut = copy.deepcopy(wf)
    del cut["extra"][H.EXTRA_KEY]["entries"][-1]
    v = H.verify(cut, keys=keys(), ledger=ledger)
    assert v.ledger == "missing entries" and v.assurance == "none"

    # A whole, internally consistent replacement history for the same shot,
    # made by someone holding the key: the ledger remembers the original.
    forged = H.empty(name="SH0120")
    forged["shot"]["id"] = H.read(wf)["shot"]["id"]
    fresh = H.stamp(load(), event="created", actor=ANA, keys=keys(), history=forged).workflow
    v = H.verify(fresh, keys=keys(), ledger=ledger)
    assert v.ledger == "mismatch" and v.assurance == "none"

    # Swapping only the editable top-level id does not dodge the ledger.
    dodge = copy.deepcopy(cut)
    dodge["extra"][H.EXTRA_KEY]["shot"]["id"] = "something-else"
    v = H.verify(dodge, keys=keys(), ledger=ledger)
    assert v.assurance == "none" and v.ledger == "missing entries"


def test_witnessed_needs_every_entry_in_the_ledger(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    wf = lifetime(ledger=str(ledger))
    rows = ledger.read_text().splitlines()
    ledger.write_text("\n".join(rows[:2]) + "\n")
    v = H.verify(wf, keys=keys(), ledger=str(ledger))
    assert v.assurance == "signed" and v.ledger == "incomplete"


def test_a_ledger_that_saw_two_versions_of_an_entry_means_a_rewrite(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    wf = lifetime(ledger=str(ledger))
    row = json.loads(ledger.read_text().splitlines()[3])
    row["hash"] = "0" * 64
    with open(ledger, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    v = H.verify(wf, keys=keys(), ledger=str(ledger))
    assert v.ledger == "rewritten" and v.assurance == "none"


def test_a_malformed_ledger_row_does_not_crash_verification(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    wf = lifetime(ledger=str(ledger))
    with open(ledger, "a") as fh:
        fh.write(json.dumps({"shot": H.chain_shot_id(H.read(wf)), "seq": "nine",
                             "hash": "x"}) + "\n")
    assert H.verify(wf, keys=keys(), ledger=str(ledger)).assurance == "witnessed"


def test_an_unsigned_entry_slipped_into_a_signed_history_is_caught():
    wf = lifetime()
    hist = copy.deepcopy(H.read(wf))
    H.append(hist, wf, event="approved", actor=H.Actor(name="Studio Counsel", role="legal"),
             keys=H.Keys())
    forged = H.write(wf, hist)
    v = H.verify(forged, keys=keys())
    assert v.assurance == "none" and v.unsigned_entries == [8]

    stripped = lifetime()
    del stripped["extra"][H.EXTRA_KEY]["entries"][2]["sig"]
    assert H.verify(stripped, keys=keys()).assurance == "none"


def test_forged_top_level_shot_details_are_caught():
    wf = lifetime()
    wf["extra"][H.EXTRA_KEY]["shot"]["external_ids"] = {"shotgrid": "Shot#FAKE"}
    v = H.verify(wf, keys=keys())
    assert v.assurance == "none"
    assert H.lineage(H.read(wf))["shot"]["external_ids"] == {"shotgrid": "Shot#4411"}


def test_no_history_is_absent_not_altered():
    v = H.verify(load(), keys=keys())
    assert v.assurance == "absent" and H.ASSURANCE_LABELS[v.assurance] == "No history"


def test_stamping_refuses_to_build_on_a_damaged_history():
    wf = lifetime()
    wf["extra"][H.EXTRA_KEY]["entries"][2]["note"] = "rewritten"
    with pytest.raises(ValueError, match="damaged"):
        H.stamp(wf, event="reviewed", actor=DOUG, keys=keys())


# -- confidentiality ----------------------------------------------------------

def test_redacted_prompts_never_appear_in_the_file_but_stay_provable():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys(mode="redact")).workflow
    text = first_text_node(wf)["widgets_values"][0]
    blob = json.dumps(wf["extra"][H.EXTRA_KEY])
    assert text not in blob
    # Keyed, so a guessed prompt cannot be confirmed by hashing it.
    assert H.sha256(text) not in blob
    assert H.fingerprint(text, keys(mode="redact")) in blob


def test_confidentiality_never_drops_below_what_the_environment_asks(monkeypatch):
    monkeypatch.setenv("COMFYAUDIT_HISTORY_ENC_KEY", "enc")
    assert H.Keys.from_environment(confidentiality="none").confidentiality == "encrypt"
    monkeypatch.delenv("COMFYAUDIT_HISTORY_ENC_KEY")
    monkeypatch.setenv("COMFYAUDIT_HISTORY_CONFIDENTIALITY", "redact")
    assert H.Keys.from_environment(confidentiality="none").confidentiality == "redact"
    assert H.Keys.from_environment(confidentiality="encrypt").confidentiality == "encrypt"


def test_prompt_only_lora_names_and_output_names_are_protected():
    wf = load()
    first_text_node(wf)["widgets_values"][0] += " <lora:CLIENT_SECRET_LOOK:0.7>"
    k = keys(mode="redact")
    wf = H.stamp(wf, event="created", actor=ANA, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=k,
                 outputs=[{"file": "ACME_launch_hero.png"}]).workflow
    blob = json.dumps(wf["extra"][H.EXTRA_KEY])
    assert "CLIENT_SECRET_LOOK" not in blob and "ACME_launch" not in blob


def test_encryption_round_trips_with_the_key():
    pytest.importorskip("cryptography")
    k = keys(mode="encrypt")
    k.enc_key = H._derive("enc-secret", b"comfyaudit-enc")
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    text = first_text_node(wf)["widgets_values"][0]
    assert text not in json.dumps(wf["extra"][H.EXTRA_KEY])
    rows = H.timeline(H.read(wf), keys=k, reveal=True)
    assert text in [p["text"] for p in rows[0]["prompts"]]
    wrong = keys(mode="encrypt")
    wrong.enc_key = H._derive("nope", b"comfyaudit-enc")
    assert all(p["text"] is None for p in H.timeline(H.read(wf), keys=wrong, reveal=True)[0]["prompts"])


def test_encryption_without_the_library_fails_closed(monkeypatch):
    monkeypatch.setattr(H, "encryption_available", lambda: False)
    k = keys(mode="encrypt")
    k.enc_key = b"x" * 32
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    text = first_text_node(wf)["widgets_values"][0]
    assert text not in json.dumps(wf["extra"][H.EXTRA_KEY])


# -- lineage -----------------------------------------------------------------

def test_lineage_tells_the_story_legal_asks_for():
    lin = H.lineage(H.read(lifetime()))
    assert [p["name"] for p in lin["people"]] == ["Ana Ruiz", "Doug Hogan", "Lee Park"]
    assert lin["shot"]["stage"] == "production"
    assert lin["approved"]["by"] == "Lee Park" and lin["approved"]["role"] == "legal"
    assert lin["final_reviewed"]
    assert lin["final_outputs"][0]["sha256"] == "ab" * 32
    assert all(p["authored_by"] == "Ana Ruiz" for p in lin["prompts"])
    declared = [s for s in lin["sources"] if s.get("origin")]
    assert declared and declared[0]["asset_id"] == "PLATE-0120-A"
    assert {"prompt", "review", "selection", "sourcing", "stage"} <= set(lin["breakpoints"])


def test_renders_with_no_person_in_between_are_called_out():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    for _ in range(3):
        wf = H.stamp(wf, event="rendered", actor=ANA, keys=keys()).workflow
    lin = H.lineage(H.read(wf))
    assert any("no recorded human step" in g for g in lin["gaps"])
    assert any("reviewing the final render" in g for g in lin["gaps"])


def test_a_new_file_in_a_source_slot_needs_declaring_again():
    wf = lifetime()
    src = next(s for s in H.lineage(H.read(wf))["sources"] if s.get("origin"))
    node = next(n for n in wf["nodes"] if str(n["id"]) == src["node"])
    node["widgets_values"][0] = "some_other_plate.exr"
    wf = H.stamp(wf, actor=ANA, keys=keys()).workflow
    again = next(s for s in H.lineage(H.read(wf))["sources"] if s["node"] == src["node"])
    assert not again["origin"]


def test_stage_and_origin_values_are_checked():
    wf = load()
    with pytest.raises(ValueError):
        H.stamp(wf, event="promoted", actor=DOUG, keys=keys(), data_payload={"stage": "done"})
    with pytest.raises(ValueError):
        H.stamp(wf, event="sourced", actor=DOUG, keys=keys(),
                data_payload={"node": "5", "origin": "found on the internet"})


def test_api_format_workflows_keep_history_in_a_sidecar():
    api = load("beauty-pass-api.json")
    result = H.stamp(api, event="created", actor=ANA, keys=keys())
    assert result.sidecar and result.entry is not None
    assert result.workflow == api, "the prompt itself must stay submittable"
    with pytest.raises(ValueError):
        H.write(api, result.history)


# -- documents ---------------------------------------------------------------

def test_c2pa_manifest_declares_generative_source():
    m = H.c2pa_manifest(H.read(lifetime()))
    actions = m["assertions"][0]["data"]["actions"]
    assert actions[0]["action"] == "c2pa.created"
    assert actions[0]["digitalSourceType"].endswith("compositeWithTrainedAlgorithmicMedia")
    ours = m["assertions"][1]["data"]
    assert ours["approved"]["by"] == "Lee Park" and ours["head"]


def test_provenance_record_renders_every_part():
    wf = lifetime()
    html = history_html.render(H.read(wf), data=wf, keys=keys(),
                               context={"distribute_in": ["EU"], "likeness": True})
    for part in ("Shot provenance record", "Human in the loop", "Life of the shot",
                 "Models over time", "Prompts and authorship", "Source material",
                 "Stages and sign-off", "Rules this shot may need to meet",
                 "Complete record", "<svg"):
        assert part in html, part
    assert "Signed" in html and "Shot#4411" in html


def test_compliance_selects_rules_from_what_the_record_shows():
    hist = H.read(lifetime())
    lin = H.lineage(hist)
    facts = compliance.signals(lin, hist, {"distribute_in": ["EU"]})
    assert {"eu", "production", "source-media", "likeness"} <= facts  # FaceID nodes in the fixture
    checks = compliance.assess(lin, hist, {"distribute_in": ["EU"]})
    assert checks["rules"]
    for rule in checks["rules"]:
        assert rule["source"], f"{rule['id']} has no source"
        # An undated source is allowed only when the rule says why.
        assert rule["source_date"] or rule["notes"], f"{rule['id']} is undated with no note"
    ids = {r["id"] for r in checks["rules"]}
    assert "eu-ai-act-art50-4-deployer-deepfake" in ids
    assert "eu-ai-act-art14-human-oversight" not in ids
    assert any(c["id"] == "eu-ai-act-art14-human-oversight" for c in checks["clarifications"])
    usco = next(r for r in checks["rules"] if r["id"] == "usco-part2-copyrightability")
    states = [e["state"] for e in usco["evidence"]]
    # A photographed plate is human-made input on record. Human edits after
    # generation (comp, paint, roto) happen outside ComfyUI, so that item
    # waits for a person to attest it rather than being inferred from tweaks.
    assert states == ["have", "need", "have", "have"]


def test_one_bare_rule_tag_does_not_tick_every_item():
    wf = H.stamp(lifetime(), event="note", actor=LEE, keys=keys(), note="disclosed",
                 data_payload={"evidence": "eu-ai-act-art50-4-deployer-deepfake"}).workflow
    hist = H.read(wf)
    checks = compliance.assess(H.lineage(hist), hist, {"distribute_in": ["FR"]})
    art = next(r for r in checks["rules"] if r["id"] == "eu-ai-act-art50-4-deployer-deepfake")
    assert all(e["state"] == "need" for e in art["evidence"])


def test_an_exported_manifest_is_not_evidence_that_marking_was_applied():
    hist = H.read(lifetime())
    checks = compliance.assess(H.lineage(hist), hist, {"distribute_in": ["EU"]})
    c2pa = next(r for r in checks["rules"] if r["id"] == "c2pa-content-credentials")
    assert c2pa["evidence"][0]["state"] == "need"


def test_an_approval_before_the_final_render_does_not_cover_it():
    wf = lifetime()
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=keys(), force=True).workflow
    lin = H.lineage(H.read(wf))
    assert lin["approved"] is None
    assert any("does not cover the current output" in g for g in lin["gaps"])


def test_importing_someone_elses_graph_does_not_credit_the_importer():
    wf = H.stamp(load(), actor=DOUG, keys=keys()).workflow
    entry = H.read(wf)["entries"][0]
    assert entry["event"] == "imported" and entry["breakpoints"] == []
    lin = H.lineage(H.read(wf))
    assert all(p["authored_by"] == "before tracking" for p in lin["prompts"])


def test_blank_notes_do_not_count_as_human_decisions():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    for _ in range(3):
        wf = H.stamp(wf, event="rendered", actor=ANA, keys=keys()).workflow
        wf = H.stamp(wf, event="note", actor=ANA, keys=keys(), note="").workflow
    lin = H.lineage(H.read(wf))
    assert any("no recorded human step" in g for g in lin["gaps"])


def test_attested_evidence_is_read_back_from_the_chain():
    wf = lifetime()
    kb = compliance.load()
    manual = next(((r["id"], i) for r in kb["rules"]
                   for i, e in enumerate(r.get("evidence", [])) if e.get("check") == "manual"), None)
    if manual is None:
        pytest.skip("no manual evidence items in the knowledge base")
    rid, idx = manual
    wf = H.stamp(wf, event="note", actor=LEE, keys=keys(), note="consent on file",
                 data_payload={"evidence": f"{rid}:{idx}"}).workflow
    assert compliance._attested(rid, H.read(wf))[idx].startswith("Lee Park")


def test_licensing_guide_is_built_from_the_knowledge_base():
    html = licensing_guide.render()
    for part in ("Commercial use permitted", "Permitted, with conditions",
                 "Not without a separate licence", "A commercial licence is not indemnity",
                 "FLUX.1 [dev]", "Black Forest Labs", "step by step"):
        assert part in html, part


def test_the_audit_report_carries_the_history(tmp_path):
    path = tmp_path / "shot.json"
    path.write_text(json.dumps(lifetime()))
    report = audit.run(str(path))
    assert report.history["lineage"]["entries"] == 7
    assert "Shot history" in html_report.render(report)
    assert "| 7 |" in md_report.render(report)
    assert report.to_dict()["history"]["lineage"]["shot"]["name"] == "SH0120"


def test_untracked_workflows_report_exactly_as_before():
    report = audit.run(os.path.join(FIXTURES, "beauty-pass.json"))
    assert report.history == {}
    assert "Shot history" not in html_report.render(report)


# -- command line ------------------------------------------------------------

def test_cli_stamps_verifies_and_reports(tmp_path, monkeypatch, capsys):
    from comfyaudit.core import cli
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "cli-secret")
    path = tmp_path / "shot.json"
    path.write_text(json.dumps(load()))
    ledger = str(tmp_path / "ledger.jsonl")
    assert cli.main(["history", "stamp", str(path), "--event", "created", "--by", "Ana",
                     "--id", "shotgrid=Shot#1", "--ledger", ledger]) == 0
    assert cli.main(["history", "stamp", str(path), "--event", "approved", "--by", "Lee",
                     "--ledger", ledger]) == 0
    assert cli.main(["history", "verify", str(path), "--ledger", ledger]) == 0
    assert "WITNESSED" in capsys.readouterr().out
    assert cli.main(["history", "record", str(path), "-o", str(tmp_path / "r.html")]) == 0
    assert cli.main(["history", "manifest", str(path)]) == 0
    assert cli.main(["guide", "-o", str(tmp_path / "g.html")]) == 0

    data = json.loads(path.read_text())
    data["extra"][H.EXTRA_KEY]["entries"][0]["note"] = "edited"
    path.write_text(json.dumps(data))
    assert cli.main(["history", "verify", str(path)]) == 3


def test_server_service_stamps_without_comfyui_and_refuses_path_escapes():
    from comfyaudit.server import history_service
    out = history_service.stamp({"workflow": load(), "event": "created", "name": "SH1",
                                 "external_ids": {"shotgrid": "Shot#2"}})
    assert out["recorded"] and out["summary"]["present"]
    assert out["summary"]["shot"]["external_ids"] == {"shotgrid": "Shot#2"}
    assert history_service.output_path({"filename": "../../etc/passwd"}) == ""


def test_a_bypassed_model_did_not_make_the_final_pixels():
    wf = H.stamp(load(), event="created", actor=ANA, keys=keys()).workflow
    restore = next(n for n in wf["nodes"]
                   if any(isinstance(v, str) and "codeformer" in v.lower()
                          for v in n.get("widgets_values") or []))
    restore["mode"] = 4
    result = H.stamp(wf, actor=DOUG, keys=keys())
    assert result.entry["changes"]["models_disabled"]
    assert "model-choice" in result.entry["breakpoints"]
    wf = H.stamp(result.workflow, event="rendered", actor=DOUG, keys=keys()).workflow
    codeformer = next(m for m in H.lineage(H.read(wf))["models"]
                      if "codeformer" in m["filename"].lower())
    assert codeformer["in_final"] is False and codeformer["removed"] is None


# -- conflicting licence data --------------------------------------------------

def test_a_recorded_licence_conflict_is_flagged_everywhere(tmp_path):
    from comfyaudit.core.knowledge import licences as L
    from comfyaudit.core.records import ModelRef
    info = L.LicenceMatcher().for_model(ModelRef(filename="flux1-dev.safetensors",
                                                 folder="diffusion_models"))
    assert info.conflicts and info.conflicts[0]["kind"] == "recorded"

    flux_dev = json.loads(json.dumps(load("clean-batch.json"))
                          .replace("flux1-schnell.safetensors", "flux1-dev.safetensors"))
    path = tmp_path / "flux.json"
    path.write_text(json.dumps(flux_dev))
    report = audit.run(str(path))
    assert report.licensing.conflicted
    assert "conflicting terms" in report.licensing.headline
    assert any(f.id == "licence.conflicting-terms" for f in report.risk.findings)
    assert "Conflicting terms in the licence" in html_report.render(report)
    assert "Conflict:" in md_report.render(report)
    assert "contradict themselves" in licensing_guide.render()

    wf = H.stamp(flux_dev, event="created", actor=ANA, keys=keys()).workflow
    wf = H.stamp(wf, event="rendered", actor=ANA, keys=keys()).workflow
    assert any(m.get("conflict") for m in H.lineage(H.read(wf))["models"])
    assert "Conflicting licence terms" in history_html.render(H.read(wf), data=wf, keys=keys())


def test_contradictory_fields_in_a_studio_entry_are_detected():
    from comfyaudit.core.knowledge import licences as L
    found = L.licence_conflicts({"name": "House licence", "commercial_use": "yes",
                                 "conditions": {"outputs_commercial": "no"}})
    assert found and found[0]["kind"] == "detected"
    assert not L.licence_conflicts({"name": "MIT", "commercial_use": "yes", "conditions": {}})
