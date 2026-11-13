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
import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

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

#: What an entry can be about. ``api`` is a hosted model reached through a
#: ComfyUI API node, keyed by node type; ``workflow`` is an approved template.
KINDS = ("model", "pack", "api", "workflow")

#: Fields added after the first schema. Left out of the file when empty, so a
#: registry that does not use them reads the way it always did.
_OPTIONAL = ("provider", "version", "expires", "clients", "client_data", "retention",
             "training", "owner", "security_review", "production_version",
             "structure", "nodes", "models_allowed", "packs_allowed", "params",
             "qc_required")

#: Node types that carry no computation, left out of a workflow's structure.
_INERT = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "PrimitiveString",
          "PrimitiveInt", "PrimitiveFloat", "PrimitiveBoolean"}

#: How close a workflow has to be to a template to count as made from it.
TEMPLATE_SIMILARITY = 0.5


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
    #: For an API model: the service it runs on, and the model version cleared.
    #: For a workflow: the template's version.
    provider: str = ""
    version: str = ""
    #: The decision lapses on this date (ISO). Licences end; so should approvals.
    expires: str = ""
    #: Cleared only for these clients. Empty means any client.
    clients: list[str] = field(default_factory=list)
    #: API models: whether client material may be sent to the service
    #: ("allowed" / "not allowed"), how long the service keeps it, and whether
    #: it trains on it, as its terms say.
    client_data: str = ""
    retention: str = ""
    training: str = ""
    #: Who answers for this entry day to day.
    owner: str = ""
    #: Packs: when and how the code was security-reviewed, and the version
    #: production must run. Any other version is not the code that was reviewed.
    security_review: str = ""
    production_version: str = ""
    #: Workflow templates: the graph's structure (node types and wiring, not
    #: values), its node types, and what a shot made from it may use.
    structure: str = ""
    nodes: list[str] = field(default_factory=list)
    models_allowed: list[str] = field(default_factory=list)
    packs_allowed: list[str] = field(default_factory=list)
    #: Widget name -> [min, max] for numbers, or a list of allowed values.
    params: dict[str, list] = field(default_factory=dict)
    qc_required: bool = False

    @property
    def settled(self) -> bool:
        return self.status in SETTLED

    def expired(self, today: _dt.date | None = None) -> bool:
        try:
            return bool(self.expires) and \
                _dt.date.fromisoformat(self.expires[:10]) < (today or _dt.date.today())
        except ValueError:
            return False

    def allows_client(self, client: str) -> bool:
        """Cleared for this client. A restricted entry does not cover a shot with no
        client recorded: nobody can say it is one of the clients it was cleared for."""
        return not self.clients or _norm(client) in {_norm(c) for c in self.clients}

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        for k in _OPTIONAL:
            if not row.get(k):
                row.pop(k, None)
        return row


@dataclass
class Match:
    """How one thing in a workflow lines up against the registry."""

    subject: str
    kind: str = "model"
    #: new | known | changed | rejected | pending | expired | not-for-client
    state: str = "new"
    entry: Entry | None = None
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.state != "known"


@dataclass
class WorkflowMatch:
    """A workflow measured against the facility's approved templates."""

    #: approved | modified | unregistered | rejected | pending | expired | not-for-client
    state: str = "unregistered"
    template: str = ""
    version: str = ""
    similarity: float = 0.0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    qc_required: bool = False
    entry: Entry | None = None
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.state != "approved" or bool(self.violations)

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "template": self.template, "version": self.version,
                # A whole percentage: sealed into sign-offs, and a float would
                # not survive the browser's round trip byte for byte.
                "similarity": int(round(self.similarity * 100)), "added": self.added,
                "removed": self.removed, "violations": self.violations,
                "qc_required": self.qc_required, "detail": self.detail,
                "decided_by": self.entry.decided_by if self.entry else "",
                "decided_on": self.entry.decided_on if self.entry else ""}


