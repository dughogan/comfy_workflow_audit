"""What the facility has already cleared, and what is new since.

A studio does not have one workflow. It has hundreds, and the same five
checkpoints recur across all of them. Auditing each one in isolation re-derives
the same findings forever, and by the second week the report is mostly noise:
the reader already knows about the CodeFormer licence, they cleared it in March.

So the registry records decisions. Once a model has been looked at and signed
off - or explicitly rejected, or parked pending a question - that decision is
kept, and every later audit answers a different and much shorter question: what
is in this workflow that we have not already dealt with?

The file is plain JSON, meant to live in the show or the facility's config repo
next to everything else that gets reviewed. It is a record of what people
decided, not a cache: nothing here is written automatically, because a decision
nobody made is not a decision.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .records import ModelRef, PackRef

SCHEMA = "comfyaudit/registry/1"

#: What a facility can have decided about a model or a pack.
STATUSES = {
    "approved": "cleared for use",
    "approved-with-conditions": "cleared, with something to observe",
    "rejected": "not to be used",
    "pending": "raised, awaiting an answer",
}

#: Statuses that let a workflow proceed without re-raising the entry.
SETTLED = ("approved", "approved-with-conditions")


@dataclass
class Entry:
    """One decision about one model or node pack."""

    key: str                       # filename, or repo for a pack
    kind: str = "model"            # model | pack
    status: str = "approved"
    #: Who made the call. A decision with no name on it is hard to revisit.
    decided_by: str = ""
    decided_on: str = ""           # ISO date
    note: str = ""
    #: The licence understood at the time. If a later audit reads a different
    #: one, the file is not what it was when the decision was made.
    licence: str = ""
    #: SHA-256 where it was recorded, which is the only identifier a rename
    #: cannot break.
    sha256: str = ""
    #: The studio profile in force when this was decided, as a description.
    profile: str = ""
    #: Free-form: a ticket, a contract reference, an email.
    reference: str = ""

    @property
    def settled(self) -> bool:
        return self.status in SETTLED


@dataclass
class Match:
    """How one thing in a workflow lines up against the registry."""

    subject: str
    kind: str = "model"
    state: str = "new"             # new | known | changed | rejected | pending
    entry: Entry | None = None
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.state != "known"


@dataclass
class RegistryCheck:
    """A workflow measured against what the facility already decided."""

    loaded: bool = False
    path: str = ""
    total_entries: int = 0
    matches: list[Match] = field(default_factory=list)

    @property
    def new(self) -> list[Match]:
        return [m for m in self.matches if m.state == "new"]

    @property
    def changed(self) -> list[Match]:
        return [m for m in self.matches if m.state == "changed"]

    @property
    def rejected(self) -> list[Match]:
        return [m for m in self.matches if m.state == "rejected"]

    @property
    def pending(self) -> list[Match]:
        return [m for m in self.matches if m.state == "pending"]

    @property
    def known(self) -> list[Match]:
        return [m for m in self.matches if m.state == "known"]

    @property
    def clean(self) -> bool:
        """Everything in this workflow has already been decided and settled."""
        return self.loaded and not any(m.needs_attention for m in self.matches)

    def headline(self) -> str:
        if not self.loaded:
            return ""
        if not self.matches:
            return "Nothing in this workflow to check against the registry."
        if self.clean:
            return (f"Everything here has been cleared before - all "
                    f"{len(self.matches)} item(s) are already in the registry.")
        parts = []
        if self.new:
            parts.append(f"{len(self.new)} not seen before")
        if self.changed:
            parts.append(f"{len(self.changed)} changed since they were cleared")
        if self.rejected:
            parts.append(f"{len(self.rejected)} previously rejected")
        if self.pending:
            parts.append(f"{len(self.pending)} still awaiting an answer")
        return (f"{sum(len(p) for p in [self.new, self.changed, self.rejected, self.pending])}"
                f" of {len(self.matches)} item(s) need attention: "
                + ", ".join(parts) + ".")

    def as_dict(self) -> dict[str, Any]:
        return {
            "loaded": self.loaded,
            "path": self.path,
            "total_entries": self.total_entries,
            "headline": self.headline(),
            "clean": self.clean,
            "counts": {
                "new": len(self.new), "changed": len(self.changed),
                "rejected": len(self.rejected), "pending": len(self.pending),
                "known": len(self.known),
            },
            "matches": [
                {"subject": m.subject, "kind": m.kind, "state": m.state,
                 "detail": m.detail,
                 "entry": asdict(m.entry) if m.entry else None}
                for m in self.matches if m.needs_attention
            ],
        }


class Registry:
    """The facility's decision record."""

    def __init__(self, entries: Iterable[Entry] = (), path: str = "",
                 meta: dict[str, Any] | None = None) -> None:
        self.path = path
        self.meta = dict(meta or {})
        self._entries: dict[tuple[str, str], Entry] = {}
        for entry in entries:
            self._entries[(entry.kind, _norm(entry.key))] = entry
        #: A second index, because a rename breaks the filename but not the hash.
        self._by_hash: dict[str, Entry] = {
            e.sha256.lower(): e for e in self._entries.values() if e.sha256
        }

    # -- loading and saving -------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "Registry":
        """Read a registry file. A missing path yields an empty registry."""
        if not path or not os.path.isfile(path):
            return cls(path=path)
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a registry object")
        entries = [Entry(**_only_known(row)) for row in data.get("entries", [])]
        meta = {k: v for k, v in data.items() if k != "entries"}
        return cls(entries, path=path, meta=meta)

    def save(self, path: str = "") -> str:
        target = path or self.path
        if not target:
            raise ValueError("no path to save the registry to")
        payload = {
            "schema": SCHEMA,
            "updated": _today(),
            **{k: v for k, v in self.meta.items()
               if k not in ("schema", "updated", "entries")},
            "entries": [asdict(e) for e in self.sorted_entries()],
        }
        directory = os.path.dirname(os.path.abspath(target))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        self.path = target
        return target

    # -- contents -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def sorted_entries(self) -> list[Entry]:
        return sorted(self._entries.values(), key=lambda e: (e.kind, e.key.lower()))

    def get(self, key: str, kind: str = "model", sha256: str = "") -> Entry | None:
        """Find an entry by name, or by hash when the name has changed."""
        entry = self._entries.get((kind, _norm(key)))
        if entry is None and sha256:
            entry = self._by_hash.get(sha256.lower())
        return entry

    def record(self, entry: Entry) -> Entry:
        """Add or replace a decision."""
        if entry.status not in STATUSES:
            raise ValueError(f"unknown status {entry.status!r}; "
                             f"expected one of {', '.join(sorted(STATUSES))}")
        entry.decided_on = entry.decided_on or _today()
        self._entries[(entry.kind, _norm(entry.key))] = entry
        if entry.sha256:
            self._by_hash[entry.sha256.lower()] = entry
        return entry

    def remove(self, key: str, kind: str = "model") -> bool:
        return self._entries.pop((kind, _norm(key)), None) is not None

    # -- the question a facility actually asks ------------------------------

    def check(self, models: Iterable[ModelRef],
              packs: Iterable[PackRef] = ()) -> RegistryCheck:
        """What in this workflow has not already been dealt with?"""
        result = RegistryCheck(loaded=True, path=self.path,
                              total_entries=len(self._entries))
        for model in models:
            if not model.enabled:
                continue
            result.matches.append(self._match_model(model))
        for pack in packs:
            if pack.identified and pack.repo:
                result.matches.append(self._match_pack(pack))
        result.matches.sort(key=lambda m: (_STATE_ORDER.get(m.state, 9),
                                           m.subject.lower()))
        return result

    def _match_model(self, model: ModelRef) -> Match:
        sha = _model_hash(model)
        entry = self.get(model.filename, "model", sha)
        if entry is None:
            return Match(subject=model.filename, kind="model", state="new",
                         detail="Not in the registry - nobody has decided about "
                                "this file yet.")

        if entry.status == "rejected":
            return Match(subject=model.filename, kind="model", state="rejected",
                         entry=entry,
                         detail=f"Recorded as not to be used"
                                + (f": {entry.note}" if entry.note else "")
                                + _who(entry))
        if entry.status == "pending":
            return Match(subject=model.filename, kind="model", state="pending",
                         entry=entry,
                         detail="Raised before and still awaiting an answer"
                                + (f": {entry.note}" if entry.note else "")
                                + _who(entry))

        # Settled - but settled about what? A file whose hash or licence has
        # moved is not the file the decision was made about.
        if entry.sha256 and sha and entry.sha256.lower() != sha.lower():
            return Match(
                subject=model.filename, kind="model", state="changed", entry=entry,
                detail="The file on disk does not match the one that was cleared "
                       f"({entry.sha256[:12]}... on {entry.decided_on}). Same name, "
                       "different weights.")

        current = model.license.name if model.license else ""
        if entry.licence and current and entry.licence != current:
            return Match(
                subject=model.filename, kind="model", state="changed", entry=entry,
                detail=f"Cleared under {entry.licence}; this audit reads it as "
                       f"{current}. Either the licence changed or the file did.")

        return Match(subject=model.filename, kind="model", state="known", entry=entry,
                     detail=f"{STATUSES[entry.status].capitalize()}"
                            + _who(entry)
                            + (f". {entry.note}" if entry.note else ""))

    def _match_pack(self, pack: PackRef) -> Match:
        entry = self.get(pack.repo, "pack")
        name = pack.title or pack.repo
        if entry is None:
            return Match(subject=name, kind="pack", state="new",
                         detail="Not in the registry - this pack has not been "
                                "reviewed.")
        if entry.status == "rejected":
            return Match(subject=name, kind="pack", state="rejected", entry=entry,
                         detail="Recorded as not to be used"
                                + (f": {entry.note}" if entry.note else "")
                                + _who(entry))
        if entry.status == "pending":
            return Match(subject=name, kind="pack", state="pending", entry=entry,
                         detail="Raised before and still awaiting an answer"
                                + _who(entry))
        if entry.licence and pack.licence and entry.licence != pack.licence:
            return Match(
                subject=name, kind="pack", state="changed", entry=entry,
                detail=f"Cleared under {entry.licence}; the repository now says "
                       f"{pack.licence}.")
        return Match(subject=name, kind="pack", state="known", entry=entry,
                     detail=f"{STATUSES[entry.status].capitalize()}" + _who(entry))


