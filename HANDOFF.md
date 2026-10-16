# Handoff: ComfyAudit shot history

*Written 2026-10-15 at the end of the session that built the `shot-history` branch.
Read this first, then `README.md` for the user-facing documentation.*

## 1. What this project is

**ComfyAudit** is a ComfyUI custom node pack, pure Python standard library, that
documents what is inside a workflow so someone else (a supervisor, a client, a
legal team) can decide whether it can go on a commercial job. It covers every
model and what its licence says, prompts, assets, custom node packs, and how
much human babysitting it needs. Optionally it reaches a go / no-go given the
studio's territory and revenue.

The `shot-history` branch adds the pieces studio legal teams asked for:

1. **Shot history.** A tamper-evident record of a workflow's whole life,
   embedded in the workflow JSON under `extra.comfyaudit_history`, so it travels
   with the file and into every PNG ComfyUI renders. It records who created and
   changed it, model and prompt changes, source-media origins, renders, reviews,
   approvals and production stage. It is hash-chained, optionally HMAC-signed,
   optionally witnessed by an external ledger, and optionally encrypts prompts.
2. **A provenance record**, an HTML/PDF document for legal. It includes a lane
   flow graph of the shot's life (one lane per person), a model lifeline,
   prompt authorship, source origins, sign-off, a rules checklist and the full
   signed log.
3. **A rules checklist** backed by a sourced knowledge base of 37 rules: union
   agreements, US Copyright Office guidance, EU AI Act, court rulings, US state
   laws and C2PA. It shows which rules apply to a shot and what evidence the
   history already holds.
4. **A commercial licensing guide**, generated from the licence knowledge base:
   how licensing works, the steps to a commercial licence, and every licence
   sorted into permitted, conditional and needs-a-licence.
5. **Conflicting licence data flags.** When a licence contradicts itself (FLUX.1
   [dev]), when an entry contradicts itself, or when sources disagree, the tool
   flags it everywhere and applies the stricter reading.

## 2. Why: the goal

Doug's framing: *make Comfy easy for artists to use **and** easy for legal teams
to trust, so studios can put Comfy workflows and models in the final pixels of
commercial productions. Comfy should become the standard for this.*

The key input was a 2026-10-14 meeting with a major studio's VFX team. Their
legal hurdles shaped the design:

| The studio said | What was built |
|---|---|
| Provenance starts at the studio's tracking-system ID, before anything enters Comfy | `external_ids` on the shot, sealed into every entry |
| "Where did the input image come from?" | Declared origin per source input (artist / client / licensed / photographed / generated / unknown) plus its asset ID |
| "What did the model change from the original?" | Input and output fingerprints per render |
| Runs, settings tried, feedback given | Iteration counts, parameter diffs, review notes |
| Ownership of prompts | Per-prompt authorship in the lineage |
| R&D short vs commercial work | Stages: `sandbox → production → cleared → delivered` |
| Air-gapped content network | Nothing contacts the network; stdlib only |
| A commercial licence raises indemnity questions | Licensing guide: "a commercial licence is not indemnity" |

They also mentioned **MovieLabs**, whose ontology would make a natural future
export target.

## 3. Where things are