@dataclass
class RegistryCheck:
    """A workflow measured against what the facility already decided."""

    loaded: bool = False
    path: str = ""
    total_entries: int = 0
    matches: list[Match] = field(default_factory=list)
    #: Set when the registry holds workflow templates.
    workflow: WorkflowMatch | None = None
    client: str = ""

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
    def expired(self) -> list[Match]:
        return [m for m in self.matches if m.state == "expired"]

    @property
    def wrong_client(self) -> list[Match]:
        return [m for m in self.matches if m.state == "not-for-client"]

    @property
    def known(self) -> list[Match]:
        return [m for m in self.matches if m.state == "known"]

    @property
    def attention(self) -> list[Match]:
        return [m for m in self.matches if m.needs_attention]

    @property
    def clean(self) -> bool:
        """Everything in this workflow has already been decided and settled."""
        return self.loaded and not any(m.needs_attention for m in self.matches) \
            and not (self.workflow and self.workflow.needs_attention)

    def problems(self) -> list[str]:
        """One line per thing that is not cleared, for gates and sign-off."""
        out = [f"{m.subject} ({m.kind}, {m.state}): {m.detail}" for m in self.attention]
        if self.workflow and self.workflow.needs_attention:
            w = self.workflow
            out.append(f"workflow ({w.state}): {w.detail}")
            out.extend(f"workflow: {v}" for v in w.violations)
        return out

    def headline(self) -> str:
        if not self.loaded:
            return ""
        flow = f" {self.workflow.detail}" if self.workflow else ""
        if not self.matches:
            return "Nothing in this workflow to check against the registry." + flow
        if not self.attention:
            return (f"Everything here has been cleared before - all "
                    f"{len(self.matches)} item(s) are already in the registry." + flow)
        parts = []
        for items, text in ((self.new, "not seen before"),
                            (self.changed, "changed since they were cleared"),
                            (self.rejected, "previously rejected"),
                            (self.pending, "still awaiting an answer"),
                            (self.expired, "cleared, but the approval has lapsed"),
                            (self.wrong_client, "not cleared for this client")):
            if items:
                parts.append(f"{len(items)} {text}")
        return (f"{len(self.attention)} of {len(self.matches)} item(s) need attention: "
                + ", ".join(parts) + "." + flow)

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
                "expired": len(self.expired), "not-for-client": len(self.wrong_client),
                "known": len(self.known),
            },
            "client": self.client,
            "matches": [
                {"subject": m.subject, "kind": m.kind, "state": m.state,
                 "detail": m.detail,
                 "entry": m.entry.as_row() if m.entry else None}
                for m in self.matches if m.needs_attention
            ],
            "workflow": self.workflow.as_dict() if self.workflow else None,
        }

    def sealed(self) -> dict[str, Any]:
        """What a sign-off records: the verdict on each item, not the whole file."""
        return {"path": os.path.basename(self.path), "clean": self.clean,
                "headline": self.headline(), "client": self.client,
                "items": [{"subject": m.subject, "kind": m.kind, "state": m.state,
                           "detail": m.detail} for m in self.matches],
                "workflow": self.workflow.as_dict() if self.workflow else None}


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
        entries = [Entry(**_only_known(row)) for row in data.get("entries", [])
                   if isinstance(row, dict)]
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
            "entries": [e.as_row() for e in self.sorted_entries()],
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
        if entry.kind not in KINDS:
            raise ValueError(f"unknown kind {entry.kind!r}; expected one of {', '.join(KINDS)}")
        if entry.expires:
            try:
                _dt.date.fromisoformat(entry.expires[:10])
            except ValueError:
                raise ValueError("expires is a date, YYYY-MM-DD") from None
        entry.decided_on = entry.decided_on or _today()
        self._entries[(entry.kind, _norm(entry.key))] = entry
        if entry.sha256:
            self._by_hash[entry.sha256.lower()] = entry
        return entry

    def remove(self, key: str, kind: str = "model") -> bool:
        return self._entries.pop((kind, _norm(key)), None) is not None

    # -- the question a facility actually asks ------------------------------

    def templates(self) -> list[Entry]:
        return [e for e in self.sorted_entries() if e.kind == "workflow"]

    def check(self, models: Iterable[ModelRef],
              packs: Iterable[PackRef] = (), api_nodes: Iterable[str] = (), *,
              client: str = "", today: _dt.date | None = None,
              hash_of: Callable[[ModelRef], str] | None = None,
              workflow: Any = None) -> RegistryCheck:
        """What in this workflow has not already been dealt with?

        ``client`` is the shot's client, for entries cleared only for some;
        ``hash_of`` fingerprints a model file the audit did not hash, so a
        renamed or retrained file is still caught; ``workflow`` is the parsed
        graph, checked against the approved templates when there are any.
        """
        models = list(models)
        packs = list(packs)
        result = RegistryCheck(loaded=True, path=self.path,
                              total_entries=len(self._entries), client=client)
        ctx = {"client": client, "today": today or _dt.date.today()}
        for model in models:
            if not model.enabled:
                continue
            sha = _model_hash(model)
            if not sha and hash_of is not None:
                try:
                    sha = hash_of(model) or ""
                except Exception:  # noqa: BLE001 - a hash is a bonus, not a need
                    sha = ""
            result.matches.append(self._match_model(model, sha, ctx))
        for pack in packs:
            if pack.identified and pack.repo:
                result.matches.append(self._match_pack(pack, ctx))
        for node_type in sorted(set(api_nodes)):
            result.matches.append(self._match_api(node_type, ctx))
        result.matches.sort(key=lambda m: (_STATE_ORDER.get(m.state, 9),
                                           m.subject.lower()))
        if workflow is not None and self.templates():
            result.workflow = self.check_workflow(workflow, models, packs, ctx)
        return result

    def _lapsed(self, entry: Entry, subject: str, kind: str, ctx: dict) -> Match | None:
        """A settled entry that no longer covers this use: lapsed, or another client's."""
        if entry.expired(ctx["today"]):
            return Match(subject=subject, kind=kind, state="expired", entry=entry,
                         detail=f"Cleared{_who(entry)}, but the approval lapsed on "
                                f"{entry.expires}. Renew it or choose another.")
        if not entry.allows_client(ctx["client"]):
            return Match(subject=subject, kind=kind, state="not-for-client", entry=entry,
                         detail=f"Cleared only for {', '.join(entry.clients)}; "
                                + (f"this shot's client is {ctx['client']}." if ctx["client"]
                                   else "this shot has no client recorded."))
        return None

    def _unsettled(self, entry: Entry, subject: str, kind: str) -> Match | None:
        if entry.status == "rejected":
            return Match(subject=subject, kind=kind, state="rejected", entry=entry,
                         detail="Recorded as not to be used"
                                + (f": {entry.note}" if entry.note else "")
                                + _who(entry))
        if entry.status == "pending":
            return Match(subject=subject, kind=kind, state="pending", entry=entry,
                         detail="Raised before and still awaiting an answer"
                                + (f": {entry.note}" if entry.note else "")
                                + _who(entry))
        return None

    def _match_api(self, node_type: str, ctx: dict) -> Match:
        entry = self.get(node_type, "api")
        if entry is None:
            return Match(subject=node_type, kind="api", state="new",
                         detail="A hosted model nobody has cleared. Work sent through "
                                "this node leaves the building; record the provider's "
                                "terms on client data, retention and training.")
        early = self._unsettled(entry, node_type, "api") or \
            self._lapsed(entry, node_type, "api", ctx)
        if early:
            return early
        if ctx["client"] and entry.client_data.lower() in ("not allowed", "no"):
            return Match(subject=node_type, kind="api", state="not-for-client", entry=entry,
                         detail="Cleared, but not for client material: the provider's terms "
                                f"were recorded as client data '{entry.client_data}', and "
                                f"this shot is for {ctx['client']}.")
        terms = ", ".join(x for x in (
            entry.provider and f"via {entry.provider}",
            entry.version and f"version {entry.version}",
            entry.client_data and f"client data {entry.client_data}",
            entry.retention and f"retention {entry.retention}",
            entry.training and f"training {entry.training}") if x)
        return Match(subject=node_type, kind="api", state="known", entry=entry,
                     detail=f"{STATUSES[entry.status].capitalize()}" + _who(entry)
                            + (f" ({terms})" if terms else ""))

    def _match_model(self, model: ModelRef, sha: str = "", ctx: dict | None = None) -> Match:
        ctx = ctx or {"client": "", "today": _dt.date.today()}
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

        lapsed = self._lapsed(entry, model.filename, "model", ctx)
        if lapsed:
            return lapsed

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

    def _match_pack(self, pack: PackRef, ctx: dict | None = None) -> Match:
        ctx = ctx or {"client": "", "today": _dt.date.today()}
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
        lapsed = self._lapsed(entry, name, "pack", ctx)
        if lapsed:
            return lapsed
        if entry.licence and pack.licence and entry.licence != pack.licence:
            return Match(
                subject=name, kind="pack", state="changed", entry=entry,
                detail=f"Cleared under {entry.licence}; the repository now says "
                       f"{pack.licence}.")
        running = (pack.pinned_version or "").lstrip("v")
        wanted = entry.production_version.lstrip("v")
        if wanted and running and running != wanted:
            return Match(
                subject=name, kind="pack", state="changed", entry=entry,
                detail=f"Production is cleared to run version {entry.production_version}"
                       + (f" (security review: {entry.security_review})"
                          if entry.security_review else "")
                       + f"; this workflow uses {pack.pinned_version}. That is not the "
                         "code that was reviewed.")
        return Match(subject=name, kind="pack", state="known", entry=entry,
                     detail=f"{STATUSES[entry.status].capitalize()}" + _who(entry)
                            + (f", version {entry.production_version}"
                               if entry.production_version else ""))

    # -- workflow templates -------------------------------------------------

    def check_workflow(self, workflow: Any, models: Iterable[ModelRef] = (),
                       packs: Iterable[PackRef] = (), ctx: dict | None = None) -> WorkflowMatch:
        """Which approved template this workflow is, and how far it has moved from it."""
        ctx = ctx or {"client": "", "today": _dt.date.today()}
        fp, types = structure(workflow)
        templates = self.templates()
        exact = next((t for t in templates if t.structure and t.structure == fp), None)
        best, score = exact, 1.0 if exact else 0.0
        if best is None:
            for t in templates:
                sim = _similarity(types, t.nodes)
                if sim > score:
                    best, score = t, sim
            if best is not None and score < TEMPLATE_SIMILARITY:
                best = None
        if best is None:
            return WorkflowMatch(state="unregistered", similarity=score,
                                 detail="It is not one of the facility's approved workflows, "
                                        "nor close to one.")
        m = WorkflowMatch(template=best.key, version=best.version, similarity=score,
                          qc_required=best.qc_required, entry=best)
        have, want = Counter(types), Counter(best.nodes)
        m.added = sorted((have - want).elements())
        m.removed = sorted((want - have).elements())
        early = self._unsettled(best, best.key, "workflow") or \
            self._lapsed(best, best.key, "workflow", ctx)
        if early:
            m.state, m.detail = early.state, f"Closest template, {best.key}: {early.detail}"
        elif exact:
            m.state = "approved"
            m.detail = (f"It is the approved workflow {best.key}"
                        + (f" v{best.version}" if best.version else "")
                        + ", unmodified" + _who(best) + ".")
        else:
            m.state = "modified"
            bits = []
            if m.added:
                bits.append("added " + ", ".join(_counted(m.added)))
            if m.removed:
                bits.append("removed " + ", ".join(_counted(m.removed)))
            m.detail = (f"Made from the approved workflow {best.key}"
                        + (f" v{best.version}" if best.version else "")
                        + ", but changed since: "
                        + ("; ".join(bits) if bits else "rewired") + ".")
        m.violations = _template_violations(best, workflow, models, packs)
        return m



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


