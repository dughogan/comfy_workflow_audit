"""How much of this workflow runs itself, and how much needs a person.

The question a supervisor actually asks is "can I put this on the farm and walk
away, or does an artist have to sit with it?".  So the score is built from
*touchpoints*: concrete places a human has to act, each tagged with when they
have to act.

* ``setup``      - once per machine or per project (install a pack, fix a path)
* ``per-run``    - every single generation (pick an image, retype a prompt)
* ``review``     - a human has to look at the result to decide anything
* ``per-output`` - a human has to save or pick the keeper

Only ``per-run``, ``review`` and ``per-output`` drive the index, because setup
cost is paid once and then amortised.  Setup is reported separately rather than
folded in, so a workflow is not punished forever for needing an install.

The index itself is deliberately not the headline any more.  A supervisor
usually knows whether their own workflow needs babysitting before they run any
tool on it, and a number out of a hundred is not something anyone acts on.  What
is worth reading is the list underneath: *which* points need a person, and
whether each one could be wired up instead.  So the reports lead with the
touchpoint count and keep the index as context.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .. import catalog
from ..graph import MODE_BYPASS, MODE_NEVER, Node, Workflow
from ..records import AssetRef, ModelRef, PromptRef, Touchpoint

# Nodes that stop the graph and wait for a person to choose something.
BLOCKING_CHOOSER_TYPES = {
    "PreviewAndChoose", "Preview Chooser", "Preview Chooser Fabric", "Image Chooser",
    "ImageChooser", "ImpactSEGSPicker", "PreviewBridge", "PreviewBridgeLatent",
    "ImageReceiver", "ImageSender", "LatentSender", "LatentReceiver",
    "MaskEditor", "OpenPose Editor", "PoseNode", "Canvas_Tab", "PainterNode",
}

# Nodes whose only purpose is for a human to look at the result.
REVIEW_TYPES = {"PreviewImage", "PreviewAny", "Preview3D", "PreviewAudio",
                "SaveImageWebsocket", "ShowText|pysssss", "DisplayText",
                "PreviewVideo", "DisplayAny"}

SAVE_TYPES_RE = re.compile(r"^(Save|Export|Write)|SaveImage|SaveVideo|SaveAudio|VHS_VideoCombine", re.IGNORECASE)

# Loaders that iterate a folder or list instead of taking one hand-picked file.
BATCH_LOADER_RE = re.compile(
    r"batch|_list|list_|folder|directory|path|sequence|frames|LoadImagesFromDir|"
    r"VHS_LoadImages|LoadImageListFromDir|Load Image Batch",
    re.IGNORECASE,
)

# Dedicated string-source nodes. A prompt held in one of these is still typed by
# a person, but it is a single named node that a script can overwrite through the
# /prompt API, which is a materially easier thing to automate than a text box
# buried inside an encoder or a subgraph.
PROMPT_INJECTION_TYPES = {
    "PrimitiveString", "PrimitiveStringMultiline", "PrimitiveNode", "String",
    "StringConstant", "String Literal", "Text Multiline", "CR Text",
    "ttN text", "ShowText|pysssss", "easy stringValue", "JWStringMultiline",
}

# Text in a Note that reads like an instruction to the operator.
INSTRUCTION_RE = re.compile(
    r"\b(set |change |adjust |you (?:need|must|should)|replace |select |choose |"
    r"pick |remember to|make sure|update the|swap |enable |disable |tweak |"
    r"drag |drop |upload )",
    re.IGNORECASE,
)

BANDS = [
    (85, "Batch-ready", "Can be queued unattended; a person only sets it up."),
    (65, "Lightly supervised", "Runs largely on its own, with an occasional look."),
    (40, "Operator-driven", "Needs a person present for each run, but the steps are mechanical."),
    (15, "Hands-on artist tool", "An artist drives this; output volume scales with their time."),
    (0, "Interactive/experimental", "Effectively a manual tool. Not a pipeline component."),
]


@dataclass
class AutomationScore:
    index: int = 0
    band: str = ""
    band_detail: str = ""
    per_run_cost: float = 0.0
    setup_cost: float = 0.0
    touchpoints: list[Touchpoint] = field(default_factory=list)
    automation_signals: list[str] = field(default_factory=list)

    @property
    def per_run_touchpoints(self) -> list[Touchpoint]:
        return [t for t in self.touchpoints if t.stage in ("per-run", "review", "per-output")]

    @property
    def setup_touchpoints(self) -> list[Touchpoint]:
        return [t for t in self.touchpoints if t.stage == "setup"]


def score(wf: Workflow, *, models: list[ModelRef], prompts: list[PromptRef],
          assets: list[AssetRef], outputs: list[AssetRef], notes: list[PromptRef],
          packs: list[Any], api_node_types: list[str],
          missing_models: list[ModelRef] | None = None) -> AutomationScore:
    result = AutomationScore()
    add = result.touchpoints.append
    signal = result.automation_signals.append

    active = {n.id: n for n in wf.active()}

    # -- inputs a human hands over each run --------------------------------
    upload_assets = [a for a in assets if a.upload_widget and a.enabled]
    driven_uploads = 0
    for asset in upload_assets:
        node = wf.nodes.get(asset.node_id)
        if node is None or node.id not in active:
            continue
        if _fed_by_link(wf, node, asset.widget) or BATCH_LOADER_RE.search(node.type):
            driven_uploads += 1
            continue
        add(Touchpoint(
            label=f"Supply {asset.kind} for {node.label}",
            node_id=node.id, node_type=node.type, stage="per-run", cost=1.0,
            detail=f"'{asset.widget}' is an upload widget currently set to "
                   f"'{asset.value}'. Each run needs a person to choose the file.",
        ))
    if driven_uploads:
        signal(f"{driven_uploads} input(s) are fed from a batch loader or upstream link rather than hand-picked")

    # -- prompts -----------------------------------------------------------
    typed_prompts = [p for p in prompts if p.enabled and not p.driven_by_link
                     and p.polarity in ("positive", "negative", "both", "system")
                     and len(p.text.strip()) > 2]
    linked_prompts = [p for p in prompts if p.driven_by_link]
    if typed_prompts:
        injectable = [p for p in typed_prompts if p.node_type in PROMPT_INJECTION_TYPES]
        embedded = [p for p in typed_prompts if p.node_type not in PROMPT_INJECTION_TYPES]

        if embedded:
            # Retyping prompts is one activity, not N separate ones; the second
            # and subsequent boxes cost less than the first.
            cost = 1.0 + 0.25 * (len(embedded) - 1)
            add(Touchpoint(
                label=f"Write or edit {len(embedded)} static prompt(s)",
                stage="per-run", cost=min(cost, 3.0),
                node_type=embedded[0].node_type,
                node_id=embedded[0].node_id,
                detail="Prompt text is typed directly into the node that consumes it, so "
                       "changing the subject means editing the workflow rather than "
                       "passing an argument.",
            ))
        if injectable:
            add(Touchpoint(
                label=f"Set {len(injectable)} prompt(s) on a dedicated input node",
                stage="per-run", cost=0.4,
                node_type=injectable[0].node_type,
                node_id=injectable[0].node_id,
                detail="The text lives in a standalone string node, which a submission "
                       "script can overwrite through the /prompt API without touching the "
                       "rest of the graph. Cheap to automate, but still a value someone "
                       "has to supply.",
            ))
    if linked_prompts:
        signal(f"{len(linked_prompts)} prompt input(s) are driven from upstream nodes and can be scripted")

    # -- seeds -------------------------------------------------------------
    fixed_seeds, auto_seeds = _seed_modes(wf)
    if fixed_seeds:
        add(Touchpoint(
            label=f"Advance {len(fixed_seeds)} fixed seed(s) by hand",
            stage="per-run", cost=0.6 * min(len(fixed_seeds), 3),
            node_id=fixed_seeds[0].id, node_type=fixed_seeds[0].type,
            detail="control_after_generate is 'fixed', so repeated runs reproduce "
                   "the same image until someone edits the seed.",
        ))
    if auto_seeds:
        signal(f"{len(auto_seeds)} sampler seed(s) advance automatically between runs")

    # -- blocking choosers -------------------------------------------------
    for node in wf.active():
        if node.type in BLOCKING_CHOOSER_TYPES:
            add(Touchpoint(
                label=f"Human selection at {node.label}",
                node_id=node.id, node_type=node.type, stage="per-run", cost=2.5,
                detail="This node halts execution until a person picks or edits "
                       "something. A queued batch will stall here.",
            ))

    # -- muted / bypassed branches ----------------------------------------
    toggled = [n for n in wf.nodes.values() if n.mode in (MODE_NEVER, MODE_BYPASS)]
    if toggled:
        add(Touchpoint(
            label=f"{len(toggled)} node(s) are muted or bypassed",
            stage="per-run", cost=0.5 + 0.05 * min(len(toggled), 20),
            node_id=toggled[0].id, node_type=toggled[0].type,
            detail="Muted and bypassed nodes are switches someone flips between "
                   "runs. They also mean the saved graph is not the graph that runs.",
        ))

    # -- review and output -------------------------------------------------
    review_nodes = [n for n in wf.active() if n.type in REVIEW_TYPES]
    save_nodes = [n for n in wf.active()
                  if SAVE_TYPES_RE.search(n.type) or _is_output_saver(n)]
    if review_nodes and not save_nodes:
        add(Touchpoint(
            label="Results are previewed but never saved",
            node_id=review_nodes[0].id, node_type=review_nodes[0].type,
            stage="per-output", cost=1.5,
            detail="With no Save node, every keeper has to be pulled out of the UI by hand.",
        ))
    elif review_nodes:
        add(Touchpoint(
            label=f"{len(review_nodes)} preview node(s) invite a human check",
            node_id=review_nodes[0].id, node_type=review_nodes[0].type,
            stage="review", cost=0.3,
            detail="Previews are cheap, but their presence usually means someone is "
                   "expected to look at each result.",
        ))
    if save_nodes:
        signal(f"{len(save_nodes)} save node(s) write results to disk without intervention")

    # -- operator instructions left in notes -------------------------------
    instructive = [n for n in notes if INSTRUCTION_RE.search(n.text)]
    if instructive:
        add(Touchpoint(
            label=f"{len(instructive)} note(s) contain operator instructions",
            node_id=instructive[0].node_id, node_type=instructive[0].node_type,
            stage="per-run", cost=0.4 * min(len(instructive), 3),
            detail="The author documented manual steps inside the graph, e.g. "
                   + _first_instruction(instructive[0].text),
        ))

    # -- setup burden ------------------------------------------------------
    _setup_touchpoints(wf, add, packs=packs, assets=assets, models=models,
                       api_node_types=api_node_types, missing_models=missing_models or [])

    result.per_run_cost = sum(t.cost for t in result.per_run_touchpoints)
    result.setup_cost = sum(t.cost for t in result.setup_touchpoints)

    # Saturating curve: the first manual step hurts most, later ones add less.
    result.index = int(round(100 * math.exp(-0.35 * result.per_run_cost)))
    for threshold, band, detail in BANDS:
        if result.index >= threshold:
            result.band, result.band_detail = band, detail
            break

    result.touchpoints.sort(key=lambda t: (-t.cost, t.stage))
    return result


# --------------------------------------------------------------------------


def _setup_touchpoints(wf: Workflow, add, *, packs, assets, models,
                       api_node_types, missing_models) -> None:
    unidentified = [p for p in packs if not p.identified]
    if unidentified:
        add(Touchpoint(
            label=f"Track down {len(unidentified)} unidentified node type(s)",
            stage="setup", cost=2.0,
            detail="These classes are not in the ComfyUI-Manager index, so there is no "
                   "known install source: " + ", ".join(p.title for p in unidentified[:5]),
        ))
    installable = [p for p in packs if p.identified]
    if installable:
        add(Touchpoint(
            label=f"Install {len(installable)} custom node pack(s)",
            stage="setup", cost=0.5 * min(len(installable), 6),
            detail=", ".join(p.title for p in installable[:8]),
        ))

    abs_paths = [a for a in assets if a.absolute_path]
    if abs_paths:
        add(Touchpoint(
            label=f"Repoint {len(abs_paths)} absolute path(s)",
            node_id=abs_paths[0].node_id, node_type=abs_paths[0].node_type,
            stage="setup", cost=1.0,
            detail="Absolute paths are machine specific and must be edited on every "
                   "workstation and render node: " + abs_paths[0].value,
        ))

    if api_node_types:
        add(Touchpoint(
            label="Configure vendor API credentials",
            stage="setup", cost=1.0,
            detail=f"{len(api_node_types)} hosted API node type(s) need an account, "
                   "credit balance and key before the graph will run.",
        ))

    if missing_models:
        add(Touchpoint(
            label=f"Fetch {len(missing_models)} missing model file(s)",
            stage="setup", cost=1.0,
            detail=", ".join(m.filename for m in missing_models[:6]),
        ))

    gated = [m for m in models if m.provenance and m.provenance.gated]
    if gated:
        add(Touchpoint(
            label=f"Accept access terms for {len(gated)} gated model(s)",
            stage="setup", cost=1.0,
            detail="Gated repositories need a signed-in account and a token on every "
                   "machine that downloads them: " + ", ".join(m.filename for m in gated[:4]),
        ))


def _fed_by_link(wf: Workflow, node: Node, widget: str) -> bool:
    return node.driven_input(widget, wf.nodes)


def _is_output_saver(node: Node) -> bool:
    schema = catalog.get_node_schema(node.type)
    if not schema or not schema.get("output_node"):
        return False
    return node.type not in REVIEW_TYPES


def _seed_modes(wf: Workflow) -> tuple[list[Node], list[Node]]:
    fixed: list[Node] = []
    auto: list[Node] = []
    for node in wf.active():
        for name, value in node.widgets.items():
            if name != "control_after_generate":
                continue
            if isinstance(value, str) and value.lower() == "fixed":
                fixed.append(node)
            elif isinstance(value, str):
                auto.append(node)
    return fixed, auto


def _first_instruction(text: str) -> str:
    match = INSTRUCTION_RE.search(text)
    if not match:
        return text[:80]
    start = max(0, match.start() - 10)
    return "'" + " ".join(text[start:start + 110].split()) + "...'"
