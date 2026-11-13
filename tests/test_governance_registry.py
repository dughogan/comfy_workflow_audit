"""Per-source rights, the facility registry wired into delivery and the gates,
and approved workflow templates."""

from __future__ import annotations

import base64
import copy
import datetime as dt
import io
import json
import os
import types
import zipfile

import pytest

from comfyaudit.core import audit as audit_mod
from comfyaudit.core import cli, graph, history as H
from comfyaudit.core import registry as R
from comfyaudit.core.report import history_html
from comfyaudit.nodes.audit_nodes import ComfyAuditGate, ComfyAuditHistoryGate
from comfyaudit.server import delivery
from comfyaudit.server import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "beauty-pass.json")
ANA = H.Actor(name="Ana Ruiz", role="compositor")
TODAY = dt.date.today()
DAY = dt.timedelta(days=1)


def load(path=FIXTURE):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def keys():
    k = H.Keys()
    k.sign_key = H._derive("studio-secret", b"comfyaudit-sign")
    k.key_id = "test"
    return k


def declared(rights, origin="licensed", wf=None):
    """The fixture with its plate (LoadImage node 5) declared, with these rights."""
    k = keys()
    wf = H.stamp(wf or load(), event="created", actor=ANA, keys=k, name="SH0300").workflow
    return H.stamp(wf, event="sourced", actor=ANA, keys=k, data_payload={
        "node": "5", "origin": origin, "owner": "Stockhouse Ltd", "rights": rights}).workflow


def audit(wf_doc, registry_path, **opts):
    wf = graph.from_dict(wf_doc)
    return audit_mod.run_workflow(wf, audit_mod.AuditOptions(registry_path=str(registry_path),
                                                             hash_models=False, **opts))


# -- rights per source --------------------------------------------------------------

def test_rights_are_validated_when_declared():
    with pytest.raises(ValueError, match="date"):
        H.clean_rights({"licence_end": "next spring"})
    with pytest.raises(ValueError, match="ends before it starts"):
        H.clean_rights({"licence_start": "2027-01-01", "licence_end": "2026-01-01"})
    with pytest.raises(ValueError, match="ai_generation"):
        H.clean_rights({"ai_generation": "maybe"})
    assert H.clean_rights({"ai_training": "NO", "territory": " worldwide ", "media": ""}) == \
        {"ai_training": "no", "territory": "worldwide"}


def test_declared_rights_are_sealed_and_read_back():
    wf = declared({"licence_start": "2026-01-01", "licence_end": "2099-12-31",
                   "territory": "worldwide", "ai_generation": "yes"})
    src = next(s for s in H.lineage(H.read(wf))["sources"] if s["node"] == "5")
    assert src["owner"] == "Stockhouse Ltd"
    assert src["rights"] == {"licence_start": "2026-01-01", "licence_end": "2099-12-31",
                             "territory": "worldwide", "ai_generation": "yes"}
    assert H.verify(wf, keys=keys()).assurance == "signed"


def test_an_expired_licence_or_a_ban_on_ai_use_blocks_and_shows_as_a_gap():
    wf = declared({"licence_end": (TODAY - DAY).isoformat(), "ai_reference": "no"})
    lin = H.lineage(H.read(wf))
    kinds = {i["kind"]: i["severity"] for i in lin["rights_issues"]}
    assert kinds == {"expired": "block", "ai_reference": "block"}
    assert any("licence ended" in g for g in lin["gaps"])
    assert any("AI" in g for g in lin["gaps"])


def test_a_licence_ending_soon_or_a_training_ban_is_a_condition_not_a_block():
    wf = declared({"licence_end": (TODAY + 10 * DAY).isoformat(), "ai_training": "no"})
    issues = H.lineage(H.read(wf))["rights_issues"]
    assert {i["kind"] for i in issues} == {"expiring", "ai_training"}
    assert all(i["severity"] == "condition" for i in issues)


def test_a_licence_not_yet_started_blocks():
    wf = declared({"licence_start": (TODAY + 5 * DAY).isoformat()})
    assert [i["kind"] for i in H.lineage(H.read(wf))["rights_issues"]] == ["not-started"]