_STATE_ORDER = {"rejected": 0, "expired": 1, "not-for-client": 1, "changed": 2,
                "pending": 3, "new": 4, "known": 5}


def structure(workflow: Any) -> tuple[str, list[str]]:
    """A workflow's shape: which nodes, and how they are wired, but not their values.

    Two copies of a template share a structure whatever prompts, seeds or
    files were typed into them, and whatever their node IDs are, so a shot can
    be traced back to the template it was made from. Returns the fingerprint
    and the node types it covers.
    """
    nodes = [n for n in workflow if n.type not in _INERT]
    ids = {n.id for n in nodes}
    types = sorted(n.type for n in nodes if n.enabled)
    edges = []
    for n in nodes:
        for slot in n.linked_inputs():
            src = workflow.nodes.get(slot.source_node) if slot.source_node in ids else None
            if src is not None:
                edges.append([src.type, slot.source_slot, n.type, slot.name])
    body = {"nodes": sorted([n.type, bool(n.enabled)] for n in nodes),
            "edges": sorted(edges, key=lambda e: [str(x) for x in e])}
    return (hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"))
                           .encode("utf-8")).hexdigest(), types)


def workflow_entry(workflow: Any, models: Iterable[ModelRef] = (),
                   packs: Iterable[PackRef] = (), *, name: str, version: str = "",
                   **fields: Any) -> Entry:
    """Draft a template entry for a workflow as it stands: its structure, and
    the models and packs it uses as the ones a shot made from it may use."""
    fp, types = structure(workflow)
    entry = Entry(key=name, kind="workflow", version=version, structure=fp, nodes=types,
                  models_allowed=sorted({m.filename for m in models if m.enabled}),
                  packs_allowed=sorted({p.repo for p in packs if p.identified and p.repo}))
    for k, v in fields.items():
        if v not in (None, "") and k in Entry.__dataclass_fields__:
            setattr(entry, k, v)
    return entry


