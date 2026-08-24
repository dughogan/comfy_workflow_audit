"""Explain the audit in plain language, without an API key.

A table of licence names tells a supervisor nothing. The facts that matter are
buried in the shape of the data: that four of the seven weights come from one
family and so stand or fall together, that the only hard limit in the whole
workflow is geographic, that the thing likeliest to stop a delivery is not a
licence at all but a hosted node uploading plates to a vendor.

So this reads the audit and says what it amounts to, in sentences. It is
deterministic - no model, no network, no key - because the report has to be
readable for everyone, and the Claude Review node is optional and costs money.

It never reaches a verdict. Without a studio profile there is nothing to reach
one from; what it does instead is name the facts a verdict would turn on, so the
reader knows what question they are actually being asked.
"""

from __future__ import annotations

from typing import Any

from ..score import clearance, licensing


def summarise(report: Any) -> list[str]:
    """The report as a handful of paragraphs. Empty when there is nothing to say."""
    paragraphs = [
        _what_it_is(report),
        _what_the_licences_turn_on(report),
        _what_would_settle_it(report),
        _what_else_matters(report),
    ]
    return [p for p in paragraphs if p]


def _n(count: int, singular: str, plural: str = "") -> str:
    """Count and noun, agreeing. "1 node type(s)" reads like a machine wrote it."""
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _verb(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _what_it_is(report: Any) -> str:
    """One sentence on the shape of the thing, before any judgement."""
    models = [m for m in report.models if m.enabled]
    packs = [p for p in report.packs if p.identified]
    src = report.source

    bits = [f"This workflow runs {src.get('nodes_total', 0)} nodes"]
    if src.get("nodes_disabled"):
        bits[0] += f" ({src['nodes_disabled']} of them disabled)"
    bits[0] += (f" and loads {_n(len(models), 'model file')}" if models
                else " and loads no model files")
    if packs:
        bits[0] += (f", drawing on {_n(len(packs), 'custom node pack')} beyond core "
                    "ComfyUI")
    if report.api_node_types:
        bits[0] += (f". {_n(len(report.api_node_types), 'node type')} "
                    f"{_verb(len(report.api_node_types), 'calls', 'call')} a hosted "
                    "service rather than running locally")
    return bits[0] + "."


def _what_the_licences_turn_on(report: Any) -> str:
    """The licence composition, said as a person would say it."""
    lic = report.licensing
    if not lic.total_models:
        return ("No model files were found, so there is nothing here to licence. "
                "That usually means the workflow is a fragment, or that every "
                "model arrives through a hosted node.")

    counts = lic.counts
    permissive = counts.get("permissive", 0)
    conditional = counts.get("conditional", 0)
    non_commercial = counts.get("non-commercial", 0)
    unstated = counts.get("unstated", 0)

    parts: list[str] = []

    if permissive and not (conditional or non_commercial or unstated):
        parts.append(
            f"All {_n(permissive, 'model')} carry permissive licences - the "
            "MIT/Apache family - which ask for a notice and nothing else. There "
            "is no fee, no territory limit and no revenue cap anywhere in this "
            "workflow.")
    else:
        lead = []
        if permissive:
            lead.append(f"{permissive} carry permissive terms that ask little "
                        "beyond attribution")
        if conditional:
            lead.append(f"{conditional} carry conditional terms - usable, but "
                        "only if particular things are true about who is using "
                        "them")
        if non_commercial:
            lead.append(f"{non_commercial} are licensed for non-commercial use "
                        "only")
        if unstated:
            lead.append(f"{unstated} could not be identified at all")
        parts.append(f"Of {_n(lic.total_models, 'model file')}, " + _join(lead) + ".")

    # Which conditions actually appear, since "conditional" alone says nothing.
    triggers = _triggers(report)
    if triggers:
        parts.append("The conditions in play are " + _join(triggers)
                     + ". Which of those bite depends entirely on the facility, "
                     "not on the workflow.")

    if non_commercial:
        names = sorted({m.license.name for m in report.models
                        if m.enabled and m.license
                        and m.license.commercial_use == "no"})
        parts.append(
            "A non-commercial licence is the one restriction no budget line "
            "lifts on its own: " + _join(names) + ". Some are written to cover "
            "the outputs as well as the weights, which is what catches people "
            "out - the frames themselves inherit the restriction.")

    if unstated:
        parts.append(
            f"The {_n(unstated, 'unidentified file')} "
            f"{_verb(unstated, 'is', 'are')} not a licensing problem "
            "yet; they are an information problem. A weight nobody can trace is "
            "one nobody can clear, and renaming a file is enough to lose its "
            "provenance entirely.")

    return " ".join(parts)


def _triggers(report: Any) -> list[str]:
    """Name the condition types present, in the language of what they do."""
    conds = [m.license.conditions for m in report.models
             if m.enabled and m.license and m.license.conditions]
    found: list[str] = []

    territories: set[str] = set()
    for cond in conds:
        territories.update(cond.get("territory_excluded") or [])
    if territories:
        found.append("a territory carve-out excluding "
                     + _join(sorted((clearance.TERRITORIES.get(t, t)
                                     for t in territories),
                                    key=_ignoring_the)))

    caps = sorted({c["revenue_cap_usd"] for c in conds if c.get("revenue_cap_usd")})
    if caps:
        found.append("a revenue cap at " + _join(_usd(c) for c in caps))

    if any(c.get("copyleft_reach") in ("integration", "network") for c in conds):
        found.append("copyleft that can reach your own code")

    if any(c.get("no_competing_training") for c in conds):
        found.append("a ban on training other models from the outputs")

    if any(c.get("attribution_visible") for c in conds):
        found.append("attribution that has to be visible in a shipped product")

    if any(c.get("prohibited_uses") for c in conds):
        found.append("a schedule of prohibited uses that travels with the weights")

    return found


def _what_would_settle_it(report: Any) -> str:
    """The question the reader is actually being asked."""
    clr = report.clearance
    if clr.determined or not clr.missing_facts:
        return ""

    return ("Whether any of this suits a given job is not something the workflow "
            "file can answer, because these licences grant rights to particular "
            "people in particular places doing particular things. "
            + _facts_sentence(clr.missing_facts)
            + " Set a Studio Profile - in the ComfyUI settings, or on the canvas "
              "- and this report will say go, no-go, or go-with-conditions, and "
              "show the reasoning behind it.")


def _facts_sentence(facts: list[str]) -> str:
    """The missing facts, as prose rather than a bullet list."""
    # The engine's facts arrive as "Territory - because ...". The label before
    # the dash is what a reader needs here; the reason is already implied.
    labels = [fact.split(" - ")[0].strip().lower() for fact in facts]
    labels = [label for label in labels if label]
    if not labels:
        return "Several facts about the facility would settle it."
    return ("The facts that would settle it here are " + _join(labels) + ".")


def _what_else_matters(report: Any) -> str:
    """The things that stop deliveries but are not licence questions."""
    parts: list[str] = []
    risk = report.risk

    if report.api_node_types:
        parts.append(
            "Independently of any licence, "
            f"{_n(len(report.api_node_types), 'node type')} here "
            f"{_verb(len(report.api_node_types), 'uploads', 'upload')} its inputs "
            "to a third party. On most client contracts that is a disclosure "
            "needing permission, and what the vendor may do with the material is "
            "set by their terms rather than by any model licence.")

    critical = [f for f in risk.findings if f.severity == "critical"]
    if critical:
        parts.append(
            f"{_n(len(critical), 'finding')} {_verb(len(critical), 'is', 'are')} "
            "marked critical, which here means "
            "the workflow will not reliably run or reproduce elsewhere as it "
            "stands: " + _join(f.title.lower() for f in critical[:2]) + ".")

    if report.missing_models:
        parts.append(
            f"{_n(len(report.missing_models), 'referenced weight')} "
            f"{_verb(len(report.missing_models), 'is', 'are')} not present "
            "on this machine, so the workflow cannot currently run to completion "
            "here regardless of what its licences say.")

    auto = report.automation
    if auto.index < 40 and auto.per_run_touchpoints:
        parts.append(
            f"An artist drives this: {_n(len(auto.per_run_touchpoints), 'point')} "
            f"{_verb(len(auto.per_run_touchpoints), 'needs', 'need')} a human on "
            "every run, so output scales with their time rather "
            "than with machine time. That is a scheduling fact, not a fault.")

    return " ".join(parts)


def _join(values: Any) -> str:
    items = [str(v) for v in values]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _ignoring_the(name: str) -> str:
    """Sort 'the United States' under U, not under T."""
    return name[4:] if name.startswith("the ") else name


def _usd(amount: int) -> str:
    if amount >= 1_000_000:
        return f"${amount // 1_000_000}M"
    return f"${amount:,}"