| Path | What it is |
|---|---|
| `core/history.py` | The engine: `snapshot`, `diff`, `breakpoints_for`, `append`, `stamp` (returns `StampResult`), `verify` (returns `Verification`), `lineage`, `timeline`, `c2pa_manifest`, `Keys`, `Actor` |
| `core/history_cli.py` | `comfyaudit history stamp/verify/log/record/manifest` and `comfyaudit guide` |
| `core/report/history_html.py` | Provenance record, `flow_svg` (lane graph) and `lifeline_svg` |
| `core/report/compliance.py` | Signals (which rules apply) and evidence checks |
| `core/report/licensing_guide.py` | The licensing guide |
| `core/knowledge/data/regulations.json` | **Generated.** Do not hand-edit; see below |
| `tools/research/regulations.research.json` | The sourced research the regulations come from |
| `tools/build_regulations.py` | Hand-written `MAP`: rule → triggers + evidence checks. Rebuilds `regulations.json` |
| `core/knowledge/data/licences.json` | Licence KB; entries may carry a `conflicts` list |
| `core/knowledge/licences.py` | `licence_conflicts()`: recorded and detected conflicts |
| `server/history_service.py` | Server-side stamping (the key never reaches the browser) |
| `server/routes.py` | `/comfyaudit/history/{stamp,record,manifest,summary}` and `/comfyaudit/licensing-guide` |
| `server/settings.py` | `history_settings()`; the ledger and key paths come from **env only** |
| `web/shothistory.js` | History sidebar tab, auto-stamp on queue and render, settings |
| `tools/make_history_example.py` | Builds a 14-entry demo shot plus ledger, for demos and screenshots |
| `tests/test_history.py` | ~50 tests, including every integrity attack from the review |

### Running things

The package **must be importable as `comfyaudit`**: the tests and the CLI do
`import comfyaudit`. Clone it into a folder literally named `comfyaudit`, or
symlink one:

```bash
# from the folder ABOVE the pack
python -m comfyaudit.core.cli history verify shot.json
cd comfyaudit && python -m pytest -q --ignore=tests/test_agent.py   # 244 passing
python tools/make_history_example.py --out examples/
python tools/build_regulations.py      # after editing MAP or the research file
```

Environment variables (server and CLI):

| Variable | Purpose |
|---|---|
| `COMFYAUDIT_HISTORY_KEY` or `COMFYAUDIT_HISTORY_KEY_FILE` | Studio signing passphrase |
| `COMFYAUDIT_HISTORY_ENC_KEY` | Prompt encryption; needs `pip install cryptography` |
| `COMFYAUDIT_HISTORY_LEDGER` | Append-only JSONL witness file |
| `COMFYAUDIT_HISTORY_CONFIDENTIALITY` | `none` / `redact` / `encrypt` |
| `COMFYAUDIT_USER` | Fallback actor name for the CLI |

## 4. Design decisions worth keeping

- **History lives in `extra`.** ComfyUI preserves `extra` on save and copies it
  into rendered PNGs. API-format prompts have no room for it (every top-level
  key is a node), so they get a sidecar, `<name>.history.json`.
- **Auto-stamp happens *before* queueing** (`app.queuePrompt` is wrapped), so the
  history is serialized into that render's metadata. The `rendered` entry comes
  after `execution_success`. Completions are matched to queued workflows FIFO.
  If the user switched tabs, the render is held in `pending` until they return.
- **Layout is not an edit.** `graph_fingerprint` ignores positions, sizes and
  colours. A save that changed nothing adds nothing; renders, reviews and
  approvals always record.
- **Unchanged snapshots are stored once**, and later entries carry
  `snapshot_ref`. Always go through `snapshot_of(entries, e)`, never
  `e["snapshot"]`.
- **The chain is the authority.** The top-level `history["shot"]` block is an
  unprotected convenience copy. Shot identity is read from `entries[-1]["shot"]`,
  the ledger is keyed by `entries[0]["shot"]["id"]`, and any mismatch is
  damage.
- **Assurance levels:**
  - `absent`: no history.
  - `none` ("Altered"): any damage.
  - `consistency` ("Unverified"): unsigned, or checked without the key.
  - `signed`: every entry signed and verified.
  - `witnessed`: signed, and every entry has a matching ledger row.

  `problems` are damage; `notes` are true but not damage (the workflow moved on,
  or the ledger started late).
- **Mixed signed and unsigned entries count as damage.** An unsigned entry in a
  signed history is exactly what a forger without the key would add.
- **`stamp()` refuses to extend a damaged history.** A new signature would
  launder the damage.
- **Break points are conservative.**
  - An import credits nobody; its prompts are "before tracking".
  - Seeds advanced by `control_after_generate` don't count.
  - Blank notes don't count.
  - A render carrying unsaved edits records them as the person's.
  - Bypassed models are not "in the final render".
