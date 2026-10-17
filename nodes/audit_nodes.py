"""ComfyUI nodes.

The headline node audits the very graph it is sitting in: ComfyUI hands any node
that asks for them the running prompt and the UI workflow through hidden inputs,
so dropping the node in and hitting Run produces an audit of that workflow with
nothing to configure.

An ``AUDIT`` object is passed between nodes rather than a JSON string, so the
report is parsed once and every downstream node sees the same thing.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from ..agent import reviewer as reviewer_mod
from ..core import audit as audit_mod
from ..core import graph as graph_mod
from ..core import history as H
from ..core.report import html as html_report
from ..core.report import markdown as md_report
from ..core.report import review as review_section
from ..core.resolve.http import Credentials, HttpClient
from ..core.resolve.resolver import ALL_SOURCES, Resolver
from ..core.score import clearance as clearance_mod
from ..server import live, settings as settings_mod

SEVERITIES = ["critical", "high", "medium", "low", "info"]
SOURCES = ["this workflow (UI graph)", "the running prompt (API format)", "a file on disk"]


def _output_dir() -> str:
    try:
        import folder_paths
        return folder_paths.get_output_directory()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "comfyaudit")


def _local_model_lister():
    """A callable the agent can use to see what is installed, or None."""
    index = live.live_model_index()
    if not index.available:
        return None

    def lister(folder: str) -> list[str]:
        names = []
        for entries in index.by_name.values():
            for entry in entries:
                if not folder or entry.folder == folder:
                    names.append(os.path.basename(entry.path))
                    break
        return names

    return lister


def parse_sources(value: str) -> tuple[str, ...]:
    """Read a comma-separated source list, falling back to all of them."""
    wanted = tuple(s.strip().lower() for s in (value or "").split(",") if s.strip())
    valid = tuple(s for s in wanted if s in ALL_SOURCES)
    return valid or ALL_SOURCES


def run_audit(workflow_doc: dict[str, Any], *, online: bool = False,
              check_local_models: bool = True, licences_path: str = "",
              sources: str = "", hash_models: bool = False,
              profile: clearance_mod.StudioProfile | None = None,
              registry_path: str = "",
              use_settings_profile: bool = True,
              name: str = "",
              ) -> audit_mod.AuditReport:
    """Audit a workflow document using whatever the live ComfyUI can tell us.

    The studio profile, when there is one, comes from ComfyUI's settings - it
    describes the facility, so it is the same for every workflow on the machine.
    A profile passed in explicitly overrides that, which is how a node on the
    canvas states a per-show exception. Neither is required.
    """
    live.install()
    wf = graph_mod.from_dict(workflow_doc)
    if name.strip():
        wf.source_name = name.strip()

    if profile is None and use_settings_profile:
        profile = settings_mod.studio_profile()
    if not registry_path and use_settings_profile:
        registry_path = settings_mod.registry_path()

    options = audit_mod.AuditOptions(
        online=online,
        sources=parse_sources(sources),
        licences_path=licences_path.strip(),
        profile=profile,
        registry_path=registry_path,
        # Hashing multi-gigabyte weights mid-queue is rude, so it is opt-in -
        # but it is also the only way to identify a renamed checkpoint.
        hash_models=hash_models,
    )
    if hash_models and check_local_models:
        index = live.live_model_index()
        if index.available:
            options.models_dir = index.root

    report = audit_mod.run_workflow(wf, options)

    # Both of these replace an inference with an observation, so the risk rules
    # have to run again afterwards against the corrected facts.
    resolved_packs = _apply_installed_packs(report)
    checked = _apply_local_models(report) if check_local_models else False
    if resolved_packs or checked:
        _recompute_risk(report, models_dir_checked=checked)

    report.diagnostics["environment"] = live.environment()
    return report


def _apply_installed_packs(report: audit_mod.AuditReport) -> bool:
    """Resolve node packs from the install, not just the public registry.

    A pack the ComfyUI-Manager index has never heard of - an in-house one, a
    fork, anything vendored into the studio image - is reported offline as an
    unidentified node, which is the correct answer when all you have is a JSON
    file. Running inside ComfyUI we can see it installed and name the directory
    and version it came from, which is a very different finding.
    """
    installed = live.installed_packs()
    if not installed:
        return False

    changed = False
    for pack in report.packs:
        hits = [installed[t] for t in pack.node_types if t in installed]
        if not hits:
            continue
        entry = hits[0]
        if not pack.identified:
            pack.identified = True
            pack.title = pack.title or entry["directory"]
            pack.notes.append(
                f"not in the public registry, but installed here as "
                f"custom_nodes/{entry['directory']}"
                + (f" ({entry['version']})" if entry["version"] else "")
            )
            changed = True
        if entry["version"] and not pack.pinned_version:
            pack.pinned_version = entry["version"]
            pack.notes.append("version read from the installed copy, not from the "
                              "workflow - saving the workflow again will record it")
            changed = True
    return changed


def _apply_local_models(report: audit_mod.AuditReport) -> bool:
    """Re-check model presence against the folders ComfyUI actually reads."""
    index = live.live_model_index()
    if not index.available:
        return False

    missing = []
    for model in report.models:
        if model.folder == "hosted-api":
            continue
        found = index.find(model.filename, model.folder)
        if found is None:
            missing.append(model)
            model.notes.append("not present in any ComfyUI model folder on this machine")
        else:
            model.notes.append(f"installed: {found.path}")

    report.missing_models = missing
    report.diagnostics["models_scanned_locally"] = index.scanned
    report.diagnostics["models_dir"] = index.root
    return True


def _recompute_risk(report: audit_mod.AuditReport, *, models_dir_checked: bool) -> None:
    from ..core.score import licensing as licensing_mod
    from ..core.score import risk as risk_mod

    report.licensing = licensing_mod.summarise(report.models, report.api_node_types)
    report.risk = risk_mod.assess(
        _RiskGraphStub(report), models=report.models, packs=report.packs,
        prompts=report.prompts, assets=report.inputs, outputs=report.outputs,
        api_node_types=report.api_node_types, missing_models=report.missing_models,
        models_dir_checked=models_dir_checked,
    )


class _RiskGraphStub:
    """Just enough of a Workflow for the risk rules, without re-parsing."""

    def __init__(self, report: audit_mod.AuditReport) -> None:
        self._report = report
        self.source_format = report.source.get("format", "ui")
        self.extra = {"frontend": report.source.get("frontend_version", "")}
        self.nodes: dict[str, Any] = {}

    def active(self) -> list[Any]:
        return []


# --------------------------------------------------------------------------



# The profile widgets read as English on the canvas; the engine wants keys.
TERRITORY_CHOICES_MAP = {
    "not set": "",
    "United States": "US",
    "European Union": "EU",
    "United Kingdom": "GB",
    "South Korea": "KR",
    "Canada": "CA",
    "Australia": "AU",
    "Japan": "JP",
    "China": "CN",
    "India": "IN",
    "elsewhere": "OTHER",
}
TERRITORY_CHOICES = list(TERRITORY_CHOICES_MAP)

REVENUE_CHOICES_MAP = {
    "not set": "unknown",
    "under $1M": "under-1m",
    "$1M - $10M": "1m-10m",
    "$10M - $20M": "10m-20m",
    "$20M - $100M": "20m-100m",
    "over $100M": "over-100m",
}
REVENUE_CHOICES = list(REVENUE_CHOICES_MAP)

SHIP_CHOICES_MAP = {
    "not set": "unknown",
    "finished frames to a client": "deliverable-only",
    "nothing leaves the building": "internal-only",
    "software containing this workflow": "software",
    "a network service": "service",
}
SHIP_CHOICES = list(SHIP_CHOICES_MAP)


class ComfyAuditStudioProfile:
    """The facts about a facility that licence terms actually turn on.

    A licence grants rights to someone, somewhere, doing something. Without
    knowing which someone, "can we use this?" has no answer - so this node is
    what turns a description of the terms into a determination. Set it once and
    keep it in a template; it is a property of the facility, not the workflow.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "territory": (TERRITORY_CHOICES, {"default": TERRITORY_CHOICES[0],
                    "tooltip": "Where the work is rendered and deployed. Several "
                    "open-weight licences exclude regions by name - MiniMax H3 "
                    "excludes the US, EU, UK and South Korea outright - so this "
                    "is often the fact that decides everything."}),
                "annual_revenue": (REVENUE_CHOICES, {"default": REVENUE_CHOICES[0],
                    "tooltip": "Total company revenue, not AI-derived revenue. "
                    "Free use is capped at $1M by Stability, $20M by MiniMax and "
                    "$100M-equivalents elsewhere; above the cap a separate "
                    "agreement is needed."}),
                "what_ships": (SHIP_CHOICES, {"default": SHIP_CHOICES[0],
                    "tooltip": "What leaves the building. Copyleft licences only "
                    "reach your own code when something is distributed, so this "
                    "decides whether an AGPL node pack is a problem or a "
                    "non-issue."}),
            },
            "optional": {
                "outputs_train_models": ("BOOLEAN", {"default": False, "tooltip":
                    "Outputs are used to train other models. Several licences "
                    "forbid this outright and worldwide, with no fee that lifts it."}),
                "real_performers": ("BOOLEAN", {"default": False, "tooltip":
                    "Real people appear in the material. No model licence grants "
                    "rights in a performer's face - that comes from their contract "
                    "and, increasingly, their union agreement."}),
                "studio_name": ("STRING", {"default": "", "tooltip":
                    "A label for the report: a facility, a show or a client."}),
            },
        }

    RETURN_TYPES = ("AUDIT_PROFILE",)
    RETURN_NAMES = ("profile",)
    OUTPUT_TOOLTIPS = ("Feed this into Audit This Workflow to get a determination.",)
    FUNCTION = "build"
    CATEGORY = "Comfy Workflow Tracker"
    DESCRIPTION = ("Describe the facility, so the audit can work out what the "
                   "licences mean for it. Without this the report lists the terms "
                   "but reaches no verdict, because a verdict against an unknown "
                   "studio would be a guess.")

    def build(self, territory, annual_revenue, what_ships,
              outputs_train_models=False, real_performers=False, studio_name=""):
        return (clearance_mod.StudioProfile(
            territory=TERRITORY_CHOICES_MAP[territory],
            revenue_band=REVENUE_CHOICES_MAP[annual_revenue],
            ships=SHIP_CHOICES_MAP[what_ships],
            trains_models=bool(outputs_train_models),
            likeness_involved=bool(real_performers),
            label=studio_name.strip(),
        ),)