def test_licensed_material_without_rights_is_a_gap_but_the_studios_own_is_not():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0300").workflow
    lic = H.stamp(wf, event="sourced", actor=ANA, keys=k,
                  data_payload={"node": "5", "origin": "licensed"}).workflow
    own = H.stamp(wf, event="sourced", actor=ANA, keys=k,
                  data_payload={"node": "5", "origin": "photographed"}).workflow
    assert any("no rights recorded" in g for g in H.lineage(H.read(lic))["gaps"])
    assert not any("no rights recorded" in g for g in H.lineage(H.read(own))["gaps"])
    assert delivery._sources(H.lineage(H.read(lic)))[0]["missing"] == ["rights"]


def test_the_legal_report_shows_each_sources_rights():
    wf = declared({"licence_end": (TODAY - DAY).isoformat(), "ai_generation": "yes",
                   "territory": "EU only"})
    html = history_html.render(H.read(wf), data=wf, keys=keys())
    assert "EU only" in html and "AI generation: allowed" in html and "licence ended" in html


def test_the_history_gate_can_stop_on_source_rights():
    gate = ComfyAuditHistoryGate()
    bad = declared({"ai_generation": "no"})
    with pytest.raises(RuntimeError, match="source rights"):
        gate.run(object(), extra_pnginfo={"workflow": bad}, stop_if_altered=False,
                 stop_on_rights=True)
    ok = declared({"ai_generation": "yes"})
    assert gate.run(object(), extra_pnginfo={"workflow": ok}, stop_if_altered=False,
                    stop_on_rights=True)[1].startswith("passed")
    # Off by default: an old workflow's gate behaves as it did.
    gate.run(object(), extra_pnginfo={"workflow": bad}, stop_if_altered=False)


# -- the registry's new fields ---------------------------------------------------------

def test_new_fields_are_left_out_of_the_file_when_unused(tmp_path):
    path = tmp_path / "reg.json"
    reg = R.Registry()
    reg.record(R.Entry(key="a.safetensors", decided_by="Lee"))
    reg.record(R.Entry(key="b.safetensors", expires="2030-01-01", clients=["Northlight"]))
    reg.save(str(path))
    rows = {r["key"]: r for r in json.loads(path.read_text())["entries"]}
    assert "expires" not in rows["a.safetensors"] and "clients" not in rows["a.safetensors"]
    assert rows["b.safetensors"]["clients"] == ["Northlight"]
    again = R.Registry.load(str(path)).get("b.safetensors")
    assert again.expires == "2030-01-01" and again.clients == ["Northlight"]


def test_an_entry_with_a_bad_kind_or_date_is_refused():
    with pytest.raises(ValueError, match="kind"):
        R.Registry().record(R.Entry(key="x", kind="plugin"))
    with pytest.raises(ValueError, match="expires"):
        R.Registry().record(R.Entry(key="x", expires="soon"))


def _everything(doc=None):
    """Entries clearing every model and pack the fixture uses."""
    report = audit_mod.run_workflow(graph.from_dict(doc or load()),
                                    audit_mod.AuditOptions(hash_models=False))
    return report, [R.Entry(key=m.filename, decided_by="Lee") for m in report.models] +         [R.Entry(key=p.repo, kind="pack", decided_by="Lee") for p in report.packs if p.identified]


def _models_registry(tmp_path, **juggernaut):
    reg = R.Registry(_everything()[1])
    reg.record(R.Entry(key="juggernautXL_v9Rundiffusion.safetensors", decided_by="Lee",
                       **juggernaut))
    path = tmp_path / "reg.json"
    reg.save(str(path))
    return path


def _state(report, name):
    return next(m.state for m in report.registry.matches if m.subject == name)


JUG = "juggernautXL_v9Rundiffusion.safetensors"


def test_a_lapsed_approval_is_not_a_clearance(tmp_path):
    path = _models_registry(tmp_path, expires=(TODAY - DAY).isoformat())
    report = audit(load(), path)
    assert _state(report, JUG) == "expired"
    assert not report.registry.clean and "lapsed" in report.registry.headline()


