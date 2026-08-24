"""Plugin behaviour, exercised against a stand-in ComfyUI.

ComfyUI cannot be imported in a test environment (it pulls in torch and a GPU
stack), but the surface the plugin actually touches is small and stable:
``nodes.NODE_CLASS_MAPPINGS`` and a handful of ``folder_paths`` functions. Those
are stubbed here so the live-introspection path, the node classes and the queue
gate are all covered rather than assumed.
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from comfyaudit.core import catalog
from comfyaudit.server import live

# --------------------------------------------------------------------------
# A stand-in ComfyUI
# --------------------------------------------------------------------------

CHECKPOINTS = ["sd_xl_base_1.0.safetensors", "juggernautXL_v9Rundiffusion.safetensors"]
LORAS = ["studio_skin_detail_v3.safetensors", "detail_tweaker_xl.safetensors"]
UPSCALERS = ["4x-UltraSharp.pth", "RealESRGAN_x4plus.pth"]


class StubCheckpointLoader:
    """Mimics the real core node closely enough to be indistinguishable."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"ckpt_name": (CHECKPOINTS,)}}

    RETURN_TYPES = ("MODEL", "CLIP", "VAE")
    CATEGORY = "loaders"


class StubLoraStack:
    """A custom node: the bundled catalog has never heard of this one."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "lora_name_1": (LORAS,),
                "strength_1": ("FLOAT", {"default": 1.0}),
                "notes": ("STRING", {"multiline": True, "default": ""}),
            },
            "optional": {"model": ("MODEL",)},
        }

    RETURN_TYPES = ("MODEL",)
    CATEGORY = "custom/loaders"


class StubUpscaleLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model_name": (UPSCALERS,)}}

    RETURN_TYPES = ("UPSCALE_MODEL",)
    CATEGORY = "loaders"


@pytest.fixture()
def comfy(tmp_path, monkeypatch):
    """Install stub ``nodes`` and ``folder_paths`` modules for one test."""
    folders = {"checkpoints": CHECKPOINTS, "loras": LORAS, "upscale_models": UPSCALERS}
    for folder, names in folders.items():
        directory = tmp_path / "models" / folder
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(b"weights" * 16)

    folder_paths = types.ModuleType("folder_paths")
    folder_paths.base_path = str(tmp_path)
    folder_paths.folder_names_and_paths = {
        name: ([str(tmp_path / "models" / name)], set()) for name in folders
    }
    folder_paths.get_filename_list = lambda name: list(folders.get(name, []))
    folder_paths.get_full_path = lambda folder, name: str(tmp_path / "models" / folder / name)
    folder_paths.get_output_directory = lambda: str(tmp_path / "output")
    folder_paths.get_folder_paths = lambda name: [str(tmp_path / name)]
    folder_paths.get_user_directory = lambda: str(tmp_path / "user")

    # Make the custom node look genuinely installed: ComfyUI attributes a class
    # to a pack by the file its module was loaded from, so the stub needs a
    # module whose __file__ sits under custom_nodes/.
    pack_dir = tmp_path / "custom_nodes" / "studio-inhouse-nodes"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "pyproject.toml").write_text(
        '[project]\nname = "studio-inhouse-nodes"\nversion = "2.4.1"\n')
    pack_module = types.ModuleType("studio_inhouse_nodes")
    pack_module.__file__ = str(pack_dir / "nodes.py")
    monkeypatch.setitem(sys.modules, "studio_inhouse_nodes", pack_module)
    monkeypatch.setattr(StubLoraStack, "__module__", "studio_inhouse_nodes")

    nodes = types.ModuleType("nodes")
    nodes.NODE_CLASS_MAPPINGS = {
        "CheckpointLoaderSimple": StubCheckpointLoader,
        "LoraStackAdvanced": StubLoraStack,
        "UpscaleModelLoader": StubUpscaleLoader,
    }

    monkeypatch.setitem(sys.modules, "folder_paths", folder_paths)
    monkeypatch.setitem(sys.modules, "nodes", nodes)
    live.uninstall()
    yield tmp_path
    live.uninstall()


WORKFLOW = {
    "nodes": [
        {"id": 1, "type": "CheckpointLoaderSimple", "mode": 0, "inputs": [],
         "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
         "properties": {"cnr_id": "comfy-core"},
         "widgets_values": ["juggernautXL_v9Rundiffusion.safetensors"]},
        {"id": 2, "type": "LoraStackAdvanced", "mode": 0,
         "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
         "outputs": [], "properties": {},
         "widgets_values": ["studio_skin_detail_v3.safetensors", 0.8, "look dev pass"]},
        {"id": 3, "type": "UpscaleModelLoader", "mode": 0, "inputs": [], "outputs": [],
         "properties": {}, "widgets_values": ["4x-UltraSharp.pth"]},
    ],
    "links": [[1, 1, 0, 2, 0, "MODEL"]],
    "version": 0.4,
}


# --------------------------------------------------------------------------
# Live introspection
# --------------------------------------------------------------------------


def test_live_provider_installs_only_when_comfyui_is_present():
    assert live.comfy_available() is False
    assert live.install() is False


def test_live_provider_installs_inside_comfyui(comfy):
    assert live.comfy_available() is True
    assert live.install() is True
    assert catalog.has_live_provider() is True


def test_custom_node_widgets_get_real_names(comfy):
    """The whole point of running inside ComfyUI.

    Offline this node's widgets are ``widget_0``/``widget_1``; live, the node
    itself tells us they are ``lora_name_1`` and ``strength_1``.
    """
    live.install()
    schema = catalog.get_node_schema("LoraStackAdvanced")
    assert schema is not None and schema["schema"] == "live"
    assert [w["name"] for w in schema["widgets"]] == ["lora_name_1", "strength_1", "notes"]


def test_model_folder_is_recovered_from_the_option_list(comfy):
    """INPUT_TYPES returns folder *contents*, so the folder is reverse-matched."""
    live.install()
    schema = catalog.get_node_schema("LoraStackAdvanced")
    assert schema["widgets"][0]["model_folder"] == "loras"
    ckpt = catalog.get_node_schema("CheckpointLoaderSimple")
    assert ckpt["widgets"][0]["model_folder"] == "checkpoints"


def test_multiline_widget_is_detected_live(comfy):
    live.install()
    schema = catalog.get_node_schema("LoraStackAdvanced")
    assert schema["widgets"][2].get("multiline") is True


def test_live_index_finds_the_installed_weights(comfy):
    index = live.live_model_index()
    assert index.available and index.scanned == 6
    found = index.find("4x-UltraSharp.pth", "upscale_models")
    assert found is not None and found.folder == "upscale_models"


def test_environment_reports_what_it_is_running_in(comfy):
    info = live.environment()
    assert info["comfyui"] is True
    assert info["installed_node_types"] == 3


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def test_node_input_types_are_well_formed():
    from comfyaudit.nodes.audit_nodes import NODE_CLASS_MAPPINGS

    for name, cls in NODE_CLASS_MAPPINGS.items():
        spec = cls.INPUT_TYPES()
        assert isinstance(spec, dict), name
        assert "required" in spec, name
        assert isinstance(cls.RETURN_TYPES, tuple), name
        assert hasattr(cls, cls.FUNCTION), name
        if hasattr(cls, "RETURN_NAMES"):
            assert len(cls.RETURN_NAMES) == len(cls.RETURN_TYPES), name


def test_audit_node_reads_the_workflow_from_hidden_inputs(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    out = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )
    report, markdown, payload, risk, automation, licences = out["result"]

    # 4x-UltraSharp is CC BY-NC-SA: reported as a count, not as a ruling.
    assert report.licensing.counts.get("non-commercial", 0) >= 1
    assert "licence" in licences
    assert 0 <= risk <= 100 and 0 <= automation <= 100
    assert "## Summary" in markdown
    assert json.loads(payload)["schema"] == "comfyaudit/1"


def test_the_custom_lora_is_found_because_the_node_named_it(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    out = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )
    report = out["result"][0]
    lora = next(m for m in report.models if m.filename.startswith("studio_skin"))
    assert lora.folder == "loras"
    assert lora.widget == "lora_name_1"
    assert lora.strength == 0.8


def test_local_presence_is_verified_against_folder_paths(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    out = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )
    report = out["result"][0]
    assert report.missing_models == []
    assert any("installed:" in n for m in report.models for n in m.notes)
    assert not any(f.id == "runtime.not-verified-locally" for f in report.risk.findings)


def test_a_missing_model_becomes_a_critical_finding(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    doc = json.loads(json.dumps(WORKFLOW))
    doc["nodes"][0]["widgets_values"] = ["not_installed_anywhere.safetensors"]
    out = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": doc},
    )
    report = out["result"][0]
    assert [m.filename for m in report.missing_models] == ["not_installed_anywhere.safetensors"]
    assert any(f.id == "runtime.missing-models" for f in report.risk.findings)


def test_audit_node_falls_back_to_the_api_prompt(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    prompt = {"1": {"class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}}}
    out = ComfyAuditWorkflow().run(
        source="the running prompt (API format)", check_local_models=False,
        online_lookups=False, prompt=prompt,
    )
    report = out["result"][0]
    assert report.source["format"] == "api"
    assert len(report.models) == 1


def test_audit_node_explains_itself_when_metadata_is_disabled(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    with pytest.raises(ValueError, match="disable-metadata"):
        ComfyAuditWorkflow().run(
            source="this workflow (UI graph)", check_local_models=False,
            online_lookups=False, prompt=None, extra_pnginfo=None,
        )


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------


def test_the_gate_ignores_licence_position_unless_asked(comfy):
    """Licence policy is the operator's call, so it is off by default."""
    from comfyaudit.nodes.audit_nodes import ComfyAuditGate, ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]
    assert report.licensing.counts.get("non-commercial", 0) >= 1

    _, verdict = ComfyAuditGate().run(audit=report, fail_on="critical")
    assert verdict.startswith("passed")

    with pytest.raises(RuntimeError, match="stop_on_non_commercial"):
        ComfyAuditGate().run(audit=report, fail_on="critical",
                             stop_on_non_commercial=True)


