"""End-to-end behaviour: extraction, licensing, scoring and reporting."""

from __future__ import annotations

import json
import os

import pytest

from comfyaudit.core import graph
from comfyaudit.core.audit import AuditOptions, run
from comfyaudit.core.extract import assets, models, packs, prompts
from comfyaudit.core.knowledge.licences import LicenceMatcher
from comfyaudit.core.records import ModelRef
from comfyaudit.core.report import html as html_report
from comfyaudit.core.report import markdown as md_report

EXAMPLES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="module")
def beauty():
    return run(os.path.join(EXAMPLES, "beauty-pass.json"))


@pytest.fixture(scope="module")
def clean():
    return run(os.path.join(EXAMPLES, "clean-batch.json"))


# -- model extraction -------------------------------------------------------


def test_every_weight_reference_is_found(beauty):
    found = {m.filename for m in beauty.models}
    for expected in (
        "juggernautXL_v9Rundiffusion.safetensors",
        "studio_skin_detail_v3.safetensors",
        "ip-adapter-faceid-plusv2_sdxl.bin",
        "codeformer-v0.1.0.pth",
        "4x-UltraSharp.pth",
        "bbox/face_yolov8m.pt",
    ):
        assert expected in found, f"missed {expected}"


def test_models_on_custom_nodes_are_found_without_a_schema(beauty):
    """IPAdapterFaceID is not a core node, so only the value shape identifies it."""
    faceid = [m for m in beauty.models if m.node_type == "IPAdapterFaceID"]
    assert len(faceid) == 2


def test_lora_strength_is_paired_with_its_file(beauty):
    lora = next(m for m in beauty.models if m.folder == "loras"
                and m.filename.startswith("studio_skin"))
    assert lora.strength == 0.75


def test_embeddings_in_prompt_text_become_model_references(beauty):
    embeddings = {m.filename for m in beauty.models if m.folder == "embeddings"}
    assert embeddings == {"studio_look_v2", "easynegative"}


def test_disabled_nodes_are_reported_but_marked(beauty):
    alt = next(m for m in beauty.models if m.filename == "RealESRGAN_x4plus.pth")
    assert alt.enabled is False


def test_model_folder_is_taken_from_the_node_schema(clean):
    unet = next(m for m in clean.models if m.filename == "flux1-schnell.safetensors")
    assert unet.folder == "diffusion_models"
    assert unet.role == "Diffusion model (UNet)"


# -- prompts ----------------------------------------------------------------


def test_prompt_polarity_comes_from_sampler_wiring(beauty):
    positive = [p for p in beauty.prompts if p.polarity == "positive"]
    negative = [p for p in beauty.prompts if p.polarity == "negative"]
    assert len(positive) == 1 and len(negative) == 1
    assert "cinematic portrait" in positive[0].text
    assert "plastic skin" in negative[0].text


def test_prompt_syntax_dependencies_are_surfaced(beauty):
    positive = next(p for p in beauty.prompts if p.polarity == "positive")
    assert positive.wildcards == ["lighting_setups"]
    assert positive.dynamic_syntax is True
    assert "studio_look_v2" in positive.embeddings


def test_a_linked_prompt_is_not_counted_as_typed(clean):
    encoded = next(p for p in clean.prompts if p.node_type == "PrimitiveStringMultiline")
    assert encoded.polarity in ("positive", "both")


def test_notes_are_separated_from_prompts(beauty):
    assert len(beauty.notes) == 1
    assert "SETUP" in beauty.notes[0].text
    assert all(n.node_type == "Note" for n in beauty.notes)


# -- assets -----------------------------------------------------------------


def test_upload_inputs_and_absolute_paths_are_flagged(beauty):
    uploads = [a for a in beauty.inputs if a.upload_widget]
    assert len(uploads) == 2
    absolute = [a for a in beauty.inputs if a.absolute_path]
    assert len(absolute) == 1
    assert absolute[0].value.startswith("D:/")


def test_output_destinations_are_recorded(beauty):
    prefixes = {o.value for o in beauty.outputs}
    assert "DEMO_SHOW/sh0120/beauty_v" in prefixes


# -- packs ------------------------------------------------------------------


def test_custom_packs_are_identified_from_node_class_names(beauty):
    titles = {p.title for p in beauty.packs}
    assert any("Impact Pack" in t for t in titles)
    assert any("IPAdapter" in t for t in titles)