- **Compliance checks are mapped only to evidence they actually prove.**
  Anything else (consent riders, disclosures, a consulted director, comp and
  paint done outside Comfy) is a **manual attestation**: a `note` entry with
  `data.evidence = "<rule-id>:<index>"`. A bare rule id deliberately ticks
  nothing.
- **Secrets and paths never live in ComfyUI settings.** Any browser that reaches
  the server can write those settings.
- **Signing scope is stated beside every badge.** A signature proves an entry
  came through the studio key (in practice the studio's ComfyAudit server), not
  which person wrote it. Names are self-declared.

## 5. What we learned

### About the law (sourced in `tools/research/regulations.research.json`)

- **No EU rule requires proving a human was in the loop for creative work.**
  - AI Act Art. 14 (human oversight) covers only high-risk systems; creative
    generation is not one.
  - What applies is **Art. 50 transparency**: providers mark output, and
    deployers disclose deepfakes. There is a lighter duty for evidently artistic
    work, in force from 2026-08-02.
  - The record shows Art. 14 under "Often assumed, but does not apply".
- **Human involvement matters for copyright, not legality.**
  - The US Copyright Office (Part 2, Jan 2025) and the AG Munich ruling
    (Feb 2026) both say prompts, selection and parameter iteration alone are
    not enough.
  - Human-made material that stays visible in the output, and human edits
    after generation, carry the weight.
  - So the tool presents prompts and selection as *process*, and source
    origins and attested edits as *evidence*.
- **MPAA is now the MPA** (renamed 2019). It is a trade association with no
  binding AI production rules.
- **The NO FAKES Act is still a bill.** The TAKE IT DOWN Act covers only
  intimate imagery, so it fires only on an explicit fact.
- **Not verified:** the full 2026 SAG-AFTRA, WGA and DGA contract texts (from
  press and law-firm summaries); C2PA 2.4; the Prague ruling's year; whether
  *Allen v. Perlmutter* has been decided. These are marked in each rule's notes
  and confidence.

### About the licence data

- **FLUX.1 [dev] contradicts itself.** The outputs clause permits commercial use
  except training a competing model. The restrictions clause bars commercial or
  production use of "any data produced by" the model. The KB now records this
  as a conflict and applies the stricter (non-commercial) reading.
  - The KB links `bfl.ai/legal/non-commercial-license-terms`, which may be a
    newer v2. It could not be fetched without Doug approving the URL.
  - Doug should confirm the current terms with Black Forest Labs.

### About building it

- **The adversarial review was worth it.** A separate reviewer found two
  no-key attacks that still showed "Signed":
  - appending an unsigned approval;
  - truncating, then editing the unprotected top-level shot id.

  It also found compliance checks ticking items they didn't prove. All are
  fixed and covered by tests. Repeat this kind of review before any release
  legal will rely on.
- **Things a fresh clone trips over:**
  - The test fixtures (`tests/fixtures/*.json`) were never committed. They are
    generated by `tools/make_example.py`, which writes to `examples/`. They are
    now committed.
  - `.gitignore` was added for `__pycache__/` and `examples/`.
  - The initial commit includes stray `__pycache__` files and a
    `dughogan-claude-comfyui-workflow-audit-gvocwc/Thumbs.db` folder. These are
    harmless; leaving them was the conservative choice.

## 6. What's left to do

**Priority 1: prove it in a real ComfyUI.** Nothing in `web/shothistory.js` has
run against a live frontend; it was only syntax-checked (`node --check`).

1. Install the branch into `custom_nodes` (folder named `comfyaudit`) and start
   ComfyUI. Confirm the log line `comfyaudit … loaded (… web routes)` and that a
   **History** tab appears.
2. Set *Settings → ComfyAudit → Shot history → Your name*.
3. Open a workflow, then **Start tracking** with a shot name and ShotGrid ID.
   Check that `app.graph.extra.comfyaudit_history` exists (browser console).
4. Queue a render. Check that:
   - an *edited* entry appears if the graph changed;
   - a *rendered* entry appears on completion, with the output fingerprinted;
   - the rendered PNG's metadata contains the history.
5. Test the edge cases:
   - batch count above 1;
   - switching workflow tabs while a render runs (the `pending` path);
   - an interrupted or errored run (no render entry);
   - the `graphChanged` listener, which may not exist in Doug's frontend
     version, so refresh may need another hook.
6. Check that `markDirty()` (`changeTracker.checkState`) marks the tab modified,
   so the user saves. If not, find this frontend's equivalent.
7. Click through Review / Approve / Declare source / Change stage / Note,
   then Provenance record, C2PA manifest and Licensing guide.
8. Restart with `COMFYAUDIT_HISTORY_KEY` and `COMFYAUDIT_HISTORY_LEDGER` set and
   confirm the badge reads **Witnessed**.

**Priority 1 result (2026-10-15): passed live** on ComfyUI 0.40.0 with frontend
1.55.17, in a throwaway instance on :8002. Every step above was exercised and
works: the load line, the History tab, the history in `extra`, *edited* before
queue and *rendered* after with a matching SHA-256, the history in the PNG's
`workflow` chunk (through the *edited* entry, by design), batch count 2,
the tab-switch *pending* path, interrupted runs (no entry), `markDirty`, the
forms, all three documents, and **Witnessed** with a key and ledger.

Fixed during the live test:
- `queuePrompt` counted jobs FIFO and multiplied the batch count by
  `app.ui.batchCount`; a rejected prompt (missing model) still added a job, so
  every later render was matched to the wrong one. Jobs are now matched by the
  server's `prompt_id` (`api.queuePrompt` is wrapped; only accepted prompts
  get one).