def test_the_two_gate_conditions_are_independent(comfy):
    """The severity gate and the licence switch must not shadow each other."""
    from comfyaudit.nodes.audit_nodes import ComfyAuditGate, ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]

    with pytest.raises(RuntimeError) as caught:
        ComfyAuditGate().run(audit=report, fail_on="medium",
                             stop_on_non_commercial=False)
    assert "stop_on_non_commercial" not in str(caught.value)
    assert "at or above 'medium'" in str(caught.value)


def test_a_locally_installed_pack_is_not_reported_as_unidentified(comfy):
    """LoraStackAdvanced is in no public registry, but it is installed here."""
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]
    assert not any(f.id == "dependency.unidentified" for f in report.risk.findings)

    pack = next(p for p in report.packs if "LoraStackAdvanced" in p.node_types)
    assert pack.identified is True
    assert pack.pinned_version == "2.4.1"
    assert any("studio-inhouse-nodes" in n for n in pack.notes)


def test_gate_passes_a_clean_workflow(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditGate, ComfyAuditWorkflow

    doc = {"nodes": [WORKFLOW["nodes"][0]], "links": [], "version": 0.4}
    doc["nodes"][0] = dict(doc["nodes"][0],
                           widgets_values=["sd_xl_base_1.0.safetensors"], inputs=[])
    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": doc},
    )["result"][0]

    _, verdict = ComfyAuditGate().run(audit=report, fail_on="critical")
    assert verdict.startswith("passed")


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------


