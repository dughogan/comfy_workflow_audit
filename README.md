# Comfy Workflow Tracker

**Every ComfyUI workflow's life, recorded as it happens, and packaged for legal.**
Built for studios putting ComfyUI in final pixels.

https://github.com/user-attachments/assets/586dfb6f-a57e-4b72-9a48-64d60e54f40f

*The demo (1 min 32 s): tracking, Life of the shot, Package for delivery, and
Comfy Audit for legal. No player? [Download it](docs/comfyaudit-demo.mp4).*

Comfy Workflow Tracker does three things:

- **Audits a workflow:** every model it loads and what each licence actually
  says for *your* studio, the custom node packs and their versions, prompts,
  external assets, and what would stop it running elsewhere. It gives a go,
  no-go or go-with-conditions, with the reasoning and a source for every claim.
- **Records the shot's whole life inside the workflow file:** who built it,
  who changed what, which models came and went, which prompts made the
  delivered frames, where the plates came from, and where a person, not a model,
  made the decisions. Tracking is automatic; the record is hash-chained, can be
  signed per person, and travels with the `.json` and its renders.
- **Packages a shot for legal:** a check against your licences and the rules for
  your production, a sign-off sealed into the history, and a zip legal can open
  in **Comfy Audit**, a browser app for legal teams that checks every file
  locally and records their decision back into the shot.

Nothing contacts the network unless you turn on online lookups, so it works on
an air-gapped content network. It is pure Python standard library, so installing
it cannot disturb your environment.

**About the names.** The artist-side tool in ComfyUI is **Comfy Workflow
Tracker**; legal's browser app is **Comfy Audit**. Both were called ComfyAudit
before 2026-10-17. Only the display names changed: the pack still installs and
imports as `comfyaudit`, and node ids, setting ids, the `/comfyaudit/...` routes,
the `COMFYAUDIT_*` environment variables and the keys saved in workflows and
packages are the same, so existing workflows, settings and packages keep loading.

---

## Install

Not in the ComfyUI-Manager registry yet, so for now it's a manual install. The
folder must be named `comfyaudit`:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/dughogan/comfy_workflow_audit comfyaudit
```

Restart ComfyUI. The console prints `comfyaudit … loaded`, and a **Comfy Workflow Tracker**
tab appears in the sidebar.

**If ComfyUI says IMPORT FAILED**, it is almost always the folder level. ComfyUI
loads a pack by looking for `__init__.py` directly inside `custom_nodes/<name>/`:

```
custom_nodes/
  comfyaudit/
    __init__.py      <-- ComfyUI needs to find this here
    core/
    nodes/
