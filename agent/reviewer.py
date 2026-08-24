"""Claude as a second pass over the audit.

The rule engine is deterministic and knows exactly what it knows. What it cannot
do is recognise that ``juggernautXL_v9Rundiffusion.safetensors`` is an SDXL
community merge, notice that a prompt names a living artist, or propose the
upscaler to swap in when the one in the graph turns out to be non-commercial.

Those are judgement calls, so they are handled by an agent that investigates
with tools rather than a single prompt handed a summary. Everything it concludes
is recorded separately from the rule findings and labelled as model-derived, so
a reader can always tell which half of the report is deterministic.

The Anthropic SDK is an optional dependency: without it, and without a key, the
audit still runs and this step reports that it was skipped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.audit import AuditReport
from ..core.records import Finding
from ..core.score.risk import RISK_BANDS, SEVERITY_WEIGHT
from .tools import Collector, build_tools

DEFAULT_MODEL = "claude-opus-5"
MODELS = ["claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]

#: Refusal fallbacks: if a safety classifier declines the request, the server
#: reroutes it by category rather than handing back an empty result.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Dynamic-filtering web search. Only the current model generation supports it.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

MODES = ["full", "identify", "clearance", "remediate", "narrative"]

SYSTEM = """\
You are auditing a ComfyUI workflow on behalf of a VFX facility that has to \
decide whether it can be used on paid client work. A deterministic rule engine \
has already extracted the models, prompts, assets and dependencies, and scored \
the licensing and production risk. Your job is the part the rules cannot do.

Work by investigation, not assumption. Use the read tools to look at the actual \
workflow before you conclude anything, and use search_licence_knowledge_base so \
your answers stay consistent with what the tool already reports.

Where lookup_huggingface, lookup_civitai and lookup_github are available, use \
them before relying on what you remember about a model. Your recollection of a \
licence may be out of date - several have changed since you were trained, and \
Stability and Black Forest Labs have both relicensed mid-flight. A hash lookup \
on Civitai is exact; a filename search is a guess and should be reported as one.

Three principles govern everything you record:

1. An honest "unknown" beats a confident guess. A wrong licence claim can put a \
delivery at legal risk, and the people reading this will act on what you write. \
Use low confidence freely, and say what would settle the question.
2. Only report what is actually there. Do not invent risks in prompt text to \
seem thorough, and do not flag ordinary descriptive language as a trademark or \
a likeness issue.
3. Be specific to this workflow. "Check your licences" helps nobody. Name the \
file, name the node, name the replacement.

Record your conclusions with the record_* tools as you reach them - those are \
what appear in the report. When you have finished investigating, write a short \
plain-prose summary for a supervisor: what the real problem is, what it blocks, \
and what you would do first. No headings, no bullet lists, under 200 words.\
"""

MODE_PROMPTS = {
    "identify": (
        "Identify the models this workflow uses that the rule engine could not. "
        "For each one, work out what it actually is, what it was built on, and what "
        "that implies for commercial use - a fine-tune or merge inherits the "
        "obligations of its base model. Record each with record_model_identification."
    ),
    "clearance": (
        "Review the prompt text for clearance risk: real people, trademarks, brands, "
        "copyrighted characters, and living artists named as a style reference. These "
        "are problems no licence check catches and they surface late, in delivery. "
        "Record each with record_content_risk. If the prompts are clean, say so and "
        "record nothing."
    ),
    "remediate": (
        "Produce a remediation plan. For every model that blocks or conditions "
        "commercial use, propose a specific replacement that does the same job in this "
        "workflow, checking what is already installed locally first, and record it with "
        "record_substitution. Then record the ordered steps with record_action, most "
        "important first."
    ),
    "narrative": (
        "Write the go/no-go brief a supervisor or producer will actually read.\n\n"
        "Start with read_determination. If a verdict was reached, that chain is "
        "your evidence and your narrative must not contradict it: you are "
        "explaining a determination, not making a new one. Check the reasoning "
        "against the licence knowledge base and, where lookups are available, "
        "against the source pages - if you find the engine has a term wrong, say "
        "so plainly rather than writing around it.\n\n"
        "If no profile was supplied, do not invent one. Explain what the licences "
        "in this workflow turn on, and which facts about the facility would settle "
        "it.\n\n"
        "Write for someone deciding whether to put this on a show, not for a "
        "lawyer. Lead with the answer. Say what blocks it and what that would "
        "cost to resolve - a territory carve-out is not the same problem as a fee, "
        "and a fee is not the same problem as an obligation to open-source. "
        "Distinguish what is certain from what is a reading. Name files, licences "
        "and rights holders; never write 'check your licences'.\n\n"
        "Record any concrete step with record_action, most important first. Then "
        "write the brief itself as your final message: plain prose, no headings, "
        "no bullet lists, under 350 words."
    ),
    "full": (
        "Work through all three of these in order.\n\n"
        "1. Identify the models the rule engine could not, and what their lineage "
        "implies for commercial use. Record with record_model_identification.\n"
        "2. Review the prompt text for clearance risk - real people, trademarks, "
        "copyrighted characters, living artists named as a style. Record with "
        "record_content_risk, and record nothing if the prompts are clean.\n"
        "3. Propose replacements for anything blocking commercial use, preferring "
        "models already installed locally, and record the ordered remediation steps.\n\n"
        "Be proportionate: if the workflow is clean, a short answer is the right answer."
    ),
}


@dataclass
class AgentResult:
    """What the agent concluded, and how it got there."""

    ran: bool = False
    mode: str = "full"
    model: str = ""
    summary: str = ""
    identifications: list[dict[str, Any]] = field(default_factory=list)
    content_risks: list[dict[str, Any]] = field(default_factory=list)
    substitutions: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    web_search_enabled: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran, "mode": self.mode, "model": self.model,
            "summary": self.summary, "identifications": self.identifications,
            "content_risks": self.content_risks, "substitutions": self.substitutions,
            "actions": self.actions, "tool_calls": self.tool_calls,
            "web_search_enabled": self.web_search_enabled,
            "usage": {"input_tokens": self.input_tokens,
                      "output_tokens": self.output_tokens, "turns": self.turns},
            "error": self.error,
        }


def available() -> tuple[bool, str]:
    """Whether the agent can run here, and why not if it cannot."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, ("the 'anthropic' package is not installed - "
                       "run: pip install anthropic")
    if not _api_key_present():
        return False, ("no Anthropic credentials found - set ANTHROPIC_API_KEY, "
                       "or run 'ant auth login', or pass a key to the node")
    return True, ""