def test_save_report_writes_every_format(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditSaveReport, ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]

    paths = ComfyAuditSaveReport().run(
        audit=report, format="all", filename_prefix="audits/test")["result"][0]
    written = paths.split("\n")
    assert len(written) == 3
    for path in written:
        assert os.path.isfile(path) and os.path.getsize(path) > 0
    assert any(p.endswith(".html") for p in written)


# --------------------------------------------------------------------------
# The agent, without the SDK installed
# --------------------------------------------------------------------------


def test_agent_reports_why_it_cannot_run():
    from comfyaudit.agent import reviewer

    ok, why = reviewer.available()
    if ok:
        pytest.skip("anthropic is installed in this environment")
    assert "anthropic" in why or "credentials" in why


def test_review_returns_a_result_object_rather_than_raising():
    from comfyaudit.agent import reviewer
    from comfyaudit.core.audit import AuditReport

    result = reviewer.review(AuditReport(), mode="full")
    if result.ran:
        pytest.skip("anthropic is installed and configured")
    assert result.error
    assert result.as_dict()["ran"] is False


def test_a_skipped_review_still_renders():
    from comfyaudit.agent.reviewer import AgentResult
    from comfyaudit.nodes.audit_nodes import render_review

    text = render_review(AgentResult(error="no key"))
    assert "Not run" in text and "no key" in text


