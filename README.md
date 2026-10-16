# ComfyAudit

**Document what's inside a ComfyUI workflow, and hand the result to someone else.**

Every model it loads and what each licence actually says, where the weights came
from, the prompts, the external assets, the custom node packs and their versions,
how much of it a human has to babysit, and what would stop it running on another
machine.

Out of the box it just reports, and opens with a plain-language summary so the
tables underneath make sense to whoever you forward them to.

Tell it what you've already cleared — optional — and it stops repeating itself.
The second audit of a workflow answers a much shorter question than the first:
*what's in here that we haven't already dealt with?*

Tell it about your facility — optional, one panel in ComfyUI's settings — and it
also works out what all of that means for **you**: go, no-go, or
go-with-conditions, with the reasoning shown.

That second part matters because a licence grants rights to *someone, somewhere,
doing something*. MiniMax H3 is a no in London and a yes in Toronto: the licence
excludes the United Kingdom by territory and says nothing about Canada. Same
file, same terms, opposite answers. So ComfyAudit doesn't ship an opinion about
what your studio can live with — it takes your circumstances as input and applies
the published terms to them.

Give it no profile — the default — and it stays descriptive: here are the terms,
here's a source for every claim, here are the facts that would settle it, written
out in prose before any table appears.

Built for the moment a workflow arrives from an artist, a vendor or a Discord
link, and someone has to decide whether it can go on a show.

---

## Install

Not in the ComfyUI-Manager registry yet, so for now it's a manual install:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/dughogan/comfyaudit
```

Restart ComfyUI. That's it — ComfyAudit is pure standard library, so installing
it cannot disturb your environment or fight with anything else you have.

**If ComfyUI says IMPORT FAILED**, it is almost always the directory level.
ComfyUI loads a pack by looking for `__init__.py` directly inside
`custom_nodes/<name>/`, so the folder it sees must be the pack itself, not a
repository containing one:

```
custom_nodes/
  comfyaudit/
    __init__.py      <-- ComfyUI needs to find this here
    core/
    nodes/