class ComfyAuditWorkflow:
    """Audit the workflow this node is running inside."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source": (SOURCES, {"default": SOURCES[0]}),
                "check_local_models": ("BOOLEAN", {"default": True, "tooltip":
                    "Verify every referenced weight exists in a ComfyUI model folder."}),
                "online_lookups": ("BOOLEAN", {"default": False, "tooltip":
                    "Resolve licences and provenance from HuggingFace, Civitai, "
                    "GitHub and the Comfy Registry. Needs outbound network access. "
                    "Results are cached on disk for a week."}),
                "hash_models": ("BOOLEAN", {"default": False, "tooltip":
                    "SHA-256 every local weight so Civitai can identify it exactly. "
                    "This is the only way to catch a renamed checkpoint, but the "
                    "first run reads every model file from disk."}),
            },
            "optional": {
                "sources": ("STRING", {"default": "", "tooltip":
                    "Limit which services are contacted, comma separated: "
                    "huggingface, civitai, github, comfy-registry. Empty means all. "
                    "Use this when model names must not leave for a given service."}),
                "workflow_path": ("STRING", {"default": "", "tooltip":
                    "Only used when source is 'a file on disk'. Accepts a workflow "
                    ".json or a PNG that ComfyUI rendered."}),
                "licence_overrides": ("STRING", {"default": "", "tooltip":
                    "Path to a studio licence file that extends the bundled one."}),
                "profile": ("AUDIT_PROFILE", {"tooltip":
                    "Optional. Overrides the studio profile set in ComfyUI's "
                    "settings (Settings -> Comfy Workflow Tracker), for a show whose "
                    "circumstances differ from the facility's. With no profile "
                    "in either place the report describes the licence terms and "
                    "reaches no verdict, which is the default."}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("AUDIT", "STRING", "STRING", "INT", "INT", "STRING")
    RETURN_NAMES = ("audit", "report_markdown", "report_json",
                    "risk_score", "automation_index", "licence_summary")
    OUTPUT_TOOLTIPS = (
        "The audit, for the other Comfy Workflow Tracker nodes.",
        "The full report as Markdown.",
        "The full report as JSON.",
        "Operational risk, 0-100. How much stands between this and running "
        "reliably somewhere else.",
        "Automation index, 0-100. Higher means less human intervention.",
        "A one-line description of the licence composition.",
    )
    FUNCTION = "run"
    CATEGORY = "Comfy Workflow Tracker"
    OUTPUT_NODE = True
    DESCRIPTION = ("Document this workflow: every model with its licence terms and "
                   "where they came from, the prompts, the assets, the custom node "
                   "dependencies, how much human intervention it needs, and what "
                   "would stop it running elsewhere. Reports; does not judge.")

    @classmethod
    def IS_CHANGED(cls, prompt=None, extra_pnginfo=None, **kwargs):
        """Re-audit when the graph changes, and only then.

        The graph can change without any widget on this node changing, so the
        default caching would report a stale verdict. Hashing the workflow
        itself fixes that without making the node re-run on every unrelated
        queue - which matters because a Claude review downstream costs money
        each time it fires.
        """
        subject = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
        if subject is None:
            subject = prompt
        payload = json.dumps({"inputs": kwargs, "workflow": subject},
                             sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def run(self, source, check_local_models, online_lookups, hash_models=False,
            sources="", workflow_path="", licence_overrides="", profile=None,
            prompt=None, extra_pnginfo=None):
        doc = self._resolve(source, workflow_path, prompt, extra_pnginfo)
        report = run_audit(doc, online=online_lookups,
                           check_local_models=check_local_models,
                           licences_path=licence_overrides,
                           sources=sources, hash_models=hash_models,
                           profile=profile)

        markdown = md_report.render(report)
        payload = json.dumps(report.to_dict(), indent=2)
        summary = _console_summary(report)

        return {
            "ui": {"text": [summary]},
            "result": (report, markdown, payload, report.risk.score,
                       report.automation.index, report.licensing.headline),
        }

    def _resolve(self, source, workflow_path, prompt, extra_pnginfo) -> dict[str, Any]:
        if source == SOURCES[2]:
            path = (workflow_path or "").strip()
            if not path:
                raise ValueError("Set workflow_path when auditing a file on disk.")
            if not os.path.isfile(path):
                raise ValueError(f"No such workflow file: {path}")
            return graph_mod.load(path).raw

        if source == SOURCES[0]:
            # The UI graph carries node titles, groups, mute/bypass state and the
            # pack version stamps - all of which the API prompt has thrown away.
            if isinstance(extra_pnginfo, dict) and isinstance(extra_pnginfo.get("workflow"), dict):
                return extra_pnginfo["workflow"]
            if isinstance(prompt, dict):
                return prompt
            raise ValueError(
                "ComfyUI did not pass the UI workflow to this node. This happens "
                "when workflow metadata is disabled (--disable-metadata); choose "
                "'the running prompt (API format)' instead."
            )

        if isinstance(prompt, dict):
            return prompt
        raise ValueError("ComfyUI did not pass the running prompt to this node.")


class ComfyAuditClaudeReview:
    """The judgement calls the rule engine cannot make."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audit": ("AUDIT",),
                "mode": (reviewer_mod.MODES, {"default": "full", "tooltip":
                    "full: all three. identify: name the unknown models. "
                    "clearance: review prompt text for naming risk. "
                    "remediate: propose replacements and an ordered plan. "
                    "narrative: write the go/no-go brief a producer reads, "
                    "explaining the determination in plain prose (needs a "
                    "Studio Profile to have something to explain)."}),
                "model": (reviewer_mod.MODELS, {"default": reviewer_mod.DEFAULT_MODEL}),
                "effort": (reviewer_mod.EFFORTS, {"default": "high", "tooltip":
                    "How hard the model works. Lower is cheaper and faster."}),
                "web_search": ("BOOLEAN", {"default": True, "tooltip":
                    "Let Claude look models up on the web to check a licence. "
                    "Turn this off if the workflow content is confidential."}),
                "model_lookups": ("BOOLEAN", {"default": True, "tooltip":
                    "Give Claude direct HuggingFace, Civitai and GitHub lookups, so "
                    "it checks a licence at source instead of recalling it."}),
            },
            "optional": {
                "question": ("STRING", {"multiline": True, "default": "", "tooltip":
                    "Ask something specific about this workflow instead of running "
                    "the selected mode."}),
                "api_key": ("STRING", {"default": "", "tooltip":
                    "Leave empty to use ANTHROPIC_API_KEY or an 'ant auth login' "
                    "profile."}),
            },
        }

    RETURN_TYPES = ("AUDIT", "STRING", "STRING")
    RETURN_NAMES = ("audit", "review_markdown", "review_json")
    FUNCTION = "run"
    CATEGORY = "Comfy Workflow Tracker"
    OUTPUT_NODE = True
    DESCRIPTION = ("Have Claude investigate the audit: identify models the rules "
                   "could not, review prompts for trademark and likeness risk, and "
                   "propose commercially clear replacements. Sends workflow "
                   "content to the Anthropic API.")

    def run(self, audit, mode, model, effort, web_search, model_lookups=True,
            question="", api_key=""):
        resolver = None
        if model_lookups:
            resolver = Resolver(http=HttpClient(),
                                credentials=Credentials.from_environment(),
                                enabled=True)
        result = reviewer_mod.review(
            audit, mode=mode, model=model, effort=effort,
            api_key=api_key.strip(), web_search=web_search,
            question=question, local_models=_local_model_lister(),
            resolver=resolver,
        )
        reviewer_mod.apply_to_report(audit, result)

        markdown = render_review(result)
        audit.diagnostics["claude_review"] = result.as_dict()
        return {
            "ui": {"text": [result.summary or result.error or "No review produced."]},
            "result": (audit, markdown, json.dumps(result.as_dict(), indent=2)),
        }