# --------------------------------------------------------------------------
# Review rendering and merge-back
# --------------------------------------------------------------------------


FAKE_REVIEW = {
    "ran": True, "mode": "full", "model": "claude-opus-5",
    "web_search_enabled": True, "usage": {"turns": 7}, "tool_calls": ["list_models()"],
    "summary": "The face pipeline is the blocker; everything else is housekeeping.",
    "identifications": [{
        "filename": "juggernautXL_v9Rundiffusion.safetensors",
        "family": "Juggernaut XL v9", "base_model": "SDXL 1.0",
        "licence": "CreativeML Open RAIL++-M", "commercial_use": "conditional",
        "confidence": "medium", "reasoning": "An SDXL community merge.",
        "verify_at": "https://civitai.com/models/133005",
    }],
    "content_risks": [{
        "kind": "artist-style", "excerpt": "in the style of a living illustrator",
        "where": "Positive", "severity": "high",
        "detail": "Naming a living artist as a style reference is a clearance question.",
        "recommendation": "Describe the look instead of naming the artist.",
    }],
    "substitutions": [{
        "replace": "4x-UltraSharp.pth", "replace_with": "RealESRGAN_x4plus.pth",
        "licence": "BSD-3-Clause, commercially clear", "available_locally": True,
        "quality_impact": "Slightly softer on fine hair detail.",
        "rationale": "Already installed and does the same job here.",
    }],
    "actions": [{"order": 1, "title": "Swap the upscaler", "detail": "One node change.",
                 "owner": "pipeline"}],
    "error": "",
}


def _report_with_review(comfy_dir):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]
    report.diagnostics["claude_review"] = FAKE_REVIEW
    return report


def test_review_appears_in_the_markdown_report(comfy):
    from comfyaudit.core.report import markdown as md_report

    text = md_report.render(_report_with_review(comfy))
    assert "## 7. Claude review" in text
    assert "Juggernaut XL v9" in text
    assert "RealESRGAN_x4plus.pth" in text
    assert "not by a rule" in text          # the provenance disclaimer


def test_review_appears_in_the_html_report(comfy):
    from comfyaudit.core.report import html as html_report

    page = html_report.render(_report_with_review(comfy))
    assert "Claude review" in page
    assert "Model-derived" in page
    assert "<script" not in page            # still self-contained


def test_a_skipped_review_is_not_rendered_as_content(comfy):
    from comfyaudit.core.report import markdown as md_report
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]
    report.diagnostics["claude_review"] = {"ran": False, "error": "no key"}
    text = md_report.render(report)
    assert "**Not run.** no key" in text