```

If you cloned a repository that has the pack nested one level down, either move
the inner folder up or symlink it:

```bash
cd ComfyUI/custom_nodes
ln -s "$PWD/<cloned-repo>/comfyaudit" comfyaudit
```

Anything else shows up as a Python traceback in the ComfyUI console, printed
just above the IMPORT FAILED line — that traceback is the answer, and the pack
now raises rather than loading empty so you actually get one.

The optional **Claude Review** node needs one extra package. Every other node
works without it, and that node tells you if it's missing:

```bash
pip install anthropic
```

## Three ways to run it

**From the sidebar** — the **Audit** tab, marked with a tick. This is the one to
reach for. It walks the three questions an audit actually needs answered, in the
order they matter, and ends with something you can hand to someone else. See
[The sidebar](#the-sidebar) below.

**From the menu** — *Extensions → ComfyAudit → Audit this workflow*, or the same
commands from the command palette. Skips the wizard and audits immediately with
whatever profile is already set. Nothing is added to the workflow.

**As nodes** — drop **Audit This Workflow** in and hit Run. It audits the graph
it's sitting in, because ComfyUI hands any node that asks for them the running
prompt and the UI workflow. Nothing to configure. This is the right flow when the
audit is part of a pipeline rather than a question someone just asked.

> The nodes live under the **`audit`** category — lowercase, at the top level of
> the node menu. Double-click the canvas and type `Audit` if you can't find them.

| Node | What it does |
|---|---|
| **Studio Profile** | Optional override of the settings profile, for a show whose circumstances differ from the facility's. |
| **Audit This Workflow** | Documents the running graph. Outputs the report as Markdown and JSON, plus the risk score, automation index and a one-line licence summary as separate sockets. |
| **Claude Review** | Optional second pass for the judgement calls — see below. |
| **Audit Gate** | Stops the queue on conditions **you** choose. Nothing is enforced by default. |
| **Shot History Gate** | Put it between VAE Decode and Save Image. Stops the run before anything is saved if the shot has no history, its history has been altered, it has not reached the stage you require (e.g. still in sandbox), or, if you switch it on, a model bars commercial use. |
| **Save Audit Report** | Writes the report into your output folder as HTML, Markdown or JSON. |

## The sidebar

Open the **Audit** tab. Three steps.

### 1. Profile — *who is asking*

The studio profile, inline: territory, annual revenue, what ships, whether
outputs train other models, whether real performers are involved, and a label
for the report.

These are the facts licence terms actually turn on, which is why they come first
rather than last. A note under the fields tells you where you stand:

> Territory and revenue are the minimum for a verdict. Without them the report
> still lists every model, licence and risk — it just will not rule on them.

Fill them in and it flips to *"so this audit can reach a go / no-go"*. They save
to ComfyUI's own settings, so this is a one-off — the same values the settings
panel writes, and the same ones the Python side reads. Skip the step entirely if
you want a descriptive report.

### 2. Scope — *how deep*

| Option | Cost |
|---|---|
| **Check models on disk** | Fast and local. On by default. Catches the missing weight before the render does. |
| **Look models up online** | Slower, needs network. Queries Hugging Face and Civitai for licences the bundled knowledge base doesn't have. |
| **Hash model files** | Slow — reads every model file. The only thing that survives a rename. |
| **Add a Claude review** | Several model calls, about a minute. Needs credentials. |

If the Anthropic SDK or a key is missing, the Claude option is replaced by the
actual reason rather than a checkbox that does nothing.

### 3. Report

The verdict leads, because it's the question that was asked — colour-toned by
determination, with the operational risk band beside it. Then the headline, the
at-a-glance figures, any blockers named individually, and every finding as a
collapsible row ordered worst-first.

**Open full report** puts the whole document over the canvas. The sidebar is a
narrow column and the report has wide tables in it; rendering it in place would
mean choosing which columns to lose.

### Export

| Format | Use |
|---|---|
| **Markdown** | Goes in a ticket, a wiki, or a commit. Carries the Claude review too, when one ran. |
| **PDF** | The thing you attach to an email. |
| **HTML** | Self-contained, styled, opens in any browser. |

PDF goes through your browser's print dialog — pick *Save as PDF* as the
destination. There is no PDF library here on purpose. This pack declares no
dependencies, which is what makes it safe to drop into someone else's ComfyUI,
and pulling in a renderer to lay out a document the browser already lays out
would trade that away for nothing. The report ships its own print stylesheet, so
it comes out as a document rather than a screenshot of one.

Files are named after the open workflow — `beauty-pass.audit.md` — and the
heading inside matches.

## What has to happen

**Entirely optional, and off until you fill it in.** With nothing set, every
report opens with *In plain terms* — a few paragraphs saying what the workflow
contains, which licence conditions are actually in play, and which facts would
settle whether they suit a job. That summary is deterministic: no API key, no
network, no cost.

Fill in the profile and those same reports also work out what the terms
mean for you.

**Settings → ComfyAudit → Studio profile.** It lives there rather than on a node
because it describes the facility, not the graph — territory and revenue are the
same for every workflow on the machine, and restating a constant in each one is
how it ends up wrong in half of them. Set it once. (There's still a **Studio
Profile** node if a particular show needs different answers; wiring one in
overrides the settings for that graph.)

Four facts decide most licence questions, and none of them are in the workflow
file:

| Fact | Why it decides things |
|---|---|
| **Territory** | Where you render and deploy. MiniMax H3 excludes the US, EU, UK and South Korea outright; Hunyuan and Kolors carve out the EU, UK and Korea. No fee lifts a territory exclusion. |
| **Revenue band** | Free use is capped at $1M by Stability, $20M by MiniMax, and at user counts by Llama and Kolors. Above the cap you need an agreement, which is a budget line, not a blocker. |
| **What ships** | Copyleft only reaches your own code when something is distributed. An AGPL node pack is a non-issue for frame delivery and a serious problem for a product. |
| **Output use** | Several licences forbid training other models on the outputs, worldwide, with no fee that lifts it. |

Set any of them and every model, copyleft node pack and hosted API node gets a
determination with its chain of reasoning attached.

The report leads with **the work outstanding, not a ruling on the workflow.**
Most "no" answers are one model swap or one licence away, and a big red NOT
USABLE stamp is exactly the fragment that gets screenshotted and forwarded
without the reasoning underneath. So each finding carries the *shape* of its
remedy — a confirmation, a credit to add, a licence to buy, a change of where it
runs — and those get counted:

```
Needs changes first — 6 things to resolve before this goes on a paid job:
4 confirmations, a licence to buy and a change of where it runs.

Has to change
  minimax_h3_ref2va…, minimax_h3_video_vae…, and 2 more
    MiniMax Community License does not grant rights in the United States,
    which is where this studio operates.
      What lifts it: run it in a territory the grant covers, or negotiate
      directly with the rights holder.
    Once that is resolved: free use is capped at USD $20M annual revenue.
      What lifts it: negotiate a separate agreement with MiniMax.
```

Jobs are counted by distinct remedy, not by file — four weights failing one
territory clause are one relocation to arrange, not four.

The same workflow, assessed for a small Toronto facility, comes back **clear
once conditions are met** — the territory clause doesn't reach Canada and the
revenue cap isn't met.

Nothing here is a policy the tool invented. Every determination names the term it
applied, the fact it applied it to, and what would lift it, so you can check the
reasoning instead of trusting the label. It is a reading of published terms, not
legal advice.

**Performer likeness is judged separately**, because it isn't a licence question
at all. No model licence grants rights in a face. If you tell it real performers
are involved and the graph does identity work, it says so — and it detects that
from the node types, not just filenames, because the face swap in a real
workflow is often done by a general video model and a segmenter with unrevealing
names.

## What you've already cleared

A studio doesn't have one workflow. It has hundreds, and the same five
checkpoints recur across all of them. Auditing each in isolation re-derives the
same findings forever — by the second week the report is mostly noise, because
the reader already knows about the CodeFormer licence. They cleared it in March.

So decisions get recorded, once:

```bash
comfyaudit registry add studio-cleared.json shot_0120.json \
    --by "D. Hogan" --reference SHOW-114 --note "cleared for Atlas, internal only"