def test_an_approval_for_one_client_does_not_cover_another(tmp_path):
    path = _models_registry(tmp_path, clients=["Northlight Pictures"])
    assert _state(audit(load(), path, client="Other Studio"), JUG) == "not-for-client"
    assert _state(audit(load(), path, client="northlight pictures"), JUG) == "known"
    assert _state(audit(load(), path), JUG) == "not-for-client"   # no client recorded


def test_a_hash_from_the_cache_catches_a_retrained_file_the_audit_did_not_hash(tmp_path):
    path = _models_registry(tmp_path, sha256="ab" * 32)
    assert _state(audit(load(), path), JUG) == "known"     # no hash: nothing to compare
    report = audit(load(), path, hash_of=lambda m: "cd" * 32)
    assert _state(report, JUG) == "changed"


def test_a_pack_not_at_its_production_version_is_not_the_reviewed_code():
    reg = R.Registry([R.Entry(key="https://github.com/ltdrdata/ComfyUI-Impact-Pack", kind="pack",
                              production_version="8.1.0", security_review="2026-09 internal")])
    pack = R.PackRef(repo="https://github.com/ltdrdata/ComfyUI-Impact-Pack", title="Impact Pack",
                     pinned_version="8.3.2")
    m = reg.check([], [pack]).matches[0]
    assert m.state == "changed" and "8.1.0" in m.detail and "reviewed" in m.detail
    pack.pinned_version = "v8.1.0"
    assert reg.check([], [pack]).matches[0].state == "known"


def test_api_models_are_cleared_with_the_providers_terms():
    reg = R.Registry([R.Entry(key="OpenAIGPTImage1", kind="api", provider="OpenAI",
                              client_data="not allowed", retention="30 days")])
    assert reg.check([], [], ["KlingImage2VideoNode"]).matches[0].state == "new"
    personal = reg.check([], [], ["OpenAIGPTImage1"]).matches[0]
    assert personal.state == "known" and "retention 30 days" in personal.detail
    client = reg.check([], [], ["OpenAIGPTImage1"], client="Northlight").matches[0]
    assert client.state == "not-for-client"


# -- workflow templates -------------------------------------------------------------------

def _template_registry(tmp_path, doc=None, **fields):
    doc = doc or load()
    wf = graph.from_dict(doc)
    report, entries = _everything(doc)
    reg = R.Registry(entries)
    reg.record(R.workflow_entry(wf, report.models, report.packs, name="Beauty pass",
                                version="3", decided_by="Lee", **fields))
    path = tmp_path / "reg.json"
    reg.save(str(path))
    return path


def test_a_copy_of_an_approved_template_with_new_values_is_still_that_template(tmp_path):
    path = _template_registry(tmp_path)
    doc = load()
    for n in doc["nodes"]:
        if n["type"] == "CLIPTextEncode":
            n["widgets_values"] = ["a different prompt entirely"]
    w = audit(doc, path).registry.workflow
    assert w.state == "approved" and w.template == "Beauty pass" and w.version == "3"
    assert "unmodified" in w.detail and audit(doc, path).registry.clean


def test_node_ids_do_not_decide_which_template_it_is(tmp_path):
    path = _template_registry(tmp_path)
    doc = load()
    for n in doc["nodes"]:
        n["id"] += 100
    for link in doc["links"]:
        link[1] += 100
        link[3] += 100
    assert audit(doc, path).registry.workflow.state == "approved"


def test_a_changed_workflow_says_what_changed_from_its_template(tmp_path):
    path = _template_registry(tmp_path)
    doc = load()
    extra = copy.deepcopy(next(n for n in doc["nodes"] if n["type"] == "LoadImage"))
    extra["id"] = 999
    for o in extra.get("outputs") or []:
        o["links"] = []
    doc["nodes"].append(extra)
    w = audit(doc, path).registry.workflow
    assert w.state == "modified" and w.added == ["LoadImage"] and "added LoadImage" in w.detail


def test_models_and_settings_outside_the_template_are_violations(tmp_path):
    path = _template_registry(tmp_path, params={"steps": [20, 30], "sampler_name": ["euler"]})
    doc = load()
    ck = next(n for n in doc["nodes"] if n["type"] == "CheckpointLoaderSimple")
    ck["widgets_values"][0] = "some_other_model.safetensors"
    report = audit(doc, path)
    v = report.registry.workflow.violations
    assert any("some_other_model.safetensors is not one of the models" in x for x in v)
    assert not report.registry.clean
    assert any(x.startswith("workflow:") for x in report.registry.problems())