def test_agent_findings_are_merged_and_labelled(comfy):
    from comfyaudit.agent.reviewer import AgentResult, apply_to_report
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]
    before = report.risk.score

    result = AgentResult(ran=True, mode="full", model="claude-opus-5")
    result.content_risks = FAKE_REVIEW["content_risks"]
    result.identifications = FAKE_REVIEW["identifications"]
    apply_to_report(report, result)

    ids = {f.id for f in report.risk.findings}
    assert "ai.content-risk" in ids
    assert "ai.model-identification" in ids
    assert report.risk.score >= before
    # Every model-derived finding must say so in its own text.
    for finding in report.risk.findings:
        if finding.id.startswith("ai."):
            assert "Claude" in finding.detail


def test_merging_a_skipped_review_changes_nothing(comfy):
    from comfyaudit.agent.reviewer import AgentResult, apply_to_report
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]
    before = (report.risk.score, len(report.risk.findings))
    apply_to_report(report, AgentResult(ran=False, error="no key"))
    assert (report.risk.score, len(report.risk.findings)) == before


def test_the_audit_reruns_when_the_graph_changes_and_not_otherwise(comfy):
    """Caching has to track the graph, not the node's own widgets.

    Returning a timestamp here would be simpler but would re-run a paid Claude
    review on every unrelated queue.
    """
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    args = dict(source="this workflow (UI graph)", check_local_models=True,
                online_lookups=False)
    same_a = ComfyAuditWorkflow.IS_CHANGED(extra_pnginfo={"workflow": WORKFLOW}, **args)
    same_b = ComfyAuditWorkflow.IS_CHANGED(extra_pnginfo={"workflow": WORKFLOW}, **args)
    assert same_a == same_b

    changed = json.loads(json.dumps(WORKFLOW))
    changed["nodes"][0]["widgets_values"] = ["sd_xl_base_1.0.safetensors"]
    assert ComfyAuditWorkflow.IS_CHANGED(
        extra_pnginfo={"workflow": changed}, **args) != same_a

    args["online_lookups"] = True
    assert ComfyAuditWorkflow.IS_CHANGED(
        extra_pnginfo={"workflow": WORKFLOW}, **args) != same_a


# --------------------------------------------------------------------------
# Source selection
# --------------------------------------------------------------------------


def test_source_list_parsing():
    from comfyaudit.core.resolve.resolver import ALL_SOURCES
    from comfyaudit.nodes.audit_nodes import parse_sources

    assert parse_sources("") == ALL_SOURCES
    assert parse_sources("civitai,github") == ("civitai", "github")
    assert parse_sources(" CIVITAI , GitHub ") == ("civitai", "github")
    # An unusable list falls back to everything rather than silently doing nothing.
    assert parse_sources("nonsense") == ALL_SOURCES