- Entries sealed a *reference* to the shot's `external_ids`, so adding a
  ShotGrid ID later rewrote earlier entries in memory (JSON round trips hid it
  in the browser; in-process callers broke). Entries now seal a copy.
- Turning the studio key on made every existing (unsigned) history read
  *Altered* and refuse further entries. An unsigned **prefix** is now
  accepted: the first signed entry vouches for it (`signed: from-entry`,
  with a note). Unsigned entries *after* a signed one are still damage.
  **This changes integrity rules: give it an adversarial review.**
  *Reviewed the same day.* The prefix rule holds against editing, inserting,
  reordering, truncating or splicing pre-key entries, replaying a signed entry
  and swapping the shot block. Fixed: (1) a forged unsigned history signed on
  top by the studio server read "Signed" and ticked the *signed* check beside
  a forged approval. The first signed entry proves only that earlier entries
  have not changed since, not that they are true. Now the wording says so,
  `verify` names pre-key decisions as unproven (`signed_from` gives the first
  signed entry), and the *signed* / *verifiable process* checks need every
  entry signed. (2) Stripping `sig` (`key_id` stays in the hash) is damage.
  (3) Cutting entries off the front crashed `verify` and `stamp` with
  infinite recursion in `snapshot_of`.
- Stale-response guard, a notice that never cleared, and a form that carried
  over between tabs.

Changed at Doug's request: **tracking is automatic.** Every workflow starts a
history when it opens (*imported* if it has nodes, crediting nobody; *created*
if blank), without marking the tab modified; there is no Start button unless
*Record history automatically* is off. Shot name defaults to the file name;
**Shot details** renames the shot and adds the ShotGrid ID later (`rename` on
`stamp`). Waits for the frontend's placeholder graph to be replaced at
startup, so no phantom shot is witnessed. New sidebar icon (CSS mask, timeline
ending in a check).

