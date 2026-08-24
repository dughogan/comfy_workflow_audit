"""Summarise what the licences in a workflow actually say.

This deliberately does not decide anything. Whether a non-commercial model is
acceptable depends on the job, the client, the jurisdiction and the facility's
own agreements, none of which a workflow file knows about. What the report can
do is lay out the terms, name the source for each one, and be clear about how
confident it is - so the person who does make the decision has something to
make it from.

So: no verdict, no pass or fail, no score. A composition, the obligations that
came with it, and a link for every claim.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from ..records import ModelRef

#: Plain-language names for the positions a licence can take. These describe the
#: licence, not the workflow: "non-commercial" means the terms say so, not that
#: the reader is doing anything wrong.
POSITIONS = {
    "yes": "permissive",
    "conditional": "conditional",
    "no": "non-commercial",
    "unknown": "unstated",
}

POSITION_ORDER = ["permissive", "conditional", "non-commercial", "unstated"]

FEE_LABELS = {
    "none": "no fee",
    "revenue-threshold": "free below a revenue threshold",
    "paid": "a licence must be obtained",
    "unknown": "fee terms unstated",
}


@dataclass
class LicenceGroup:
    """One licence, and every model in the workflow that carries it."""

    licence: str = "Unknown"
    position: str = "unstated"
    commercial_use: str = "unknown"
    fee: str = "unknown"
    summary: str = ""
    restrictions: list[str] = field(default_factory=list)
    url: str = ""
    confidence: str = "low"
    models: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.models)


@dataclass
class LicenceSummary:
    """The licence composition of a workflow, as information."""

    headline: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    groups: list[LicenceGroup] = field(default_factory=list)
    obligations: list[str] = field(default_factory=list)
    to_verify: list[str] = field(default_factory=list)
    hosted_api_types: list[str] = field(default_factory=list)
    total_models: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "counts": self.counts,
            "total_models": self.total_models,
            "groups": [asdict(g) for g in self.groups],
            "obligations": self.obligations,
            "to_verify": self.to_verify,
            "hosted_api_types": self.hosted_api_types,
        }

    def position_count(self, position: str) -> int:
        return self.counts.get(position, 0)


def summarise(models: Iterable[ModelRef],
              api_node_types: Iterable[str] = ()) -> LicenceSummary:
    """Group a workflow's models by the licence each one carries."""
    enabled = [m for m in models if m.enabled]
    result = LicenceSummary(total_models=len(enabled),
                            hosted_api_types=sorted(api_node_types))

    grouped: dict[str, LicenceGroup] = {}
    for model in enabled:
        lic = model.license
        name = lic.name if lic else "Unknown"
        position = POSITIONS.get(lic.commercial_use if lic else "unknown", "unstated")

        group = grouped.get(name)
        if group is None:
            group = LicenceGroup(
                licence=name,
                position=position,
                commercial_use=lic.commercial_use if lic else "unknown",
                fee=lic.fee if lic else "unknown",
                summary=lic.summary if lic else "",
                restrictions=list(lic.restrictions) if lic else [],
                url=lic.url if lic else "",
                confidence=lic.confidence if lic else "low",
            )
            grouped[name] = group
        group.models.append(model.filename)
        # Within a group, report the weakest confidence: one shaky match makes
        # the whole grouping worth a second look.
        if _rank_confidence(model.license) < _rank_confidence_value(group.confidence):
            group.confidence = model.license.confidence if model.license else "low"

    result.groups = sorted(
        grouped.values(),
        key=lambda g: (POSITION_ORDER.index(g.position), -g.count, g.licence.lower()),
    )
    for group in result.groups:
        result.counts[group.position] = result.counts.get(group.position, 0) + group.count

    result.headline = _headline(result)
    result.obligations = _obligations(enabled)
    result.to_verify = _to_verify(enabled)
    return result


def _headline(summary: LicenceSummary) -> str:
    if not summary.total_models:
        return "No models were found in this workflow."

    parts = [f"{summary.counts[p]} {p}" for p in POSITION_ORDER
             if summary.counts.get(p)]
    licences = len(summary.groups)
    return (f"{summary.total_models} model(s) across {licences} licence(s): "
            + ", ".join(parts) + ".")


def _obligations(models: list[ModelRef]) -> list[str]:
    """Things a licence asks of you that are easy to miss at delivery."""
    out: list[str] = []

    attribution = sorted({m.license.name for m in models
                          if m.license and m.license.attribution_required})
    if attribution:
        out.append("Attribution or a notice is required by: " + ", ".join(attribution))

    share_alike = sorted({m.license.name for m in models if m.license and any(
        "same licence" in r.lower() or "same license" in r.lower()
        or "share" in r.lower() and "alike" in r.lower()
        for r in m.license.restrictions)})
    if share_alike:
        out.append("Derivatives must carry the same licence under: "
                   + ", ".join(share_alike))

    thresholds = sorted({m.license.name for m in models
                         if m.license and m.license.fee == "revenue-threshold"})
    if thresholds:
        out.append("Free use is capped by a revenue threshold under: "
                   + ", ".join(thresholds))

    paid = sorted({m.license.name for m in models
                   if m.license and m.license.fee == "paid"})
    if paid:
        out.append("A separate licence must be obtained from the rights holder for: "
                   + ", ".join(paid))

    territory = sorted({m.license.name for m in models if m.license and any(
        "territor" in r.lower() or "excluding the european union" in r.lower()
        for r in m.license.restrictions)})
    if territory:
        out.append("Territorial limits apply to: " + ", ".join(territory))

    return out


def _to_verify(models: list[ModelRef]) -> list[str]:
    """Models whose licence the reader should confirm rather than take on trust."""
    out: list[str] = []
    for model in models:
        lic = model.license
        if lic is None:
            continue
        if lic.commercial_use == "unknown":
            out.append(f"{model.filename} - no licence could be identified"
                       + (f" ({model.provenance.url})" if model.provenance
                          and model.provenance.url else ""))
        elif lic.confidence == "low":
            reason = ("sources disagree about this file"
                      if any("Sources disagree" in r for r in lic.restrictions)
                      else f"matched on {lic.matched_on or 'a weak signal'}")
            out.append(f"{model.filename} - {lic.name}, low confidence ({reason})")
    return out


def _rank_confidence(licence: Any) -> int:
    return _rank_confidence_value(licence.confidence if licence else "low")


def _rank_confidence_value(value: str) -> int:
    return {"high": 2, "medium": 1}.get(value, 0)


def describe_fee(fee: str) -> str:
    return FEE_LABELS.get(fee, fee or "unstated")
