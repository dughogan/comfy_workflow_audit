"""Render an audit as a Markdown report."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

from .. import __version__
from ..audit import AuditReport
from ..records import Finding, ModelRef
from . import narrative as narrative_section
from . import review as review_section
from ..score import clearance, licensing

SEVERITY_MARK = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM",
                 "low": "LOW", "info": "INFO"}

COMMERCIAL_MARK = {"yes": "Yes", "conditional": "Conditional", "no": "No", "unknown": "Unknown"}


class _Sections:
    """Hands out section numbers in the order sections are actually written.

    The determination section only exists when a studio profile was given, so
    hard-coded numbers would leave a gap in every report without one.
    """

    def __init__(self, write):
        self._write = write
        self._n = 0

    def __call__(self, title: str) -> None:
        self._n += 1
        self._write(f"## {self._n}. {title}")
        self._write("")


def render(report: AuditReport) -> str:
    out: list[str] = []
    w = out.append
    section = _Sections(w)
    src = report.source

    w(f"# ComfyUI workflow audit - {src.get('name', 'workflow')}")
    w("")
    w(f"*Generated {_dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
      f"by comfyaudit {__version__}.*")
    w("")

    # -- verdict -----------------------------------------------------------
    lic = report.licensing
    clr = report.clearance
    w("## Summary")
    w("")
    w("| | |")
    w("|---|---|")
    if report.registry.loaded:
        w(f"| **Since last cleared** | {report.registry.headline()} |")
    if clr.determined:
        outstanding = sum(clr.actions.values())
        w(f"| **Assessment** | {clearance.VERDICT_LABELS[clr.verdict]}"
          + (f" - {outstanding} thing{'' if outstanding == 1 else 's'} to resolve"
             if outstanding else "")
          + f", for {clr.profile.describe()} |")
    w(f"| **Licences** | {lic.headline or 'No models found.'} |")
    w(f"| **Operational risk** | {report.risk.score}/100 - {report.risk.band} |")
    auto = report.automation
    w(f"| **Human touchpoints** | {len(auto.per_run_touchpoints)} per run"
      + (f", {len(auto.setup_touchpoints)} at setup" if auto.setup_touchpoints else "")
      + f" - {auto.band.lower()} |")
    w(f"| **Graph** | {src.get('nodes_total', 0)} nodes "
      f"({src.get('nodes_disabled', 0)} disabled), {len(report.models)} models, "
      f"{len(report.packs)} custom packs |")
    w("")
    w(f"{report.risk.band_detail} {report.automation.band_detail}")
    w("")
    if clr.determined:
        w(f"{clr.headline}")
        w("")

    # The plain-language summary runs either way. Without a profile it is the
    # only thing standing between a reader and a stack of tables; with one it is
    # what keeps the assessment legible when the reasoning gets skimmed, which
    # it will be.
    w("### In plain terms")
    w("")
    for paragraph in narrative_section.summarise(report):
        w(paragraph)
        w("")

    if clr.determined:
        w("*This applies the licence terms to the studio profile given above, and "
          "nothing else. Change the profile and it changes. It is not legal "
          "advice; it is a reading of published terms, with the reasoning shown "
          "so it can be checked.*")
    else:
        w("*This report describes what the licences say. It does not decide whether "
          "they suit your job - that depends on the client, the territory and any "
          "agreements you already hold.*")
    w("")

    if report.freshness.state in ("old", "unknown"):
        w(f"> **Check this at source.** {report.freshness.message}")
        w("")

    counts = report.risk.counts()
    if counts:
        parts = [f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low", "info")
                 if counts.get(s)]
        w(f"**Findings:** {', '.join(parts)}.")
        w("")

    _headline_actions(w, report)

    # -- what is new, then what has to happen ------------------------------
    _registry_section(w, section, report)
    _clearance_section(w, section, report)

    # -- licence composition -----------------------------------------------
    _licence_section(w, section, report)

    # -- models ------------------------------------------------------------
    section("Models")
    if not report.models:
        w("No model references were found in this workflow.")
        w("")
    else:
        w("| Model | Role | Licence | Commercial use | Fee | Source |")
        w("|---|---|---|---|---|---|")
        for model in report.models:
            lic = model.license
            prov = model.provenance
            w("| {file} | {role} | {lic} | {comm} | {fee} | {src} |".format(
                file=_cell(model.filename) + ("" if model.enabled else " *(disabled)*"),
                role=_cell(model.role),
                lic=_cell(lic.name if lic else "Unknown"),
                comm=COMMERCIAL_MARK.get(lic.commercial_use if lic else "unknown", "Unknown"),
                fee=_cell(_fee_label(lic.fee if lic else "unknown")),
                src=_source_cell(prov),
            ))
        w("")
        _model_details(w, report.models)

    # -- prompts -----------------------------------------------------------
    section("Prompts")
    if not report.prompts:
        w("No prompt text was found.")
        w("")
    else:
        for polarity in ("positive", "negative", "both", "system", "unknown"):
            group = [p for p in report.prompts if p.polarity == polarity]
            if not group:
                continue
            w(f"### {polarity.capitalize()} ({len(group)})")
            w("")
            for prompt in group:
                head = f"**{prompt.node_label}** - `{prompt.widget}`"
                flags = []
                if prompt.driven_by_link:
                    flags.append("driven from upstream at run time")
                if not prompt.enabled:
                    flags.append("node disabled")
                if prompt.dynamic_syntax:
                    flags.append("contains {a|b} alternation")
                if prompt.token_estimate > 77 and prompt.node_type.startswith("CLIPTextEncode"):
                    flags.append(f"~{prompt.token_estimate} tokens, over the 77-token CLIP window")
                if flags:
                    head += f" *({'; '.join(flags)})*"
                w(head)
                w("")
                w("```text")
                w(prompt.text.strip()[:2000])
                w("```")
                if prompt.consumers:
                    w(f"Consumed by: {', '.join(prompt.consumers)}")
                w("")

        deps = report.prompt_dependencies
        extras = [(k, v) for k, v in deps.items() if v and k != "dynamic_syntax"]
        if extras:
            w("**Dependencies referenced inside prompt text**")
            w("")
            for key, values in extras:
                w(f"- {key.replace('_', ' ')}: {', '.join(values)}")
            w("")

    if report.notes:
        w("### Notes left in the graph")
        w("")
        for note in report.notes:
            w(f"- **{note.node_label}**: {_oneline(note.text, 400)}")
        w("")

    # -- assets ------------------------------------------------------------
    section("Assets")
    if report.inputs:
        w("**Inputs**")
        w("")
        w("| Asset | Kind | Node | Supplied how |")
        w("|---|---|---|---|")
        for asset in report.inputs:
            how = "upload widget (a person picks it)" if asset.upload_widget else "path in the graph"
            if asset.kind == "url":
                how = "downloaded at run time"
            if asset.absolute_path:
                how += " - absolute path"
            w(f"| {_cell(asset.value)} | {asset.kind} | {_cell(asset.node_label)} | {how} |")
        w("")
    else:
        w("No external input assets - this workflow generates from scratch.")
        w("")

    if report.outputs:
        w("**Outputs**")
        w("")
        for out_asset in report.outputs:
            w(f"- `{out_asset.value}` from {out_asset.node_label} (`{out_asset.node_type}`)")
        w("")

    # -- dependencies ------------------------------------------------------
    section("Node dependencies")
    w(f"Core ComfyUI node types used: **{len(report.core_node_types)}**"
      + (f" | Hosted API node types: **{len(report.api_node_types)}**" if report.api_node_types else ""))
    w("")
    if report.packs:
        w("| Pack | Author | Licence | Nodes used | Version pinned | Stars | Last commit |")
        w("|---|---|---|---|---|---|---|")
        for pack in report.packs:
            if not pack.identified:
                w(f"| {_cell(pack.title)} **(unidentified)** | - | - | "
                  f"{pack.node_count} | - | - | - |")
                continue
            w("| [{title}]({url}) | {author} | {lic} | {n} ({types}) | {ver} | {stars} | {last} |".format(
                title=_cell(pack.title), url=pack.reference or "#",
                author=_cell(pack.author or "-"),
                lic=_cell(pack.licence or "not checked"),
                n=pack.node_count,
                types=_cell(", ".join(pack.node_types[:3]) + ("..." if len(pack.node_types) > 3 else "")),
                ver=pack.pinned_version or "**not pinned**",
                stars=pack.stars if pack.stars is not None else "-",
                last=pack.last_update.split(" ")[0] if pack.last_update else "-",
            ))
        w("")
        notes = [(p.title, n) for p in report.packs for n in p.notes]
        if notes:
            for title, note in notes:
                w(f"- **{title}**: {note}")
            w("")
    else:
        w("This workflow uses only core ComfyUI nodes - the strongest possible position "
          "for portability and long-term maintenance.")
        w("")

    if report.api_node_types:
        w("**Hosted API nodes** (billed per call, run on vendor infrastructure):")
        w("")
        for node_type in report.api_node_types:
            w(f"- `{node_type}`")
        w("")

    # -- automation --------------------------------------------------------
    section("Where a human has to be")
    auto = report.automation
    w(f"**{auto.index}/100 - {auto.band}.** {auto.band_detail}")
    w("")
    w(f"Per-run human cost: {auto.per_run_cost:.1f} | one-off setup cost: {auto.setup_cost:.1f}")
    w("")
    per_run = auto.per_run_touchpoints
    if per_run:
        w("### Human touchpoints on every run")
        w("")
        w("| Weight | When | Touchpoint | Why |")
        w("|---|---|---|---|")
        for tp in per_run:
            w(f"| {tp.cost:.1f} | {tp.stage} | {_cell(tp.label)} | {_cell(tp.detail)} |")
        w("")
    else:
        w("No per-run human touchpoints were detected: this graph can be queued as-is.")
        w("")

    setup = auto.setup_touchpoints
    if setup:
        w("### One-off setup before it will run anywhere else")
        w("")
        for tp in setup:
            w(f"- **{tp.label}** - {tp.detail}")
        w("")

    if auto.automation_signals:
        w("### What is already automated")
        w("")
        for sig in auto.automation_signals:
            w(f"- {sig}")
        w("")

    # -- risks -------------------------------------------------------------
    section("Operational risks")
    if not report.risk.findings:
        w("No risks were identified.")
        w("")
    else:
        if report.risk.by_category:
            w("| Category | Weighted score |")
            w("|---|---|")
            for cat, value in sorted(report.risk.by_category.items(), key=lambda kv: -kv[1]):
                w(f"| {cat} | {value} |")
            w("")
        for finding in report.risk.findings:
            w(f"### [{SEVERITY_MARK.get(finding.severity, finding.severity.upper())}] "
              f"{finding.title}")
            w("")
            w(f"*{finding.category}*")
            w("")
            w(finding.detail)
            w("")
            if finding.evidence:
                for item in finding.evidence[:12]:
                    w(f"- {item}")
                if len(finding.evidence) > 12:
                    w(f"- ...and {len(finding.evidence) - 12} more")
                w("")
            if finding.recommendation:
                w(f"**What to do:** {finding.recommendation}")
                w("")

    # -- claude review -----------------------------------------------------
    review = review_section.get_review(report)
    if review:
        w(review_section.markdown(review))
        w("")

    # -- appendix ----------------------------------------------------------
    _appendix(w, report)
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------


def _registry_section(w, section, report: AuditReport) -> None:
    """What is new since the facility last looked.

    This runs before everything else when a registry exists, because by the
    second workflow it is the only part anyone reads: the licences were settled
    in March and nobody needs telling again.
    """
    reg = report.registry
    if not reg.loaded:
        return

    section("New since last cleared")
    w(reg.headline())
    w("")

    if reg.clean:
        w("Nothing here requires a fresh decision. The sections below restate "
          "what those decisions were, for the record.")
        w("")
        return

    for state, heading, lead in (
        ("rejected", "Previously rejected",
         "Someone has already decided these should not be used. If that has "
         "changed, the registry entry is what to update."),
        ("changed", "Changed since they were cleared",
         "These were signed off, but not in the state they are in now. A "
         "decision is about a specific file and a specific licence."),
        ("pending", "Still awaiting an answer",
         "Raised before and never resolved."),
        ("new", "Not seen before",
         "Nobody has decided about these yet. This is the list that needs "
         "someone's time."),
    ):
        items = [m for m in reg.matches if m.state == state]
        if not items:
            continue
        w(f"### {heading}")
        w("")
        w(lead)
        w("")
        for match in items:
            w(f"- **{_cell(match.subject)}** ({match.kind}) - {match.detail}")
        w("")

    if reg.known:
        w(f"*{len(reg.known)} other item(s) were already cleared and are not "
          "repeated here.*")
        w("")


def _clearance_section(w, section, report: AuditReport) -> None:
    """What the licences mean for this particular studio.

    Grouped by the reasoning rather than by file: four MiniMax weights that all
    fail on the same territory clause are one finding, not four.
    """
    clr = report.clearance
    if not clr.determined:
        # Without a profile there is nothing to determine, and a section saying
        # so would outrank the licence summary for no reason. The facts that
        # would settle it are appended to that section instead.
        return

    section("What has to happen")
    w(f"**{clearance.VERDICT_LABELS[clr.verdict]}.** {clr.headline}")
    w("")
    w(f"*Assessed for: {clr.profile.describe()}*"
      + (f" *({clr.profile.label})*" if clr.profile.label else ""))
    w("")

    for verdict, heading, lead in (
        ("no-go", "Has to change", "These need resolving before the workflow "
                                   "goes on a paid job. Each names what lifts it, "
                                   "and most are one swap or one purchase."),
        ("conditions", "Conditions to meet", "None of these block. Each is "
                                             "something to do, budget for or confirm."),
        ("unknown", "Unresolved", "Not enough is known to say either way."),
    ):
        items = clr.by_verdict(verdict)
        if not items:
            continue
        w(f"### {heading}")
        w("")
        w(lead)
        w("")
        for reasons, subjects, also in _group(items, verdict):
            w(_join_subjects(subjects))
            w("")
            for reason in reasons:
                w(f"- {reason.text}")
                if reason.remedy:
                    w(f"  - *What lifts it:* {reason.remedy}")
            for reason in also:
                w(f"- *Also, once that is resolved:* {reason.text}")
                if reason.remedy:
                    w(f"  - *What lifts it:* {reason.remedy}")
            w("")

    cleared = clr.by_verdict("go")
    if cleared:
        w("### Clear")
        w("")
        w("No condition in these licences is triggered by this studio's "
          "circumstances.")
        w("")
        for det in cleared:
            w(f"- **{det.subject}** - {det.licence}")
        w("")


def _group(items, verdict):
    """Collapse determinations that fail for identical reasons.

    Reporting the same territory clause four times because four files share a
    licence buries the one thing the reader has to act on.
    """
    def split(det):
        primary = [r for r in det.reasons if r.verdict == verdict]
        # Anything short of a clean pass still costs the reader something.
        also = [r for r in det.reasons
                if r.verdict not in (verdict, "go")]
        return primary, also

    buckets: dict[tuple, list[str]] = {}
    for det in items:
        primary, also = split(det)
        key = tuple((r.text, r.remedy) for r in primary + also)
        buckets.setdefault(key, []).append(det.subject)

    out = []
    for det in items:
        primary, also = split(det)
        key = tuple((r.text, r.remedy) for r in primary + also)
        if key in buckets:
            out.append((primary, buckets.pop(key), also))
    return out


def _join_subjects(subjects: list[str]) -> str:
    """Filenames read as code; a heading like 'Performer likeness' does not."""
    def mark(name: str) -> str:
        return f"`{name}`" if ("." in name or "/" in name) else name

    if len(subjects) <= 3:
        return "**" + ", ".join(mark(s) for s in subjects) + "**"
    return ("**" + ", ".join(mark(s) for s in subjects[:3])
            + f" and {len(subjects) - 3} more**")


def _licence_section(w, section, report: AuditReport) -> None:
    """What the licences in this workflow say, grouped and sourced."""
    lic = report.licensing
    section("Licence summary")
    if not lic.groups:
        w("No models were found, so there is nothing to report here.")
        w("")
        return

    w("| Licence | Models | Commercial use | Fee | Confidence |")
    w("|---|---|---|---|---|")
    for group in lic.groups:
        w(f"| {_cell(group.licence)} | {group.count} | {group.position} | "
          f"{_cell(licensing.describe_fee(group.fee))} | {group.confidence} |")
    w("")

    for group in lic.groups:
        w(f"**{group.licence}** — {', '.join(f'`{m}`' for m in group.models[:8])}"
          + (f" and {len(group.models) - 8} more" if len(group.models) > 8 else ""))
        w("")
        if group.summary:
            w(group.summary)
            w("")
        if group.restrictions:
            for restriction in group.restrictions[:10]:
                w(f"- {restriction}")
            w("")
        if group.url:
            w(f"*[Licence terms]({group.url})*")
            w("")

    if lic.obligations:
        w("### Obligations that come with these licences")
        w("")
        for obligation in lic.obligations:
            w(f"- {obligation}")
        w("")

    if lic.to_verify:
        w("### Worth confirming at source")
        w("")
        w("These are the entries the tool is least sure about. A licence is matched "
          "from a filename, and filenames can be changed by anyone.")
        w("")
        for item in lic.to_verify:
            w(f"- {item}")
        w("")

    if lic.hosted_api_types:
        w("### Hosted models")
        w("")
        w(f"{len(lic.hosted_api_types)} node type(s) call a vendor API rather than "
          "loading local weights. Their terms come from that vendor's contract, "
          "which is not visible in the workflow: "
          + ", ".join(f"`{t}`" for t in lic.hosted_api_types))
        w("")

    facts = report.clearance.missing_facts
    if facts and not report.clearance.determined:
        w("### What would turn this into an answer")
        w("")
        w("Whether these terms suit a given job depends on facts about the studio "
          "that a workflow file cannot contain. Supply them and this report will "
          "say go, no-go, or go-with-conditions, and show its reasoning:")
        w("")
        for fact in facts:
            w(f"- {fact}")
        w("")


def _headline_actions(w, report: AuditReport) -> None:
    """The operational items most likely to bite first."""
    top = [f for f in report.risk.findings if f.severity in ("critical", "high")][:3]
    if not top:
        return
    w("### Most likely to bite first")
    w("")
    for i, finding in enumerate(top, 1):
        w(f"{i}. **{finding.title}** - {finding.recommendation or finding.detail}")
    w("")


def _model_details(w, models: list[ModelRef]) -> None:
    interesting = [m for m in models if m.license and (
        m.license.restrictions or m.license.commercial_use in ("no", "conditional", "unknown")
    )]
    if not interesting:
        return
    w("### Licence detail")
    w("")
    for model in interesting:
        lic = model.license
        w(f"**{model.filename}** - {lic.name}")
        w("")
        if lic.summary:
            w(f"{lic.summary}")
            w("")
        if lic.restrictions:
            for restriction in lic.restrictions:
                w(f"- {restriction}")
            w("")
        meta = []
        if lic.matched_on:
            meta.append(f"matched on `{lic.matched_on}`")
        meta.append(f"confidence: {lic.confidence}")
        if lic.url:
            meta.append(f"[licence source]({lic.url})")
        if model.provenance and model.provenance.url:
            meta.append(f"[model source]({model.provenance.url})")
        w(f"*{' | '.join(meta)}*")
        w("")


def _appendix(w, report: AuditReport) -> None:
    w("## Appendix")
    w("")
    know = report.knowledge
    lic_meta = know.get("licences", {})
    diag = report.diagnostics
    w("**Knowledge sources**")
    w("")
    w(f"- Core node schemas scraped from ComfyUI {know.get('comfyui_catalog_version', '?')}")
    w(f"- Custom node index: {know.get('node_packs_indexed', 0)} packs from the ComfyUI-Manager registry")
    w(f"- Licence knowledge base v{lic_meta.get('version', '?')} "
      f"({lic_meta.get('model_rules', 0)} model rules, "
      f"{lic_meta.get('licence_terms', 0)} licence definitions), last verified "
      f"{lic_meta.get('checked', '?')}")
    if know.get("base_models"):
        w(f"- Base-model licence table: {know['base_models']} base models, derived "
          "from Civitai's own published mapping")
    if diag.get("sources"):
        w(f"- Online sources consulted: {', '.join(diag['sources'])}")
    for note in diag.get("rate_limits", []):
        w(f"- {note}")
    if lic_meta.get("overrides_from"):
        w(f"- Studio licence overrides: {lic_meta['overrides_from']}")
    w("")

    w("")
    w("**Audit coverage**")
    w("")
    w(f"- Online lookups: {'enabled' if diag.get('online') else 'disabled (offline knowledge base only)'}")
    if diag.get("models_dir"):
        w(f"- Local models directory: `{diag['models_dir']}` "
          f"({diag.get('models_scanned_locally', 0)} weight files indexed, "
          f"{diag.get('models_hashed', 0)} hashed)")
    else:
        w("- Local models directory: not supplied, so model presence was not verified")
    if diag.get("parser_warning_count"):
        w(f"- Parser warnings: {diag['parser_warning_count']} "
          "(nodes whose stored widget values did not match a known schema)")
    if diag.get("lookup_errors"):
        w(f"- Failed lookups: {len(diag['lookup_errors'])}")
        for err in diag["lookup_errors"][:5]:
            w(f"  - {err}")
    w("")

    w("**Reading this report**")
    w("")
    w(report.freshness.message)
    w("")
    w("Licence findings are derived by matching model filenames against a curated "
      "knowledge base. Filenames are not authoritative - anyone can rename a weight - "
      "so every licence verdict carries the pattern it matched on and a confidence "
      "level. Treat a `low` confidence verdict as a prompt to check the source page, "
      "not as an answer.")
    w("")
    w("This is an engineering tool, not legal advice. It is designed to find the "
      "questions worth asking your legal or production team before a delivery, and to "
      "make the answers reproducible.")
    w("")


def _cell(text: Any) -> str:
    value = str(text if text is not None else "")
    return value.replace("|", "\\|").replace("\n", " ")[:160]


def _oneline(text: str, limit: int) -> str:
    return " ".join(str(text).split())[:limit]


def _fee_label(fee: str) -> str:
    return {
        "none": "None",
        "revenue-threshold": "Above revenue cap",
        "paid": "Licence required",
        "unknown": "Unknown",
    }.get(fee, fee)


def _source_cell(prov: Any) -> str:
    if prov is None or prov.source == "unknown":
        return "unresolved"
    if prov.url:
        return f"[{prov.source}]({prov.url})"
    return prov.source