def test_pinned_version_is_read_from_node_properties(beauty):
    impact = next(p for p in beauty.packs if "Impact Pack" in p.title)
    assert impact.pinned_version == "8.15.3"


def test_missing_version_is_reported_as_unpinned(beauty):
    ipadapter = next(p for p in beauty.packs if "IPAdapter" in p.title)
    assert ipadapter.pinned_version == ""


def test_core_only_workflow_needs_no_packs(clean):
    assert clean.packs == []
    assert "KSampler" in clean.core_node_types


# -- licensing --------------------------------------------------------------


def test_non_commercial_models_are_caught(beauty):
    blocked = {m.filename for m in beauty.models
               if m.license and m.license.commercial_use == "no"}
    assert "codeformer-v0.1.0.pth" in blocked
    assert "ip-adapter-faceid-plusv2_sdxl.bin" in blocked
    assert "4x-UltraSharp.pth" in blocked


def test_non_commercial_models_are_counted_not_judged(beauty):
    """The report states the composition; it does not rule on it."""
    counts = beauty.licensing.counts
    assert counts.get("non-commercial", 0) >= 3
    assert "non-commercial" in beauty.licensing.headline
    # No verdict field exists any more - that was the point of the change.
    assert not hasattr(beauty.risk, "commercial_verdict")


def test_licences_are_grouped_with_their_models(beauty):
    group = next(g for g in beauty.licensing.groups if "CodeFormer" in g.licence)
    assert group.position == "non-commercial"
    assert "codeformer-v0.1.0.pth" in group.models
    assert group.url


def test_permissive_workflow_reads_as_permissive(clean):
    assert clean.licensing.counts.get("permissive", 0) >= 1
    schnell = next(m for m in clean.models if "schnell" in m.filename)
    assert schnell.license.commercial_use == "yes"


def test_obligations_are_surfaced_separately(beauty):
    """Attribution and revenue caps are easy to miss at delivery."""
    text = " ".join(beauty.licensing.obligations)
    assert "Attribution" in text or "licence must be obtained" in text


def test_low_confidence_entries_are_flagged_for_checking(beauty):
    assert beauty.licensing.to_verify
    assert any("no licence could be identified" in item
               for item in beauty.licensing.to_verify)


def test_licence_position_does_not_move_the_risk_score(beauty):
    """Policy is the reader's; the score measures operational readiness only."""
    ids = {f.id for f in beauty.risk.findings}
    assert not any(i.startswith("licence.") for i in ids)
    assert "licensing" not in beauty.risk.by_category


def test_schnell_is_not_confused_with_dev():
    """The longest matching token must win, or every FLUX model reads as dev."""
    matcher = LicenceMatcher()
    dev = matcher.for_model(ModelRef(filename="flux1-dev.safetensors", folder="diffusion_models"))
    schnell = matcher.for_model(ModelRef(filename="flux1-schnell.safetensors",
                                         folder="diffusion_models"))
    assert dev.commercial_use == "no"
    assert schnell.commercial_use == "yes"


def test_separator_style_does_not_change_the_verdict():
    matcher = LicenceMatcher()
    for name in ("flux1_dev.safetensors", "FLUX.1-dev.sft", "flux1-dev-fp8.safetensors"):
        assert matcher.for_model(ModelRef(filename=name)).commercial_use == "no", name


def test_unmatched_lora_gets_the_folder_default_warning():
    matcher = LicenceMatcher()
    info = matcher.for_model(ModelRef(filename="some_random_style.safetensors", folder="loras"))
    assert info.commercial_use == "unknown"
    assert "trained it" in info.summary


def test_studio_overrides_take_precedence(tmp_path):
    override = tmp_path / "studio.json"
    override.write_text(json.dumps({
        "licences": {"inhouse": {"name": "Studio internal", "commercial_use": "yes",
                                 "fee": "none", "summary": "Owned by the facility."}},
        "models": [{"id": "inhouse-lora", "family": "House LoRA", "licence": "inhouse",
                    "match": {"filename": ["studio_skin_detail"]}, "confidence": "high"}],
    }))
    matcher = LicenceMatcher(str(override))
    info = matcher.for_model(ModelRef(filename="studio_skin_detail_v3.safetensors",
                                      folder="loras"))
    assert info.commercial_use == "yes"
    assert "Studio internal" in info.name


