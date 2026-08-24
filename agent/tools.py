"""The tools Claude uses to investigate a workflow.

The rule engine produces facts; the agent's job is the judgement the rules
cannot reach - what an unrecognised checkpoint actually *is*, whether a prompt
names something that needs clearing, and what to swap a blocked model for. That
only works if the model can go and look, rather than being handed a summary and
asked to opine, so the audit is exposed as a set of read tools plus a set of
record tools that collect structured results.

Nothing here imports the Anthropic SDK; :mod:`agent.reviewer` binds these to it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.audit import AuditReport
from ..core.knowledge import licences as licences_mod
from ..core.resolve.resolver import Resolver
from ..core.resolve.sources import normalise_repo

MAX_TEXT = 4000


@dataclass
class Collector:
    """Structured results the agent records as it works."""

    identifications: list[dict[str, Any]] = field(default_factory=list)
    content_risks: list[dict[str, Any]] = field(default_factory=list)
    substitutions: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifications": self.identifications,
            "content_risks": self.content_risks,
            "substitutions": self.substitutions,
            "actions": sorted(self.actions, key=lambda a: a.get("order", 99)),
            "tool_calls": self.tool_calls,
        }

    @property
    def is_empty(self) -> bool:
        return not (self.identifications or self.content_risks
                    or self.substitutions or self.actions)


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=1, default=str)[:12000]


def build_tools(report: AuditReport, collector: Collector,
                decorator: Callable[[Callable], Any],
                local_models: Callable[[str], list[str]] | None = None,
                resolver: Resolver | None = None) -> list[Any]:
    """Bind the audit to a list of decorated tool callables.

    ``decorator`` is the SDK's ``@beta_tool``; passing it in keeps this module
    importable without the Anthropic package installed.
    """
    note = collector.tool_calls.append

    # -- reading the audit -------------------------------------------------

    @decorator
    def list_models(only_unidentified: bool = False) -> str:
        """List the models this workflow loads, with their licence status.

        Args:
            only_unidentified: When true, return only models whose licence the
                rule engine could not determine. These are the ones worth your
                attention.
        """
        note(f"list_models(only_unidentified={only_unidentified})")
        rows = []
        for model in report.models:
            lic = model.license
            if only_unidentified and lic and lic.commercial_use != "unknown":
                continue
            rows.append({
                "filename": model.filename,
                "role": model.role,
                "folder": model.folder,
                "loaded_by": f"{model.node_label} ({model.node_type})",
                "enabled": model.enabled,
                "strength": model.strength,
                "licence": lic.name if lic else "Unknown",
                "commercial_use": lic.commercial_use if lic else "unknown",
                "matched_on": lic.matched_on if lic else "",
                "source": (model.provenance.url if model.provenance else "") or "unresolved",
                "sha256": next((n.split("sha256: ")[1] for n in model.notes
                                if n.startswith("sha256: ")), ""),
            })
        return _json(rows) if rows else "No models matched."

    @decorator
    def get_prompts(polarity: str = "all") -> str:
        """Read the prompt text in the workflow.

        Args:
            polarity: One of "all", "positive", "negative", "system" or
                "unknown". Positive prompts are where naming risk usually lives.
        """
        note(f"get_prompts({polarity})")
        rows = []
        for prompt in report.prompts:
            if polarity != "all" and prompt.polarity != polarity:
                continue
            rows.append({
                "node": prompt.node_label,
                "widget": prompt.widget,
                "polarity": prompt.polarity,
                "enabled": prompt.enabled,
                "wildcards": prompt.wildcards,
                "text": prompt.text[:MAX_TEXT],
            })
        for note_ref in report.notes:
            if polarity in ("all", "unknown"):
                rows.append({"node": note_ref.node_label, "widget": "note",
                             "polarity": "note", "text": note_ref.text[:MAX_TEXT]})
        return _json(rows) if rows else "No prompt text found."

    @decorator
    def list_findings(severity: str = "all") -> str:
        """List the production-risk findings the rule engine produced.

        Args:
            severity: One of "all", "critical", "high", "medium", "low", "info".
        """
        note(f"list_findings({severity})")
        rows = [
            {"id": f.id, "severity": f.severity, "category": f.category,
             "title": f.title, "detail": f.detail, "evidence": f.evidence[:8],
             "recommendation": f.recommendation}
            for f in report.risk.findings
            if severity == "all" or f.severity == severity
        ]
        return _json(rows) if rows else "No findings at that severity."

    @decorator
    def list_custom_node_packs() -> str:
        """List the custom node packs this workflow depends on."""
        note("list_custom_node_packs()")
        rows = [
            {"title": p.title, "author": p.author, "repository": p.reference,
             "nodes_used": p.node_types, "pinned_version": p.pinned_version or None,
             "stars": p.stars, "last_commit": p.last_update,
             "identified": p.identified, "notes": p.notes}
            for p in report.packs
        ]
        return _json(rows) if rows else "This workflow uses only core ComfyUI nodes."

    @decorator
    def describe_workflow() -> str:
        """Get the shape of the workflow: node counts, groups, outputs, scores."""
        note("describe_workflow()")
        return _json({
            "source": report.source,
            "licence_summary": report.licensing.headline,
            "licence_counts": report.licensing.counts,
            "risk_score": report.risk.score,
            "automation_index": report.automation.index,
            "automation_band": report.automation.band,
            "human_touchpoints": [
                {"label": t.label, "stage": t.stage, "weight": t.cost}
                for t in report.automation.per_run_touchpoints
            ],
            "hosted_api_nodes": report.api_node_types,
            "inputs": [{"value": a.value, "kind": a.kind,
                        "upload_widget": a.upload_widget} for a in report.inputs],
            "outputs": [o.value for o in report.outputs],
        })

    @decorator
    def search_licence_knowledge_base(query: str) -> str:
        """Search the auditor's own licence knowledge base.

        Use this before claiming a licence, so your answer stays consistent with
        what the tool already reports and you can reuse an existing entry.

        Args:
            query: A model family or licence name, e.g. "flux", "insightface".
        """
        note(f"search_licence_knowledge_base({query!r})")
        kb = licences_mod.load_kb()
        needle = query.strip().lower()
        hits = []
        for rule in kb["models"]:
            haystack = " ".join([
                rule["id"], rule["family"], rule["licence"],
                " ".join(rule.get("match", {}).get("filename", [])),
            ]).lower()
            if needle in haystack:
                terms = kb["licences"].get(rule["licence"], {})
                hits.append({
                    "family": rule["family"], "licence_id": rule["licence"],
                    "licence_name": terms.get("name"),
                    "commercial_use": terms.get("commercial_use"),
                    "summary": terms.get("summary"),
                    "source": rule.get("source"),
                })
        known = [k for k in kb["licences"] if needle in k.lower()]
        return _json({"model_rules": hits[:12], "licence_ids": known[:12]}) \
            if (hits or known) else "Nothing in the knowledge base matches that."

    @decorator
    def list_models_available_locally(folder: str) -> str:
        """List weights actually installed on this machine, by model folder.

        Use this before proposing a substitution: a replacement the facility
        already has on disk can be swapped in today, one they do not have is a
        download and a re-approval.

        Args:
            folder: A ComfyUI model folder, e.g. "checkpoints", "loras",
                "upscale_models", "vae", "controlnet".
        """
        note(f"list_models_available_locally({folder!r})")
        if local_models is None:
            return ("No local model index is available - this audit was not run "
                    "against a live ComfyUI install.")
        names = local_models(folder)
        if not names:
            return f"No models found in the '{folder}' folder."
        return _json(sorted(names)[:200])

    # -- recording results -------------------------------------------------

    @decorator
    def record_model_identification(filename: str, family: str, base_model: str,
                                    licence: str, commercial_use: str,
                                    confidence: str, reasoning: str,
                                    verify_at: str = "") -> str:
        """Record what you believe an unidentified model actually is.

        Only call this for models the rule engine could not identify. Be honest
        about uncertainty - a wrong licence claim is worse than "unknown".

        Args:
            filename: The model filename exactly as it appears in the workflow.
            family: The model family or product name, e.g. "SDXL 1.0 community merge".
            base_model: The architecture it derives from, e.g. "SDXL", "FLUX.1 [dev]", "SD 1.5".
            licence: The licence you believe applies, or "unknown".
            commercial_use: One of "yes", "conditional", "no", "unknown".
            confidence: One of "high", "medium", "low". Use "low" freely.
            reasoning: Why you think so, including what the name tells you.
            verify_at: A URL where a human can confirm this, if you know one.
        """
        note(f"record_model_identification({filename!r})")
        collector.identifications.append({
            "filename": filename, "family": family, "base_model": base_model,
            "licence": licence, "commercial_use": commercial_use,
            "confidence": confidence, "reasoning": reasoning, "verify_at": verify_at,
        })
        return f"Recorded identification for {filename}."

    @decorator
    def record_content_risk(kind: str, excerpt: str, where: str, severity: str,
                            detail: str, recommendation: str) -> str:
        """Record a clearance risk found in the prompt text itself.

        This is the class of problem no licence check can catch: a prompt that
        names a real person, a trademark, a copyrighted character, or a living
        artist's style. Report only what is actually present in the text.

        Args:
            kind: One of "trademark", "likeness", "artist-style", "character",
                "copyrighted-work", "other".
            excerpt: The exact words from the prompt that triggered this.
            where: Which prompt node it came from.
            severity: One of "critical", "high", "medium", "low".
            detail: Why this is a clearance problem in a commercial delivery.
            recommendation: What to do about it.
        """
        note(f"record_content_risk({kind!r})")
        collector.content_risks.append({
            "kind": kind, "excerpt": excerpt[:400], "where": where,
            "severity": severity, "detail": detail, "recommendation": recommendation,
        })
        return "Recorded content risk."

    @decorator
    def record_substitution(replace: str, replace_with: str, licence: str,
                            available_locally: bool, quality_impact: str,
                            rationale: str) -> str:
        """Propose a commercially clear replacement for a blocked model.

        Args:
            replace: The filename being replaced.
            replace_with: The suggested replacement, named precisely enough to find.
            licence: The replacement's licence, and why it is acceptable.
            available_locally: Whether it is already installed on this machine.
            quality_impact: Honest assessment of what changes visually. Say so if
                the replacement is worse.
            rationale: Why this is the right swap for this workflow's job.
        """
        note(f"record_substitution({replace!r} -> {replace_with!r})")
        collector.substitutions.append({
            "replace": replace, "replace_with": replace_with, "licence": licence,
            "available_locally": available_locally,
            "quality_impact": quality_impact, "rationale": rationale,
        })
        return "Recorded substitution."

    @decorator
    def record_action(order: int, title: str, detail: str, owner: str = "") -> str:
        """Record one step of the remediation plan, in the order it should happen.

        Args:
            order: 1 for the first thing to do, 2 for the next, and so on.
            title: A short imperative, e.g. "Replace the face pipeline".
            detail: What doing it actually involves for this workflow.
            owner: Who should own it, e.g. "pipeline", "legal", "supervisor".
        """
        note(f"record_action({order}, {title!r})")
        collector.actions.append({"order": order, "title": title,
                                  "detail": detail, "owner": owner})
        return f"Recorded action {order}."

    # -- looking things up upstream ---------------------------------------

    @decorator
    def lookup_huggingface(repository_or_filename: str) -> str:
        """Look a model up on HuggingFace.

        Pass an "owner/name" repository id when you know it, or a weights
        filename to search for the repository that actually contains that file.
        Returns the licence tag, the declared base models, gating status and
        download counts. Prefer this over recalling what you think a model is.

        Args:
            repository_or_filename: e.g. "black-forest-labs/FLUX.1-dev" or
                "flux1-dev.safetensors".
        """
        note(f"lookup_huggingface({repository_or_filename!r})")
        if resolver is None or not resolver.uses("huggingface"):
            return "HuggingFace lookups are not enabled for this audit."
        query = repository_or_filename.strip()
        facts = (resolver.huggingface.repo(query) if "/" in query and "." not in query.split("/")[-1]
                 else None)
        if facts is None:
            facts = resolver.huggingface.repo(query) or resolver.huggingface.find_file(query)
        if facts is None:
            return f"Nothing on HuggingFace matched '{query}'."
        return _json(_facts_dict(facts))

    @decorator
    def lookup_civitai(filename_or_hash: str) -> str:
        """Look a community model up on Civitai.

        A SHA-256 hash gives an exact answer; a filename is a search and can be
        wrong, because filenames on Civitai are whatever the downloader called
        them. The audit records a model's hash when it was run against a local
        install - check list_models for one before searching by name.

        Args:
            filename_or_hash: a SHA-256 hex digest, or a weights filename.
        """
        note(f"lookup_civitai({filename_or_hash!r})")
        if resolver is None or not resolver.uses("civitai"):
            return "Civitai lookups are not enabled for this audit."
        query = filename_or_hash.strip()
        looks_like_hash = len(query) >= 32 and all(c in "0123456789abcdefABCDEF" for c in query)
        facts = (resolver.civitai.by_hash(query) if looks_like_hash
                 else resolver.civitai.by_filename(query))
        if facts is None:
            return (f"Nothing on Civitai matched '{query}'."
                    + ("" if looks_like_hash else
                       " A filename search is unreliable; try the file's SHA-256."))
        return _json(_facts_dict(facts))

    @decorator
    def lookup_github(repository: str) -> str:
        """Look a repository up on GitHub - a node pack, or a model's home.

        Returns the declared licence, stars, whether it is archived and when it
        was last pushed. Use this for custom node packs: their code runs inside
        the studio's own process, so a copyleft licence there reaches further
        than a model licence does.

        Args:
            repository: "owner/name" or any GitHub URL.
        """
        note(f"lookup_github({repository!r})")
        if resolver is None or not resolver.uses("github"):
            return "GitHub lookups are not enabled for this audit."
        repo = normalise_repo(repository)
        if not repo:
            return f"'{repository}' does not name a GitHub repository."
        facts = resolver.github.repo(repo)
        if facts is None:
            return f"No GitHub repository at {repo}."
        return _json(_facts_dict(facts))

    @decorator
    def read_determination() -> str:
        """Read the go/no-go the rule engine reached, and its reasoning.

        Each determination carries the licence terms that were applied, the
        studio fact each was applied to, and what would lift it. Read this
        before writing a narrative: the chain is the evidence, and the narrative
        must not contradict it.

        Returns a note instead when no studio profile was supplied, in which
        case no determination exists and none should be invented.
        """
        note("read_determination()")
        clr = report.clearance
        if not clr.determined:
            return _json({
                "determined": False,
                "why": "No studio profile was supplied, so no verdict was reached.",
                "facts_that_would_settle_it": clr.missing_facts,
            })
        return _json(clr.as_dict())

    tools = [
        describe_workflow, list_models, get_prompts, list_findings,
        list_custom_node_packs, search_licence_knowledge_base, read_determination,
        list_models_available_locally,
        record_model_identification, record_content_risk,
        record_substitution, record_action,
    ]
    if resolver is not None and resolver.enabled:
        tools[8:8] = [lookup_huggingface, lookup_civitai, lookup_github]
    return tools


def _facts_dict(facts: Any) -> dict[str, Any]:
    """The parts of a SourceFacts worth spending context on."""
    return {k: v for k, v in {
        "source": facts.source,
        "identifier": facts.identifier,
        "url": facts.url,
        "author": facts.author,
        "downloads": facts.downloads,
        "likes": facts.likes,
        "last_modified": facts.last_modified,
        "gated": facts.gated,
        "licence_tag": facts.licence_tag,
        "licence_name": facts.licence_name,
        "licence_url": facts.licence_url,
        "base_models": facts.base_models,
        "uploader_permissions": facts.permissions,
        "warnings": facts.warnings,
        "evidence": facts.evidence,
        "confidence": facts.confidence,
    }.items() if v not in (None, "", [], {})}