def test_a_workflow_nothing_like_any_template_is_unregistered(tmp_path):
    path = _template_registry(tmp_path)
    other = load(os.path.join(HERE, "fixtures", "clean-batch.json"))
    assert audit(other, path).registry.workflow.state == "unregistered"


def test_a_registry_without_templates_does_not_judge_workflows(tmp_path):
    path = _models_registry(tmp_path)
    assert audit(load(), path).registry.workflow is None


# -- the gate --------------------------------------------------------------------------

def _fake_audit(registry):
    return types.SimpleNamespace(registry=registry, models=[],
                                 risk=types.SimpleNamespace(findings=[]))


def test_the_gate_can_stop_on_anything_not_cleared_in_the_registry(tmp_path):
    gate = ComfyAuditGate()
    path = _models_registry(tmp_path, status="rejected")
    with pytest.raises(RuntimeError, match="not cleared in the registry"):
        gate.run(audit(load(), path), "critical", stop_on_registry=True)
    with pytest.raises(RuntimeError, match="no registry is loaded"):
        gate.run(_fake_audit(R.RegistryCheck()), "critical", stop_on_registry=True)
    clean = audit(load(), _models_registry(tmp_path))
    assert clean.registry.clean
    assert gate.run(clean, "critical", stop_on_registry=True)[1].startswith("passed")


# -- delivery ------------------------------------------------------------------------------

@pytest.fixture
def studio(monkeypatch):
    cfg = {"user": "Priya Rao", "role": "VFX supervisor", "auto": True, "ledger": "",
           "key_file": "", "confidentiality": "none", "distribute_in": [], "territory": "",
           "likeness": False, "clients": []}
    monkeypatch.setattr(settings_mod, "history_settings", lambda user="default": dict(cfg))
    monkeypatch.setenv("COMFYAUDIT_HISTORY_KEY", "studio-secret")
    return monkeypatch


def rendered(wf):
    k = H.Keys.from_environment()
    if not H.read(wf):
        wf = H.stamp(wf, event="created", actor=ANA, keys=k, name="SH0300").workflow
    return H.stamp(wf, event="rendered", actor=ANA, keys=k,
                   outputs=[{"file": "a.png", "sha256": "ab" * 32}]).workflow


def test_a_registry_finding_holds_delivery_until_a_reason_is_recorded(studio, tmp_path):
    path = _models_registry(tmp_path, status="rejected", note="licence pulled")
    studio.setattr(settings_mod, "registry_path", lambda user="default": str(path))
    wf = rendered(load())
    checked = delivery.check({"workflow": wf, "name": "SH0300"})
    assert checked["registry"]["loaded"] and not checked["registry"]["clean"]
    assert any("registry" in x for x in checked["holds"])
    refused = delivery.deliver({"workflow": wf, "name": "SH0300"})
    assert "registry" in refused["error"]
    out = delivery.deliver({"workflow": wf, "name": "SH0300", "exception": "client signed waiver"})
    sealed = H.delivery_clearance(H.read(out["workflow"]))
    assert sealed["registry"]["path"] == "reg.json"
    assert any(i["state"] == "rejected" for i in sealed["registry"]["items"])
    index = json.loads(zipfile.ZipFile(io.BytesIO(base64.b64decode(out["zip_b64"])))
                       .read("SH0300.package.json"))
    assert index["decision"]["registry"]["clean"] is False
    assert "Facility registry at sign-off" in out["report_html"]


def test_source_rights_hold_delivery_and_are_sealed(studio, tmp_path):
    studio.setattr(settings_mod, "registry_path", lambda user="default": "")
    wf = rendered(declared({"ai_generation": "no"}))
    checked = delivery.check({"workflow": wf, "name": "SH0300"})
    assert any("Source node 5" in x for x in checked["holds"])
    assert "error" in delivery.deliver({"workflow": wf, "name": "SH0300"})
    out = delivery.deliver({"workflow": wf, "name": "SH0300", "exception": "rights cleared by email"})
    sealed = H.delivery_clearance(H.read(out["workflow"]))
    assert sealed["rights"][0]["kind"] == "ai_generation"