**One tool, and Package for delivery (2026-10-15, Doug's design).** The Audit
and History tabs are now one **ComfyAudit** tab (`web/shothistory.js`;
`web/comfyaudit.js` keeps only the studio-profile settings, status and
export helpers). The everyday view is read-only: stage, commercial standing,
assurance, a dated flag whenever a change makes the shot non-commercial
(`H.commercial_timeline`, which also flags a *second* blocker), the life of
the shot. No "record a human step" buttons. **Package for delivery**
(`server/delivery.py`, routes `history/check` and `history/deliver`):
1. *Check* - asks the studio profile and production facts once, then runs the
   audit on the server.
2. *Make it safe* - fixes flattened from the clearance remedies, with licensed
   alternatives (`core/knowledge/data/alternatives.json`: functional
   equivalents only; each option's licence is read from licences.json and it
   is offered only if commercially usable; installed ones first); inputs with
   no declared origin; rules wanting a person's confirmation (recorded as
   tagged notes). Re-check after changes.
3. *Sign off* - the one deliberate human act. A no-go or no verdict needs a
   recorded exception reason.
4. *Deliver* - the server re-runs the audit, seals the clearance into a
   signed *approved* entry, promotes to *delivered*, and returns a zip:
   workflow, legal report (provenance record with a new Delivery clearance
   section read from the chain), clearance sheet, C2PA manifest (new
   `com.comfyaudit.clearance` assertion), README.

Automatic human steps: a render brought back in as an input is matched by
**content** fingerprint (`snapshot(resolve=...)`, `content_sha256`) and
recorded as a *selection*, with the input's origin set to *generated* (entry N);
the next person to change the shot gets a *handoff* breakpoint. Review stays
deliberate (sign-off). Server resolves inputs only inside ComfyUI's
input/output/temp folders.