# ----------------------------------------------------------------------------
# Turning a report into proposed entries
# ----------------------------------------------------------------------------


def entries_from_report(report: Any, *, status: str = "approved",
                        decided_by: str = "", reference: str = "",
                        only_new: bool = True) -> list[Entry]:
    """Draft registry entries for what a workflow uses.

    The caller decides whether to keep them. This never writes: a decision
    nobody made is not a decision, and a registry that fills itself in is just
    an expensive way of approving everything.
    """
    existing = report.registry if getattr(report, "registry", None) else None
    seen: set[str] = set()
    if only_new and existing and existing.loaded:
        seen = {m.subject for m in existing.matches if m.state == "known"}

    profile = ""
    clearance = getattr(report, "clearance", None)
    if clearance is not None and clearance.determined:
        profile = clearance.profile.describe()

    out: list[Entry] = []
    for model in report.models:
        if not model.enabled or model.filename in seen:
            continue
        out.append(Entry(
            key=model.filename, kind="model", status=status,
            decided_by=decided_by, reference=reference, profile=profile,
            licence=model.license.name if model.license else "",
            sha256=_model_hash(model),
        ))
    for pack in report.packs:
        if not pack.identified or not pack.repo:
            continue
        if (pack.title or pack.repo) in seen:
            continue
        out.append(Entry(
            key=pack.repo, kind="pack", status=status, decided_by=decided_by,
            reference=reference, profile=profile, licence=pack.licence,
        ))
    return out


