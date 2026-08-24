"""Production risk findings.

Each rule answers one question a supervisor would ask before letting a workflow
near a paying job: can we legally ship the output, can we reproduce this shot in
six months, will it run on the farm, and what happens when it breaks at 2am.

Findings carry evidence and a recommendation, because "high risk" on its own is
not something anyone can act on.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..graph import MODE_BYPASS, MODE_NEVER, Workflow
from ..records import AssetRef, Finding, ModelRef, PackRef, PromptRef
from ..resolve.sources import COPYLEFT_SPDX, WEAK_COPYLEFT_SPDX

SEVERITY_WEIGHT = {"critical": 25.0, "high": 12.0, "medium": 5.0, "low": 2.0, "info": 0.0}

#: These describe how much operational work the workflow needs before it runs
#: reliably somewhere else. They are not a judgement on whether to use it.
RISK_BANDS = [
    (75, "Severe", "Several things here will stop this running or reproducing elsewhere."),
    (50, "High", "Significant operational gaps; expect work before this moves machines."),
    (25, "Elevated", "Some open items to own before this is dependable."),
    (10, "Moderate", "Routine housekeeping."),
    (0, "Low", "Nothing significant found."),
]

STALE_DAYS = 550          # ~18 months without a commit
ABANDONED_DAYS = 900      # ~2.5 years

# Model families whose use raises likeness/consent questions independent of
# licence, which matters for anything featuring a real performer.
IDENTITY_MODEL_RE = re.compile(
    r"insightface|antelope|buffalo_|inswapper|instantid|faceid|reactor|roop|facefusion|"
    r"photomaker|pulid|arcface|facerestore",
    re.IGNORECASE,
)


@dataclass
class RiskScore:
    score: int = 0
    band: str = ""
    band_detail: str = ""
    findings: list[Finding] = field(default_factory=list)
    by_category: dict[str, float] = field(default_factory=dict)
    #: A factual sentence about the licence composition. Not a verdict: the
    #: report states the terms and the reader decides what they mean.
    licence_note: str = ""

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for finding in self.findings:
            out[finding.severity] = out.get(finding.severity, 0) + 1
        return out


def assess(wf: Workflow, *, models: list[ModelRef], packs: list[PackRef],
           prompts: list[PromptRef], assets: list[AssetRef], outputs: list[AssetRef],
           api_node_types: list[str], missing_models: list[ModelRef] | None = None,
           models_dir_checked: bool = False) -> RiskScore:
    result = RiskScore()
    findings = result.findings

    enabled_models = [m for m in models if m.enabled]

    findings.extend(_licence_findings(enabled_models, api_node_types))
    findings.extend(_provenance_findings(enabled_models))
    findings.extend(_dependency_findings(packs, wf))
    findings.extend(_reproducibility_findings(wf, packs, prompts, api_node_types))
    findings.extend(_runtime_findings(wf, assets, outputs, api_node_types,
                                      missing_models or [], models_dir_checked))
    findings.extend(_data_findings(enabled_models, api_node_types))

    for finding in findings:
        finding.score = SEVERITY_WEIGHT.get(finding.severity, 0.0)
        result.by_category[finding.category] = round(
            result.by_category.get(finding.category, 0.0) + finding.score, 1
        )

    total = sum(f.score for f in findings)
    result.score = int(min(100, round(total)))
    for threshold, band, detail in RISK_BANDS:
        if result.score >= threshold:
            result.band, result.band_detail = band, detail
            break

    findings.sort(key=lambda f: (f.rank, -f.score, f.title))
    return result


# --------------------------------------------------------------------------
# Licensing
# --------------------------------------------------------------------------


def _licence_findings(models: list[ModelRef], api_node_types: list[str]) -> list[Finding]:
    """Licence *facts* live in the licence summary, not here.

    Whether a non-commercial model is a problem depends on the job, the client
    and the facility's own agreements - none of which a workflow file knows - so
    the report states the terms and leaves the call to the reader. What belongs
    in a risk score is only what is a problem regardless of anyone's policy: not
    knowing what a file actually is, and code whose licence reaches your own.
    """
    out: list[Finding] = []

    conflicted = [m for m in models if m.license
                  and any("Sources disagree" in r for r in m.license.restrictions)]
    if conflicted:
        out.append(Finding(
            id="provenance.conflict",
            title=f"{len(conflicted)} model(s) are described differently by different sources",
            severity="high",
            category="provenance",
            detail="The filename, the upstream licence tag and the base model do not "
                   "agree about what this file is. That is a fact about the file rather "
                   "than a licence question: it usually means the weight was renamed "
                   "after it was downloaded, so its name no longer says what it is.",
            evidence=[f"{m.filename}: " + next(r for r in m.license.restrictions
                                               if "Sources disagree" in r)[:220]
                      for m in conflicted],
            recommendation="Hash each file and look it up by hash rather than by name "
                           "(--models-dir with --online), then record what it is.",
        ))

    return out


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def _provenance_findings(models: list[ModelRef]) -> list[Finding]:
    out: list[Finding] = []

    unsourced = [m for m in models
                 if not m.provenance or m.provenance.source == "unknown"]
    if unsourced:
        out.append(Finding(
            id="provenance.unsourced",
            title=f"{len(unsourced)} model(s) could not be traced to a source",
            severity="medium",
            category="provenance",
            detail="The filename does not appear in the bundled model index and no "
                   "upstream repository was resolved. A weight nobody can point at is a "
                   "weight nobody can re-download, re-verify, or clear.",
            evidence=[f"{m.filename} ({m.role})" for m in unsourced[:12]],
            recommendation="Record the download URL and SHA-256 for each of these in the "
                           "show's asset register. Run with --models-dir to hash the local "
                           "copies and --online to match them against Civitai.",
        ))

    gated = [m for m in models if m.provenance and m.provenance.gated]
    if gated:
        out.append(Finding(
            id="provenance.gated",
            title=f"{len(gated)} model(s) live behind a gated repository",
            severity="medium",
            category="provenance",
            detail="Gated repositories require an accepted licence agreement and an "
                   "authenticated token. An unattended render node without that token "
                   "fails at download time, not at review time.",
            evidence=[f"{m.filename} - {m.provenance.url}" for m in gated],
            recommendation="Mirror these weights to internal storage once, with the accepted "
                           "terms recorded alongside them.",
        ))

    remote = [m for m in models if m.repo_id]
    if remote:
        out.append(Finding(
            id="provenance.auto-download",
            title=f"{len(remote)} model(s) are fetched from a remote repo at run time",
            severity="medium",
            category="provenance",
            detail="The node names a repository rather than a local file, so the weights "
                   "are downloaded on first use. Whatever is at that address on the day is "
                   "what you get, and it can be updated or withdrawn without warning.",
            evidence=[f"{m.repo_id} (via {m.node_type})" for m in remote],
            recommendation="Pin these to a local mirror with a recorded revision hash.",
        ))

    return out


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def _dependency_findings(packs: list[PackRef], wf: Workflow) -> list[Finding]:
    out: list[Finding] = []

    unidentified = [p for p in packs if not p.identified]
    if unidentified:
        out.append(Finding(
            id="dependency.unidentified",
            title=f"{len(unidentified)} node type(s) cannot be matched to any known pack",
            severity="critical",
            category="dependency",
            detail="These classes are not in the ComfyUI-Manager registry index. On any "
                   "machine that does not already have them, the workflow fails to load "
                   "with a red missing-node error and there is no automatic way to install them.",
            evidence=[p.title for p in unidentified[:12]],
            recommendation="Get the source repository from whoever built the workflow and "
                           "vendor it into the studio's ComfyUI image. If nobody knows where "
                           "it came from, that node needs replacing.",
        ))

    identified = [p for p in packs if p.identified]
    unpinned = [p for p in identified if not p.pinned_version]
    if unpinned:
        out.append(Finding(
            id="dependency.unpinned",
            title=f"{len(unpinned)} custom node pack(s) have no version recorded",
            severity="high",
            category="dependency",
            detail="Without a version, 'install the custom nodes' means 'install whatever "
                   "is on main today'. Node authors rename inputs and change defaults "
                   "between commits, which silently changes the image or breaks the graph.",
            evidence=[p.title for p in unpinned[:12]],
            recommendation="Freeze each pack at a known-good commit in the studio image and "
                           "record it with the show, the same way you would a DCC plugin version.",
        ))

    nightly = [p for p in identified if p.pinned_version == "nightly"]
    if nightly:
        out.append(Finding(
            id="dependency.nightly",
            title=f"{len(nightly)} pack(s) are pinned to 'nightly'",
            severity="high",
            category="dependency",
            detail="A nightly pin is a moving target: it names the tip of a branch, not a "
                   "release, so two machines installing 'the same' version get different code.",
            evidence=[p.title for p in nightly],
            recommendation="Replace nightly pins with a released version before locking the show.",
        ))

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    stale, abandoned = [], []
    for pack in identified:
        age = _age_days(pack.last_update, now)
        if age is None:
            continue
        if age >= ABANDONED_DAYS:
            abandoned.append((pack, age))
        elif age >= STALE_DAYS:
            stale.append((pack, age))

    if abandoned:
        out.append(Finding(
            id="dependency.abandoned",
            title=f"{len(abandoned)} pack(s) look abandoned",
            severity="high",
            category="dependency",
            detail="No commits for over two and a half years. When ComfyUI's core API "
                   "changes - and it does, often - nobody is going to fix these, and the "
                   "fix becomes the studio's problem mid-show.",
            evidence=[f"{p.title} - last commit {p.last_update} ({age // 365} years ago)"
                      for p, age in abandoned[:10]],
            recommendation="Plan a replacement now, or accept that the studio owns maintenance "
                           "of a fork.",
        ))
    if stale:
        out.append(Finding(
            id="dependency.stale",
            title=f"{len(stale)} pack(s) have not been updated in over 18 months",
            severity="medium",
            category="dependency",
            detail="Slow-moving packs are not automatically bad, but they are the ones that "
                   "break first on a ComfyUI upgrade.",
            evidence=[f"{p.title} - last commit {p.last_update}" for p, age in stale[:10]],
            recommendation="Test these explicitly against the ComfyUI version you intend to ship.",
        ))

    low_traction = [p for p in identified
                    if isinstance(p.stars, int) and p.stars < 25]
    if low_traction:
        out.append(Finding(
            id="dependency.low-traction",
            title=f"{len(low_traction)} pack(s) have very little community traction",
            severity="medium",
            category="dependency",
            detail="Custom nodes run with the full privileges of the ComfyUI process: they "
                   "execute arbitrary Python, can reach the network and can read anything "
                   "the render user can. A pack almost nobody uses has had almost nobody "
                   "review that code.",
            evidence=[f"{p.title} ({p.stars} stars, {p.author or 'unknown author'})"
                      for p in low_traction[:10]],
            recommendation="Read the source of these before they go on a machine with client "
                           "material on it, or sandbox the render user's access.",
        ))

    collisions = [p for p in identified if p.collisions]
    if collisions:
        out.append(Finding(
            id="dependency.collision",
            title=f"{len(collisions)} pack(s) export node names claimed by other packs",
            severity="medium",
            category="dependency",
            detail="When two installed packs export the same class name, ComfyUI keeps "
                   "whichever loaded last. The workflow then silently runs a different "
                   "node's code depending on install order.",
            evidence=[f"{p.title}: {', '.join(p.node_types[:3])} also in {p.collisions[0]}"
                      for p in collisions[:8]],
            recommendation="Verify which implementation is actually bound on the render image.",
        ))

    with_deps = [p for p in identified if p.pip or p.apt]
    if with_deps:
        out.append(Finding(
            id="dependency.system",
            title=f"{len(with_deps)} pack(s) pull in extra Python or system packages",
            severity="medium",
            category="dependency",
            detail="Custom node packs install their own dependencies into the shared "
                   "environment. Conflicting pins between packs are the most common cause "
                   "of a ComfyUI install that worked yesterday and does not today.",
            evidence=[f"{p.title}: {', '.join((p.pip + p.apt)[:4])}" for p in with_deps[:8]],
            recommendation="Build the environment once from a locked requirements file rather "
                           "than letting each pack resolve its own dependencies.",
        ))

    copyleft = [p for p in identified if p.licence in COPYLEFT_SPDX]
    if copyleft:
        out.append(Finding(
            id="dependency.copyleft",
            title=f"{len(copyleft)} custom node pack(s) are under a strong copyleft licence",
            severity="high",
            category="licensing",
            detail="A node pack is not like a model: its code is imported into the "
                   "ComfyUI process and called directly by whatever the studio builds "
                   "around it. GPL and especially AGPL terms can therefore reach the "
                   "pipeline tooling itself, and AGPL's network clause treats serving "
                   "the result to users as distribution.",
            evidence=[f"{p.title} - {p.licence} ({p.reference})" for p in copyleft],
            recommendation="Worth knowing before it goes in a shared image, because "
                           "these terms can attach to code that imports the pack rather "
                           "than staying with the pack itself. What that means for your "
                           "situation is a question for whoever owns open-source policy.",
        ))

    weak = [p for p in identified if p.licence in WEAK_COPYLEFT_SPDX]
    if weak:
        out.append(Finding(
            id="dependency.weak-copyleft",
            title=f"{len(weak)} pack(s) carry file-level copyleft obligations",
            severity="low",
            category="licensing",
            detail="LGPL, MPL and EPL obligations attach to the pack's own files rather "
                   "than to your code, so they are usually satisfiable, but modifications "
                   "to the pack itself have to be published.",
            evidence=[f"{p.title} - {p.licence}" for p in weak],
            recommendation="Record these in the third-party licence list, and publish any "
                           "local patches to the pack.",
        ))

    resolved_any = any(p.licence for p in identified)
    unlicensed = [p for p in identified if not p.licence]
    if resolved_any and unlicensed:
        out.append(Finding(
            id="dependency.no-licence",
            title=f"{len(unlicensed)} pack(s) publish no licence at all",
            severity="medium",
            category="licensing",
            detail="With no licence file, the default is all rights reserved: the author "
                   "has granted nothing, not even the right to use it. Widespread "
                   "practice is not permission, and this is the case a client's legal "
                   "team is most likely to ask about.",
            evidence=[f"{p.title} ({p.reference})" for p in unlicensed[:10]],
            recommendation="Asking the author to add one usually works. Until then the "
                           "terms of use are simply unstated.",
        ))

    if len(identified) >= 8:
        out.append(Finding(
            id="dependency.surface",
            title=f"{len(identified)} custom node packs is a large dependency surface",
            severity="medium",
            category="dependency",
            detail="Every pack is an independent upstream that can break, change behaviour "
                   "or disappear. The chance that all of them survive a ComfyUI upgrade "
                   "intact falls quickly with count.",
            evidence=[p.title for p in identified[:12]],
            recommendation="Consider consolidating: several of these usually provide one or "
                           "two nodes that core or a single maintained pack can cover.",
        ))

    return out


# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------


def _reproducibility_findings(wf: Workflow, packs: list[PackRef],
                              prompts: list[PromptRef], api_node_types: list[str]) -> list[Finding]:
    out: list[Finding] = []

    if api_node_types:
        out.append(Finding(
            id="repro.api-nondeterminism",
            title="Hosted API nodes make the workflow non-reproducible",
            severity="high",
            category="reproducibility",
            detail="The vendor controls the model behind the endpoint and can update or "
                   "retire it. The same graph, seed and prompt will not necessarily produce "
                   "the same frame next month, and there is no local copy to fall back on.",
            evidence=sorted(api_node_types)[:10],
            recommendation="Cache and version every API result as a rendered asset. Never "
                           "rely on being able to re-run the shot to recover a frame.",
        ))

    randomised = [n for n in wf.active()
                  if isinstance(n.widgets.get("control_after_generate"), str)
                  and n.widgets["control_after_generate"].lower() != "fixed"]
    if randomised:
        out.append(Finding(
            id="repro.seed-drift",
            title=f"{len(randomised)} sampler seed(s) change on every run",
            severity="medium",
            category="reproducibility",
            detail="control_after_generate advances the seed after each generation. That is "
                   "what you want for exploration and for unattended batches, but it means "
                   "the seed in the saved file is not the seed that made the approved frame.",
            evidence=[f"{n.label}: seed={n.widgets.get('seed', n.widgets.get('noise_seed'))}, "
                      f"mode={n.widgets['control_after_generate']}" for n in randomised[:8]],
            recommendation="Capture the seed from each render's metadata into the shot record. "
                           "Set it back to 'fixed' once a look is approved.",
        ))

    dynamic = [p for p in prompts if p.dynamic_syntax or p.wildcards]
    if dynamic:
        out.append(Finding(
            id="repro.dynamic-prompts",
            title=f"{len(dynamic)} prompt(s) resolve differently on each run",
            severity="medium",
            category="reproducibility",
            detail="Wildcard and {a|b} syntax is expanded at run time, so the prompt recorded "
                   "in the workflow is not the prompt that was used. Wildcards also depend on "
                   "text files on disk that are not part of the workflow.",
            evidence=[f"{p.node_label}: "
                      + (", ".join(p.wildcards) if p.wildcards else "{a|b} alternation")
                      for p in dynamic[:8]],
            recommendation="Log the resolved prompt with each output, and version the wildcard "
                           "files alongside the workflow.",
        ))

    if wf.source_format == "ui" and not any(
        p.pinned_version for p in packs if p.identified
    ) and packs:
        out.append(Finding(
            id="repro.no-version-metadata",
            title="The workflow carries no version metadata for its dependencies",
            severity="medium",
            category="reproducibility",
            detail="Recent ComfyUI frontends stamp each node with the pack id and version it "
                   "was created with. None is present here, which usually means the file was "
                   "saved by an older frontend or hand-edited.",
            evidence=[f"format: {wf.source_format}",
                      f"frontend: {wf.extra.get('frontend') or 'not recorded'}"],
            recommendation="Re-save the workflow from a current ComfyUI so the version stamps "
                           "are written, then archive that copy as the show reference.",
        ))

    unaligned = [n for n in wf.nodes.values() if n.unaligned]
    if unaligned:
        out.append(Finding(
            id="repro.audit-confidence",
            title=f"{len(unaligned)} node(s) could not be read with full confidence",
            severity="info",
            category="reproducibility",
            detail="These nodes store more or fewer widget values than their known schema, "
                   "usually because their inputs are built dynamically at run time. Values "
                   "read from them were aligned positionally and may be mislabelled.",
            evidence=sorted({f"{n.type}" for n in unaligned})[:10],
            recommendation="Confirm these by eye in the ComfyUI graph if any of them carry "
                           "licence-relevant model choices.",
        ))

    return out


# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------


def _runtime_findings(wf: Workflow, assets: list[AssetRef], outputs: list[AssetRef],
                      api_node_types: list[str], missing_models: list[ModelRef],
                      models_dir_checked: bool) -> list[Finding]:
    out: list[Finding] = []

    if missing_models:
        out.append(Finding(
            id="runtime.missing-models",
            title=f"{len(missing_models)} referenced model(s) are not present locally",
            severity="critical",
            category="runtime",
            detail="These filenames do not exist under the models directory that was "
                   "scanned, so the workflow cannot run on this machine as saved.",
            evidence=[f"{m.filename} (expected in models/{m.folder})" for m in missing_models[:12]],
            recommendation="Fetch them before queueing, and add them to the show's model "
                           "manifest so the farm image carries them too.",
        ))

    abs_paths = [a for a in assets if a.absolute_path] + [o for o in outputs if o.absolute_path]
    if abs_paths:
        out.append(Finding(
            id="runtime.absolute-paths",
            title=f"{len(abs_paths)} absolute path(s) hard-coded in the graph",
            severity="high",
            category="runtime",
            detail="Absolute paths tie the workflow to one machine's disk layout. On a farm "
                   "node or another artist's workstation these resolve to nothing.",
            evidence=[f"{a.node_label}.{a.widget or 'output'} = {a.value}" for a in abs_paths[:8]],
            recommendation="Move these behind ComfyUI's input/output directories or a mapped "
                           "show root that exists identically everywhere.",
        ))

    urls = [a for a in assets if a.kind == "url"]
    if urls:
        out.append(Finding(
            id="runtime.remote-fetch",
            title=f"{len(urls)} asset(s) are fetched from the network at run time",
            severity="medium",
            category="runtime",
            detail="A render that depends on a live URL fails when the host is down, the "
                   "content changes, or the farm has no outbound access - which most "
                   "secure facilities do not.",
            evidence=[a.value for a in urls[:8]],
            recommendation="Localise these assets into the show tree.",
        ))

    disabled = [n for n in wf.nodes.values() if n.mode in (MODE_NEVER, MODE_BYPASS)]
    if disabled:
        severity = "medium" if len(disabled) > 5 else "low"
        out.append(Finding(
            id="runtime.disabled-nodes",
            title=f"{len(disabled)} node(s) are muted or bypassed",
            severity=severity,
            category="runtime",
            detail="Disabled branches are dead weight that still has to be understood by "
                   "the next person, and they are a common source of 'it worked for me' - "
                   "the graph that ran is not the graph in the file.",
            evidence=[f"{n.label} ({n.mode_name})" for n in disabled[:10]],
            recommendation="Delete the dead branches before locking, or move them into a "
                           "clearly labelled group so their state is obvious.",
        ))

    if not outputs:
        out.append(Finding(
            id="runtime.no-output",
            title="The workflow has no node that writes a file",
            severity="high",
            category="runtime",
            detail="Nothing in this graph saves its result to disk, so a queued run produces "
                   "nothing durable.",
            evidence=[],
            recommendation="Add a Save node before using this in any batch context.",
        ))

    if not models_dir_checked:
        out.append(Finding(
            id="runtime.not-verified-locally",
            title="Model availability was not verified against a real install",
            severity="info",
            category="runtime",
            detail="The audit read the workflow only. Whether these weights exist on the "
                   "machine that has to render, and whether their contents match what the "
                   "names claim, was not checked.",
            evidence=[],
            recommendation="Re-run with --models-dir pointing at the ComfyUI models folder to "
                           "confirm presence and hash every weight.",
        ))

    return out


# --------------------------------------------------------------------------
# Data handling
# --------------------------------------------------------------------------


def _data_findings(models: list[ModelRef], api_node_types: list[str]) -> list[Finding]:
    out: list[Finding] = []

    if api_node_types:
        out.append(Finding(
            id="data.egress",
            title="Client material is sent to third-party servers",
            severity="high",
            category="data",
            detail="Every hosted API node uploads its inputs - plates, reference, prompts - "
                   "to an external vendor. On most client contracts and NDAs that is a "
                   "disclosure that needs explicit permission, and some vendors reserve the "
                   "right to train on submitted content.",
            evidence=sorted(api_node_types)[:10],
            recommendation="Check the vendor's data-retention and training terms against the "
                           "show's NDA before any real footage goes through this.",
        ))

    identity = [m for m in models if IDENTITY_MODEL_RE.search(m.filename)
                or IDENTITY_MODEL_RE.search(m.node_type)]
    if identity:
        out.append(Finding(
            id="data.likeness",
            title=f"{len(identity)} face/identity model(s) in use",
            severity="medium",
            category="data",
            detail="Face analysis, swap and identity-transfer models raise performer consent "
                   "and likeness questions that sit outside the model licence, and in some "
                   "jurisdictions biometric-processing rules apply as well.",
            evidence=sorted({m.filename for m in identity})[:8],
            recommendation="Confirm written performer consent covers synthetic likeness use, "
                           "and check the relevant union or guild terms.",
        ))

    return out


# --------------------------------------------------------------------------


def _age_days(timestamp: str, now: _dt.datetime) -> int | None:
    if not timestamp:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            parsed = _dt.datetime.strptime(timestamp, fmt).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
        return max(0, (now - parsed).days)
    return None
