"""Provenance sources, exercised against stub HuggingFace/Civitai/GitHub servers.

The responses below use the real wire shapes, taken from upstream source rather
than from memory: HuggingFace's from ``huggingface_hub``'s ``ModelInfo`` (note
the camelCase ``lastModified`` / ``baseModels`` / ``securityRepoStatus`` that the
Python client renames), Civitai's from their ``model.schema.ts``, GitHub's from
the documented repository object.

If any of these ever stops matching production, that is a bug in the fixture as
much as in the code, and this file is where to fix it.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from comfyaudit.core.knowledge.licences import LicenceMatcher
from comfyaudit.core.records import ModelRef, PackRef
from comfyaudit.core.resolve import sources as src
from comfyaudit.core.resolve.http import Credentials, HttpClient
from comfyaudit.core.resolve.resolver import Resolver

# --------------------------------------------------------------------------
# Fixtures: real response shapes
# --------------------------------------------------------------------------

HF_FLUX_DEV = {
    "id": "black-forest-labs/FLUX.1-dev",
    "author": "black-forest-labs",
    "sha": "0ef5fff789c832c5c7f4e127f94c8b54bbcced44",
    "lastModified": "2026-03-11T14:02:55.000Z",
    "createdAt": "2024-07-31T21:13:44.000Z",
    "private": False,
    "gated": "manual",
    "disabled": False,
    "downloads": 1_842_113,
    "downloadsAllTime": 41_002_774,
    "likes": 10_233,
    "library_name": "diffusers",
    "tags": ["diffusers", "text-to-image", "flux", "license:other"],
    "pipeline_tag": "text-to-image",
    "cardData": {
        "license": "other",
        "license_name": "flux-1-dev-non-commercial-license",
        "license_link": "https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md",
        "tags": ["text-to-image", "flux"],
    },
    "siblings": [
        {"rfilename": "flux1-dev.safetensors"},
        {"rfilename": "ae.safetensors"},
        {"rfilename": "LICENSE.md"},
    ],
    "securityRepoStatus": {"scansDone": True, "filesWithIssues": []},
}

HF_DERIVED_LORA = {
    "id": "someone/cinematic-flux-lora",
    "author": "someone",
    "lastModified": "2026-05-02T09:00:00.000Z",
    "gated": False,
    "downloads": 4200,
    "likes": 88,
    "tags": ["lora", "diffusers"],
    # The author claims Apache; the hub records what it was trained on.
    "cardData": {"license": "apache-2.0", "base_model": "black-forest-labs/FLUX.1-dev"},
    "baseModels": ["black-forest-labs/FLUX.1-dev"],
    "siblings": [{"rfilename": "cinematic_flux_v2.safetensors"}],
}

HF_MISLABELLED = {
    "id": "reuploader/flux-mirror",
    "author": "reuploader",
    "lastModified": "2026-02-01T00:00:00.000Z",
    "gated": False,
    # Claims Apache on a file that is plainly FLUX.1 [dev]. This is what a
    # renamed or re-uploaded weight looks like from the outside.
    "cardData": {"license": "apache-2.0"},
    "siblings": [{"rfilename": "flux1-dev.safetensors"}],
}

#: Every HF repo the stub knows. The search endpoint returns all of them, which
#: is the realistic case: hub search ranks on names and popularity, and a mirror
#: can easily outrank the original. What makes a match trustworthy is the file
#: list check, not the search result.
HF_REPOS = {
    "reuploader/flux-mirror": HF_MISLABELLED,
    "black-forest-labs/FLUX.1-dev": HF_FLUX_DEV,
    "someone/cinematic-flux-lora": HF_DERIVED_LORA,
}

HF_UNRELATED = {
    "id": "other/unrelated",
    "author": "other",
    "cardData": {"license": "mit"},
    "siblings": [{"rfilename": "something_else.safetensors"}],
}

CIVITAI_VERSION = {
    "id": 128713,
    "modelId": 133005,
    "name": "v9 + RunDiffusion Photo",
    "baseModel": "SDXL 1.0",
    "publishedAt": "2026-01-14T18:22:03.000Z",
    "files": [{
        "name": "juggernautXL_v9Rundiffusion.safetensors",
        "sizeKB": 6938040.06,
        "type": "Model",
        "hashes": {"SHA256": "C9E3E68F89BFA8B6DA6D24F8CE31A5A5A4EAB1C9A3C4E2D1B0A9F8E7D6C5B4A3"},
        "downloadUrl": "https://civitai.com/api/download/models/128713",
    }],
}

CIVITAI_MODEL = {
    "id": 133005,
    "name": "Juggernaut XL",
    "type": "Checkpoint",
    "nsfw": False,
    "allowNoCredit": True,
    "allowCommercialUse": ["Image", "Rent"],
    "allowDerivatives": True,
    "allowDifferentLicense": False,
    "creator": {"username": "KandooAI"},
    "stats": {"downloadCount": 2_311_004, "thumbsUpCount": 41_233},
}

CIVITAI_NC_MODEL = {
    **CIVITAI_MODEL, "id": 999, "name": "A Flux LoRA",
    "allowCommercialUse": ["Sell"],          # the uploader claims full rights...
}
CIVITAI_NC_VERSION = {
    **CIVITAI_VERSION, "id": 998, "modelId": 999,
    "baseModel": "Flux.1 D",                 # ...on top of a non-commercial base
    "files": [{"name": "my_flux_lora.safetensors",
               "hashes": {"SHA256": "AB" * 32}}],
}

GITHUB_REPO = {
    "full_name": "cubiq/ComfyUI_IPAdapter_plus",
    "html_url": "https://github.com/cubiq/ComfyUI_IPAdapter_plus",
    "owner": {"login": "cubiq"},
    "stargazers_count": 6102,
    "pushed_at": "2026-04-14T11:20:31Z",
    "created_at": "2023-08-02T10:00:00Z",
    "default_branch": "main",
    "archived": False,
    "disabled": False,
    "license": {"key": "gpl-3.0", "name": "GNU General Public License v3.0",
                "spdx_id": "GPL-3.0"},
}

GITHUB_NO_LICENCE = {
    **GITHUB_REPO, "full_name": "someone/mystery-nodes",
    "html_url": "https://github.com/someone/mystery-nodes",
    "license": None, "stargazers_count": 12, "archived": True,
}

MIT_TEXT = ("MIT License\n\nCopyright (c) 2026 Someone\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining a "
            "copy of this software and associated documentation files.\n")


class StubAPI(BaseHTTPRequestHandler):
    """Serves all three APIs from one process, keyed by path."""

    calls: list = []
    headers_seen: list = []
    force_status: dict = {}

    def do_GET(self):  # noqa: N802
        StubAPI.calls.append(self.path)
        StubAPI.headers_seen.append(dict(self.headers))

        for prefix, status in StubAPI.force_status.items():
            if prefix in self.path:
                self._send(status, {"message": "forced"},
                           extra={"X-RateLimit-Remaining": "0",
                                  "X-RateLimit-Limit": "60"})
                return

        path = self.path
        body, status = self._route(path)
        if body is None:
            self._send(404, {"error": "not found"})
        elif isinstance(body, str):
            self._send_text(status, body)
        else:
            self._send(status, body)

    def _route(self, path):
        # HuggingFace
        if path.startswith("/api/models?search="):
            return [{"id": rid} for rid in HF_REPOS] + [{"id": "other/unrelated"}], 200
        if path.startswith("/api/models/other/unrelated"):
            return HF_UNRELATED, 200
        for repo_id, payload in HF_REPOS.items():
            if path.startswith(f"/api/models/{repo_id}"):
                return payload, 200
        # Civitai
        if "/model-versions/by-hash/" in path:
            digest = path.rsplit("/", 1)[-1].upper()
            if digest.startswith("C9E3"):
                return CIVITAI_VERSION, 200
            if digest.startswith("ABAB"):
                return CIVITAI_NC_VERSION, 200
            return None, 404
        if path.startswith("/api/v1/models/133005"):
            return CIVITAI_MODEL, 200
        if path.startswith("/api/v1/models/999"):
            return CIVITAI_NC_MODEL, 200
        if path.startswith("/api/v1/models?query="):
            return {"items": [{**CIVITAI_MODEL, "modelVersions": [CIVITAI_VERSION]}]}, 200
        # GitHub
        if path == "/repos/cubiq/ComfyUI_IPAdapter_plus":
            return GITHUB_REPO, 200
        if path == "/repos/someone/mystery-nodes":
            return GITHUB_NO_LICENCE, 200
        if path.endswith("/main/LICENSE"):
            return MIT_TEXT, 200
        return None, 404

    def _send(self, status, payload, extra=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """Point every source at one local stub, with an isolated cache."""
    StubAPI.calls = []
    StubAPI.headers_seen = []
    StubAPI.force_status = {}

    server = HTTPServer(("127.0.0.1", 0), StubAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    monkeypatch.setenv("COMFYAUDIT_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(src, "HF_API", f"{base}/api")
    monkeypatch.setattr(src, "CIVITAI_API", f"{base}/api/v1")
    monkeypatch.setattr(src, "GITHUB_API", base)
    monkeypatch.setattr(src, "GITHUB_RAW", base)
    monkeypatch.setattr(src, "COMFY_REGISTRY_API", base)

    yield base
    server.shutdown()
    server.server_close()


def _client() -> HttpClient:
    return HttpClient(timeout=5.0)


# --------------------------------------------------------------------------
# HuggingFace
# --------------------------------------------------------------------------


def test_huggingface_reads_the_camelcase_wire_fields(api):
    facts = src.HuggingFace(_client(), Credentials()).repo("black-forest-labs/FLUX.1-dev")

    assert facts.identifier == "black-forest-labs/FLUX.1-dev"
    assert facts.downloads == 1_842_113
    assert facts.last_modified.startswith("2026-03-11")   # lastModified, not last_modified
    assert facts.licence_tag == "other"
    assert facts.licence_name == "flux-1-dev-non-commercial-license"
    assert "flux1-dev.safetensors" in facts.files


def test_a_gated_repo_says_which_kind_of_gate(api):
    """"auto" means click-through; "manual" means a person has to approve you."""
    facts = src.HuggingFace(_client(), Credentials()).repo("black-forest-labs/FLUX.1-dev")
    assert facts.gated == "manual"
    assert any("gated" in w and "unattended" in w for w in facts.warnings)


def test_the_hub_ancestry_is_captured(api):
    facts = src.HuggingFace(_client(), Credentials()).repo("someone/cinematic-flux-lora")
    assert facts.base_models == ["black-forest-labs/FLUX.1-dev"]


def test_file_search_requires_the_repo_to_actually_hold_the_file(api):
    hf = src.HuggingFace(_client(), Credentials())

    found = hf.find_file("cinematic_flux_v2.safetensors")
    assert found is not None
    assert found.identifier == "someone/cinematic-flux-lora"
    assert found.confidence == "high"

    # The search returns candidates for this too, but none of them hold the file.
    assert hf.find_file("a_file_nobody_hosts.safetensors") is None


def test_the_token_is_sent_when_present(api):
    src.HuggingFace(_client(), Credentials(huggingface="hf_test")).repo(
        "black-forest-labs/FLUX.1-dev")
    assert any(h.get("Authorization") == "Bearer hf_test" for h in StubAPI.headers_seen)


def test_responses_are_cached_between_calls(api):
    http = _client()
    hf = src.HuggingFace(http, Credentials())
    hf.repo("black-forest-labs/FLUX.1-dev")
    hf.repo("black-forest-labs/FLUX.1-dev")
    assert http.requests == 1 and http.hits == 1


# --------------------------------------------------------------------------
# Civitai
# --------------------------------------------------------------------------


def test_civitai_hash_lookup_is_exact(api):
    facts = src.Civitai(_client(), Credentials()).by_hash("c9e3" + "0" * 60)

    assert facts.confidence == "high"
    assert facts.author == "KandooAI"
    assert facts.base_models == ["SDXL 1.0"]
    assert facts.permissions["allowCommercialUse"] == ["Image", "Rent"]
    assert any("exact Civitai file hash match" in e for e in facts.evidence)


def test_uploader_flags_reduce_to_a_position():
    position = src.Civitai.commercial_position
    assert position({"allowCommercialUse": ["Sell"]}) == "yes"
    assert position({"allowCommercialUse": ["Image", "Rent"]}) == "conditional"
    assert position({"allowCommercialUse": ["None"]}) == "no"
    assert position({"allowCommercialUse": []}) == "unknown"
    assert position({}) == "unknown"


def test_restrictive_uploader_flags_become_warnings(api):
    facts = src.Civitai(_client(), Credentials()).by_hash("c9e3" + "0" * 60)
    assert any("must keep the uploader's licence" in w for w in facts.warnings)
    assert any("not a verified licence" in w for w in facts.warnings)


def test_filename_search_checks_the_file_list(api):
    facts = src.Civitai(_client(), Credentials()).by_filename(
        "juggernautXL_v9Rundiffusion.safetensors")
    assert facts is not None
    assert facts.confidence == "medium"      # a name match is weaker than a hash


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------


def test_github_repo_metadata(api):
    facts = src.GitHub(_client(), Credentials()).repo(
        "https://github.com/cubiq/ComfyUI_IPAdapter_plus")
    assert facts.licence_tag == "GPL-3.0"
    assert facts.likes == 6102
    assert facts.last_modified.startswith("2026-04-14")


def test_a_repo_with_no_licence_is_flagged_as_such(api):
    facts = src.GitHub(_client(), Credentials()).repo("someone/mystery-nodes")
    assert facts.licence_tag == ""
    assert any("all rights reserved" in w for w in facts.warnings)
    assert any("archived" in w for w in facts.warnings)


def test_github_falls_back_to_the_raw_licence_file_when_rate_limited(api):
    """60 requests an hour is easy to exhaust; raw.githubusercontent is unmetered."""
    StubAPI.force_status = {"/repos/": 403}
    facts = src.GitHub(_client(), Credentials()).repo("cubiq/ComfyUI_IPAdapter_plus")

    assert facts is not None
    assert facts.licence_tag == "MIT"        # identified from the file's text
    assert any("GitHub API unavailable" in e for e in facts.evidence)


def test_rate_limit_headers_are_remembered(api):
    StubAPI.force_status = {"/repos/": 403}
    http = _client()
    src.GitHub(http, Credentials()).repo("cubiq/ComfyUI_IPAdapter_plus")
    notes = http.rate_limit_notes()
    assert notes and "rate limit reached" in notes[0]


@pytest.mark.parametrize("text,expected", [
    ("GNU AFFERO GENERAL PUBLIC LICENSE Version 3", "AGPL-3.0"),
    ("Apache License Version 2.0, January 2004", "Apache-2.0"),
    ("Permission is hereby granted, free of charge, to any person", "MIT"),
    ("Attribution-NonCommercial-ShareAlike 4.0 International", "CC-BY-NC-SA-4.0"),
    ("some bespoke terms nobody has ever seen", ""),
])
def test_licence_text_identification(text, expected):
    assert src.identify_licence_text(text) == expected


@pytest.mark.parametrize("value,expected", [
    ("https://github.com/cubiq/ComfyUI_IPAdapter_plus", "cubiq/ComfyUI_IPAdapter_plus"),
    ("github.com/cubiq/ComfyUI_IPAdapter_plus.git", "cubiq/ComfyUI_IPAdapter_plus"),
    ("cubiq/ComfyUI_IPAdapter_plus", "cubiq/ComfyUI_IPAdapter_plus"),
    ("https://github.com/cubiq/ComfyUI_IPAdapter_plus/tree/main/docs",
     "cubiq/ComfyUI_IPAdapter_plus"),
    ("not-a-repo", ""),
])
def test_repo_normalisation(value, expected):
    assert src.normalise_repo(value) == expected


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


def _resolver(**kwargs) -> Resolver:
    return Resolver(http=_client(), credentials=Credentials(),
                    matcher=LicenceMatcher(), enabled=True, **kwargs)


def test_a_permissive_lora_cannot_escape_a_restricted_base(api):
    """The heart of it: the author says Apache, the base says non-commercial."""
    ref = ModelRef(filename="cinematic_flux_v2.safetensors", folder="loras")
    _resolver().resolve_model(ref)

    assert ref.license.commercial_use == "no"
    assert any("Inherited from the base model" in r for r in ref.license.restrictions)


def test_overclaiming_a_base_model_is_explained_not_called_a_conflict(api):
    """Granting less than your base allows is normal; granting more is not."""
    ref = ModelRef(filename="cinematic_flux_v2.safetensors", folder="loras")
    outcome = _resolver().resolve_model(ref)

    # Only one description of the file itself, so nothing contradicts anything.
    assert outcome.conflicts == []
    assert any("cannot grant more than the model it was trained on" in r
               for r in ref.license.restrictions)


def test_two_contradictory_descriptions_of_one_file_are_a_conflict(api):
    """A well-known filename whose upstream repo tags it permissively.

    Almost always a re-upload or a rename, and exactly the case that quietly
    clears a non-commercial model for delivery.
    """
    ref = ModelRef(filename="flux1-dev.safetensors", folder="diffusion_models")
    outcome = _resolver(sources=("huggingface",)).resolve_model(ref)

    assert outcome.conflicts, "the filename says FLUX dev, the repo says Apache"
    assert ref.license.commercial_use == "no"        # most restrictive wins
    assert ref.license.confidence == "low"
    assert any("Sources disagree" in r for r in ref.license.restrictions)


def test_a_non_commercial_base_overrides_generous_uploader_flags(api):
    """Civitai lets an uploader tick "Sell" on a FLUX dev LoRA. It does not help."""
    ref = ModelRef(filename="my_flux_lora.safetensors", folder="loras")
    _resolver(sources=("civitai",)).resolve_model(ref, sha256="ab" * 32)

    assert ref.license.commercial_use == "no"
    assert any("Flux.1 D" in r for r in ref.license.restrictions)


def test_an_agreeing_base_leaves_a_permissive_verdict_alone(api):
    ref = ModelRef(filename="juggernautXL_v9Rundiffusion.safetensors",
                   folder="checkpoints")
    outcome = _resolver(sources=("civitai",)).resolve_model(
        ref, sha256="c9e3" + "0" * 60)

    # SDXL 1.0 base is OpenRAIL++ (commercial), uploader allows Image/Rent.
    assert ref.license.commercial_use == "conditional"
    assert outcome.conflicts == []
    assert ref.provenance.source == "civitai"


def test_provenance_prefers_the_strongest_source(api):
    ref = ModelRef(filename="juggernautXL_v9Rundiffusion.safetensors",
                   folder="checkpoints")
    _resolver().resolve_model(ref, sha256="c9e3" + "0" * 60)
    assert ref.provenance.resolved_by == "civitai-api"
    assert ref.provenance.confidence == "high"


def test_sources_can_be_narrowed(api):
    """A facility may not want model names leaving for a particular service."""
    ref = ModelRef(filename="cinematic_flux_v2.safetensors", folder="loras")
    _resolver(sources=("github",)).resolve_model(ref)
    assert not any("huggingface" in c for c in StubAPI.calls)


def test_offline_resolution_touches_nothing(api):
    ref = ModelRef(filename="cinematic_flux_v2.safetensors", folder="loras")
    Resolver(http=_client(), credentials=Credentials(),
             matcher=LicenceMatcher(), enabled=False).resolve_model(ref)
    assert StubAPI.calls == []
    assert ref.license is not None


def test_pack_licence_is_resolved_from_github(api):
    pack = PackRef(title="ComfyUI_IPAdapter_plus", identified=True,
                   reference="https://github.com/cubiq/ComfyUI_IPAdapter_plus",
                   node_types=["IPAdapterFaceID"])
    _resolver().resolve_pack(pack)

    assert pack.licence == "GPL-3.0"
    assert pack.stars == 6102
    assert pack.last_update.startswith("2026-04-14")


def test_a_copyleft_pack_becomes_a_finding(api):
    from comfyaudit.core.score import risk as risk_mod

    pack = PackRef(title="ComfyUI_IPAdapter_plus", identified=True,
                   reference="https://github.com/cubiq/ComfyUI_IPAdapter_plus",
                   node_types=["IPAdapterFaceID"], pinned_version="1.0.0")
    _resolver().resolve_pack(pack)

    findings = risk_mod._dependency_findings([pack], None)
    ids = {f.id for f in findings}
    assert "dependency.copyleft" in ids
    finding = next(f for f in findings if f.id == "dependency.copyleft")
    assert finding.severity == "high"
    assert "AGPL" in finding.detail or "GPL" in finding.detail


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_a_pack_licence_reaches_both_report_formats(api):
    from comfyaudit.core.audit import AuditReport
    from comfyaudit.core.report import html as html_report
    from comfyaudit.core.report import markdown as md_report

    pack = PackRef(title="ComfyUI_IPAdapter_plus", identified=True, author="cubiq",
                   reference="https://github.com/cubiq/ComfyUI_IPAdapter_plus",
                   node_types=["IPAdapterFaceID"], node_count=1,
                   pinned_version="1.0.0")
    _resolver().resolve_pack(pack)

    report = AuditReport()
    report.source = {"name": "t.json", "format": "ui", "nodes_total": 1}
    report.packs = [pack]

    report.licensing = __import__(
        "comfyaudit.core.score.licensing", fromlist=["summarise"]).summarise([])
    text = md_report.render(report)
    assert "| Pack | Author | Licence |" in text
    assert "GPL-3.0" in text

    page = html_report.render(report)
    assert "GPL-3.0" in page
    # Strong copyleft is toned as a stop, not a neutral fact.
    assert "chip stop'>GPL-3.0" in page


def test_an_unchecked_pack_says_unchecked_rather_than_none(api):
    from comfyaudit.core.audit import AuditReport
    from comfyaudit.core.report import markdown as md_report

    report = AuditReport()
    report.source = {"name": "t.json", "format": "ui", "nodes_total": 1}
    report.packs = [PackRef(title="Some Pack", identified=True, node_count=1)]
    assert "not checked" in md_report.render(report)