# -- scoring ----------------------------------------------------------------


def test_hands_on_workflow_scores_low_on_automation(beauty):
    assert beauty.automation.index < 40
    labels = " ".join(t.label for t in beauty.automation.per_run_touchpoints)
    assert "Supply image" in labels
    assert "fixed seed" in labels


def test_batch_workflow_scores_high_on_automation(clean):
    assert clean.automation.index >= 85
    # Its one touchpoint is a prompt on a standalone string node, which a
    # submission script can overwrite - cheap, but still not zero.
    touchpoints = clean.automation.per_run_touchpoints
    assert len(touchpoints) == 1
    assert touchpoints[0].cost <= 0.5
    assert "dedicated input node" in touchpoints[0].label


def test_a_prompt_on_a_string_node_costs_less_than_one_inside_an_encoder(beauty, clean):
    embedded = next(t for t in beauty.automation.per_run_touchpoints
                    if "static prompt" in t.label)
    injectable = next(t for t in clean.automation.per_run_touchpoints
                      if "dedicated input node" in t.label)
    assert injectable.cost < embedded.cost


def test_setup_cost_is_kept_out_of_the_headline_index(beauty):
    assert beauty.automation.setup_cost > 0
    setup_labels = " ".join(t.label for t in beauty.automation.setup_touchpoints)
    assert "custom node pack" in setup_labels


def test_risky_workflow_scores_higher_than_the_clean_one(beauty, clean):
    assert beauty.risk.score > clean.risk.score


def test_findings_carry_evidence_and_a_recommendation(beauty):
    for finding in beauty.risk.findings:
        assert finding.detail
        if finding.severity in ("critical", "high"):
            assert finding.recommendation, finding.id


def test_identity_models_raise_a_likeness_finding(beauty):
    ids = {f.id for f in beauty.risk.findings}
    assert "data.likeness" in ids


def test_unpinned_packs_raise_a_reproducibility_finding(beauty):
    ids = {f.id for f in beauty.risk.findings}
    assert "dependency.unpinned" in ids


# -- reporting --------------------------------------------------------------


def test_markdown_report_covers_every_section(beauty):
    text = md_report.render(beauty)
    for heading in ("## Summary", "## 1. Licence summary", "## 2. Models",
                    "## 3. Prompts", "## 4. Assets", "## 5. Node dependencies",
                    "## 6. Where a human has to be",
                    "## 7. Operational risks"):
        assert heading in text
    assert "not legal advice" in text
    assert "does not decide whether they suit your job" in text


def test_a_determination_inserts_a_section_and_renumbers_the_rest(beauty):
    """Numbering has to survive a section that is only sometimes there."""
    determined = md_report.render(_with_profile(beauty, territory="US",
                                                revenue_band="over-100m"))
    assert "## 1. What has to happen" in determined
    assert "## 2. Licence summary" in determined
    assert "## 8. Operational risks" in determined
    # ...and the profile-less report keeps its original numbering.
    assert "## 1. Licence summary" in md_report.render(beauty)


def _with_profile(report, **profile):
    """A copy of a report, determined against a studio profile.

    A copy rather than a mutation: the fixture is shared, and a test that leaves
    a profile on it changes what every later test sees.
    """
    import copy

    from comfyaudit.core.score import clearance

    out = copy.deepcopy(report)
    out.clearance = clearance.determine(
        out.models, packs=out.packs,
        profile=clearance.StudioProfile.from_dict(profile),
        api_node_types=out.api_node_types)
    return out


def test_markdown_escapes_pipes_so_tables_do_not_break():
    ref = ModelRef(filename="weird|name.safetensors", folder="loras", node_label="a|b")
    assert "\\|" in md_report._cell(ref.filename)


def test_html_report_is_self_contained(beauty):
    page = html_report.render(beauty)
    assert page.startswith("<!doctype html>")
    # No scripts and no remotely loaded assets: the file has to survive being
    # archived next to the show and opened years later on an offline machine.
    assert "<script" not in page
    assert 'src="http' not in page and "src='http" not in page
    assert "<img" not in page


def test_html_report_defines_both_themes_at_token_level(beauty):
    page = html_report.render(beauty)
    assert "prefers-color-scheme" in page
    assert '[data-theme="dark"]' in page
    assert ':root:not([data-theme="light"])' in page


