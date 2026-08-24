"""Turn licence terms plus a studio's own facts into a determination.

The licence summary describes what the terms say. This decides what they mean
*for one particular studio*, which is the only level at which "can we use this?"
has an answer at all: MiniMax H3 is a no in London and a yes in Toronto, because
the licence carves out the United Kingdom by territory and says nothing about
Canada. Nothing here encodes a policy or a risk appetite. It applies stated
terms to stated facts and shows the chain it followed, so a reader can check the
reasoning rather than trust the label.

With no profile supplied, no determination is made. A verdict against an unknown
studio would be a guess wearing a verdict's clothes.

The structured terms it reads live under ``conditions`` on each licence
definition in ``knowledge/data/licences.json``:

``territory_excluded``      list of territory codes the grant does not cover
``revenue_cap_usd``         free use ends above this annual revenue
``monthly_active_users_cap`` free use ends above this MAU count
``copyleft_reach``          none | model-only | integration | network
``outputs_commercial``      whether the restriction reaches the outputs too
``no_competing_training``   outputs may not train a competing model
``attribution_visible``     text that must appear in a shipped product
``commercial_licence_available`` a paid licence exists that lifts the terms
``separate_licence_holder`` who to go to for it
``prohibited_uses``         a use schedule travels with the weights
``hosted``                  runs on someone else's servers
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from ..records import ModelRef, PackRef

#: The territories licences in this knowledge base actually name.
TERRITORIES = {
    "US": "the United States",
    "EU": "the European Union",
    "GB": "the United Kingdom",
    "KR": "South Korea",
    "CA": "Canada",
    "AU": "Australia",
    "JP": "Japan",
    "CN": "China",
    "IN": "India",
    "OTHER": "elsewhere",
}

#: Revenue bands, as the lower bound of each in USD. The bands are chosen to sit
#: either side of the thresholds licences actually name - $1M, $10M, $20M, $100M
#: - so a studio picking a band always lands unambiguously on one side.
REVENUE_BANDS = {
    "under-1m": 0,
    "1m-10m": 1_000_000,
    "10m-20m": 10_000_000,
    "20m-100m": 20_000_000,
    "over-100m": 100_000_000,
    "unknown": None,
}

REVENUE_LABELS = {
    "under-1m": "under $1M annual revenue",
    "1m-10m": "$1M-$10M annual revenue",
    "10m-20m": "$10M-$20M annual revenue",
    "20m-100m": "$20M-$100M annual revenue",
    "over-100m": "over $100M annual revenue",
    "unknown": "revenue not stated",
}

#: What leaves the building. Each is a superset of the one before it.
SHIPS = {
    "deliverable-only": "finished frames or footage delivered to a client",
    "software": "the workflow, or software containing it, is distributed",
    "service": "it is exposed to users over a network",
    "internal-only": "nothing leaves the building",
    "unknown": "not stated",
}

VERDICTS = ["no-go", "conditions", "go", "unknown"]

#: Ordered worst-first, so the overall verdict is the first one present.
VERDICT_RANK = {"no-go": 0, "conditions": 1, "unknown": 2, "go": 3}

VERDICT_LABELS = {
    "go": "Clear as-is",
    "conditions": "Clear once conditions are met",
    "no-go": "Needs changes first",
    "unknown": "Needs more information",
}

#: The kind of work that lifts a finding, which is what a supervisor is actually
#: deciding about. "Not usable" reads as a wall; most of these are an afternoon.
#: Ordered by how much they cost to do.
ACTIONS = {
    "confirm": "a confirmation",
    "information": "information to find",
    "configure": "a configuration change",
    "attribute": "a credit to add",
    "licence": "a licence to buy",
    "relocate": "a change of where it runs",
    "swap": "a model or pack to swap",
}

ACTION_ORDER = ["confirm", "information", "configure", "attribute", "licence",
                "relocate", "swap"]


@dataclass
class StudioProfile:
    """The facts about a facility that licence terms actually turn on."""

    #: Where the work is rendered and deployed. A territory code from TERRITORIES.
    territory: str = ""
    #: A key from REVENUE_BANDS.
    revenue_band: str = "unknown"
    #: A key from SHIPS.
    ships: str = "unknown"
    #: Outputs are used to train other models.
    trains_models: bool = False
    #: Real performers' likenesses are involved.
    likeness_involved: bool = False
    #: Monthly active users, where the product has them at all.
    monthly_active_users: int | None = None
    #: Free-text label for the report - a facility name, a show, a client.
    label: str = ""

    @property
    def is_set(self) -> bool:
        """A profile with no territory and no revenue band decides nothing."""
        return bool(self.territory) or self.revenue_band != "unknown"

    @property
    def revenue_floor(self) -> int | None:
        return REVENUE_BANDS.get(self.revenue_band)

    def describe(self) -> str:
        parts = []
        if self.territory:
            parts.append(TERRITORIES.get(self.territory, self.territory))
        if self.revenue_band != "unknown":
            parts.append(REVENUE_LABELS.get(self.revenue_band, self.revenue_band))
        if self.ships != "unknown":
            parts.append(SHIPS[self.ships])
        if self.trains_models:
            parts.append("outputs train other models")
        if self.likeness_involved:
            parts.append("real performers involved")
        return " · ".join(parts) or "no profile set"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StudioProfile":
        data = data or {}
        return cls(
            territory=str(data.get("territory", "") or "").upper()[:5],
            revenue_band=str(data.get("revenue_band", "unknown") or "unknown"),
            ships=str(data.get("ships", "unknown") or "unknown"),
            trains_models=bool(data.get("trains_models")),
            likeness_involved=bool(data.get("likeness_involved")),
            monthly_active_users=data.get("monthly_active_users"),
            label=str(data.get("label", "") or ""),
        )


@dataclass
class Reason:
    """One step in the chain that produced a verdict."""

    verdict: str            # what this step alone would conclude
    text: str               # what it concluded, in a sentence
    term: str = ""          # the condition key it came from
    fact: str = ""          # the profile fact it was applied to
    remedy: str = ""        # what would turn this into a yes
    action: str = ""        # the shape of that remedy: a key from ACTIONS


@dataclass
class Determination:
    """The verdict for one model or node pack, and how it was reached."""

    subject: str
    kind: str = "model"     # model | pack
    licence: str = "Unknown"
    verdict: str = "unknown"
    reasons: list[Reason] = field(default_factory=list)
    url: str = ""
    confidence: str = "low"

    @property
    def headline(self) -> str:
        blocking = [r for r in self.reasons if r.verdict == self.verdict]
        return blocking[0].text if blocking else VERDICT_LABELS[self.verdict]


@dataclass
class ClearanceResult:
    """Every determination, plus what they add up to."""

    profile: StudioProfile = field(default_factory=StudioProfile)
    determined: bool = False
    verdict: str = "unknown"
    headline: str = ""
    determinations: list[Determination] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    #: How many distinct jobs of each kind are outstanding, keyed by ACTIONS.
    actions: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "determined": self.determined,
            "profile": asdict(self.profile),
            "profile_description": self.profile.describe(),
            "verdict": self.verdict,
            "verdict_label": VERDICT_LABELS.get(self.verdict, self.verdict),
            "headline": self.headline,
            "determinations": [asdict(d) for d in self.determinations],
            "blockers": self.blockers,
            "conditions": self.conditions,
            "unresolved": self.unresolved,
            "missing_facts": self.missing_facts,
            "actions": self.actions,
            "actions_described": _describe_actions(self.actions),
            "total_actions": sum(self.actions.values()),
        }

    def by_verdict(self, verdict: str) -> list[Determination]:
        return [d for d in self.determinations if d.verdict == verdict]

    def distinct_blockers(self) -> list[str]:
        """One line per distinct reason, not per affected file.

        Four weights sharing a licence share its blocking clause, and listing it
        four times hides how few things actually have to be resolved.
        """
        seen: dict[str, list[str]] = {}
        for det in self.by_verdict("no-go"):
            for reason in det.reasons:
                if reason.verdict == "no-go":
                    seen.setdefault(reason.text, []).append(det.subject)
        return [f"{text} ({len(subjects)} file(s))" if len(subjects) > 1 else
                f"{text} ({subjects[0]})" for text, subjects in seen.items()]


def determine(models: Iterable[ModelRef],
              packs: Iterable[PackRef] = (),
              profile: StudioProfile | None = None,
              api_node_types: Iterable[str] = (),
              node_types: Iterable[str] = ()) -> ClearanceResult:
    """Apply each licence's terms to the studio's facts."""
    profile = profile or StudioProfile()
    models = list(models)
    packs = list(packs)
    api_node_types = list(api_node_types)
    result = ClearanceResult(profile=profile)

    if not profile.is_set:
        result.missing_facts = _missing_facts(profile, models)
        result.headline = ("No studio profile was supplied, so no determination "
                           "was made.")
        return result

    result.determined = True
    for model in models:
        if not model.enabled:
            continue
        result.determinations.append(_judge_model(model, profile))
    for pack in packs:
        found = _judge_pack(pack, profile)
        if found is not None:
            result.determinations.append(found)

    for node_type in sorted(set(api_node_types)):
        result.determinations.append(_judge_hosted(node_type, profile))

    likeness = _judge_likeness(models, list(node_types) + api_node_types, profile)
    if likeness is not None:
        result.determinations.append(likeness)

    result.determinations.sort(key=lambda d: (VERDICT_RANK[d.verdict], d.subject.lower()))
    _roll_up(result, profile)
    return result


# ----------------------------------------------------------------------------
# Per-subject judgements
# ----------------------------------------------------------------------------


def _judge_model(model: ModelRef, profile: StudioProfile) -> Determination:
    lic = model.license
    out = Determination(subject=model.filename, kind="model",
                        licence=lic.name if lic else "Unknown",
                        url=lic.url if lic else "",
                        confidence=lic.confidence if lic else "low")
    if lic is None or lic.commercial_use == "unknown":
        out.verdict = "unknown"
        out.reasons.append(Reason(
            verdict="unknown",
            text="No licence could be identified for this file, so nothing can be "
                 "concluded about it either way.",
            action="information",
            remedy="Establish where this file came from and what it was released under."))
        return out

    out.reasons = _apply_conditions(lic, profile, subject=model.filename)
    out.verdict = _worst(out.reasons)
    if not out.reasons:
        out.reasons.append(Reason(
            verdict="go",
            text=f"{lic.name} places no condition that this studio's "
                 f"circumstances trigger.",
            term="", fact=profile.describe()))
        out.verdict = "go"
    return out


def _judge_pack(pack: PackRef, profile: StudioProfile) -> Determination | None:
    """Node packs matter when their licence reaches the studio's own code.

    A pack's code runs inside the ComfyUI process, so a copyleft pack is a very
    different proposition from a copyleft model: it can reach anything the
    studio builds around it.
    """
    spdx = (pack.licence or "").upper()
    if not spdx:
        return None
    strong = spdx.startswith("AGPL")
    weak = spdx.startswith("GPL") or spdx.startswith("LGPL")
    if not (strong or weak):
        return None

    out = Determination(subject=pack.title or pack.repo, kind="pack",
                        licence=pack.licence, url=pack.licence_url, confidence="medium")
    if profile.ships == "service" and strong:
        out.verdict = "conditions"
        out.reasons.append(Reason(
            verdict="conditions", term="copyleft_reach", fact=SHIPS[profile.ships],
            text=f"{pack.title or pack.repo} is {pack.licence}. Exposing it over a "
                 "network counts as distribution, so the source of anything it is "
                 "combined with must be released under the same licence.",
            action="swap",
            remedy="Replace the pack, isolate it behind a process boundary, or "
                   "release the surrounding source."))
    elif profile.ships == "software":
        out.verdict = "conditions"
        out.reasons.append(Reason(
            verdict="conditions", term="copyleft_reach", fact=SHIPS[profile.ships],
            text=f"{pack.title or pack.repo} is {pack.licence} and the workflow is "
                 "distributed, so its terms reach the code shipped with it.",
            action="swap",
            remedy="Replace the pack, or release the surrounding source under the "
                   "same licence."))
    else:
        out.verdict = "go"
        out.reasons.append(Reason(
            verdict="go", term="copyleft_reach", fact=SHIPS.get(profile.ships, ""),
            text=f"{pack.title or pack.repo} is {pack.licence}, but nothing is "
                 "distributed, so the copyleft obligation is not triggered.",
            remedy="Revisit this if the workflow is ever shipped or hosted."))
    return out


def _judge_hosted(node_type: str, profile: StudioProfile) -> Determination:
    """A hosted API node is a contract question, not a licence question."""
    return Determination(
        subject=node_type, kind="api", licence="Vendor terms of service",
        verdict="unknown", confidence="medium",
        reasons=[Reason(
            verdict="unknown", term="hosted", fact="client material leaves the site",
            text=f"{node_type} sends material to a third-party service. What may be "
                 "done with it is set by that vendor's terms and your agreement with "
                 "them, not by a model licence.",
            action="confirm",
            remedy="Check the vendor's data-retention and training terms against the "
                   "show's NDA before real footage goes through it.")])


# ----------------------------------------------------------------------------
# The rules themselves
# ----------------------------------------------------------------------------


def _apply_conditions(lic: Any, profile: StudioProfile, subject: str) -> list[Reason]:
    """Every condition on a licence, tested against the studio's facts."""
    cond = lic.conditions or {}
    out: list[Reason] = []

    # -- territory: the hardest stop there is, because no fee lifts it --------
    excluded = cond.get("territory_excluded") or []
    if excluded and profile.territory:
        if profile.territory in excluded:
            where = TERRITORIES.get(profile.territory, profile.territory)
            out.append(Reason(
                verdict="no-go", term="territory_excluded", fact=where,
                text=f"{lic.name} does not grant rights in {where}, which is where "
                     "this studio operates.",
                action="relocate",
                remedy="Run the model in a territory the grant covers, or negotiate "
                       "directly with the rights holder."))
        else:
            out.append(Reason(
                verdict="go", term="territory_excluded",
                fact=TERRITORIES.get(profile.territory, profile.territory),
                text=f"{lic.name} carves out "
                     + _join(TERRITORIES.get(t, t) for t in excluded)
                     + ", none of which is where this studio operates."))
    elif excluded:
        out.append(Reason(
            verdict="unknown", term="territory_excluded", fact="territory not stated",
            text=f"{lic.name} excludes " + _join(TERRITORIES.get(t, t) for t in excluded)
                 + ", and no territory was given for this studio.",
            action="information",
            remedy="Set the studio's territory."))

    # -- outright non-commercial ---------------------------------------------
    if lic.commercial_use == "no":
        reaches_outputs = cond.get("outputs_commercial") == "no"
        holder = cond.get("separate_licence_holder")
        purchasable = bool(cond.get("commercial_licence_available") and holder)
        remedy = (f"Obtain a commercial licence from {holder}." if purchasable
                  else "Replace this model with one licensed for commercial use.")
        out.append(Reason(
            verdict="no-go", term="commercial_use", fact="commercial work",
            action="licence" if purchasable else "swap",
            text=f"{lic.name} does not permit commercial use"
                 + (", and the restriction is written to cover the outputs as well "
                    "as the weights." if reaches_outputs else "."),
            remedy=remedy))

    # -- revenue and user caps ------------------------------------------------
    cap = cond.get("revenue_cap_usd")
    if cap:
        floor = profile.revenue_floor
        if floor is None:
            out.append(Reason(
                verdict="unknown", term="revenue_cap_usd", fact="revenue not stated",
                text=f"{lic.name} is free only below {_usd(cap)} "
                     f"({cond.get('revenue_basis', 'annual revenue')}), and no revenue "
                     "band was given.",
                action="information",
                remedy="Set the studio's revenue band."))
        elif floor >= cap:
            holder = cond.get("separate_licence_holder", "the rights holder")
            out.append(Reason(
                verdict="conditions", term="revenue_cap_usd",
                fact=REVENUE_LABELS.get(profile.revenue_band, profile.revenue_band),
                text=f"{lic.name} is free only below {_usd(cap)} "
                     f"({cond.get('revenue_basis', 'annual revenue')}). This studio is "
                     "above that, so free use does not apply.",
                action="licence",
                remedy=f"Negotiate a separate agreement with {holder}."))
        else:
            out.append(Reason(
                verdict="go", term="revenue_cap_usd",
                fact=REVENUE_LABELS.get(profile.revenue_band, profile.revenue_band),
                text=f"{lic.name} is free below {_usd(cap)}, and this studio is under "
                     "that threshold."))

    mau_cap = cond.get("monthly_active_users_cap")
    if mau_cap and profile.monthly_active_users is not None:
        if profile.monthly_active_users >= mau_cap:
            out.append(Reason(
                verdict="conditions", term="monthly_active_users_cap",
                fact=f"{profile.monthly_active_users:,} monthly active users",
                text=f"{lic.name} requires a separate licence above "
                     f"{mau_cap:,} monthly active users.",
                action="licence",
                remedy="Negotiate with "
                       + cond.get("separate_licence_holder", "the rights holder") + "."))

    # -- copyleft, which only bites if something leaves the building ----------
    reach = cond.get("copyleft_reach")
    if reach in ("integration", "network"):
        if profile.ships in ("software", "service"):
            holder = cond.get("separate_licence_holder", "the rights holder")
            note = cond.get("copyleft_note", "")
            out.append(Reason(
                verdict="conditions", term="copyleft_reach", fact=SHIPS[profile.ships],
                text=f"{lic.name} reaches the code it is combined with"
                     + (f": {note}." if note else ".")
                     + " This studio distributes software, so that obligation applies.",
                action="licence",
                remedy=f"Buy the commercial licence from {holder}, or release the "
                       "surrounding source under the same terms."))
        elif profile.ships in ("deliverable-only", "internal-only"):
            vendor = cond.get("vendor_position_internal_use")
            if vendor:
                out.append(Reason(
                    verdict="conditions", term="copyleft_reach",
                    fact=SHIPS[profile.ships],
                    text=f"Only finished work leaves the building, so {lic.name}'s "
                         f"distribution obligation is not triggered by delivery. "
                         f"However, {vendor}.",
                    action="confirm",
                    remedy="Confirm the position with "
                           + cond.get("separate_licence_holder", "the vendor")
                           + ", or budget for their commercial licence."))
            else:
                out.append(Reason(
                    verdict="go", term="copyleft_reach", fact=SHIPS[profile.ships],
                    text=f"{lic.name} is copyleft, but nothing containing it is "
                         "distributed, so the obligation is not triggered.",
                    remedy="Revisit if the workflow is ever shipped or hosted."))
        else:
            out.append(Reason(
                verdict="unknown", term="copyleft_reach", fact="not stated",
                text=f"{lic.name} is copyleft and whether anything ships was not "
                     "stated, which is what decides whether it bites.",
                action="information",
                remedy="State what leaves the building."))

    # -- training other models ------------------------------------------------
    if cond.get("no_competing_training") and profile.trains_models:
        out.append(Reason(
            verdict="no-go", term="no_competing_training",
            fact="outputs train other models",
            text=f"{lic.name} forbids using outputs to train a competing model, and "
                 "this studio does exactly that.",
            action="configure",
            remedy="Exclude this model's outputs from any training set."))

    # -- obligations that do not block, but must be met -----------------------
    visible = cond.get("attribution_visible")
    if visible and profile.ships in ("software", "service"):
        out.append(Reason(
            verdict="conditions", term="attribution_visible", fact=SHIPS[profile.ships],
            text=f"{lic.name} requires '{visible}' to be displayed in a shipped "
                 "product's interface.",
            action="attribute",
            remedy=f"Add the '{visible}' notice before release."))

    prohibited = cond.get("prohibited_uses") or []
    if prohibited:
        out.append(Reason(
            verdict="conditions", term="prohibited_uses", fact="use restrictions",
            text=f"{lic.name} carries a use schedule: " + _join(prohibited) + ".",
            action="confirm",
            remedy="Confirm the intended use is not on that list, and pass the "
                   "schedule on with any redistribution."))

    out.sort(key=lambda r: VERDICT_RANK[r.verdict])
    return out


