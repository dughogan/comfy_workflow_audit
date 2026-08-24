"""Reconcile what the sources say into one verdict, showing its working.

A model can be described by four parties at once - the filename, the uploader's
own permission flags, the hub's licence tag, and the licence of the model it was
trained on - and they do not always agree. The rules here are deliberately
boring:

* **The most restrictive known position wins.** A LoRA's author cannot grant
  more than the base model allows, so a permissive derivative of FLUX.1 [dev] is
  still non-commercial.
* **Disagreement is reported, not resolved silently.** When the filename says
  one thing and the hub says another, that is itself the finding - usually it
  means the file was renamed, and nobody should discover that during delivery.
* **Unknown never upgrades anything.** An unclassified base model adds a caveat;
  it does not turn a restricted model into a permitted one, or vice versa.

Every verdict carries the chain of evidence that produced it, so a reader can
disagree with the conclusion on the facts rather than on faith.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .. import catalog
from ..knowledge.licences import LicenceMatcher
from ..records import LicenseInfo, ModelRef, PackRef, Provenance
from .http import Credentials, HttpClient
from .sources import (Civitai, ComfyRegistry, GitHub, HuggingFace, SourceFacts,
                      normalise_repo)

ALL_SOURCES = ("huggingface", "civitai", "github", "comfy-registry")

#: Lower is more restrictive. "unknown" sits between conditional and yes: it
#: cannot clear a model, but it must not condemn one either.
RESTRICTIVENESS = {"no": 0, "conditional": 1, "unknown": 2, "yes": 3}

#: Folders whose contents are community uploads worth searching Civitai for.
CIVITAI_FOLDERS = {"checkpoints", "loras", "embeddings", "unknown",
                   "diffusion_models", "upscale_models"}


@dataclass
class Resolution:
    """The outcome for one artefact."""

    provenance: Provenance = field(default_factory=Provenance)
    licence: LicenseInfo | None = None
    facts: list[SourceFacts] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


class Resolver:
    """Looks artefacts up across the enabled sources and reconciles the answers."""

    def __init__(self, http: HttpClient | None = None,
                 credentials: Credentials | None = None,
                 matcher: LicenceMatcher | None = None,
                 sources: Iterable[str] = ALL_SOURCES,
                 enabled: bool = True) -> None:
        self.http = http or HttpClient()
        self.credentials = credentials or Credentials.from_environment()
        self.matcher = matcher or LicenceMatcher()
        self.enabled = enabled
        self.sources = {s for s in sources}

        self.huggingface = HuggingFace(self.http, self.credentials)
        self.civitai = Civitai(self.http, self.credentials)
        self.github = GitHub(self.http, self.credentials)
        self.registry = ComfyRegistry(self.http, self.credentials)

    def uses(self, name: str) -> bool:
        return self.enabled and name in self.sources

    # -- models ------------------------------------------------------------

    def resolve_model(self, ref: ModelRef, sha256: str = "") -> Resolution:
        """Fill in ``ref.provenance`` and ``ref.license``, in place."""
        result = Resolution()
        baseline = self._bundled(ref)
        result.provenance = baseline

        for facts in self._gather(ref, sha256):
            result.facts.append(facts)

        ref.provenance = self._best_provenance(baseline, result.facts)
        ref.license = self._reconcile(ref, result)
        for facts in result.facts:
            for warning in facts.warnings:
                if warning not in ref.notes:
                    ref.notes.append(warning)
        return result

    def _gather(self, ref: ModelRef, sha256: str) -> list[SourceFacts]:
        """Ask each enabled source, cheapest and most certain first."""
        found: list[SourceFacts] = []
        if ref.folder == "hosted-api":
            return found

        # An exact hash beats every other identification, so try it first.
        if sha256 and self.uses("civitai"):
            facts = self.civitai.by_hash(sha256)
            if facts:
                found.append(facts)

        if self.uses("huggingface"):
            facts = None
            if ref.repo_id:
                facts = self.huggingface.repo(ref.repo_id)
            if facts is None:
                known = catalog.known_model(ref.filename) or {}
                repo = _hf_repo_from_url(known.get("reference") or known.get("url", ""))
                if repo:
                    facts = self.huggingface.repo(repo)
                    if facts:
                        facts.evidence.append(
                            "repository taken from the bundled model index by filename")
            if facts is None:
                facts = self.huggingface.find_file(ref.filename)
            if facts:
                found.append(facts)

        if self.uses("civitai") and not any(f.source == "civitai" for f in found):
            if ref.folder in CIVITAI_FOLDERS:
                facts = self.civitai.by_filename(ref.filename)
                if facts:
                    found.append(facts)

        if self.uses("github"):
            known = catalog.known_model(ref.filename) or {}
            repo = normalise_repo(known.get("reference") or "")
            if repo and not any(f.source == "huggingface" for f in found):
                facts = self.github.repo(repo)
                if facts:
                    found.append(facts)

        return found

    # -- packs -------------------------------------------------------------

    def resolve_pack(self, pack: PackRef) -> Resolution:
        """Licence and health for a custom node pack.

        A node pack's licence is not a formality: its code runs inside the
        studio's own process, so a copyleft pack can reach the tools built
        around it in a way a model licence never does.
        """
        result = Resolution()

        if self.uses("comfy-registry") and pack.registry_id:
            facts = self.registry.pack(pack.registry_id)
            if facts:
                result.facts.append(facts)
                if facts.licence_tag:
                    pack.notes.append(f"registry licence: {facts.licence_tag}")
                latest = facts.permissions.get("latest_version")
                if latest and pack.pinned_version and latest != pack.pinned_version:
                    pack.notes.append(
                        f"pinned {pack.pinned_version}, latest published {latest}")

        repo = normalise_repo(pack.reference or pack.aux_id or pack.repo)
        if self.uses("github") and repo:
            facts = self.github.repo(repo)
            if facts:
                result.facts.append(facts)
                if facts.licence_tag:
                    pack.licence = facts.licence_tag
                    pack.licence_url = facts.licence_url
                if facts.likes is not None:
                    pack.stars = facts.likes
                if facts.last_modified:
                    pack.last_update = facts.last_modified.replace("T", " ").rstrip("Z")
                for warning in facts.warnings:
                    if warning not in pack.notes:
                        pack.notes.append(warning)
                pack.notes.extend(e for e in facts.evidence if e not in pack.notes)

        if not pack.licence:
            for facts in result.facts:
                if facts.licence_tag:
                    pack.licence = facts.licence_tag
                    break
        return result

    # -- reconciliation ----------------------------------------------------

    def _reconcile(self, ref: ModelRef, result: Resolution) -> LicenseInfo:
        """Pick a verdict from every candidate, and say when they disagree.

        Two different things look like disagreement and only one of them is a
        problem. A model granting *less* than its base model allows is ordinary -
        authors restrict their own work all the time. A model granting *more*
        than its base allows is not something the author can do, and two
        descriptions of the same file that flatly contradict each other usually
        mean the file was renamed on the way to the drive. Only those get raised.
        """
        baseline = self.matcher.for_model(ref)
        candidates: list[tuple[LicenseInfo, str, str]] = []
        if baseline.commercial_use != "unknown":
            candidates.append((baseline, f"filename ({baseline.matched_on})", "self"))

        evidence: list[str] = []
        for facts in result.facts:
            evidence.extend(facts.evidence)
            candidates.extend(self._candidates_from(facts))

        if not candidates:
            baseline.restrictions.extend(_dedupe(evidence))
            return baseline

        rank = lambda info: RESTRICTIVENESS.get(info.commercial_use, 2)  # noqa: E731
        candidates.sort(key=lambda item: rank(item[0]))
        winner, why, _ = candidates[0]

        known = [c for c in candidates if c[0].commercial_use in ("no", "conditional", "yes")]
        selves = [c for c in known if c[2] == "self"]
        inherited = [c for c in known if c[2] == "inherited"]

        # Two independent descriptions of the same file that do not match.
        self_positions = {info.commercial_use for info, _, _ in selves}
        if len(self_positions) > 1:
            summary = "; ".join(f"{label} says {info.commercial_use}"
                                for info, label, _ in selves)
            result.conflicts.append(summary)
            winner.restrictions.append(
                "Sources disagree about this model - " + summary
                + ". The most restrictive reading has been applied. A mismatch "
                  "usually means the file was renamed after it was downloaded, so "
                  "confirm which model this actually is.")
            winner.confidence = "low"

        # A derivative claiming more than the model it was trained on.
        if inherited and selves:
            strictest_base = min(inherited, key=lambda c: rank(c[0]))
            overclaiming = [c for c in selves if rank(c[0]) > rank(strictest_base[0])]
            if overclaiming:
                claims = "; ".join(f"{label} says {info.commercial_use}"
                                   for info, label, _ in overclaiming)
                winner.restrictions.append(
                    f"{claims}, but {strictest_base[1]} only permits "
                    f"'{strictest_base[0].commercial_use}'. A derivative cannot grant "
                    "more than the model it was trained on, so the base model's terms "
                    "are the ones that apply.")

        winner.restrictions.extend(_dedupe(evidence))
        if why and why not in winner.matched_on:
            winner.matched_on = f"{winner.matched_on} + {why}" if winner.matched_on else why
        return winner

    def _candidates_from(self,
                         facts: SourceFacts) -> list[tuple[LicenseInfo, str, str]]:
        """Turn one source's facts into licence candidates.

        Each is tagged "self" (a description of this file) or "inherited" (the
        terms of something it was built on), because the two are weighed
        differently when they disagree.
        """
        out: list[tuple[LicenseInfo, str, str]] = []

        if facts.licence_tag:
            info = self.matcher.by_tag(facts.licence_tag, facts.licence_url)
            if info is not None:
                out.append((info, f"{facts.source} licence tag", "self"))

        if facts.source == "civitai" and facts.permissions:
            position = Civitai.commercial_position(facts.permissions)
            if position != "unknown":
                info = LicenseInfo(
                    name="Civitai uploader permissions",
                    commercial_use=position,
                    fee="none" if position == "yes" else "unknown",
                    redistribution="conditional",
                    url=facts.url,
                    matched_on="uploader's Civitai permission flags",
                    confidence="medium",
                    summary=("What the uploader says they permit. This is a claim on "
                             "the upload form, not a licence document, and it cannot "
                             "grant more than the base model allows."),
                )
                out.append((info, "Civitai uploader flags", "self"))

        # The base model's terms travel to everything trained on it.
        for base in facts.base_models[:3]:
            info, name = self.matcher.by_base_model(base)
            if info is None:
                continue
            if info.commercial_use == "unknown":
                continue
            info.restrictions.insert(
                0, f"Inherited from the base model {name}: a derivative cannot be "
                   "more permissive than what it was trained on.")
            info.matched_on = f"base model {name}"
            out.append((info, f"base model {name}", "inherited"))

        return out

    def _bundled(self, ref: ModelRef) -> Provenance:
        known = catalog.known_model(ref.filename)
        if not known:
            return Provenance(source="unknown", confidence="low",
                              notes=["not present in the bundled model index"])
        return Provenance(
            source="comfy-manager",
            identifier=known.get("name", ""),
            url=known.get("reference") or known.get("url", ""),
            resolved_by="bundled-index",
            confidence="medium",
            notes=[n for n in (
                f"base model family: {known['base']}" if known.get("base") else "",
                f"published size: {known['size']}" if known.get("size") else "",
            ) if n],
        )

    def _best_provenance(self, baseline: Provenance,
                         facts: list[SourceFacts]) -> Provenance:
        """Prefer a live source over the bundled index, hash over name."""
        order = {"high": 0, "medium": 1, "low": 2}
        best = sorted(facts, key=lambda f: order.get(f.confidence, 3))
        if not best:
            return baseline

        chosen = best[0]
        provenance = Provenance(
            source=chosen.source,
            identifier=chosen.identifier,
            url=chosen.url,
            author=chosen.author,
            downloads=chosen.downloads,
            likes=chosen.likes,
            last_modified=chosen.last_modified,
            gated=bool(chosen.gated),
            resolved_by=f"{chosen.source}-api",
            confidence=chosen.confidence,
        )
        provenance.notes.extend(_dedupe(
            [e for f in facts for e in f.evidence] + baseline.notes))
        if chosen.gated:
            provenance.notes.append(f"gated repository (mode: {chosen.gated})")
        return provenance

    # -- diagnostics -------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        return {
            "sources": sorted(self.sources) if self.enabled else [],
            "credentials": self.credentials.describe(),
            "http_cache_hits": self.http.hits,
            "http_requests": self.http.requests,
            "rate_limits": self.http.rate_limit_notes(),
            "lookup_errors": self.http.errors[:20],
        }


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def _hf_repo_from_url(url: str) -> str:
    import re
    match = re.match(r"https?://huggingface\.co/([\w.-]+/[\w.-]+)", url or "")
    return match.group(1) if match else ""