comfyaudit registry set studio-cleared.json 4x-UltraSharp.pth \
    --status rejected --note "non-commercial, use RealESRGAN instead"
```

Point at that file — **Settings → ComfyAudit → Registry**, or `--registry` — and
every later report leads with what's new:

```
## 1. New since last cleared

1 of 12 item(s) need attention: 1 previously rejected.

### Previously rejected
- 4x-UltraSharp.pth (model) — Recorded as not to be used: non-commercial,
  use RealESRGAN instead by D. Hogan on 2026-03-14

*11 other item(s) were already cleared and are not repeated here.*
```

A decision is about a *specific file under a specific licence*, so it reopens
when either moves. A weight whose SHA-256 no longer matches what was signed off
comes back as **changed**, not quietly approved. So does one now reading as a
different licence. And a renamed file is still recognised by its hash — which is
the one identifier a rename can't break.

Nothing is ever written automatically. A registry that fills itself in is an
expensive way of approving everything.

## Keeping the licence knowledge current

The knowledge base is hand-curated and accurate as of a date, and licences move:
Stability relicensed SD3 mid-flight, Black Forest Labs revised the FLUX dev
terms. Both turned a cleared model into an uncleared one overnight.

Every report states how old its knowledge is, and says so loudly once it's old
enough for a term to have shifted underneath it. To replace it:

```bash
comfyaudit update-knowledge --dry-run   # what would change, and how
comfyaudit update-knowledge             # fetch it
```

It prints the licences that changed and what moved in each before writing, and
keeps the old file as `licences.json.previous` — because "the licence changed"
and "the knowledge base changed" look identical from a report, and only one of
them is the tool's fault. Never automatic: a licence base that updates unnoticed
is how a delivery gets cleared against terms nobody read.

## What the report contains

### 1. Licence summary

Models grouped by the licence they carry, with the commercial position, the fee
terms, a confidence level and a link to the licence itself:

| Licence | Models | Commercial use | Fee | Confidence |
|---|---|---|---|---|
| Apache License 2.0 (ViTPose) | 1 | permissive | no fee | low |
| Meta SAM License (Segment Anything 3) | 1 | permissive | no fee | medium |
| MiniMax Community License | 4 | conditional | free below a revenue threshold | medium |
| AGPL-3.0 (Ultralytics YOLO detectors) | 1 | conditional | a licence must be obtained | high |

Plus two sections that are easy to miss at delivery:

- **Obligations that come with these licences** — attribution and notices,
  share-alike terms, revenue thresholds, territorial limits.
- **Worth confirming at source** — the entries the tool is least sure about,
  because a licence is matched from a filename and filenames can be changed by
  anyone.

The knowledge base covers the families that actually turn up in production. Some
of what it knows:

| Model | What the licence says |
|---|---|
| FLUX.1 [dev] | Non-commercial, written to cover the **outputs**, not only the weights |
| Stable Diffusion 3.5 | Free commercially below $1M total company revenue, not AI revenue |
| MiniMax H3 | Territory excludes **US, EU, UK and Korea**; $20M revenue cap; visible attribution required |
| InsightFace (antelopev2, buffalo) | Non-commercial, and **inherited** by IP-Adapter FaceID, InstantID and the face-swap packs built on it |
| CodeFormer | Non-commercial (S-Lab 1.0), and it arrives bundled inside face-restore packs |
| Ultralytics YOLO | AGPL-3.0; their published position is that internal production use needs an Enterprise Licence |
| HunyuanVideo | Territory excludes the EU, UK and South Korea |
| Illustrious / NoobAI | FAIPL — outputs are unrestricted, but derivative *models* are copyleft |
| Depth Anything V2 | Base and Large are CC BY-NC; only Small is Apache 2.0 |

### 2. Models

Every weight the graph loads, including the ones a simple parser misses: models
named by custom nodes with no published schema, `embedding:` references buried in
prompt text, inline `<lora:...>` syntax, LoRA strengths paired with the right
file, and hosted models that only exist on a vendor's servers.

### 3. Prompts

Positive and negative worked out from the sampler wiring rather than guessed from
node titles, so it stays right through reroutes, conditioning combines,
ControlNet stacks, subgraphs, and KJNodes' `Set`/`Get` pass-by-name wiring.

### 4. Assets

Every external input, marked by how it's supplied: an upload widget a person
clicks, a path baked into the graph, an absolute path off someone's D: drive, or
a URL fetched at run time.

### 5. Node dependencies

Which packs are needed, who wrote them, their licence, stars, last commit, and
whether the workflow pins a version. Also whether two installed packs claim the
same node class name — they shadow each other by load order.

### 6. Automation vs human intervention

Answers "can I queue this and walk away, or does an artist have to sit with it?"
Built from *touchpoints* — concrete places a human has to act — weighted by how
often they recur:

```
| Weight | When     | Touchpoint                          | Why
|    1.2 | per-run  | Write or edit 2 static prompt(s)    | Typed into the node that consumes it...
|    1.0 | per-run  | Supply image for Plate              | 'image' is an upload widget...
|    0.6 | per-run  | Advance 1 fixed seed(s) by hand     | control_after_generate is 'fixed'...
|    0.6 | per-run  | 1 node(s) are muted or bypassed     | Switches someone flips between runs...
```

One-off setup is reported separately rather than held against the workflow
forever. A prompt typed into a dedicated string node also scores better than one
buried inside an encoder, because a submission script can overwrite the first
through the `/prompt` API.

### 7. Operational risks

What would stop this running or reproducing somewhere else: missing weights,
absolute paths, unpinned packs, seeds that drift, hosted APIs that can change
under you, node classes nothing can install. **Licence position is deliberately
not part of this score** — that's policy, and policy is yours.

## Conflicting licence data

Some licences contradict themselves, and sources sometimes disagree about what
a file is. Neither is ever resolved silently. Three kinds are flagged:

| Kind | Example |
|---|---|
| **Conflicting terms in the licence** | FLUX.1 [dev] permits commercial use of outputs in one clause and bars production use of anything the model produces in another. Recorded in the knowledge base with both clauses, the reading applied and the source. |
| **Contradictory licence data** | An entry, including a studio's own override, that says two incompatible things, such as *commercial use: yes* with *outputs: non-commercial*. Detected automatically. |
| **Sources disagree** | With `--online`, the filename, the Hugging Face tag, Civitai and the base model describe one file differently. |

Each conflict appears in the licence summary, as a high-severity finding, in
the licensing guide, and on the model's lifeline in the provenance record. The
stricter reading is applied until the licence holder or counsel confirms
otherwise. To record a new conflict, add a `conflicts` list to the licence's
entry in `licences.json`, with `summary`, `clauses`, `reading`, `source` and
`checked`.

## Looking models up

With `--online` (or the node's `online_lookups` switch), models and packs are
resolved against the services that know about them:

| Source | What it settles |
|---|---|
| **HuggingFace** | The repo's licence tag, whether it's **gated** (and whether a human approves each request, which stalls a render node), and the **base models** the hub recorded |
| **Civitai** | Community checkpoints and LoRAs, by **SHA-256** where possible — filenames there are whatever the downloader called them. Returns the uploader's own permission flags and the base model |
| **GitHub** | Node pack licences, stars, last push, archived state. Falls back to reading `LICENSE` from `raw.githubusercontent.com` when the API's 60-an-hour anonymous cap runs out |
| **Comfy Registry** | Publisher, latest published version, declared licence |

### Inheritance

A model's author can grant less than their base model allows. They cannot grant
more. So when Civitai says the uploader ticked "Sell" and the base model is
`Flux.1 D`, the report says non-commercial — and shows why:

```
my_flux_lora.safetensors — FLUX.1 [dev] Non-Commercial License
  Inherited from the base model Flux.1 D: a derivative cannot be more
  permissive than what it was trained on.
  Civitai uploader flags says yes, but base model Flux.1 D only permits 'no'.
  exact Civitai file hash match (ab12cd34ef56...)