def _similarity(a: list[str], b: list[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    union = sum((ca | cb).values())
    return sum((ca & cb).values()) / union if union else 0.0


def _counted(types: list[str]) -> list[str]:
    return [f"{t} x{n}" if n > 1 else t for t, n in sorted(Counter(types).items())]


def _base(name: str) -> str:
    return _norm(name).rsplit("/", 1)[-1]


def _template_violations(t: Entry, workflow: Any, models: Iterable[ModelRef],
                         packs: Iterable[PackRef]) -> list[str]:
    """What a shot uses that its template does not allow."""
    out: list[str] = []
    if t.models_allowed:
        allowed = {_base(x) for x in t.models_allowed}
        for m in models:
            if m.enabled and _base(m.filename) not in allowed:
                out.append(f"{m.filename} is not one of the models {t.key} allows.")
    if t.packs_allowed:
        allowed = {_norm(x) for x in t.packs_allowed}
        for p in packs:
            if p.identified and p.repo and _norm(p.repo) not in allowed:
                out.append(f"Node pack {p.title or p.repo} is not one {t.key} allows.")
    for widget, rule in (t.params or {}).items():
        if not isinstance(rule, list) or not rule:
            continue
        numeric = len(rule) == 2 and all(isinstance(x, (int, float))
                                         and not isinstance(x, bool) for x in rule)
        for n in workflow:
            if not n.enabled or widget not in n.widgets:
                continue
            v = n.widgets[widget]
            if numeric:
                if isinstance(v, (int, float)) and not isinstance(v, bool) \
                        and not rule[0] <= v <= rule[1]:
                    out.append(f"{n.label} {widget} is {v}; {t.key} allows "
                               f"{rule[0]} to {rule[1]}.")
            elif v not in rule:
                out.append(f"{n.label} {widget} is {v!r}; {t.key} allows "
                           + ", ".join(repr(x) for x in rule) + ".")
    return out


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