#: Identity work is not always done by a model with a telling name - this
#: workflow drives a face swap through a general video model and a segmenter -
#: so the node types the graph uses are as good a signal as the filenames.
IDENTITY_RE = re.compile(
    r"insightface|antelope|buffalo_|inswapper|instantid|faceid|reactor|roop|facefusion|"
    r"photomaker|pulid|arcface|facerestore|face.?swap|face.?detail|sam.?3|"
    r"ref2v|reference.?to.?video|portrait",
    re.IGNORECASE,
)


def _judge_likeness(models: Iterable[ModelRef], node_types: Iterable[str],
                    profile: StudioProfile) -> Determination | None:
    """Performer consent, which no model licence speaks to.

    A licence grants rights in the weights. It says nothing about the rights in
    a face those weights reproduce, and on a job with real performers that is
    the question likelier to stop a delivery.
    """
    if not profile.likeness_involved:
        return None
    evidence = sorted({m.filename for m in models
                       if m.enabled and (IDENTITY_RE.search(m.filename)
                                         or IDENTITY_RE.search(m.node_type))}
                      | {t for t in node_types if IDENTITY_RE.search(t)})
    if not evidence:
        return None
    return Determination(
        subject="Performer likeness", kind="practice",
        licence="Not a licence question", verdict="conditions", confidence="high",
        reasons=[Reason(
            verdict="conditions", term="likeness", fact="real performers involved",
            text="This workflow performs identity or face work ("
                 + _join(evidence[:3]) + (", among others" if len(evidence) > 3 else "")
                 + ") and real performers are involved. No model licence grants "
                   "rights in a performer's face; that comes from their contract.",
            action="confirm",
            remedy="Confirm written consent covers synthetic reproduction, and check "
                   "the relevant union agreement - several now require separate "
                   "consent and payment for digital replicas.")])