**The history follows its files (2026-10-15).**
- *Sidecars:* each render gets `<output>.provenance.json` beside it
  (`history_service.write_sidecar`): the history through the entry that
  made it, plus the file's fingerprint. `comfyaudit history verify-file
  <output>` checks the chain, that the entry is unchanged, and that the file
  is the one recorded (exit 3 if not); the ledger confirms the entry was
  witnessed. Setting: *Provenance file next to every render* (default on).
  The CLI now reads `COMFYAUDIT_HISTORY_KEY_FILE` like the server.
- *Forks:* every entry records the workflow file it was made in (`file`,
  "" when unsaved). `H.fork` starts a new record (new id; same name,
  project and studio IDs; cleared/delivered drop to production) with the
  parent chain embedded frozen under `history.parent`; `verify` checks
  the parent and the link to its head. Triggers: Save As or a copy where the
  old file still holds the shot (`history/relocate`; a missing old file is
  a rename, recorded as `moved`), a duplicated tab (another open tab with
  the same shot), and a stamp onto a copy the ledger shows the shot has moved
  past (`H.ledger_diverged`, auto-fork). A shot only ever named after an
  unsaved tab takes its file's name on first save or fork.
- Not covered: two machines editing the same file without a shared ledger
  (no way to see the other copy); renders whose save node does not report
  outputs to the frontend get no sidecar.
Also: `dreamshaper_8` and many community fine-tunes aren't in the licence KB.

**Life of the shot (2026-10-16).** `core/report/life_viewer.py` renders one
self-contained page from the history: the graph replayed at each entry (each
snapshot now carries `topology` - node ids, types, positions, sizes, modes,
links; titles only when prompts are readable), added/changed/removed nodes and
non-commercial models marked, a scrubber with flags where the shot became
non-commercial, and a story and log in sync. Opens from **Life of the shot**
in the panel (`history/life`) and ships in the delivery zip as
`<shot>.life.html`. Every value from the workflow reaches the page as JSON
and is drawn with textContent.

Multiple tabs and reloads: each tab's history lives in its own graph's
`extra`; renders are matched to their shot by record id (not path), and the
job map and held renders persist in ComfyUI's user data on disk
(`user/<user>/comfyaudit/comfyaudit.history.{jobs,pending}.json`, via
`api.storeUserData`), with localStorage only as a fallback. That matters on
ComfyUI Desktop: browser storage is per origin, and Desktop moves to another
port when 8000 is taken. A reload mid-render recovers the render from
`/history/<prompt_id>`. Live-tested 2026-10-16: render queued in one tab, tab
switched, browser storage wiped, page reloaded; the render was recorded in
its own tab on return. Held renders are recorded
when their tab is next shown, panel open or not. Live-tested with two tabs,
both renders finishing during a reload.

Found in that test: Python floats like 450.0 come back from the browser as
450, which changed entry bytes and read as tampering. Topology now stores
whole numbers as ints, and `test_a_history_survives_a_round_trip_through_the_browser`
guards every entry. A render that cannot be recorded now says so in the panel.

**Smaller gaps closed (2026-10-16).** All configured from the server's
environment only, never ComfyUI settings.
- *Per-person keys* (`4dbdd71`). `COMFYAUDIT_HISTORY_KEYRING` names a JSON
  keyring (`{"people": {"ana": {"name", "role", "secret_file"}}}`);
  `COMFYAUDIT_TRUSTED_USER_HEADER` names the header the studio's sign-in
  proxy sets. Entries are then signed with that person's key
  (`person:<user>:<sha12>`), `verify` checks the name matches the key, and a
  request with no signed-in user or no keyring entry is refused. Without a
  keyring the studio key still applies. Adversarially reviewed 2026-10-16
  (`tests/test_people.py`): the key binds the sign-in *account* (`actor.id`),
  so renaming someone's display name in the keyring is a note, not damage;
  studio-key entries are checked against the studio key even while signing
  as a person, and go unverified (never "Signed") where it is absent; a
  person removed from the keyring leaves their entries unverified; a
  doubled sign-in header is refused. **Deployment rule:** the proxy must
  replace the header, and ComfyUI must be reachable only through it -
  anyone who can reach ComfyUI directly can name any user.
- *C2PA* (`6fc6948`, `server/c2pa.py`). At delivery, each final render whose
  bytes still match its recorded fingerprint is signed with c2patool into
  `signed/` in the zip. `COMFYAUDIT_C2PATOOL` (or PATH),
  `COMFYAUDIT_C2PA_SIGNER` or `COMFYAUDIT_C2PA_KEY` + `_CERT` (`_ALG`).
  c2patool's silent fallback to its development certificate is refused. EXR
  cannot carry an embedded manifest. **Tested only against a stand-in
  c2patool;** run once with the real binary and a studio certificate.
- *MovieLabs OMC-JSON v2.8* (`9fec5cb`, `core/report/omc.py`). In the
  delivery zip as `<shot>.omc.json` and `comfyaudit history omc`. Validated
  against the official schema (fixture in `tests/fixtures/omc/`). OMC has no
  checksum or AI fields: SHA-256 goes in as a second identifier, AI details in
  `customData`.
- *Licence KB 2026-10-16* (`85aa7f2`). 6 licences and 15 rules for popular
  community fine-tunes (33 licences, 69 rules). Note: Z-Image's VAE is named
  `ae.safetensors`, same as FLUX's, so it can be flagged non-commercial when it
  isn't; flagged as a known false positive.
- *ShotGrid / Flow Production Tracking* (`server/shotgrid.py`). REST API,
  standard library. `COMFYAUDIT_SHOTGRID_URL`, `_SCRIPT`, `_KEY` (or
  `_KEY_FILE`), optional `_API` (default `/api/v1.1`) and `_HASH_FIELD` (a
  custom text field on Shot, e.g. `sg_comfyaudit_head`). Shot details checks a
  ShotGrid ID against the site; delivery posts a Note on the shot (verdict,
  sign-off, exception, head hash), attaches the zip, and writes the head hash
  to the field. A ShotGrid failure never undoes a delivery. **Tested only
  against a local fake of the documented endpoints** (`tests/test_shotgrid.py`);
  run once on a trial site or Imageworks' sandbox before Sony relies on it.
  Things a real site may differ on: the upload flow for S3-backed sites
  (signed URL, no auth header), and whether the script user may create Notes.

**Priority 2: known gaps.**

- [ ] **FLUX.1 [dev] terms.** Confirm the current BFL licence and update the
      `flux1-dev-nc` entry and its `conflicts` in `licences.json`.
- [x] **Per-person keys.** Done 2026-10-16 (above). Today one studio key means "Signed" can't name the
      person. Options: per-user HMAC keys (`key_id`), or the ComfyUI multi-user
      header (`comfy-user`) bound to server-side identities.
- [ ] **Recording work done outside ComfyUI.** Comp, paint and grade are the
      strongest copyright evidence, and today they are only attestable as
      notes. A Nuke-side stamp (see `nuke_to_comfyui.py`) or a structured
      "external edit" event with before/after fingerprints would close this.
- [x] **ShotGrid integration** done 2026-10-16 (above); ftrack not started. Read the asset ID automatically
      instead of typing it, and optionally push the head hash back as the
      ledger.
- [x] **C2PA signing.** Done 2026-10-16 (above). Today an unsigned manifest *definition* is exported.
      Wire it to `c2patool` or a signing service when a studio certificate
      exists, and embed it at delivery.
- [x] **MovieLabs OMC export.** Done 2026-10-16 (above). Sony mentioned MovieLabs, not OMC itself, but
      Doug wants studios to read exported history natively. Export each shot
      as OMC Assets plus Provenance (OMC v2.8 has a generic Provenance entity,
      and a creator can be a software Service). Alongside it, fill C2PA 2.4's
      `c2pa.ai-disclosure` assertion (model plus `humanOversightLevel`). The
      only MovieLabs ComfyUI repo (`MovieLabs/comfyui-movielabs-util`) publishes
      to ShotGrid but records no prompts or models; see `interop` in the
      research file.
- [ ] **A delivery gate node.** Fail the queue if a tracked shot lacks a
      history, is Altered, is still in sandbox, or has unconfirmed licence
      conflicts. The existing **Audit Gate** node is the place.
- [ ] **Signing oracle.** Anyone who can reach ComfyUI can POST to
      `/history/stamp` and get a signed entry under any name. Acceptable for a
      single-artist workstation; studios need auth or per-person keys.
- [x] **Regulations refresh (2026-10-15, second pass).** All 37 rules were
      re-checked (several corrected) and 48 added, for 85 in total. Each rule
      now carries `verified_on`, `basis` (what the check rests on),
      `check_result` and `sources`, and the record shows them with an "About
      this research" box. A test fails if any rule lacks them. Rerun yearly or
      when union terms, the EU guidelines or US state laws change.
- [x] **Clearance timeline.** Doug's request: a per-entry commercial status
      (cleared / conditional / blocked / unknown) with a dated, explained flag
      whenever a change makes the workflow unusable commercially (or clears
      it), read both as recorded at the time and against today's licence KB.
- [x] **History viewer: story, log and graph.** Doug's vision: read the shot's
      life as a narrative, as a timestamped log, and as the actual node graph
      replayed over a timeline scrubber, all in sync. The graph needs topology
      (node ids, types, positions, links) in each snapshot, which it does not
      store today.
- [ ] **Production readiness checklist.** One list at the top of the record of
      everything a shot needs before delivery (TPN written client approval and
      AI inventory, performer consent, client notification, commercial
      clearance), feeding the Audit Gate. Shot-level facts (third-party
      characters, AI music) should be declared as signed history entries, not
      global settings; today they come only from the CLI and the server body.
- [ ] **MiniMax risk signal.** *Disney v. MiniMax* (C.D. Cal.; motion to
      dismiss denied 2026-05-22) concerns the Hailuo service. Whether it reaches
      open-weight MiniMax models such as H3 is not addressed. Doug to raise
      with Comfy partnerships/counsel.
- [ ] **Open PR.** Merge `shot-history` into `main` once it has been tested live.

## 7. Context from the wider conversation

- **Doug:** Doug Hogan, Forward Deployed Creative Technologist at Comfy Org, VFX
  and Nuke background. The tool is part of making enterprise studios
  comfortable putting Comfy in final pixels.