def test_the_audit_node_stays_offline_by_default(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditWorkflow

    report = ComfyAuditWorkflow().run(
        source="this workflow (UI graph)", check_local_models=True,
        online_lookups=False, extra_pnginfo={"workflow": WORKFLOW},
    )["result"][0]
    assert report.diagnostics["online"] is False
    assert report.diagnostics["http_requests"] == 0
    assert report.diagnostics["sources"] == []


# --------------------------------------------------------------------------
# The web overlay and the route it consumes
# --------------------------------------------------------------------------


def test_the_overlay_only_reads_summary_keys_the_route_sends(comfy):
    """Guard the seam between the Python payload and the browser.

    Nothing type-checks across that boundary, so a renamed field shows up as
    ``undefined`` in the UI and nowhere else. This asserts every ``summary.x``
    the script reads is a key the route actually emits.
    """
    import re
    from pathlib import Path

    from comfyaudit.nodes.audit_nodes import run_audit
    from comfyaudit.server import routes

    report = run_audit(WORKFLOW, online=False)
    sent = set(routes._payload(report)["summary"])

    script = Path(routes.__file__).resolve().parents[1] / "web" / "comfyaudit.js"
    read = set(re.findall(r"summary\.([A-Za-z_]\w*)", script.read_text()))

    assert read, "the overlay reads nothing from the summary - has it moved?"
    assert read <= sent, f"overlay reads keys the route never sends: {sorted(read - sent)}"


# --------------------------------------------------------------------------
# The studio profile, and the determination it makes possible
# --------------------------------------------------------------------------


def test_no_profile_means_no_determination(comfy):
    from comfyaudit.nodes.audit_nodes import run_audit

    report = run_audit(WORKFLOW, online=False)
    assert report.clearance.determined is False
    assert report.clearance.verdict == "unknown"
    # It should still say what would settle it, rather than going quiet.
    assert report.clearance.missing_facts


def test_the_profile_node_maps_its_widgets_onto_engine_keys(comfy):
    from comfyaudit.nodes.audit_nodes import ComfyAuditStudioProfile

    profile = ComfyAuditStudioProfile().build(
        territory="United Kingdom", annual_revenue="$10M - $20M",
        what_ships="software containing this workflow",
        real_performers=True, studio_name="Example Post")[0]
    assert (profile.territory, profile.revenue_band, profile.ships) == (
        "GB", "10m-20m", "software")
    assert profile.likeness_involved is True
    assert profile.is_set


def test_every_profile_widget_choice_is_a_key_the_engine_knows(comfy):
    """The canvas shows English; the engine wants keys. Guard the mapping."""
    from comfyaudit.core.score import clearance
    from comfyaudit.nodes import audit_nodes as n

    assert set(n.TERRITORY_CHOICES_MAP.values()) - {""} <= set(clearance.TERRITORIES)
    assert set(n.REVENUE_CHOICES_MAP.values()) <= set(clearance.REVENUE_BANDS)
    assert set(n.SHIP_CHOICES_MAP.values()) <= set(clearance.SHIPS)


def test_a_stricter_profile_is_never_given_a_kinder_verdict(comfy):
    """The whole point: a licence grants rights to someone, somewhere."""
    from comfyaudit.core.score.clearance import VERDICT_RANK, StudioProfile
    from comfyaudit.nodes.audit_nodes import run_audit

    def verdict(**profile):
        return run_audit(WORKFLOW, online=False,
                         profile=StudioProfile.from_dict(profile)).clearance

    strict = verdict(territory="US", revenue_band="over-100m", ships="service")
    lenient = verdict(territory="CA", revenue_band="under-1m", ships="internal-only")
    assert strict.determined and lenient.determined
    assert VERDICT_RANK[strict.verdict] <= VERDICT_RANK[lenient.verdict]


# --------------------------------------------------------------------------
# The profile as a ComfyUI setting
# --------------------------------------------------------------------------


def _write_settings(tmp_path, **values):
    """Write a comfy.settings.json the way the frontend would."""
    directory = tmp_path / "user" / "default"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "comfy.settings.json").write_text(json.dumps(values))


def test_no_settings_file_means_no_profile(comfy):
    from comfyaudit.server import settings

    assert settings.studio_profile() is None


def test_a_settings_file_of_defaults_still_means_no_profile(comfy):
    """"not set" everywhere is an operator declining to say, not a profile."""
    from comfyaudit.server import settings

    _write_settings(comfy, **{settings.TERRITORY: "not set",
                              settings.REVENUE: "not set",
                              settings.SHIPS: "not set"})
    assert settings.studio_profile() is None