```

That mapping is bundled from Civitai's own published base-model licence table (80
base models, 77 mapped onto licence definitions). Refresh it any time:

```bash
python tools/build_base_models.py --url
```

### When sources disagree

Two contradictory descriptions of one file is reported as its own finding, and
usually means the weight was renamed on the way to your drive. Note that a model
granting *less* than its base allows is ordinary and isn't flagged — only a
derivative claiming *more*, or a flat contradiction.

### Credentials

All optional; each buys something specific:

```bash
export HF_TOKEN=...          # gated repos, higher rate limit
export CIVITAI_API_KEY=...   # early-access models
export GITHUB_TOKEN=...      # lifts the 60-an-hour anonymous cap
```

Responses cache on disk for a week, and rate limits are tracked per host so the
report can say *"GitHub rate limit reached, resets in about 40 minutes"* rather
than silently returning less. If model names shouldn't leave for a given service:

```bash
--online --sources huggingface,github
```

## Running inside ComfyUI makes it sharper

The same engine works on a bare `.json`, but in-process it stops guessing:

- **Custom node widgets get real names.** Offline, a widget on a node nobody
  published a schema for is `widget_0`. In-process the node is asked directly, so
  it's `lora_name_1`, with the model folder recovered by matching the combo's
  options against `folder_paths`.
- **Model presence is a fact**, checked against the folders ComfyUI actually reads.
- **In-house packs stop reading as unidentified** — resolved to
  `custom_nodes/<dir>` with the version read off disk.

## The Claude Review node

Optional. The rule engine knows exactly what it knows; what it can't do is
recognise that `juggernautXL_v9Rundiffusion.safetensors` is an SDXL community
merge, notice that a prompt names a living artist, or work out what to swap in
when an upscaler turns out to be non-commercial.

It *investigates* — reads the models, prompts, findings and packs through tools,
checks the auditor's own knowledge base so its answers stay consistent, and looks
models up on HuggingFace, Civitai and GitHub rather than recalling them. Modes:

| Mode | What it does |
|---|---|
| `identify` | Works out what the unnamed models actually are, and what their lineage inherits |
| `clearance` | Reads the prompt text for real people, trademarks, characters, living artists named as a style |
| `remediate` | Proposes specific replacements and an ordered plan |
| `narrative` | Writes the go/no-go brief a supervisor reads — plain prose, leading with the answer |
| `full` | The first three in order |

You can also just ask it something.

`narrative` is the one that turns a determination into something you can forward.
It reads the chain the rule engine produced, checks it against the knowledge base
and the source pages, and explains it — distinguishing a territory carve-out (no
fee lifts it) from a revenue cap (a budget line) from an open-source obligation.
It is explicitly told not to invent a verdict: with no Studio Profile it explains
what the licences turn on and which facts would settle them.

Everything it concludes is kept separate from the rule findings, labelled
model-derived, and carries its own confidence.

**It sends model names and prompt text to the Anthropic API.** Web search is on
by default so it can check a licence at source — turn it off for confidential
work. It's opt-in for exactly that reason.

## Shot history

An audit answers *what is in this workflow now*. Months later, on a commercial
job, legal asks something else: **how did this frame come to exist?** Who built
the graph, who changed it, which models came and went, which prompts made the
delivered pixels, where the source plates came from, and where a person, not a
model, made the decisions.

Shot history keeps that answer inside the workflow itself, under
`extra.comfyaudit_history`. ComfyUI preserves `extra` on save and copies it into
every PNG it renders, so the record travels with the file and with its outputs.
Nothing contacts the network, which makes it usable on an air-gapped content
network.

### What gets recorded

Every entry is a snapshot of the workflow's substance plus who acted and what
changed:

| Captured | Detail |
|---|---|
| **Who** | Name and role as set in *Settings → ComfyAudit → Shot history*, the machine, and the tool |
| **Models** | Every weight, with its licence reading at that moment. Additions, removals and LoRA strength changes are diffed per entry |
| **Prompts** | Each prompt's text (or only its SHA-256, or encrypted) and **who first wrote it** |
| **Nodes** | Core, custom (with pack and pinned version) and hosted-API nodes |
| **Source media** | Every supplied image, plate or clip, fingerprinted, with a declared origin and the studio's own asset ID |
| **Settings** | Seeds, steps, CFG, sampler, resolution, bypassed branches |
| **Outputs** | Rendered files, fingerprinted, so a delivered frame can be traced to the entry that made it |
| **Stage** | `sandbox → production → cleared → delivered`. Sandbox work never ships; promotion is where clearance starts |
| **Studio IDs** | The ShotGrid, ftrack or Sparky entity the asset was born as, so provenance starts there and not in ComfyUI |

Laying out nodes, panning and resizing are not changes; a save that changed
nothing adds nothing. Renders, reviews and approvals always record, because
the fact that they happened is the point. Unchanged states are stored once and
referenced, so a long life with hundreds of reviews stays small.

### Human break points

Every entry is tagged with the human decisions it contains: *authored*,
*prompt*, *source-media*, *model-choice*, *branch*, *parameters*, *seed*,
*review*, *selection*, *sourcing*, *stage*, *handoff* and *note*. The tagging
is deliberately conservative:

- Taking an existing graph under tracking credits the importer with nothing,
  and its prompts are listed as written "before tracking".
- Seeds a sampler advanced on its own never count as a choice.
- A blank note is not a decision.

The record calls out the gaps: renders with no person between them, a final
render nobody reviewed, sources with no known origin, and a sign-off that a
later render or rejection has overtaken.

Read the result for what it is: a record of **process**. For copyright, the US
Copyright Office and EU courts give most weight to human-made material that
stays visible in the output and to human edits after generation. They treat
prompts, selection and parameter changes alone as generally not enough. So the
history records the origin of every source plate. Work done outside ComfyUI,
such as comp, paint and grade, can be attested against the shot.

### Can the record be trusted?

| Level | What it means |
|---|---|
| **Witnessed** | Chain intact, every entry signed with the studio key, and every entry's hash matches a row in the studio ledger |
| **Signed** | Chain intact and every entry signed: nobody without the key wrote or changed an entry |
| **Unverified** | Chain intact but unsigned (or checked without the key). Catches accidents, not a determined forger |
| **Altered** | Something was edited, removed, reordered, cut short, replaced, or an unsigned entry was slipped into a signed history; the first problem is named |
| **No history** | The workflow carries none |

Each entry hashes the one before it, so editing one breaks the chain at that
point. Signing uses HMAC-SHA256 with a studio passphrase.

The **ledger** is an append-only JSONL file outside the workflow, recording
each entry's hash. It is keyed by the shot id sealed inside the entries, so
editing the file's visible shot id does not dodge it. It catches a history cut
short, replaced wholesale, or rewritten after the fact: the ledger will have
seen the same entry with two different contents. The tool also refuses to add
an entry on top of a damaged history, because a fresh signature would launder
the damage.

Secrets and paths come from the **server's environment only**. That includes
`COMFYAUDIT_HISTORY_KEY` (or `COMFYAUDIT_HISTORY_KEY_FILE`),
`COMFYAUDIT_HISTORY_ENC_KEY` and `COMFYAUDIT_HISTORY_LEDGER`. ComfyUI settings
are writable from any browser that can reach the server, so they never hold a
key, a key path or a ledger path.

A signature proves an entry was written through a holder of the studio key. In
ComfyUI that is the studio's ComfyAudit server. It does not prove *which*
person wrote the entry. Names are recorded as each person declared them, and
the record says so beside every badge. Per-person keys narrow this.

Prompts are often the client's creative. Set *Prompts in the history* to
**fingerprints only** to store just a keyed fingerprint (HMAC-SHA256). It
proves which prompt was used without revealing it, and it cannot be confirmed
by guessing. LoRA or embedding names written inside a protected prompt, and
output filenames, are protected too. The environment's setting always wins
over a weaker one chosen in the UI. Or set **encrypted** for AES-256-GCM, which needs
`pip install cryptography`. Encryption fails closed: with the library missing it
stores the fingerprint, never the plaintext. Models, nodes and licences always
stay readable, since they are what the review is about.

### In ComfyUI

The **ComfyAudit** tab in the sidebar. Tracking is automatic: every workflow
starts its history the moment it is opened, and each open tab keeps its own.

- An *edited* entry is added just before a queue, if the graph changed, which
  is what embeds the history in that render's metadata. A *rendered* entry is
  added when the render finishes, with its output files fingerprinted. Bringing
  a render back in as an input, handing the shot to someone else, Save As and
  duplicating a tab are recorded too, without any clicks.
- The header shows the shot's stage, commercial standing and whether its record
  verifies, with **Package for delivery**, **Life of the shot** (the graph
  replayed over a timeline, with a story and a log) and **Provenance record**.
- **Shot details** names the shot and links it to its project and studio asset
  ID. The project is what shares show-level answers between shots.
- **Package for delivery** checks the shot, shows what would make it safe,
  takes the sign-off and returns a zip for legal.

Entries are made server-side with the studio key, then adopted into the open
graph. Save the workflow to keep them.

### Where the history lives

In the workflow file itself, under `extra.comfyaudit_history`, so it goes
wherever the `.json` goes. Each render also carries it in the PNG's workflow
metadata (up to the moment it was queued) and gets a
`<render>.provenance.json` beside it: the history up to the entry that made
that render, plus the render's fingerprint, checkable on its own with
`comfyaudit history verify-file`. An optional ledger file
(`COMFYAUDIT_HISTORY_LEDGER`) keeps an independent copy of every entry, so a
history that was removed or rewritten shows up.

It does not travel with **Export (API Format)**: that format has no room for
it, and the panel asks before exporting a tracked shot that way. Copying nodes
into another workflow does not carry it either.

### ShotGrid (beta)

With `COMFYAUDIT_SHOTGRID_URL`, `COMFYAUDIT_SHOTGRID_SCRIPT` and
`COMFYAUDIT_SHOTGRID_KEY` set, Shot details checks a ShotGrid shot ID against
your site, and delivery posts a Note on the shot with the verdict, the sign-off
and the history's latest hash, with the package attached
(`COMFYAUDIT_SHOTGRID_HASH_FIELD` also writes that hash to a custom field).

**Beta: not yet tested against a live ShotGrid site.** It is built from
Autodesk's REST API documentation and tested against a local stand-in of those
endpoints only. Run it on a trial or sandbox site before relying on it, and
please report what differs.

### The provenance record

A document for legal, exportable as HTML or PDF. It contains:

- Whether the record can be trusted.
- Human-in-the-loop figures.
- **The life of the shot as a flow graph**: one lane per person, the chain
  between entries (a line crossing lanes is a handoff), stage bands, model,
  prompt and source markers, and break-point flags.
- A model lifeline, from first appearance to removal, coloured by commercial
  position.
- Prompts with their authors, source material and its origins, stages and
  sign-off.
- The rules the shot may need to meet, with the evidence the record already
  holds.
- The complete signed log.

### Rules and agreements

The provenance record ends with the published rules a shot may have to meet, and
what the record already evidences for each. The bundled knowledge base
(`core/knowledge/data/regulations.json`) has 37 entries, each with its source,
that source's date and a confidence level. It covers:

- **Union and guild agreements:** SAG-AFTRA TV/Theatrical (2023 and 2026),
  Commercials (2025) and Interactive Media (2025), plus the WGA and DGA AI terms
  and the Academy's AI rules.
- **US copyright:** the US Copyright Office's copyrightability report and its
  registration-disclosure guidance, and *Thaler v. Perlmutter*.
- **EU:** the AI Act's Article 50 marking and deepfake-disclosure duties and the
  Code of Practice, the CJEU originality test, and the German and Czech AI
  rulings.
- **UK and China:** the UK's computer-generated works provision, China's
  labelling measures and *Li v Liu*.
- **US states and federal:** the California, New York and Tennessee
  digital-replica and transparency laws, and the federal bills.
- **Provenance and platforms:** C2PA content credentials, and the platform and
  FTC disclosure rules.

Which rules appear depends on what the history shows (identity or voice nodes,
LLM nodes, training nodes, the production stage, supplied media). It also
depends on what the studio sets: territory, where the work is distributed, and
whether it is a union production, advertising or a feature.

Some evidence is checked automatically, and only where the check actually
proves the item: human-made source material, declared sources, prompt
authorship, a reviewed final, a sign-off that covers the final render, a
verified signature, and fingerprinted outputs. Exporting a manifest is *not*
treated as proof that marking was applied. Neither is a reviewer standing in
for a consulted director. Evidence only a
person can attest, such as a signed consent rider or a disclosure in the
credits, is recorded as a note tagged with the rule. That way it is signed into
the chain rather than left in an email.

One common assumption is answered directly. **No EU rule requires proof that a
human was in the loop for a creative shot.** The AI Act's human-oversight
article (Art. 14) applies only to high-risk systems, and the record says so.
Human involvement matters for *copyright*, in the US, the EU and China, which
is why the history records it so carefully.

Refresh the research, then rebuild with `python tools/build_regulations.py`. The
mapping from each rule to its triggers and checks is set by hand in that
script, not inferred.

### Content credentials

`comfyaudit history manifest` writes a C2PA manifest *definition* for the final
render. It opens with the required `c2pa.created` action. The people and
decisions behind the render are carried in the tool's own assertion, keyed to
the history's head hash. It marks generated output with the IPTC `trainedAlgorithmicMedia`
source type, or `compositeWithTrainedAlgorithmicMedia` when supplied media went
in, and lists the people who acted and the models used. Signing needs a
certificate the studio holds, so sign and embed it at delivery with `c2patool`
or your own signing service.

### Command line

```bash
export COMFYAUDIT_HISTORY_KEY=...            # studio passphrase (server env only)
export COMFYAUDIT_HISTORY_LEDGER=/studio/ledger.jsonl
comfyaudit history stamp shot.json --event created --by "Ana Ruiz" --role compositor \
    --name SH0120 --id shotgrid=Shot#4411 --ledger /studio/ledger.jsonl