def test_a_template_that_requires_qc_will_not_deliver_without_a_pass(studio, tmp_path):
    path = _template_registry(tmp_path, qc_required=True)
    studio.setattr(settings_mod, "registry_path", lambda user="default": str(path))
    wf = rendered(load())
    checked = delivery.check({"workflow": wf, "name": "SH0300"})
    assert checked["qc_required"] and checked["registry"]["workflow"]["state"] == "approved"
    body = {"workflow": wf, "name": "SH0300", "exception": "licence audit"}
    assert "quality check" in delivery.deliver(dict(body))["error"]
    assert "quality check" in delivery.deliver({**body, "qc": {"result": "fail"}})["error"]
    out = delivery.deliver({**body, "qc": {"result": "pass", "checks": "4K"}})
    assert "error" not in out, out
    sealed = H.delivery_clearance(H.read(out["workflow"]))
    assert sealed["registry"]["workflow"]["template"] == "Beauty pass"


# -- the CLI ---------------------------------------------------------------------------------

def test_the_cli_registers_a_template_and_sets_the_new_fields(tmp_path, capsys):
    path = str(tmp_path / "reg.json")
    assert cli.main(["registry", "add-workflow", path, FIXTURE, "--name", "Beauty pass",
                     "--version", "3", "--by", "Lee", "--param", "steps=20..40",
                     "--param", "sampler_name=euler,dpmpp_2m", "--qc-required"]) == 0
    t = R.Registry.load(path).get("Beauty pass", "workflow")
    assert t.version == "3" and t.qc_required and t.structure
    assert t.params == {"steps": [20, 40], "sampler_name": ["euler", "dpmpp_2m"]}
    assert JUG in t.models_allowed
    assert cli.main(["registry", "set", path, "OpenAIGPTImage1", "--kind", "api",
                     "--provider", "OpenAI", "--client-data", "not allowed",
                     "--expires", "2027-06-30", "--clients", "Northlight, Atlas"]) == 0
    e = R.Registry.load(path).get("OpenAIGPTImage1", "api")
    assert e.provider == "OpenAI" and e.clients == ["Northlight", "Atlas"]
    # A later set changes only what it names.
    assert cli.main(["registry", "set", path, "OpenAIGPTImage1", "--kind", "api",
                     "--retention", "30 days"]) == 0
    e = R.Registry.load(path).get("OpenAIGPTImage1", "api")
    assert e.retention == "30 days" and e.provider == "OpenAI"
    assert cli.main(["registry", "set", path, "x", "--expires", "soon"]) == 2


# -- the record survives the browser ------------------------------------------------------

def _as_browser_writes_it(obj):
    """JSON as JavaScript writes it back: one number type, so 1.0 becomes 1."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: _as_browser_writes_it(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_as_browser_writes_it(v) for v in obj]
    return obj


def test_entry_data_with_whole_floats_still_verifies_after_the_canvas_saves_it():
    k = keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0300").workflow
    wf = H.stamp(wf, event="note", actor=ANA, keys=k,
                 data_payload={"score": 1.0, "nested": [{"x": 2.0, "y": 0.5}], "flag": True}).workflow
    assert H.read(wf)["entries"][-1]["data"] == {"score": 1, "nested": [{"x": 2, "y": 0.5}],
                                                 "flag": True}
    saved = _as_browser_writes_it(json.loads(json.dumps(wf)))
    assert H.verify(saved, keys=k).assurance == "signed"


def test_a_sealed_registry_verdict_survives_the_canvas(studio, tmp_path):
    path = _template_registry(tmp_path)
    studio.setattr(settings_mod, "registry_path", lambda user="default": str(path))
    out = delivery.deliver({"workflow": rendered(load()), "name": "SH0300",
                            "exception": "no studio profile in tests", "qc": {"result": "pass"}})
    assert "error" not in out, out
    saved = _as_browser_writes_it(json.loads(json.dumps(out["workflow"])))
    assert H.verify(saved, keys=H.Keys.from_environment()).assurance == "signed"
    assert H.delivery_clearance(H.read(saved))["registry"]["workflow"]["similarity"] == 100