# ----------------------------------------------------------------------------
# Rolling up
# ----------------------------------------------------------------------------


def _roll_up(result: ClearanceResult, profile: StudioProfile) -> None:
    """What the determinations add up to, said as work rather than as judgement.

    "Not usable" reads as a wall, and most of the time it is an afternoon: swap
    one model, buy one licence, confirm one thing with the vendor. The headline
    counts the jobs outstanding and names their shape, because that is what a
    supervisor is actually deciding about.
    """
    verdicts = [d.verdict for d in result.determinations]
    if not verdicts:
        result.verdict = "go"
        result.headline = "No models were found in this workflow."
        return

    result.verdict = min(verdicts, key=lambda v: VERDICT_RANK[v])

    for det in result.determinations:
        for reason in det.reasons:
            if reason.verdict == "no-go":
                result.blockers.append(f"{det.subject}: {reason.text}")
            elif reason.verdict == "conditions":
                result.conditions.append(f"{det.subject}: {reason.text}")
            elif reason.verdict == "unknown":
                result.unresolved.append(f"{det.subject}: {reason.text}")

    result.actions = _actions(result)
    total = sum(result.actions.values())
    shape = _describe_actions(result.actions)

    if result.verdict == "go":
        result.headline = (
            f"Every one of {len(verdicts)} item(s) checks out against this "
            "profile, with nothing outstanding.")
        return

    if not total:
        result.headline = (
            f"{len(verdicts)} item(s) assessed; nothing here is settled either "
            "way from what is known.")
        return

    jobs = "One thing" if total == 1 else f"{total} things"
    if result.verdict == "no-go":
        result.headline = (
            f"{jobs} to resolve before this goes on a paid job: {shape}. "
            "Each one names what lifts it.")
    elif result.verdict == "conditions":
        result.headline = (
            f"Nothing here blocks outright. {jobs} to see to before delivery: "
            f"{shape}.")
    else:
        result.headline = (
            f"{jobs} outstanding, mostly missing facts rather than "
            f"restrictions: {shape}.")


