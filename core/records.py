"""Value objects shared by the extractors, scorers and reporters."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


def _clean(obj: Any) -> Any:
    """Recursively drop empty values so JSON output stays readable."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if v not in (None, "", [], {}, ())}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


@dataclass
class Provenance:
    """Where a weight came from, and how sure we are."""

    source: str = "unknown"          # huggingface | civitai | github | comfy-manager | unknown
    identifier: str = ""             # repo id, model version id, ...
    url: str = ""
    author: str = ""
    downloads: int | None = None
    likes: int | None = None
    last_modified: str = ""
    gated: bool = False              # HF repos behind an access agreement
    resolved_by: str = ""            # "bundled-index" | "huggingface-api" | ...
    confidence: str = "low"          # low | medium | high
    notes: list[str] = field(default_factory=list)


@dataclass
class LicenseInfo:
    """A licence assessment for one model."""

    name: str = "Unknown"
    spdx: str = ""
    commercial_use: str = "unknown"  # yes | conditional | no | unknown
    fee: str = "unknown"             # none | revenue-threshold | paid | unknown
    redistribution: str = "unknown"  # yes | conditional | no | unknown
    output_ownership: str = "unknown"
    attribution_required: bool | None = None
    restrictions: list[str] = field(default_factory=list)
    url: str = ""
    matched_on: str = ""             # what in the filename/repo triggered the match
    confidence: str = "low"
    summary: str = ""
    #: The terms that depend on who is asking, in a form a verdict can be
    #: derived from: territory carve-outs, revenue caps, how far copyleft
    #: reaches, whether the restriction covers outputs. See
    #: ``core/score/clearance.py`` for the keys and what each one means.
    conditions: dict = field(default_factory=dict)

    @property
    def blocks_commercial(self) -> bool:
        return self.commercial_use == "no"


@dataclass
class ModelRef:
    """One weight file referenced by the workflow."""

    filename: str
    folder: str = "unknown"
    role: str = "Model"
    node_id: str = ""
    node_type: str = ""
    node_label: str = ""
    widget: str = ""
    enabled: bool = True
    strength: float | None = None
    repo_id: str = ""                # when the widget names a HF repo rather than a file
    confidence: str = "high"
    provenance: Provenance | None = None
    license: LicenseInfo | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.folder}/{self.filename}".lower()


@dataclass
class PromptRef:
    """A text prompt, with the polarity inferred from sampler wiring."""

    text: str
    polarity: str = "unknown"        # positive | negative | both | unknown
    node_id: str = ""
    node_type: str = ""
    node_label: str = ""
    widget: str = ""
    enabled: bool = True
    driven_by_link: bool = False     # text arrives from upstream, not typed here
    consumers: list[str] = field(default_factory=list)
    embeddings: list[str] = field(default_factory=list)
    inline_loras: list[str] = field(default_factory=list)
    wildcards: list[str] = field(default_factory=list)
    dynamic_syntax: bool = False     # {a|b} style randomisation
    char_count: int = 0
    token_estimate: int = 0


@dataclass
class AssetRef:
    """External media or file path the workflow needs at run time."""

    value: str
    kind: str = "file"               # image | video | audio | file | url | directory
    node_id: str = ""
    node_type: str = ""
    node_label: str = ""
    widget: str = ""
    enabled: bool = True
    upload_widget: bool = False      # a UI slot a human drops a file onto
    absolute_path: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class PackRef:
    """A custom node pack the workflow depends on."""

    repo: str = ""
    title: str = ""
    author: str = ""
    reference: str = ""
    install_type: str = ""
    description: str = ""
    node_types: list[str] = field(default_factory=list)
    node_count: int = 0
    registry_id: str = ""            # properties.cnr_id written by the frontend
    pinned_version: str = ""         # properties.ver
    aux_id: str = ""                 # properties.aux_id (owner/repo)
    stars: int | None = None
    last_update: str = ""
    #: SPDX id resolved from the pack's repository. A node pack's licence reaches
    #: further than a model's: its code runs inside the studio's own process.
    licence: str = ""
    licence_url: str = ""
    pip: list[str] = field(default_factory=list)
    apt: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    identified: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class Touchpoint:
    """One place a human has to intervene for the workflow to produce output."""

    label: str
    node_id: str = ""
    node_type: str = ""
    stage: str = "per-run"           # setup | per-run | review | per-output
    cost: float = 1.0                # weight in the automation score
    detail: str = ""


@dataclass
class Finding:
    """A production risk."""

    id: str
    title: str
    severity: str = "medium"         # critical | high | medium | low | info
    category: str = "general"        # licensing | provenance | reproducibility |
                                     # dependency | runtime | cost | data
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    recommendation: str = ""
    score: float = 0.0

    SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

    @property
    def rank(self) -> int:
        return self.SEVERITY_ORDER.get(self.severity, 9)


def to_jsonable(obj: Any) -> Any:
    """Convert dataclass trees into plain JSON-safe structures."""
    if hasattr(obj, "__dataclass_fields__"):
        return _clean(asdict(obj))
    if isinstance(obj, list):
        return [to_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj
