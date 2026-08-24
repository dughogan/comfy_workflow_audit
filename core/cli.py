"""Command line interface for comfyaudit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__, catalog, graph
from .knowledge import freshness
from .knowledge import licences as licences_mod
from .audit import AuditOptions, AuditReport, run
from .report import markdown as md_report
from .records import Finding
from .registry import Entry, Registry, STATUSES as REG_STATUSES, entries_from_report
from .score import clearance

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comfyaudit",
        description="Evaluate and audit ComfyUI workflows: models, licences, prompts, "
                    "assets, automation level and production risk.",
    )
    parser.add_argument("--version", action="version", version=f"comfyaudit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="run a full audit on one or more workflows")
    audit.add_argument("workflows", nargs="+",
                       help="workflow .json files, or PNG/WebP images with embedded metadata")
    audit.add_argument("-o", "--output", default="",
                       help="write the report here (a directory when auditing several files)")
    audit.add_argument("-f", "--format", default="markdown",
                       choices=["markdown", "md", "json", "html", "text"],
                       help="report format (default: markdown)")
    audit.add_argument("--online", action="store_true",
                       help="look up provenance and licences on HuggingFace, Civitai and "
                            "the Comfy Registry (results are cached on disk)")
    audit.add_argument("--models-dir", default="",
                       help="path to a ComfyUI models/ folder, to verify the weights exist "
                            "and hash them")
    audit.add_argument("--licences", "--licenses", dest="licences", default="",
                       help="a studio licence file that extends or overrides the bundled one")
    audit.add_argument("--sources", default="",
                       help="limit which services --online contacts, comma separated: "
                            "huggingface, civitai, github, comfy-registry (default: all)")
    audit.add_argument("--hf-token", default="",
                       help="HuggingFace token for gated repositories (or set HF_TOKEN)")
    audit.add_argument("--civitai-token", default="",
                       help="Civitai API key (or set CIVITAI_API_KEY)")
    audit.add_argument("--github-token", default="",
                       help="GitHub token; lifts the 60-requests-an-hour anonymous "
                            "limit (or set GITHUB_TOKEN)")
    audit.add_argument("--no-hash", action="store_true",
                       help="skip SHA-256 hashing of local weights")
    audit.add_argument("--fail-on", default="", choices=[""] + SEVERITY_ORDER,
                       help="exit non-zero if a finding at this severity or worse is present, "
                            "for use as a pipeline gate")
    audit.add_argument("--quiet", action="store_true", help="suppress the console summary")

    # -- the studio's own facts, without which no verdict is possible --------
    profile = audit.add_argument_group(
        "studio profile",
        "Facts about the facility. Licence terms turn on these, so supplying them "
        "is what turns a description of the terms into a go / no-go. Omit them "
        "and the report describes the terms and stops there.")
    profile.add_argument("--territory", default="", metavar="CODE",
                         choices=[""] + sorted(clearance.TERRITORIES),
                         help="where the work is rendered and deployed; several "
                              "licences exclude regions by name")
    profile.add_argument("--revenue", default="unknown", metavar="BAND",
                         choices=sorted(clearance.REVENUE_BANDS),
                         help="annual company revenue band; free use is capped by "
                              "revenue under several licences")
    profile.add_argument("--ships", default="unknown", metavar="WHAT",
                         choices=sorted(clearance.SHIPS),
                         help="what leaves the building; decides whether copyleft "
                              "reaches your own code")
    profile.add_argument("--trains-models", action="store_true",
                         help="outputs are used to train other models, which "
                              "several licences forbid outright")
    profile.add_argument("--likeness", action="store_true",
                         help="real performers are involved, which is a consent "
                              "question no model licence answers")
    profile.add_argument("--studio", default="", metavar="NAME",
                         help="a label for the report - a facility, show or client")
    profile.add_argument("--profile", default="", metavar="PATH",
                         help="read the above from a JSON file instead, so a "
                              "facility states its circumstances once")
    audit.add_argument("--registry", default="", metavar="PATH",
                       help="the facility's decision record. With one, the report "
                            "leads with what is new rather than restating what was "
                            "cleared months ago")
    audit.add_argument("--claude", nargs="?", const="full", default="",
                       choices=["", "full", "identify", "clearance", "remediate",
                                "narrative"],
                       help="have Claude investigate the audit as well: identify models "
                            "the rules could not, review prompt text for trademark and "
                            "likeness risk, and propose commercially clear replacements")
    audit.add_argument("--claude-model", default="claude-opus-5",
                       help="model for the review (default: claude-opus-5)")
    audit.add_argument("--claude-effort", default="high",
                       choices=["low", "medium", "high", "xhigh", "max"])
    audit.add_argument("--no-web-search", action="store_true",
                       help="stop the review looking models up on the web; use this "
                            "when the workflow content is confidential")
    audit.add_argument("--ask", default="",
                       help="ask Claude a specific question about the workflow "
                            "instead of running a review mode")

    listing = sub.add_parser("models", help="list just the models a workflow references")
    listing.add_argument("workflow")
    listing.add_argument("--online", action="store_true")
    listing.add_argument("--models-dir", default="")

    sub.add_parser("info", help="show what the bundled knowledge base contains")

    update = sub.add_parser(
        "update-knowledge",
        help="fetch a newer licence knowledge base",
        description="Licences move - Stability relicensed SD3 mid-flight, Black "
                    "Forest Labs revised the FLUX dev terms - so the bundled "
                    "knowledge has a shelf life. This replaces it, keeping the "
                    "old file alongside and printing what changed.")
    update.add_argument("--source", default=freshness.DEFAULT_SOURCE,
                        help="where to fetch from; defaults to the project repo")
    update.add_argument("--to", default="", metavar="PATH",
                        help="write here instead of over the bundled file, for a "
                             "facility that keeps its own copy")
    update.add_argument("--dry-run", action="store_true",
                        help="show what would change without writing anything")

    # -- registry ----------------------------------------------------------
    reg = sub.add_parser(
        "registry",
        help="the facility's record of what it has already cleared",
        description="A studio has hundreds of workflows and the same few models. "
                    "The registry records decisions once, so every later audit "
                    "answers the short question - what is new here? - instead of "
                    "restating what was settled in March.")
    reg_sub = reg.add_subparsers(dest="registry_command", required=True)

    reg_list = reg_sub.add_parser("list", help="show what has been decided")
    reg_list.add_argument("path")
    reg_list.add_argument("--status", default="", choices=[""] + sorted(REG_STATUSES),
                          help="show only entries with this status")

    reg_add = reg_sub.add_parser(
        "add", help="record decisions for everything a workflow uses",
        description="Drafts an entry per model and pack, then writes them. Review "
                    "the workflow first: this records that a decision was made, "
                    "and one nobody made is not a decision.")
    reg_add.add_argument("path")
    reg_add.add_argument("workflow")
    reg_add.add_argument("--status", default="approved", choices=sorted(REG_STATUSES))
    reg_add.add_argument("--by", default="", metavar="NAME",
                         help="who made the call - a decision with no name on it "
                              "is hard to revisit")
    reg_add.add_argument("--note", default="", help="why, in a sentence")
    reg_add.add_argument("--reference", default="", metavar="REF",
                         help="a ticket, contract or email to point back at")
    reg_add.add_argument("--models-dir", default="",
                         help="hash local weights so a rename cannot break the entry")
    reg_add.add_argument("--all", action="store_true",
                         help="re-record entries that already exist, rather than "
                              "only what is new")

    reg_set = reg_sub.add_parser("set", help="record a decision about one item")
    reg_set.add_argument("path")
    reg_set.add_argument("key", help="a model filename, or a pack repository")
    reg_set.add_argument("--kind", default="model", choices=["model", "pack"])
    reg_set.add_argument("--status", default="approved", choices=sorted(REG_STATUSES))
    reg_set.add_argument("--by", default="", metavar="NAME")
    reg_set.add_argument("--note", default="")
    reg_set.add_argument("--reference", default="", metavar="REF")
    reg_set.add_argument("--licence", "--license", dest="licence", default="")

    reg_rm = reg_sub.add_parser("remove", help="drop a decision")
    reg_rm.add_argument("path")
    reg_rm.add_argument("key")
    reg_rm.add_argument("--kind", default="model", choices=["model", "pack"])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "info":
        return _cmd_info()
    if args.command == "models":
        return _cmd_models(args)
    if args.command == "registry":
        return _cmd_registry(args)
    if args.command == "update-knowledge":
        return _cmd_update_knowledge(args)
    return _cmd_audit(args)


# --------------------------------------------------------------------------


def _cmd_audit(args: argparse.Namespace) -> int:
    from ..core.resolve.resolver import ALL_SOURCES
    wanted = tuple(s.strip().lower() for s in args.sources.split(",") if s.strip())
    unknown = [s for s in wanted if s not in ALL_SOURCES]
    if unknown:
        print(f"unknown source(s): {', '.join(unknown)}. "
              f"Valid: {', '.join(ALL_SOURCES)}", file=sys.stderr)
        return 2

    opts = AuditOptions(
        online=args.online,
        sources=tuple(s for s in wanted if s in ALL_SOURCES) or ALL_SOURCES,
        models_dir=args.models_dir,
        licences_path=args.licences,
        hf_token=args.hf_token,
        civitai_token=args.civitai_token,
        github_token=args.github_token,
        hash_models=not args.no_hash,
        profile=_studio_profile(args),
        registry_path=args.registry,
    )

    paths = _expand(args.workflows)
    if not paths:
        print("no workflow files matched", file=sys.stderr)
        return 2

    multiple = len(paths) > 1
    reports: list[tuple[str, AuditReport]] = []
    failures = 0

    for path in paths:
        try:
            report = run(path, opts)
        except (OSError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            failures += 1
            continue

        if args.claude or args.ask:
            _add_claude_review(report, args)

        reports.append((path, report))
        _emit(report, path, args, multiple)

    if not args.quiet:
        if multiple:
            _print_batch_summary(reports)
        elif reports:
            _print_summary(reports[0][1])

    if failures and not reports:
        return 2

    if args.fail_on:
        threshold = SEVERITY_ORDER.index(args.fail_on)
        for _, report in reports:
            for finding in report.risk.findings:
                if SEVERITY_ORDER.index(finding.severity) <= threshold:
                    return 1
    return 0 if not failures else 2


def _add_claude_review(report: AuditReport, args: argparse.Namespace) -> None:
    """Run the agent and attach the result so every format renders it."""
    from ..agent import reviewer as reviewer_mod
    from ..core.resolve.http import Credentials, HttpClient
    from ..core.resolve.resolver import Resolver

    # Give the agent the same lookups the audit used, so it can check a licence
    # at source instead of recalling one.
    resolver = None
    if args.online:
        from ..core.resolve.resolver import ALL_SOURCES
        wanted = tuple(s.strip().lower() for s in args.sources.split(",") if s.strip())
        resolver = Resolver(
            http=HttpClient(),
            credentials=Credentials.from_environment(
                huggingface=args.hf_token, civitai=args.civitai_token,
                github=args.github_token),
            sources=tuple(s for s in wanted if s in ALL_SOURCES) or ALL_SOURCES,
            enabled=True,
        )

    if not args.quiet:
        print("running the Claude review...", file=sys.stderr)
    result = reviewer_mod.review(
        report,
        mode=args.claude or "full",
        model=args.claude_model,
        effort=args.claude_effort,
        web_search=not args.no_web_search,
        question=args.ask,
        resolver=resolver,
    )
    reviewer_mod.apply_to_report(report, result)
    report.diagnostics["claude_review"] = result.as_dict()
    if result.error and not args.quiet:
        print(f"claude review: {result.error}", file=sys.stderr)


def _emit(report: AuditReport, path: str, args: argparse.Namespace, multiple: bool) -> None:
    fmt = "markdown" if args.format == "md" else args.format
    body = _render(report, fmt)

    if not args.output:
        if fmt == "text":
            return  # the console summary is the text format
        print(body)
        return

    if multiple or os.path.isdir(args.output) or args.output.endswith(os.sep):
        os.makedirs(args.output, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        target = os.path.join(args.output, f"{stem}.audit.{_extension(fmt)}")
    else:
        target = args.output

    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body)
    if not args.quiet:
        print(f"wrote {target}", file=sys.stderr)


def _render(report: AuditReport, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(report.to_dict(), indent=2)
    if fmt == "html":
        from .report import html as html_report
        return html_report.render(report)
    return md_report.render(report)


def _extension(fmt: str) -> str:
    return {"json": "json", "html": "html"}.get(fmt, "md")


# --------------------------------------------------------------------------


def _cmd_models(args: argparse.Namespace) -> int:
    report = run(args.workflow, AuditOptions(online=args.online, models_dir=args.models_dir))
    if not report.models:
        print("no models found")
        return 0
    width = max(len(m.filename) for m in report.models)
    for model in report.models:
        lic = model.license
        flag = {"no": "non-commercial", "conditional": "conditional",
                "unknown": "unstated", "yes": "permissive"}.get(
                    lic.commercial_use if lic else "unknown", "?")
        print(f"{model.filename:<{width}}  {model.role:<28}  {flag:<16}  "
              f"{lic.name if lic else 'Unknown'}")
    return 0


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def _cmd_registry(args) -> int:
    if args.registry_command == "list":
        return _registry_list(args)
    if args.registry_command == "add":
        return _registry_add(args)
    if args.registry_command == "set":
        return _registry_set(args)
    return _registry_remove(args)


def _registry_list(args) -> int:
    registry = Registry.load(args.path)
    entries = [e for e in registry.sorted_entries()
               if not args.status or e.status == args.status]
    if not entries:
        print("no entries" + (f" with status {args.status}" if args.status else ""))
        return 0
    width = min(52, max(len(e.key) for e in entries))
    for entry in entries:
        who = f"{entry.decided_by or '?'} {entry.decided_on}".strip()
        print(f"{entry.status:24} {entry.kind:5} {entry.key[:width]:{width}}  {who}"
              + (f"  [{entry.reference}]" if entry.reference else ""))
    print(f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} in {args.path}")
    return 0


def _registry_add(args) -> int:
    registry = Registry.load(args.path)
    report = run(args.workflow, AuditOptions(
        models_dir=args.models_dir,
        hash_models=bool(args.models_dir),
        registry_path=args.path,
    ))
    drafted = entries_from_report(
        report, status=args.status, decided_by=args.by,
        reference=args.reference, only_new=not args.all)
    if args.note:
        for entry in drafted:
            entry.note = args.note
    if not drafted:
        print("nothing new in this workflow - the registry already covers it")
        return 0
    for entry in drafted:
        registry.record(entry)
        print(f"{entry.status:24} {entry.kind:5} {entry.key}")
    registry.save(args.path)
    print(f"\n{len(drafted)} entr{'y' if len(drafted) == 1 else 'ies'} written to "
          f"{args.path}")
    return 0


def _registry_set(args) -> int:
    registry = Registry.load(args.path)
    registry.record(Entry(
        key=args.key, kind=args.kind, status=args.status, decided_by=args.by,
        note=args.note, reference=args.reference, licence=args.licence,
    ))
    registry.save(args.path)
    print(f"{args.status}: {args.key}")
    return 0


def _registry_remove(args) -> int:
    registry = Registry.load(args.path)
    if not registry.remove(args.key, args.kind):
        print(f"no {args.kind} entry for {args.key}", file=sys.stderr)
        return 1
    registry.save(args.path)
    print(f"removed {args.key}")
    return 0


def _cmd_update_knowledge(args) -> int:
    """Replace the licence knowledge base, showing what moved."""
    current_path = args.to or licences_mod.bundled_kb_path()
    try:
        current = licences_mod.load_kb(current_path if os.path.isfile(current_path)
                                       else None)
    except (OSError, ValueError):
        current = {"licences": {}, "models": []}

    print(f"fetching {args.source}", file=sys.stderr)
    try:
        fetched = freshness.fetch(args.source)
    except Exception as exc:  # noqa: BLE001 - the reason matters more than the type
        print(f"could not fetch a knowledge base: {exc}", file=sys.stderr)
        return 1

    diff = freshness.compare(current, fetched)
    print(f"{diff['from_version'] or 'unknown'} -> {diff['to_version'] or 'unknown'}")
    if diff["added"]:
        print(f"  added   : {', '.join(diff['added'])}")
    if diff["removed"]:
        print(f"  removed : {', '.join(diff['removed'])}")
    for change in diff["changed"]:
        print(f"  changed : {change['name']}")
        for field in change["fields"]:
            print(f"              {field}: {change['was'][field]!r} -> "
                  f"{change['now'][field]!r}")
    if diff["model_rules"]:
        print(f"  model rules: {diff['model_rules']:+d}")
    if not (diff["added"] or diff["removed"] or diff["changed"]
            or diff["model_rules"]):
        print("  no substantive changes")

    if args.dry_run:
        print("\ndry run - nothing written")
        return 0

    written = freshness.install(fetched, current_path)
    print(f"\nwrote {written}")
    if os.path.isfile(written + ".previous"):
        print(f"previous version kept at {written}.previous")
    return 0


def _cmd_info() -> int:
    packs = catalog.node_packs()
    meta = licences_mod.kb_metadata()
    print(f"comfyaudit {__version__}")
    print(f"  core node catalog     ComfyUI {catalog.comfyui_version()}, "
          f"{len(catalog.core_nodes()['nodes'])} node types")
    print(f"  custom node index     {len(packs['packs'])} packs, "
          f"{len(packs['node_index'])} node classes")
    print(f"  known model index     {len(catalog.known_models())} filenames")
    print(f"  licence knowledge     v{meta['version']} ({meta['model_rules']} model rules, "
          f"{meta['licence_terms']} licence definitions), verified {meta['checked']}")
    bases = licences_mod.load_base_models().get("base_models", {})
    classified = sum(1 for r in bases.values() if r.get("licence_id"))
    print(f"  base model licences   {len(bases)} base models, {classified} classified")
    from ..core.resolve.http import Credentials
    from ..core.resolve.resolver import ALL_SOURCES
    have = Credentials.from_environment().describe()
    tokens = ", ".join(f"{k}{'*' if v else ''}" for k, v in have.items())
    print(f"  online sources        {', '.join(ALL_SOURCES)}")
    print(f"  credentials found     {tokens or 'none'}   (* = token present)")
    return 0


# --------------------------------------------------------------------------



def _studio_profile(args) -> clearance.StudioProfile | None:
    """Build the profile from flags, or from the file that stands in for them."""
    if getattr(args, "profile", ""):
        with open(args.profile, "r", encoding="utf-8") as handle:
            return clearance.StudioProfile.from_dict(json.load(handle))
    built = clearance.StudioProfile(
        territory=args.territory,
        revenue_band=args.revenue,
        ships=args.ships,
        trains_models=args.trains_models,
        likeness_involved=args.likeness,
        label=args.studio,
    )
    return built if built.is_set else None


def _expand(paths: list[str]) -> list[str]:
    out: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.lower().endswith((".json", ".png", ".webp")):
                    out.append(os.path.join(path, name))
        else:
            out.append(path)
    return out


def _print_summary(report: AuditReport) -> None:
    risk = report.risk
    auto = report.automation
    counts = risk.counts()
    line = "=" * 68
    print(line, file=sys.stderr)
    print(f" {report.source.get('name', 'workflow')}", file=sys.stderr)
    print(line, file=sys.stderr)
    clr = report.clearance
    if clr.determined:
        outstanding = sum(clr.actions.values())
        print(f" Assessment           : {clearance.VERDICT_LABELS[clr.verdict]}",
              file=sys.stderr)
        print(f"   for                : {clr.profile.describe()}", file=sys.stderr)
        if outstanding:
            print(f"   to resolve         : {clearance._describe_actions(clr.actions)}",
                  file=sys.stderr)
        for blocker in clr.distinct_blockers()[:3]:
            print(f"   has to change      : {blocker}", file=sys.stderr)
    print(f" Licences             : {report.licensing.headline}", file=sys.stderr)
    print(f" Operational risk     : {risk.score}/100 ({risk.band})", file=sys.stderr)
    print(f" Human touchpoints    : {len(auto.per_run_touchpoints)} per run, "
          f"{len(auto.setup_touchpoints)} at setup ({auto.band})", file=sys.stderr)
    print(f" Models / packs       : {len(report.models)} / {len(report.packs)}", file=sys.stderr)
    if counts:
        parts = [f"{counts[s]} {s}" for s in SEVERITY_ORDER if counts.get(s)]
        print(f" Findings             : {', '.join(parts)}", file=sys.stderr)
    for finding in risk.findings[:3]:
        if finding.severity in ("critical", "high"):
            print(f"   [{finding.severity}] {finding.title}", file=sys.stderr)
    print(line, file=sys.stderr)


def _print_batch_summary(reports: list[tuple[str, AuditReport]]) -> None:
    if not reports:
        return
    name_w = max(len(os.path.basename(p)) for p, _ in reports)
    print("", file=sys.stderr)
    print(f"{'workflow':<{name_w}}  {'risk':>7}  {'auto':>5}  licences", file=sys.stderr)
    for path, report in reports:
        counts = report.licensing.counts
        composition = ", ".join(f"{counts[p]} {p}" for p in
                                ("permissive", "conditional", "non-commercial", "unstated")
                                if counts.get(p)) or "none found"
        print(f"{os.path.basename(path):<{name_w}}  "
              f"{report.risk.score:>3}/100  {report.automation.index:>3}  "
              f"{composition}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