class ComfyAuditGate:
    """Stop the queue when the audit finds something serious."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audit": ("AUDIT",),
                "fail_on": (SEVERITIES, {"default": "critical", "tooltip":
                    "Abort the run if any finding is at this severity or worse."}),
                "stop_on_non_commercial": ("BOOLEAN", {"default": False, "tooltip":
                    "Your policy, not the tool's: abort if any model's licence says "
                    "non-commercial. Off by default, because whether that matters "
                    "depends on the job."}),
            },
        }

    RETURN_TYPES = ("AUDIT", "STRING")
    RETURN_NAMES = ("audit", "verdict")
    FUNCTION = "run"
    CATEGORY = "Comfy Workflow Tracker"
    DESCRIPTION = ("Stop the queue on conditions you choose, so a workflow that "
                   "fails your own bar never renders. The thresholds are yours to "
                   "set; nothing is enforced by default beyond critical findings.")

    def run(self, audit, fail_on, stop_on_non_commercial=False):
        threshold = SEVERITIES.index(fail_on)
        tripped = [f for f in audit.risk.findings
                   if f.severity in SEVERITIES
                   and SEVERITIES.index(f.severity) <= threshold]

        if stop_on_non_commercial:
            non_commercial = sorted({m.filename for m in audit.models
                                     if m.enabled and m.license
                                     and m.license.commercial_use == "no"})
            if non_commercial:
                raise RuntimeError(
                    "Comfy Workflow Tracker gate: stop_on_non_commercial is set and these models' "
                    f"licences say non-commercial: {', '.join(non_commercial)}. "
                    "Turn the switch off to render anyway."
                )

        if tripped:
            listed = "; ".join(f"[{f.severity}] {f.title}" for f in tripped[:5])
            raise RuntimeError(
                f"Comfy Workflow Tracker gate: {len(tripped)} finding(s) at or above "
                f"'{fail_on}'. {listed}"
            )

        return (audit, f"passed: nothing at or above '{fail_on}'")


#: Stages in the order a shot moves through them.
HISTORY_STAGES = ["any", "production", "cleared", "delivered"]


class ComfyAuditHistoryGate:
    """Refuse to save anything from a shot whose history cannot be trusted.

    Reads the shot history ComfyUI sends with every run (the UI workflow in
    ``extra_pnginfo``, which carries ``extra.comfyaudit_history``). Placed
    between VAE Decode and Save Image, it runs before anything is written, so
    a run that fails the gate leaves no render behind.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "Passed through unchanged when the gate passes."}),
                "require_history": ("BOOLEAN", {"default": True, "tooltip":
                    "Stop if this workflow carries no shot history at all."}),
                "stop_if_altered": ("BOOLEAN", {"default": True, "tooltip":
                    "Stop if the history fails verification: an entry was changed, "
                    "removed or reordered, or a signature does not match."}),
                "minimum_stage": (HISTORY_STAGES, {"default": "any", "tooltip":
                    "Stop unless the shot has reached this stage. 'production' keeps "
                    "sandbox experiments out of real renders."}),
                "stop_if_not_commercial": ("BOOLEAN", {"default": False, "tooltip":
                    "Your policy, not the tool's: stop while any model in the shot "
                    "bars commercial use."}),
            },
            # Optional, so workflows saved before it existed load unchanged.
            "optional": {
                "require_legal_clearance": ("BOOLEAN", {"default": False, "tooltip":
                    "Stop unless legal has cleared this exact version of the shot in "
                    "Comfy Audit. A change to the graph since the clearance stops it too."}),
            },
            "hidden": {"extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "verdict")
    FUNCTION = "run"
    CATEGORY = "Comfy Workflow Tracker"
    DESCRIPTION = ("Stop the run before anything is saved if this shot's history is "
                   "missing or altered, the shot has not reached the stage you require, "
                   "or (optionally) a model bars commercial use. Put it between VAE "
                   "Decode and Save Image.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # The history changes with every edit, so the gate is judged every run.
        return float("nan")

    def run(self, images, require_history=True, stop_if_altered=True, minimum_stage="any",
            stop_if_not_commercial=False, require_legal_clearance=False, extra_pnginfo=None):
        workflow = (extra_pnginfo or {}).get("workflow") if isinstance(extra_pnginfo, dict) else None
        history = H.read(workflow) if isinstance(workflow, dict) else None
        if not history:
            if require_history:
                raise RuntimeError(
                    "Comfy Workflow Tracker history gate: this workflow carries no shot history, so "
                    "nothing it renders can be traced. Open it with Comfy Workflow Tracker's automatic "
                    "tracking on, or turn require_history off.")
            return (images, "passed: no history, and none required")
        if stop_if_altered:
            v = H.verify(workflow, keys=H.Keys.from_environment(),
                         ledger=os.environ.get("COMFYAUDIT_HISTORY_LEDGER", ""))
            if v.assurance == "none":
                raise RuntimeError(
                    "Comfy Workflow Tracker history gate: this shot's history has been altered: "
                    + "; ".join(v.problems[:3]))
        stage = (history.get("shot") or {}).get("stage", "sandbox")
        order = ["sandbox"] + HISTORY_STAGES[1:]
        if minimum_stage != "any" and order.index(stage) < order.index(minimum_stage):
            raise RuntimeError(
                f"Comfy Workflow Tracker history gate: the shot is in {stage}, and this gate needs "
                f"{minimum_stage} or later. Move the shot on in the Comfy Workflow Tracker panel.")
        if stop_if_not_commercial:
            timeline = H.commercial_timeline(history)
            now = timeline[-1] if timeline else {}
            if now.get("status") == "blocked":
                models = ", ".join(m["filename"] for m in now.get("models", []))
                raise RuntimeError(
                    "Comfy Workflow Tracker history gate: stop_if_not_commercial is set and these "
                    f"models bar commercial use: {models}.")
        if require_legal_clearance:
            legal = H.legal_decision(history)
            if not legal or legal["decision"] != "cleared":
                raise RuntimeError(
                    "Comfy Workflow Tracker history gate: require_legal_clearance is set and legal has "
                    + ("sent this shot back: " + legal["reason"] if legal else "not cleared this shot")
                    + ". Clearance is recorded in Comfy Audit.")
            if not legal["current"]:
                raise RuntimeError(
                    "Comfy Workflow Tracker history gate: legal cleared an earlier version of this shot; "
                    "the graph has changed since. Send it to legal again.")
            if not legal["signed"]:
                raise RuntimeError(
                    "Comfy Workflow Tracker history gate: the legal clearance on this shot is unsigned, "
                    "so it cannot be relied on.")
            return (images, f"passed: history intact, shot in {stage}, cleared by legal ({legal['by']})")
        return (images, f"passed: history intact, shot in {stage}")


class ComfyAuditSaveReport:
    """Write the report next to the renders."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audit": ("AUDIT",),
                "format": (["html", "markdown", "json", "all"], {"default": "html"}),
                "filename_prefix": ("STRING", {"default": "audits/workflow"}),
            },
            "optional": {
                "review_json": ("STRING", {"default": "", "forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("paths",)
    FUNCTION = "run"
    CATEGORY = "Comfy Workflow Tracker"
    OUTPUT_NODE = True
    DESCRIPTION = "Write the audit report into the ComfyUI output folder."

    def run(self, audit, format, filename_prefix, review_json=""):
        base = os.path.join(_output_dir(), filename_prefix)
        os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")

        wanted = ["html", "markdown", "json"] if format == "all" else [format]
        written: list[str] = []
        for kind in wanted:
            if kind == "html":
                body, ext = html_report.render(audit), "html"
            elif kind == "markdown":
                body, ext = md_report.render(audit), "md"
            else:
                payload = audit.to_dict()
                if review_json.strip():
                    try:
                        payload["claude_review"] = json.loads(review_json)
                    except json.JSONDecodeError:
                        pass
                body, ext = json.dumps(payload, indent=2), "json"
            path = f"{base}_{stamp}.{ext}"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            written.append(path)

        return {"ui": {"text": ["\n".join(written)]}, "result": ("\n".join(written),)}


# --------------------------------------------------------------------------


def render_review(result: reviewer_mod.AgentResult) -> str:
    """Format the agent's conclusions as Markdown."""
    return review_section.markdown(result.as_dict())


def _console_summary(report: audit_mod.AuditReport) -> str:
    counts = report.risk.counts()
    parts = [f"{counts[s]} {s}" for s in SEVERITIES if counts.get(s)]
    lines = [
        report.licensing.headline,
        f"risk: {report.risk.score}/100 ({report.risk.band})",
        f"automation: {report.automation.index}/100 ({report.automation.band})",
        f"models: {len(report.models)}  packs: {len(report.packs)}",
        f"findings: {', '.join(parts) if parts else 'none'}",
    ]
    for finding in report.risk.findings[:3]:
        if finding.severity in ("critical", "high"):
            lines.append(f"  [{finding.severity}] {finding.title}")
    return "\n".join(lines)


NODE_CLASS_MAPPINGS = {
    "ComfyAuditStudioProfile": ComfyAuditStudioProfile,
    "ComfyAuditWorkflow": ComfyAuditWorkflow,
    "ComfyAuditClaudeReview": ComfyAuditClaudeReview,
    "ComfyAuditGate": ComfyAuditGate,
    "ComfyAuditHistoryGate": ComfyAuditHistoryGate,
    "ComfyAuditSaveReport": ComfyAuditSaveReport,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ComfyAuditStudioProfile": "Studio Profile",
    "ComfyAuditWorkflow": "Audit This Workflow",
    "ComfyAuditClaudeReview": "Claude Review",
    "ComfyAuditGate": "Audit Gate",
    "ComfyAuditHistoryGate": "Shot History Gate",
    "ComfyAuditSaveReport": "Save Audit Report",
}
