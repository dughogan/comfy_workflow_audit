"""HuggingFace, Civitai, GitHub and the Comfy Registry.

Each source answers the same two questions in its own dialect - *what is this
file* and *what may we do with it* - and each returns the same
:class:`SourceFacts`, with an evidence trail saying how it knows.

Field names here were taken from upstream source rather than memory:
HuggingFace's from ``huggingface_hub``'s own ``ModelInfo``, Civitai's from their
``model.schema.ts`` and the ``baseModelLicenses`` table in their repo.  That
matters because the wire format is camelCase in places the Python client is
not (``lastModified``, ``baseModels``, ``downloadsAllTime``), and a silently
missing field reads as "unknown provenance" rather than as a bug.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .http import Credentials, Fetched, HttpClient

HF_API = "https://huggingface.co/api"
CIVITAI_API = "https://civitai.com/api/v1"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
COMFY_REGISTRY_API = "https://api.comfy.org"


@dataclass
class SourceFacts:
    """What one source knows about one artefact."""

    source: str = ""                     # huggingface | civitai | github | comfy-registry
    identifier: str = ""
    url: str = ""
    author: str = ""
    downloads: int | None = None
    likes: int | None = None
    last_modified: str = ""
    created_at: str = ""

    #: "" | "auto" | "manual" - manual means a human approves each request,
    #: which will stall an unattended render node indefinitely.
    gated: str = ""
    private: bool = False

    licence_tag: str = ""                # raw upstream tag, e.g. "apache-2.0", "other"
    licence_name: str = ""
    licence_url: str = ""

    base_models: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    sha256: str = ""

    warnings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    confidence: str = "medium"           # low | medium | high

    def __bool__(self) -> bool:
        return bool(self.source)


def _iso(value: Any) -> str:
    return str(value) if value else ""


# --------------------------------------------------------------------------
# HuggingFace
# --------------------------------------------------------------------------


class HuggingFace:
    """The hub API. Anonymous access works; a token buys gated repos."""

    name = "huggingface"

    def __init__(self, http: HttpClient, credentials: Credentials) -> None:
        self.http = http
        self.token = credentials.huggingface

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def repo(self, repo_id: str) -> SourceFacts | None:
        """Full metadata for a known ``owner/name`` repository."""
        if not repo_id or "/" not in repo_id:
            return None
        url = f"{HF_API}/models/{urllib.parse.quote(repo_id, safe='/')}"
        got = self.http.get_json(url, self._headers())
        if not got or not isinstance(got.data, dict) or "id" not in got.data:
            return None
        facts = self._facts(got.data)
        facts.evidence.append(f"HuggingFace repository {facts.identifier}")
        facts.confidence = "high"
        return facts

    def find_file(self, filename: str) -> SourceFacts | None:
        """Find the repository that actually contains this weights file.

        A name search alone is not evidence - plenty of repos are *named* after
        a model without hosting it - so each candidate's file list is checked
        before the match is accepted.
        """
        stem = _stem(filename)
        if len(stem) < 4:
            return None

        listing = self.http.get_json(
            f"{HF_API}/models?search={urllib.parse.quote(stem)}&limit=5",
            self._headers())
        if not listing or not isinstance(listing.data, list):
            return None

        target = os.path.basename(filename).lower()
        for entry in listing.data:
            repo_id = entry.get("id") if isinstance(entry, dict) else None
            if not repo_id:
                continue
            detail = self.http.get_json(
                f"{HF_API}/models/{urllib.parse.quote(repo_id, safe='/')}",
                self._headers())
            if not detail or not isinstance(detail.data, dict):
                continue
            facts = self._facts(detail.data)
            if target in {name.lower() for name in facts.files}:
                facts.confidence = "high"
                facts.evidence.append(
                    f"HuggingFace repository {facts.identifier} contains a file named "
                    f"'{os.path.basename(filename)}'")
                return facts

        return None

    def _facts(self, data: dict[str, Any]) -> SourceFacts:
        repo_id = str(data.get("id", ""))
        card = data.get("cardData") or {}

        licence_tag = card.get("license") or ""
        if isinstance(licence_tag, list):
            licence_tag = licence_tag[0] if licence_tag else ""
        if not licence_tag:
            for tag in data.get("tags") or []:
                if isinstance(tag, str) and tag.startswith("license:"):
                    licence_tag = tag.split(":", 1)[1]
                    break

        # The hub records the ancestry itself; card_data.base_model is the
        # author's claim, baseModels is the hub's computed chain.
        bases: list[str] = []
        for value in (data.get("baseModels"), card.get("base_model")):
            if isinstance(value, str):
                bases.append(value)
            elif isinstance(value, list):
                bases.extend(str(v) for v in value if v)

        gated = data.get("gated")
        facts = SourceFacts(
            source=self.name,
            identifier=repo_id,
            url=f"https://huggingface.co/{repo_id}",
            author=str(data.get("author") or repo_id.split("/")[0]),
            downloads=data.get("downloads"),
            likes=data.get("likes"),
            last_modified=_iso(data.get("lastModified")),
            created_at=_iso(data.get("createdAt")),
            gated=gated if isinstance(gated, str) else "",
            private=bool(data.get("private")),
            licence_tag=str(licence_tag or ""),
            licence_name=str(card.get("license_name") or ""),
            licence_url=str(card.get("license_link") or ""),
            base_models=list(dict.fromkeys(bases)),
            files=[str(s.get("rfilename", "")) for s in data.get("siblings") or []
                   if isinstance(s, dict)],
        )

        if facts.licence_tag:
            facts.evidence.append(f"hub licence tag: {facts.licence_tag}")
        if facts.base_models:
            facts.evidence.append("declared base model(s): "
                                  + ", ".join(facts.base_models[:4]))
        if facts.gated:
            facts.warnings.append(
                "repository is gated ("
                + ("a human approves each request" if facts.gated == "manual"
                   else "auto-approved on acceptance")
                + ") - an unattended render node without a token fails at download time")
        if data.get("disabled"):
            facts.warnings.append("repository is disabled on the hub")
        status = data.get("securityRepoStatus") or {}
        unsafe = status.get("filesWithIssues") if isinstance(status, dict) else None
        if unsafe:
            facts.warnings.append(
                f"HuggingFace's scanner flagged {len(unsafe)} file(s) in this repo")
        return facts


# --------------------------------------------------------------------------
# Civitai
# --------------------------------------------------------------------------

#: Civitai's CommercialUse enum, in increasing permissiveness.
CIVITAI_COMMERCIAL = {
    "None": "no",
    "Image": "conditional",     # sell the images, not the model
    "RentCivit": "conditional",
    "Rent": "conditional",
    "Sell": "yes",
}


class Civitai:
    """Community models. Hash lookup is exact; name search is a guess."""

    name = "civitai"

    def __init__(self, http: HttpClient, credentials: Credentials) -> None:
        self.http = http
        self.token = credentials.civitai

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def by_hash(self, sha256: str) -> SourceFacts | None:
        """The only reliable way to identify a community checkpoint.

        Filenames on Civitai are whatever the downloader called them; the hash
        is what the file actually is.
        """
        if not sha256 or len(sha256) < 32:
            return None
        version = self.http.get_json(
            f"{CIVITAI_API}/model-versions/by-hash/{sha256}", self._headers())
        if not version or not isinstance(version.data, dict):
            return None
        return self._from_version(version.data, sha256=sha256, exact=True)

    def by_filename(self, filename: str) -> SourceFacts | None:
        """Search by name, and only accept a version that lists the file."""
        stem = _stem(filename)
        if len(stem) < 4:
            return None
        listing = self.http.get_json(
            f"{CIVITAI_API}/models?query={urllib.parse.quote(stem)}&limit=5",
            self._headers())
        if not listing or not isinstance(listing.data, dict):
            return None

        target = os.path.basename(filename).lower()
        for model in listing.data.get("items") or []:
            for version in model.get("modelVersions") or []:
                names = {str(f.get("name", "")).lower()
                         for f in version.get("files") or []}
                if target in names:
                    facts = self._from_version(version, model=model, exact=False)
                    if facts:
                        facts.evidence.append(
                            f"Civitai version {version.get('id')} lists a file named "
                            f"'{os.path.basename(filename)}'")
                    return facts
        return None

    def _from_version(self, version: dict[str, Any], model: dict[str, Any] | None = None,
                      sha256: str = "", exact: bool = False) -> SourceFacts | None:
        model_id = version.get("modelId") or (model or {}).get("id")
        if model_id is None:
            return None

        if model is None:
            fetched = self.http.get_json(f"{CIVITAI_API}/models/{model_id}", self._headers())
            model = fetched.data if fetched and isinstance(fetched.data, dict) else {}

        stats = model.get("stats") or {}
        permissions = {
            "allowCommercialUse": model.get("allowCommercialUse"),
            "allowNoCredit": model.get("allowNoCredit"),
            "allowDerivatives": model.get("allowDerivatives"),
            "allowDifferentLicense": model.get("allowDifferentLicense"),
        }

        facts = SourceFacts(
            source=self.name,
            identifier=f"model {model_id} version {version.get('id')}",
            url=f"https://civitai.com/models/{model_id}",
            author=str((model.get("creator") or {}).get("username") or ""),
            downloads=stats.get("downloadCount"),
            likes=stats.get("thumbsUpCount"),
            last_modified=_iso(version.get("publishedAt")),
            base_models=[str(version.get("baseModel"))] if version.get("baseModel") else [],
            permissions={k: v for k, v in permissions.items() if v is not None},
            sha256=sha256,
            confidence="high" if exact else "medium",
        )

        if exact:
            facts.evidence.append(f"exact Civitai file hash match ({sha256[:16]}...)")
        if facts.base_models:
            facts.evidence.append(f"Civitai base model: {facts.base_models[0]}")

        allow = permissions.get("allowCommercialUse")
        if isinstance(allow, list):
            facts.evidence.append(
                "uploader's commercial-use flags: "
                + (", ".join(allow) if allow else "none"))
        elif isinstance(allow, str):
            facts.evidence.append(f"uploader's commercial-use flag: {allow}")

        if permissions.get("allowNoCredit") is False:
            facts.warnings.append("the uploader requires credit")
        if permissions.get("allowDerivatives") is False:
            facts.warnings.append("the uploader forbids derivatives, including merges "
                                  "and further training")
        if permissions.get("allowDifferentLicense") is False:
            facts.warnings.append("derivatives must keep the uploader's licence")
        if model.get("nsfw"):
            facts.warnings.append("flagged NSFW on Civitai")
        facts.warnings.append("Civitai permission flags are set by the uploader and are "
                              "not a verified licence")
        return facts

    @staticmethod
    def commercial_position(permissions: dict[str, Any]) -> str:
        """Reduce the uploader's flag list to a commercial-use position."""
        allow = permissions.get("allowCommercialUse")
        values = allow if isinstance(allow, list) else ([allow] if allow else [])
        if not values:
            return "unknown"
        ranked = [CIVITAI_COMMERCIAL.get(str(v), "unknown") for v in values]
        for best in ("yes", "conditional", "no"):
            if best in ranked:
                return best
        return "unknown"


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