comfyaudit history stamp shot.json --event sourced --source 5=photographed --asset-id PLATE-0120-A
comfyaudit history stamp shot.json --event rendered --output-file out/SH0120_0001.exr
comfyaudit history stamp shot.json --event reviewed --by "Doug Hogan" --note "keep the grain"
comfyaudit history stamp shot.json --event promoted --stage production
comfyaudit history stamp shot.json --event approved --by "Lee Park" --role legal
comfyaudit history verify shot.json --ledger /studio/ledger.jsonl    # exit 3 if altered
comfyaudit history log shot.json
comfyaudit history record shot.json --distribute US,EU --likeness   # provenance record
comfyaudit history manifest shot.json                                # C2PA definition
```

UI-format workflows are updated in place. API-format prompts have nowhere to
carry history, so theirs lives beside them as `<name>.history.json`.

## Commercial licensing guide

```bash
comfyaudit guide -o licensing-guide.html
```

It is also in the History tab and at `/comfyaudit/licensing-guide`. It is a
plain-language document for production and legal, built from the same
knowledge base the auditor uses, so it cannot drift from it. It covers:

- How model licensing actually works: outputs versus weights, inheritance,
  conditional terms, hosted APIs, why a commercial licence is not indemnity,
  and when code licences matter.
- The steps from audit to a commercial licence, including what to ask a
  licence holder for.
- Every licence sorted into *permitted*, *permitted with conditions* and *not
  without a separate licence*, with the terms that matter, who grants a
  commercial licence, and a source link.

## Command line

The same auditor runs headless. From the directory *above* the pack:

```bash
python -m comfyaudit.core.cli audit workflow.json                       # report on stdout
python -m comfyaudit.core.cli audit workflow.json -f html -o out.html   # self-contained page
python -m comfyaudit.core.cli audit workflow.json --models-dir /opt/ComfyUI/models
python -m comfyaudit.core.cli audit workflow.json --online --claude
python -m comfyaudit.core.cli audit shots/ -o audits/ --fail-on critical
python -m comfyaudit.core.cli info                                     # what's bundled
python -m comfyaudit.core.cli update-knowledge --dry-run                # licence KB age
python -m comfyaudit.core.cli registry list studio-cleared.json         # what's decided
```

Supply the facility's circumstances and it works out what the terms mean there:

```bash
python -m comfyaudit.core.cli audit workflow.json \
    --territory US --revenue over-100m --ships deliverable-only \
    --likeness --studio "Example Post, Los Angeles"