# ----------------------------------------------------------------------------


_STATE_ORDER = {"rejected": 0, "changed": 1, "pending": 2, "new": 3, "known": 4}


def _model_hash(model: ModelRef) -> str:
    """The SHA-256 the audit recorded, if hashing was on."""
    for note in getattr(model, "notes", []) or []:
        if note.lower().startswith("sha256:"):
            return note.split(":", 1)[1].strip()
    provenance = getattr(model, "provenance", None)
    if provenance is not None and getattr(provenance, "resolved_by", "") == "sha256":
        return provenance.identifier
    return ""


def _norm(key: str) -> str:
    """Compare keys the way a filesystem would, not the way a byte string does."""
    return (key or "").strip().replace("\\", "/").lower()


def _who(entry: Entry) -> str:
    bits = []
    if entry.decided_by:
        bits.append(f"by {entry.decided_by}")
    if entry.decided_on:
        bits.append(f"on {entry.decided_on}")
    return (" " + " ".join(bits)) if bits else ""


def _only_known(row: dict[str, Any]) -> dict[str, Any]:
    """Ignore fields a future version added, rather than refusing to load."""
    fields = set(Entry.__dataclass_fields__)
    return {k: v for k, v in row.items() if k in fields}


def _today() -> str:
    return _dt.date.today().isoformat()