def _actions(result: ClearanceResult) -> dict[str, int]:
    """How many distinct jobs of each kind the workflow implies.

    Distinct by remedy, not by file: four weights failing one territory clause
    are one relocation to arrange, not four.
    """
    seen: dict[str, set[str]] = {}
    for det in result.determinations:
        for reason in det.reasons:
            if reason.verdict == "go" or not reason.action:
                continue
            seen.setdefault(reason.action, set()).add(reason.remedy)
    return {action: len(remedies) for action, remedies in seen.items()}


def _describe_actions(actions: dict[str, int]) -> str:
    """The outstanding jobs as a phrase: "a licence to buy and 2 confirmations"."""
    parts = []
    for action in ACTION_ORDER:
        count = actions.get(action, 0)
        if not count:
            continue
        # The singular labels carry their own article, so a leading "1" would
        # produce "1 a licence to buy".
        parts.append(ACTIONS[action] if count == 1
                     else f"{count} {_plural_action(action)}")
    return _join(parts) if parts else "nothing outstanding"


def _plural_action(action: str) -> str:
    """"2 a licence to buy" is not English."""
    return {
        "confirm": "confirmations",
        "information": "facts to find",
        "configure": "configuration changes",
        "attribute": "credits to add",
        "licence": "licences to buy",
        "relocate": "changes of where it runs",
        "swap": "models or packs to swap",
    }[action]