# or keep them in a file, since they describe the facility and not the workflow
python -m comfyaudit.core.cli audit shots/ --profile studio.json -o audits/
python -m comfyaudit.core.cli audit workflow.json --profile studio.json \
    --claude narrative

# and skip everything already signed off
python -m comfyaudit.core.cli audit shots/ --profile studio.json \
    --registry studio-cleared.json -o audits/
```

`studio.json` is just the same fields:

```json
{
  "territory": "US",
  "revenue_band": "over-100m",
  "ships": "deliverable-only",
  "trains_models": false,
  "likeness_involved": true,
  "label": "Example Post, Los Angeles"
}
```

Reads both workflow formats — the UI graph from **Save**, the API format from
**Export (API)** — and will pull the workflow straight out of a PNG that ComfyUI
rendered:

```bash
python -m comfyaudit.core.cli audit render_00042_.png
```

## Extending the knowledge base

The bundled data is a starting point, not gospel. Add your own weights, or
overrule what's there, in the same format:

```json
{
  "licences": {
    "inhouse": {
      "name": "Facility internal",
      "commercial_use": "yes",
      "fee": "none",
      "summary": "Trained in house on cleared material."
    }
  },
  "models": [
    {
      "id": "house-loras",
      "family": "House LoRAs",
      "licence": "inhouse",
      "match": { "filename": ["studio_skin_detail"] },
      "confidence": "high"
    }
  ]
}
```

Point `--licences` (or the node's `licence_overrides` widget) at it. Pull requests
adding models to the shared knowledge base are very welcome — every entry needs a
`source` link and an honest `confidence`.

## How it knows what it knows

Everything works offline, from data built into the package:

- **Core node schemas** scraped from a real ComfyUI release with an AST pass over
  `nodes.py`, `comfy_extras/` and `comfy_api_nodes/`, handling both the legacy
  `INPUT_TYPES` form and the newer `io.Schema` form. Without the real widget
  order you can't read a UI-format workflow at all — `widgets_values` is a bare
  positional array, so a prompt, a filename and a seed are indistinguishable.
- **Custom node index** from the ComfyUI-Manager registry: ~5,900 packs and
  ~40,000 node class names, with stars and last-commit dates.
- **Known model index** mapping common weight filenames to upstream repos.
- **Licence knowledge base** — every entry carries a `source` and a `confidence`.
- **Base-model licence table** derived from Civitai's published mapping.

Rebuild the catalogs against a newer ComfyUI whenever you like:

```bash
python tools/build_catalog.py --comfyui /path/to/ComfyUI --manager /path/to/manager-json
python tools/build_base_models.py --url
```

## Reading the output honestly

Licences are matched from **filenames**, which aren't authoritative — anyone can
rename a checkpoint. Every verdict reports the pattern it matched and a
confidence level, and a `low` confidence entry is a prompt to check the source,
not an answer. The matcher is strict about boundaries: `ae.safetensors` matches
the FLUX autoencoder, not every file ending in `_vae`.

Against the 588 official ComfyUI workflow templates the current rules identify a
licence for most models and say "unstated" for the rest, which is the honest
answer when a filename tells you nothing.

**This is an engineering tool, not legal advice.** It exists to surface the
questions worth putting to your legal or production team, and to make the answers
reproducible six months later.

## Development

```bash
python -m pytest        # 257 tests
```

The 13 agent tests need `anthropic>=1.0`; on an older SDK they fail with
`tool_runner() got an unexpected keyword argument 'fallbacks'`, which is the
version floor talking rather than a real failure.

```bash
python -m pytest --ignore=tests/test_agent.py   # 244, no SDK needed
```

The agent tests point a real Anthropic client at a local stub of the Messages
API, so tool schemas, the multi-turn loop and the paused-turn restart are
exercised rather than mocked. ComfyUI-facing tests run against a stand-in
`nodes`/`folder_paths` pair. Provenance tests replay real HuggingFace, Civitai
and GitHub response shapes — those fixtures came from upstream source
(`huggingface_hub`'s own `ModelInfo`, Civitai's `model.schema.ts`) rather than
from memory, because the wire format is camelCase where the Python client isn't.

Issues and PRs welcome, particularly:

- workflows that break the parser — that's the most useful bug report there is
- licence entries for models the knowledge base doesn't know
- corrections where a licence reading is wrong or out of date

## Licence

MIT. See [LICENSE](LICENSE).