def test_html_body_fragment_omits_the_document_skeleton(beauty):
    fragment = html_report.render(beauty, standalone=False)
    for tag in ("<!doctype", "<html", "<head>", "<body"):
        assert tag not in fragment.lower()
    assert "<style>" in fragment


def test_json_report_round_trips(beauty):
    payload = json.loads(json.dumps(beauty.to_dict()))
    assert payload["schema"] == "comfyaudit/1"
    assert payload["summary"]["licences"] == beauty.licensing.headline
    assert payload["licensing"]["counts"]["non-commercial"] >= 3
    assert len(payload["models"]) == len(beauty.models)


def test_offline_audit_makes_no_network_calls(beauty):
    assert beauty.diagnostics["online"] is False
    assert beauty.diagnostics["http_requests"] == 0


# -- matcher precision (regressions) ----------------------------------------


@pytest.mark.parametrize("filename", [
    "flux2-vae.safetensors",
    "ace_1.5_vae.safetensors",
    "minimax_h3_audio_vae_fp32.safetensors",
    "ema_vae_fp16.safetensors",
    "wan_2.1_vae.safetensors",
])
def test_unrelated_vae_files_do_not_inherit_the_flux_licence(filename):
    """`ae.safetensors` must not match every file ending in `vae`.

    An over-broad pattern here reads as a false non-commercial verdict, which is
    the most damaging mistake this tool can make.
    """
    info = LicenceMatcher().for_model(ModelRef(filename=filename, folder="vae"))
    assert info.commercial_use != "no", f"{filename} wrongly blocked by {info.matched_on}"


def test_the_bare_flux_autoencoder_still_matches():
    info = LicenceMatcher().for_model(ModelRef(filename="ae.safetensors", folder="vae"))
    assert "FLUX" in info.name


def test_short_patterns_do_not_match_inside_longer_words():
    matcher = LicenceMatcher()
    # "clip_l" must not claim an unrelated "clip_large_custom" checkpoint.
    info = matcher.for_model(ModelRef(filename="clip_large_custom_v2.safetensors",
                                      folder="text_encoders"))
    assert info.commercial_use == "unknown"


def test_quantised_and_scaled_variants_still_match():
    matcher = LicenceMatcher()
    for name in ("flux1-dev-fp8.safetensors", "flux1-dev-kontext_fp8_scaled.safetensors",
                 "depth_anything_v2_vitl_fp32.safetensors"):
        assert matcher.for_model(ModelRef(filename=name)).commercial_use == "no", name


# --------------------------------------------------------------------------
# The plain-language summary
# --------------------------------------------------------------------------


def test_a_report_without_a_profile_explains_itself_in_prose(beauty):
    """The tables assume a reader who knows how to read them. This does not."""
    from comfyaudit.core.report import narrative

    paragraphs = narrative.summarise(beauty)
    assert paragraphs
    text = " ".join(paragraphs)
    assert "This workflow runs" in text
    # It describes, and it never rules. Offering a verdict as something a
    # profile would unlock is fine; asserting one is not.
    from comfyaudit.core.score import clearance

    for label in clearance.VERDICT_LABELS.values():
        assert label.lower() not in text.lower()


def test_the_narrative_leads_every_report(beauty):
    """It runs with or without a profile - an assessment still needs explaining."""
    text = md_report.render(beauty)
    assert "### In plain terms" in text
    assert text.index("### In plain terms") < text.index("## 1. Licence summary")

    determined = md_report.render(_with_profile(beauty, territory="US",
                                                revenue_band="over-100m"))
    assert "### In plain terms" in determined
    assert determined.index("### In plain terms") < determined.index(
        "## 1. What has to happen")


def test_the_narrative_counts_agree_with_their_nouns(beauty):
    """"1 node type(s)" reads like a machine wrote it, because one did."""
    from comfyaudit.core.report import narrative

    text = " ".join(narrative.summarise(beauty))
    assert "(s)" not in text


def test_the_narrative_survives_a_workflow_with_nothing_in_it():
    """An empty graph must produce prose, not an exception or a blank."""
    from comfyaudit.core import audit
    from comfyaudit.core.report import narrative

    empty = audit.AuditReport()
    paragraphs = narrative.summarise(empty)
    assert paragraphs
    assert "nothing here to licence" in " ".join(paragraphs)