```

Anything else shows up as a Python traceback in the ComfyUI console, just above
the IMPORT FAILED line; that traceback is the answer.

Optional extras, each only for the feature named:

```bash
pip install anthropic      # the Claude Review node
pip install cryptography   # encrypted prompts in the history
```

## Quick start for artists

1. **Set your name once:** *Settings → Comfy Workflow Tracker → Shot history → Your name*
   (and your role). Entries are recorded under it.
2. **Open a workflow.** Comfy Workflow Tracker starts its history the moment it opens; there
   is no button to press. Each open tab keeps its own history.
3. **Open the Comfy Workflow Tracker tab** in the sidebar. The header shows the shot's stage,
   whether it can be used commercially, and whether its record verifies. Click
   **edit** under it to name the shot and give its **project / show** (the show
   is what shares legal's answers between shots) and its studio asset ID.
4. **Work as usual.** Edits and renders are recorded on their own, every output
   is fingerprinted, and a render brought back in as an input, a hand-off to
   someone else, Save As and duplicated tabs are noted too. Save the workflow to
   keep the latest entries in the file.
5. **Life of the shot** replays the graph over a timeline, with a plain-language
   story and a timestamped log.
6. **Package for delivery** when the shot is ready:
   - **Check:** your studio profile (asked once) and the licences of every model
     and pack, with the rules that apply to this production.
   - **Make it safe:** what blocks a clean yes and how to fix it, including
     licensed alternatives you already have installed. Items only legal or
     production can confirm (a client's written approval, a credit disclosure)
     are listed under *Left for legal*; there is nothing for the artist to tick.
   - **Sign off:** the audit runs again on the server, and its verdict is sealed
     into the history with your name. A no-go needs a recorded reason.
   - **Deliver:** you get a zip for legal.

**What's in the package:**

| File | For |
|---|---|
| `<shot>.json` | The workflow, with its full history and the sealed sign-off |
| `<shot>.legal-report.html` | The provenance record for legal: who did what and when, models over time, prompt authorship, sources, the rules checklist |
| `<shot>.licence-audit.html` | Every model and node pack, its licence, what it allows for this studio, and the risk score |
| `<shot>.life.html` | The Life of the shot page; opens in any browser, offline |
| `<shot>.omc.json` | The same history as MovieLabs OMC-JSON v2.8, for studio asset systems |
| `<shot>.c2pa.json`, `signed/` | A C2PA manifest, and renders signed with it when the studio has set up c2patool |
| `<shot>.package.json` | An index for software and legal agents: the decision, what is still open, every file and its SHA-256 |
| `README.txt` | The package explained for people, with legal's open checklist |

Every HTML report also carries its data as JSON in
`<script type="application/json" id="comfyaudit-data">`, so an agent reading
the package gets exact data instead of scraping prose.

## For legal teams: Comfy Audit

A static web app (`review-web/`) that legal opens in a browser. They drop in a
delivery zip and it:

- opens the package **in the browser**, checks every file's SHA-256 against the
  index and re-hashes the history's chain, so nothing is uploaded anywhere;
- shows what was decided at sign-off, what is still open, and what was already
  answered for the show or the studio;
- lets them **answer an item once for the whole show** (for example, the
  client's written approval of the AI tools) or for the whole studio; every
  shot of that show inherits the answer;
- lets them **clear a shot or send it back** with a reason. The decision is
  signed into the shot's history and shows in the artist's Comfy Workflow Tracker tab as
  *Legal cleared* or *Sent back by legal*.

Each studio deploys its own copy. The page can go on any static host, and writes
go only to the studio's own review service, `python -m comfyaudit.review`, which
signs each person's decisions. See [review-web/README.md](review-web/README.md)
to deploy it, and set `COMFYAUDIT_REVIEW_URL` on the ComfyUI server so the
package step links artists to it. Open the page with `?demo` to try it with a
fictional show.

## Studio setup

People set their own name and role, and the studio profile, in ComfyUI's
settings (*Settings → Comfy Workflow Tracker*). Everything that is a secret or a file path
comes **only from the server's environment**, never from ComfyUI settings,
because those are writable from any browser that can reach the server.

| Variable | What it does |
|---|---|
| `COMFYAUDIT_HISTORY_KEY` or `COMFYAUDIT_HISTORY_KEY_FILE` | Studio key that signs every history entry (HMAC-SHA256) |
| `COMFYAUDIT_HISTORY_KEYRING` + `COMFYAUDIT_TRUSTED_USER_HEADER` | Per-person keys: sign each entry with the signed-in person's own key, named by your sign-in proxy's header |
| `COMFYAUDIT_HISTORY_LEDGER` | Append-only ledger file outside the workflow; catches histories that were cut short, replaced or rewritten |
| `COMFYAUDIT_HISTORY_ENC_KEY`, `COMFYAUDIT_HISTORY_CONFIDENTIALITY` | Keep prompts as fingerprints only, or encrypted |
| `COMFYAUDIT_ANSWERS_FILE` | Where show and studio answers live; point every machine at one shared file |
| `COMFYAUDIT_C2PATOOL`, `COMFYAUDIT_C2PA_KEY` + `_CERT` (or `_SIGNER`) | Sign delivered renders with C2PA Content Credentials using your studio certificate |
| `COMFYAUDIT_REVIEW_URL` | Your Comfy Audit page, linked from the package step |
| `COMFYAUDIT_SHOTGRID_URL`, `_SCRIPT`, `_KEY` | ShotGrid integration (**beta**, see below) |

## Other ways to run it

**From the menu:** *Extensions → Comfy Workflow Tracker* has the Comfy Workflow Tracker tab, Package for
delivery, the provenance record and the commercial licensing guide, also
available from the command palette.

**As nodes:** they live under the **`audit`** category. Double-click the canvas
and type `Audit`.

| Node | What it does |
|---|---|
| **Studio Profile** | Optional override of the settings profile, for a show whose circumstances differ from the facility's. |
| **Audit This Workflow** | Documents the running graph. Outputs the report as Markdown and JSON, plus the risk score, automation index and a one-line licence summary as separate sockets. |
| **Claude Review** | Optional second pass for the judgement calls; see below. |
| **Audit Gate** | Stops the queue on conditions **you** choose. Nothing is enforced by default. |
| **Shot History Gate** | Put it between VAE Decode and Save Image. Stops the run before anything is saved if the shot has no history, its history has been altered, it has not reached the stage you require (e.g. still in sandbox), or, if you switch them on, a model bars commercial use or legal has not cleared the shot. |
| **Save Audit Report** | Writes the report into your output folder as HTML, Markdown or JSON. |

**From the command line:** see [Command line](#command-line) below.

Documents open over the canvas and save as HTML or PDF (PDF goes through your
browser's print dialog: pick *Save as PDF*). There is no PDF library here on
purpose; the pack declares no dependencies.

## What has to happen

**Entirely optional, and off until you fill it in.** With nothing set, every
report opens with *In plain terms* — a few paragraphs saying what the workflow
contains, which licence conditions are actually in play, and which facts would
settle whether they suit a job. That summary is deterministic: no API key, no
network, no cost.

Fill in the profile and those same reports also work out what the terms
mean for you.

**Settings → Comfy Workflow Tracker → Studio profile.** It lives there rather than on a node
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

Point at that file — **Settings → Comfy Workflow Tracker → Registry**, or `--registry` — and
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
| **Who** | Name and role as set in *Settings → Comfy Workflow Tracker → Shot history*, the machine, and the tool |
| **Models** | Every weight, with its licence reading at that moment. Additions, removals and LoRA strength changes are diffed per entry |
| **Prompts** | Each prompt's text (or only its SHA-256, or encrypted) and **who first wrote it** |
| **Nodes** | Core, custom (with pack and pinned version) and hosted-API nodes |
| **Source media** | Every supplied image, plate or clip, fingerprinted, with a declared origin and the studio's own asset ID |
| **Settings** | Seeds, steps, CFG, sampler, resolution, bypassed branches |
| **Outputs** | Rendered files, fingerprinted, so a delivered frame can be traced to the entry that made it |
| **Stage** | `sandbox → production → cleared → delivered`. Sandbox work never ships; promotion is where clearance starts |
| **Studio IDs** | The ShotGrid, ftrack or other tracking-system entity the asset was born as, so provenance starts there and not in ComfyUI |

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
ComfyUI that is the studio's Comfy Workflow Tracker server. It does not prove *which*
person wrote the entry. Names are recorded as each person declared them, and
the record says so beside every badge. **Per-person keys** close this gap:
with `COMFYAUDIT_HISTORY_KEYRING` and a trusted sign-in header
(`COMFYAUDIT_TRUSTED_USER_HEADER`) from the studio's own sign-in proxy, every
entry is signed with the signed-in person's own key, and the record names who
signed it.

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

The **Comfy Workflow Tracker** tab in the sidebar. Tracking is automatic: every workflow
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
`comfyaudit history verify-file`. If that file is lost, the saved workflow is
enough: `comfyaudit history verify-file render.png --workflow shot.json` finds
the entry that recorded the render, by its contents rather than its name
(including renders made before a Save As), and checks the whole history. It
needs the workflow to have been saved after the render. An optional ledger file
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
(`core/knowledge/data/regulations.json`) has 85 rules, each with its sources,
the date it was last checked, what the check rests on, and a confidence level.
Every report says when this research was done and that it is AI-researched, not
legal advice. It covers:

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

Some evidence is checked automatically, and only where the record fully
proves the item: the model behind each shot, prompt and iteration logs, human
authors, human-made source material, declared sources, a reviewed final, a
sign-off that covers the final render, a verified signature, fingerprinted
outputs, a delivered final, and renders signed with C2PA at delivery. Anything
that needs a judgement stays with a person: a reviewer is not a consulted
director, and a prompt log is not proof that no real performer was used.

Evidence only a person can attest is answered at the level it belongs to:

- **per shot:** consents, per-deliverable flags, similarity checks, edits made
  outside ComfyUI;
- **once per show:** for example a client's written approval of the AI tools
  for the project, or a union notice; every shot whose project matches inherits
  the answer;
- **once per studio:** for example the AI policy, or provider-or-deployer status
  under the EU AI Act.

Show and studio answers are signed like history entries and stored outside the
workflow (`COMFYAUDIT_ANSWERS_FILE`); a delivered package seals a copy of every
answer it relied on. Legal records them in Comfy Audit; anyone can also
record them from the package step's *Left for legal* list.

One common assumption is answered directly. **No EU rule requires proof that a
human was in the loop for a creative shot.** The AI Act's human-oversight
article (Art. 14) applies only to high-risk systems, and the record says so.
Human involvement matters for *copyright*, in the US, the EU and China, which
is why the history records it so carefully.

Refresh the research, then rebuild with `python tools/build_regulations.py`. The
mapping from each rule to its triggers and checks is set by hand in that
script, not inferred.

### Content credentials

Every package carries a C2PA manifest for the final render. It marks generated
output with the IPTC `trainedAlgorithmicMedia` source type, or
`compositeWithTrainedAlgorithmicMedia` when supplied media went in, and carries
the history (keyed to its head hash) and the sealed clearance as Comfy Workflow Tracker's
own assertions.

When the studio has set up [c2patool](https://github.com/contentauth/c2patool)
with its own certificate (`COMFYAUDIT_C2PATOOL`, `COMFYAUDIT_C2PA_KEY` and
`COMFYAUDIT_C2PA_CERT`, or `COMFYAUDIT_C2PA_SIGNER` for a KMS or HSM),
delivery signs each final render into the package's `signed/` folder, but only
if the file still matches the fingerprint recorded when it was rendered. The
originals are never touched. c2patool's built-in development certificate is
refused, and a certificate that is not on the C2PA trust list is signed but
reported as such. EXR cannot carry an embedded manifest; those renders rely on
the provenance file and the manifest. `comfyaudit history manifest` writes the
manifest definition on its own.

### Shot history from the command line

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
comfyaudit history verify-file render.png                            # against its .provenance.json
comfyaudit history verify-file render.png --workflow shot.json       # or against the saved workflow
comfyaudit history log shot.json
comfyaudit history record shot.json --distribute US,EU --likeness   # provenance record
comfyaudit history manifest shot.json                                # C2PA definition
comfyaudit history omc shot.json                                     # MovieLabs OMC-JSON v2.8
```

UI-format workflows are updated in place. API-format prompts have nowhere to
carry history, so theirs lives beside them as `<name>.history.json`.

## Commercial licensing guide

```bash
comfyaudit guide -o licensing-guide.html
```

It is also under *Extensions → Comfy Workflow Tracker* and at `/comfyaudit/licensing-guide`. It is a
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