def _missing_facts(profile: StudioProfile, models: Iterable[ModelRef]) -> list[str]:
    """Name the facts that would let a determination be made at all."""
    wanted: list[str] = []
    conds = [m.license.conditions for m in models
             if m.enabled and m.license and m.license.conditions]

    if not profile.territory and any(c.get("territory_excluded") for c in conds):
        wanted.append("Territory - one or more licences here carve out regions by name.")
    if profile.revenue_floor is None and any(c.get("revenue_cap_usd") for c in conds):
        wanted.append("Revenue band - free use here is capped by company revenue.")
    if profile.ships == "unknown" and any(
            c.get("copyleft_reach") in ("integration", "network") for c in conds):
        wanted.append("What ships - copyleft only bites when something is distributed.")
    if any(c.get("no_competing_training") for c in conds):
        wanted.append("Whether outputs train other models - forbidden by at least one "
                      "licence here.")
    if not wanted:
        wanted.append("Territory and revenue band, at minimum.")
    return wanted


def _join(values: Iterable[str]) -> str:
    items = [v for v in values]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _usd(amount: int) -> str:
    if amount >= 1_000_000:
        return f"USD ${amount // 1_000_000}M"
    return f"USD ${amount:,}"


def _worst(reasons: list[Reason]) -> str:
    if not reasons:
        return "go"
    return min((r.verdict for r in reasons), key=lambda v: VERDICT_RANK[v])