#: Licences that reach into a studio's own code if a node pack ships under them.
COPYLEFT_SPDX = {"AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
                 "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
                 "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later"}

WEAK_COPYLEFT_SPDX = {"LGPL-3.0", "LGPL-3.0-only", "LGPL-2.1", "MPL-2.0", "EPL-2.0"}

LICENSE_FILENAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE",
                     "LICENCE.md", "COPYING")


class GitHub:
    """Repository metadata, for custom node packs and model repos alike.

    The API caps anonymous callers at 60 requests an hour, which one audit of a
    dependency-heavy workflow can exhaust on its own. So when the API is out of
    budget this falls back to ``raw.githubusercontent.com``, which is unmetered
    and still answers the question that matters most: what licence file is in
    the repository.
    """

    name = "github"

    def __init__(self, http: HttpClient, credentials: Credentials) -> None:
        self.http = http
        self.token = credentials.github

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def repo(self, owner_repo: str) -> SourceFacts | None:
        owner_repo = normalise_repo(owner_repo)
        if not owner_repo:
            return None

        got = self.http.get_json(f"{GITHUB_API}/repos/{owner_repo}", self._headers())
        if got and isinstance(got.data, dict) and got.data.get("full_name"):
            return self._facts(got.data)

        # Out of budget, or unauthenticated and blocked: fall back to raw files.
        if got.status in (403, 429) or got.error == "offline":
            return self._from_raw(owner_repo, reason=got.error or "API unavailable")
        if got.status == 404:
            return None
        return self._from_raw(owner_repo, reason=got.error or "API unavailable")

    def _facts(self, data: dict[str, Any]) -> SourceFacts:
        licence = data.get("license") or {}
        spdx = str(licence.get("spdx_id") or "")
        if spdx in ("NOASSERTION", "null"):
            spdx = ""

        facts = SourceFacts(
            source=self.name,
            identifier=str(data.get("full_name", "")),
            url=str(data.get("html_url", "")),
            author=str((data.get("owner") or {}).get("login", "")),
            likes=data.get("stargazers_count"),
            last_modified=_iso(data.get("pushed_at")),
            created_at=_iso(data.get("created_at")),
            licence_tag=spdx,
            licence_name=str(licence.get("name") or ""),
            licence_url=(f"{data.get('html_url', '')}/blob/"
                         f"{data.get('default_branch', 'main')}/LICENSE" if spdx else ""),
            confidence="high",
        )
        facts.evidence.append(
            f"GitHub {facts.identifier}: licence {spdx or 'not declared'}, "
            f"{facts.likes or 0} stars, last push {facts.last_modified[:10]}")

        if data.get("archived"):
            facts.warnings.append("the repository is archived - it is read-only and "
                                  "will not be fixed")
        if data.get("disabled"):
            facts.warnings.append("the repository is disabled on GitHub")
        if not spdx and licence:
            facts.warnings.append("GitHub could not identify the licence file")
        elif not licence:
            facts.warnings.append("no licence file in the repository, so no licence is "
                                  "granted - the default is all rights reserved")
        return facts

    def _from_raw(self, owner_repo: str, reason: str) -> SourceFacts | None:
        """Read the LICENSE file directly when the API will not answer."""
        for branch in ("main", "master"):
            for name in LICENSE_FILENAMES:
                got = self.http.get_text(f"{GITHUB_RAW}/{owner_repo}/{branch}/{name}")
                if not got or not isinstance(got.data, str) or len(got.data) < 40:
                    continue
                spdx = identify_licence_text(got.data)
                facts = SourceFacts(
                    source=self.name,
                    identifier=owner_repo,
                    url=f"https://github.com/{owner_repo}",
                    author=owner_repo.split("/")[0],
                    licence_tag=spdx,
                    licence_url=f"{GITHUB_RAW}/{owner_repo}/{branch}/{name}",
                    confidence="medium" if spdx else "low",
                )
                facts.evidence.append(
                    f"read {name} directly from {owner_repo}@{branch} "
                    f"(GitHub API unavailable: {reason})")
                if not spdx:
                    facts.warnings.append(
                        "a licence file is present but its text was not recognised - "
                        "read it by hand")
                return facts
        return None