def _api_key_present() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    # An `ant auth login` profile is picked up by the SDK with no env var set.
    profile_dir = os.path.join(os.path.expanduser("~"), ".config", "anthropic")
    return os.path.isdir(profile_dir)


def review(report: AuditReport, *, mode: str = "full", model: str = DEFAULT_MODEL,
           effort: str = "high", api_key: str = "", web_search: bool = True,
           max_turns: int = 24, question: str = "",
           local_models: Callable[[str], list[str]] | None = None,
           resolver: Any = None) -> AgentResult:
    """Run the agent over a completed audit."""
    result = AgentResult(mode=mode, model=model, web_search_enabled=web_search)

    ok, why = available()
    if api_key:
        ok, why = True, ""
    if not ok:
        result.error = why
        return result

    try:
        import anthropic
        from anthropic import beta_tool
    except ImportError as exc:  # pragma: no cover - guarded by available()
        result.error = f"anthropic import failed: {exc}"
        return result

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    collector = Collector()
    tools: list[Any] = build_tools(report, collector, beta_tool, local_models, resolver)
    if web_search:
        tools.append(WEB_SEARCH_TOOL)

    instruction = question.strip() if question.strip() else MODE_PROMPTS.get(
        mode, MODE_PROMPTS["full"])
    messages: list[Any] = [{"role": "user", "content": instruction}]

    try:
        last = _run_with_pause_resume(client, model, effort, tools, messages,
                                      result, max_turns)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, never fatal
        result.error = _explain(exc)
        result.ran = bool(result.turns)
        _absorb(result, collector)
        return result

    result.ran = True
    result.summary = _final_text(last)
    _absorb(result, collector)
    return result


# --------------------------------------------------------------------------


def _run_with_pause_resume(client: Any, model: str, effort: str, tools: list[Any],
                           messages: list[Any], result: AgentResult,
                           max_turns: int) -> Any:
    """Drive the tool runner, resuming turns the server pauses.

    A long web-search turn can come back with ``stop_reason: "pause_turn"``. The
    Python runner exits when no client tool ran, so a paused turn would end the
    loop silently with a half-finished answer; restarting with the paused turn
    appended is the documented way to continue it.
    """
    restarts = 0
    last = None

    while True:
        runner = client.beta.messages.tool_runner(**_request(model, effort, tools, messages))
        for message in runner:
            last = message
            result.turns += 1
            usage = getattr(message, "usage", None)
            if usage is not None:
                result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                result.output_tokens += getattr(usage, "output_tokens", 0) or 0
            # Mirror the history: the runner keeps its own copy and will not
            # hand it back, but a restart needs it.
            messages.append({"role": "assistant", "content": message.content})
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                messages.append(tool_response)
            if result.turns >= max_turns:
                return last

        if last is None or getattr(last, "stop_reason", "") != "pause_turn":
            return last
        restarts += 1
        if restarts > 4:
            return last