def test_the_settings_profile_is_read_and_used_by_default(comfy):
    from comfyaudit.server import settings
    from comfyaudit.nodes.audit_nodes import run_audit

    _write_settings(comfy, **{settings.TERRITORY: "United Kingdom",
                              settings.REVENUE: "$10M - $20M",
                              settings.SHIPS: "finished frames to a client",
                              settings.LIKENESS: True,
                              settings.LABEL: "Example Post"})
    profile = settings.studio_profile()
    assert (profile.territory, profile.revenue_band, profile.ships) == (
        "GB", "10m-20m", "deliverable-only")
    assert profile.label == "Example Post"

    report = run_audit(WORKFLOW, online=False)
    assert report.clearance.determined
    assert report.clearance.profile.territory == "GB"


def test_a_node_profile_overrides_the_settings_one(comfy):
    """The settings describe the facility; a node states a per-show exception."""
    from comfyaudit.core.score.clearance import StudioProfile
    from comfyaudit.server import settings
    from comfyaudit.nodes.audit_nodes import run_audit

    _write_settings(comfy, **{settings.TERRITORY: "United Kingdom"})
    report = run_audit(WORKFLOW, online=False,
                       profile=StudioProfile.from_dict({"territory": "CA"}))
    assert report.clearance.profile.territory == "CA"


def test_a_corrupt_settings_file_does_not_break_a_queued_audit(comfy):
    """ComfyUI's own settings being broken is not this pack's crash to take."""
    from comfyaudit.nodes.audit_nodes import run_audit
    from comfyaudit.server import settings

    directory = comfy / "user" / "default"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "comfy.settings.json").write_text("{ this is not json")

    assert settings.studio_profile() is None
    assert run_audit(WORKFLOW, online=False).clearance.determined is False


def test_the_settings_ids_match_the_ones_the_frontend_registers(comfy):
    """Nothing type-checks across the JS/Python seam.

    A renamed setting id would silently mean the dialog writes one key and the
    audit reads another, and the only symptom would be a profile that never
    seems to take effect.
    """
    import re
    from pathlib import Path

    from comfyaudit.server import settings

    script = Path(settings.__file__).resolve().parents[1] / "web" / "comfyaudit.js"
    text = script.read_text()
    registered = set(re.findall(r'id:\s*"(ComfyAudit\.[\w.]+)"', text))
    expected = {settings.TERRITORY, settings.REVENUE, settings.SHIPS,
                settings.TRAINS, settings.LIKENESS, settings.LABEL,
                settings.REGISTRY}
    assert registered == expected

    # ...and every choice the dialog offers has to be one Python can map.
    for options, mapping in ((r'"ComfyAudit\.Studio\.Territory"', settings.TERRITORY_OPTIONS),
                             (r'"ComfyAudit\.Studio\.Revenue"', settings.REVENUE_OPTIONS),
                             (r'"ComfyAudit\.Studio\.Ships"', settings.SHIP_OPTIONS)):
        block = text[text.index(re.search(options, text).group()):]
        listed = re.search(r"options:\s*\[(.*?)\]", block, re.S).group(1)
        for choice in re.findall(r'"([^"]+)"', listed):
            assert choice in mapping, f"{choice!r} has no mapping in settings.py"


def test_the_registry_path_comes_from_the_settings_too(comfy):
    """It describes the installation, so it belongs beside the profile."""
    from comfyaudit.core import registry as reg
    from comfyaudit.nodes.audit_nodes import run_audit
    from comfyaudit.server import settings

    path = str(comfy / "cleared.json")
    reg.Registry([reg.Entry(key="juggernautXL_v9Rundiffusion.safetensors",
                            status="rejected",
                            note="not for this show")]).save(path)
    _write_settings(comfy, **{settings.REGISTRY: path})

    assert settings.registry_path() == path
    report = run_audit(WORKFLOW, online=False)
    assert report.registry.loaded
    assert any("not for this show" in m.detail for m in report.registry.rejected)


def test_no_registry_setting_means_no_registry_section(comfy):
    from comfyaudit.nodes.audit_nodes import run_audit

    assert run_audit(WORKFLOW, online=False).registry.loaded is False