def identify_licence_text(text: str) -> str:
    """Best-effort SPDX identification from the opening of a licence file."""
    head = " ".join(text[:4000].split()).lower()
    patterns = [
        ("AGPL-3.0", r"gnu affero general public license"),
        ("GPL-3.0", r"gnu general public license.{0,80}version 3"),
        ("GPL-2.0", r"gnu general public license.{0,80}version 2"),
        ("LGPL-3.0", r"gnu lesser general public license"),
        ("Apache-2.0", r"apache license.{0,40}version 2\.0"),
        ("MPL-2.0", r"mozilla public license.{0,40}version 2\.0"),
        ("BSD-3-Clause", r"redistribution and use in source and binary forms.{0,600}"
                         r"neither the name"),
        ("BSD-2-Clause", r"redistribution and use in source and binary forms"),
        ("MIT", r"permission is hereby granted, free of charge"),
        ("Unlicense", r"this is free and unencumbered software released into the public domain"),
        ("CC-BY-NC-SA-4.0", r"attribution-noncommercial-sharealike 4\.0"),
        ("CC-BY-NC-4.0", r"attribution-noncommercial 4\.0"),
        ("CC-BY-SA-4.0", r"attribution-sharealike 4\.0"),
        ("CC0-1.0", r"cc0 1\.0 universal"),
    ]
    for spdx, pattern in patterns:
        if re.search(pattern, head):
            return spdx
    return ""