def _request(model: str, effort: str, tools: list[Any],
             messages: list[Any]) -> dict[str, Any]:
    """Build the request, dropping options an older SDK does not know about."""
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": 16000,
        "system": [{"type": "text", "text": SYSTEM,
                    "cache_control": {"type": "ephemeral"}}],
        "tools": tools,
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }
    if model.startswith("claude-opus-5") or model.startswith("claude-fable-5"):
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def _final_text(message: Any) -> str:
    if message is None:
        return ""
    parts = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def _absorb(result: AgentResult, collector: Collector) -> None:
    data = collector.as_dict()
    result.identifications = data["identifications"]
    result.content_risks = data["content_risks"]
    result.substitutions = data["substitutions"]
    result.actions = data["actions"]
    result.tool_calls = data["tool_calls"]


def _explain(exc: Exception) -> str:
    """Turn an SDK exception into something a supervisor can act on."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return f"{type(exc).__name__}: {exc}"

    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic rejected the credentials - check the API key."
    if isinstance(exc, anthropic.NotFoundError):
        return (f"Model not available to this account: {exc}. "
                "Try a different model in the node's model list.")
    if isinstance(exc, anthropic.RateLimitError):
        return "Rate limited by the Anthropic API - retry in a moment."
    if isinstance(exc, anthropic.APIStatusError):
        return f"Anthropic API error {exc.status_code}: {exc}"
    if isinstance(exc, anthropic.APIConnectionError):
        return ("Could not reach the Anthropic API - check network access from "
                "this machine.")
    if isinstance(exc, TypeError):
        return (f"The installed 'anthropic' package does not accept a parameter this "
                f"node sends ({exc}). Upgrade it: pip install -U anthropic")
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------
# Folding results back into the audit
# --------------------------------------------------------------------------


def apply_to_report(report: AuditReport, result: AgentResult) -> None:
    """Merge the agent's conclusions into the audit as labelled findings.

    Content risks become real findings because they are real risks, but they are
    tagged ``ai.*`` and every entry says it is model-derived, so nobody mistakes
    them for the deterministic half of the report.
    """
    if not result.ran:
        return

    added: list[Finding] = []

    by_severity: dict[str, list[dict[str, Any]]] = {}
    for risk in result.content_risks:
        by_severity.setdefault(risk.get("severity", "medium"), []).append(risk)

    for severity, group in by_severity.items():
        kinds = sorted({r.get("kind", "other") for r in group})
        added.append(Finding(
            id="ai.content-risk",
            title=f"{len(group)} clearance risk(s) in the prompt text ({', '.join(kinds)})",
            severity=severity if severity in SEVERITY_WEIGHT else "medium",
            category="content",
            detail=("Identified by Claude reading the prompts, not by a rule. Prompt "
                    "text that names a real person, a brand, a copyrighted character "
                    "or a living artist's style is a clearance problem regardless of "
                    "how the models are licensed. Verify each excerpt before acting."),
            evidence=[f"{r.get('where', '?')}: \"{r.get('excerpt', '')}\" - {r.get('detail', '')}"
                      for r in group],
            recommendation="; ".join(
                dict.fromkeys(r.get("recommendation", "") for r in group if r.get("recommendation"))
            ) or "Review each excerpt with the production or legal team.",
        ))

    blocking = [i for i in result.identifications
                if i.get("commercial_use") in ("no", "conditional")]
    if blocking:
        added.append(Finding(
            id="ai.model-identification",
            title=f"{len(blocking)} unidentified model(s) look commercially restricted",
            severity="high",
            category="licensing",
            detail=("Claude identified these models that the rule engine could not, and "
                    "believes their lineage restricts commercial use. This is an "
                    "inference from the filename and the model's known family, not a "
                    "licence lookup - confirm each before relying on it either way."),
            evidence=[f"{i['filename']} -> {i.get('family', '?')} "
                      f"(base: {i.get('base_model', '?')}, {i.get('commercial_use')}, "
                      f"confidence {i.get('confidence', '?')})" for i in blocking],
            recommendation="Confirm each at its source page, then add it to the studio "
                           "licence file so the rule engine catches it next time.",
        ))

    if not added:
        return

    for finding in added:
        finding.score = SEVERITY_WEIGHT.get(finding.severity, 0.0)
        report.risk.by_category[finding.category] = round(
            report.risk.by_category.get(finding.category, 0.0) + finding.score, 1)
    report.risk.findings.extend(added)
    report.risk.findings.sort(key=lambda f: (f.rank, -f.score, f.title))

    total = sum(f.score for f in report.risk.findings)
    report.risk.score = int(min(100, round(total)))
    for threshold, band, detail in RISK_BANDS:
        if report.risk.score >= threshold:
            report.risk.band, report.risk.band_detail = band, detail
            break