def normalise_repo(value: str) -> str:
    """Reduce anything that names a GitHub repo to ``owner/name``."""
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^https?://(www\.)?github\.com/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^github\.com/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.git$", "", text).strip("/")
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


# --------------------------------------------------------------------------
# Comfy Registry
# --------------------------------------------------------------------------


class ComfyRegistry:
    """The official node registry: publisher, version and declared licence."""

    name = "comfy-registry"

    def __init__(self, http: HttpClient, credentials: Credentials) -> None:
        self.http = http

    def pack(self, pack_id: str) -> SourceFacts | None:
        if not pack_id:
            return None
        got = self.http.get_json(
            f"{COMFY_REGISTRY_API}/nodes/{urllib.parse.quote(pack_id)}")
        if not got or not isinstance(got.data, dict) or not got.data.get("id"):
            return None

        data = got.data
        latest = data.get("latest_version") or {}
        facts = SourceFacts(
            source=self.name,
            identifier=str(data.get("id", "")),
            url=str(data.get("repository") or ""),
            author=str((data.get("publisher") or {}).get("name", "")),
            downloads=data.get("downloads"),
            licence_tag=str(data.get("license") or ""),
            last_modified=_iso(latest.get("createdAt")),
            confidence="high",
        )
        facts.evidence.append(
            f"Comfy Registry: {facts.identifier} by {facts.author or 'unknown'}, "
            f"latest {latest.get('version', '?')}")
        facts.permissions["latest_version"] = latest.get("version", "")
        status = str(data.get("status") or "")
        if status and status.lower() not in ("active", "nodestatusactive"):
            facts.warnings.append(f"registry status: {status}")
        return facts


def _stem(filename: str) -> str:
    base = os.path.basename(filename or "")
    return re.sub(r"\.[A-Za-z0-9]{2,12}$", "", base)
