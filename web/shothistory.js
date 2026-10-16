/**
 * ComfyAudit shot history - the canvas side.
 *
 * The history lives inside the workflow (extra.comfyaudit_history), so it has
 * to be written by whoever holds the open graph, which is this page. The
 * signing key lives on the server, so every entry is made there: we post the
 * serialised graph, the server appends a signed entry, and we adopt the
 * returned history into app.graph.extra.
 *
 * Automatic entries:
 *   - as soon as a workflow is open: tracking starts by itself ("imported" for
 *     a graph that already has nodes, which credits nobody for them; "created"
 *     for a blank one). The tab is not marked modified just for opening.
 *   - just before a queue: an "edited" entry if the graph changed since the last
 *     one. Stamping *before* serialisation is what puts the history into the
 *     PNG/video metadata of everything that render produces.
 *   - when a queued prompt finishes: a "rendered" entry naming its saved outputs,
 *     which the server fingerprints.
 * Everything a person decides - review, approval, where a plate came from,
 * moving the shot to production - is a button in the History tab, because
 * those are exactly the moments that have to be attributable to a person.
 */
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { PROFILE_FIELDS, profileIsSet, setSetting, saveBlob, downloadPdf } from "./comfyaudit.js";

const PREFIX = "/comfyaudit/history";
const SIDEBAR_ID = "comfyaudit.history";
const EXTRA_KEY = "comfyaudit_history";
const ICON_CLASS = "comfyaudit-history-icon";

/** Sidebar mark: a timeline of entries ending in a sign-off. */
const ICON_SVG = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' "
  + "stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><circle cx='5' cy='5' r='2'/>"
  + "<circle cx='5' cy='12' r='2'/><circle cx='5' cy='19' r='2'/><path d='M5 7v3M5 14v3M10 5h10M10 12h7"
  + "M11 18.5l2.5 2.5 5.5-6'/></svg>";

function installIcon() {
  if (document.getElementById(ICON_CLASS)) return;
  const url = `url("data:image/svg+xml,${encodeURIComponent(ICON_SVG)}")`;
  const style = document.createElement("style");
  style.id = ICON_CLASS;
  style.textContent = `.${ICON_CLASS}{display:inline-block;width:1.15em;height:1.15em;`
    + `background-color:currentColor;-webkit-mask:${url} center/contain no-repeat;mask:${url} center/contain no-repeat}`;
  document.head.append(style);
}

const UI = {
  fg: "var(--fg-color, #e6e6e6)",
  dim: "var(--descrip-text, #9aa0a6)",
  bg: "var(--comfy-menu-bg, #202020)",
  field: "var(--comfy-input-bg, #2a2a2a)",
  line: "var(--border-color, #3a3a3a)",
};
const TONE = { ok: "#4a9c63", warn: "#c08a2e", stop: "#c4483a", flat: "#8a929b", accent: "#4fa3c4" };
const ASSURANCE = {
  witnessed: ["Witnessed", TONE.ok],
  signed: ["Signed", TONE.ok],
  consistency: ["Unverified", TONE.warn],
  none: ["Altered", TONE.stop],
  absent: ["No history", TONE.flat],
};

/* ------------------------------------------------------------------ */
/* Settings                                                            */
/* ------------------------------------------------------------------ */

const SETTINGS = [
  {
    id: "ComfyAudit.History.UserName", name: "Your name", type: "text", defaultValue: "",
    tooltip: "Recorded on every history entry you make. Use the name your legal and production teams know you by.",
    category: ["ComfyAudit", "Shot history", "Your name"],
  },
  {
    id: "ComfyAudit.History.Role", name: "Your role", type: "text", defaultValue: "",
    tooltip: "Compositor, VFX supervisor, legal, client... shown beside your name.",
    category: ["ComfyAudit", "Shot history", "Your role"],
  },
  {
    id: "ComfyAudit.History.Auto", name: "Record history automatically", type: "boolean", defaultValue: true,
    tooltip: "Track every workflow from the moment it is open, add an entry when it is queued with changes, and another when its render finishes. Turn off to start tracking by hand.",
    category: ["ComfyAudit", "Shot history", "Automatic"],
  },
  {
    id: "ComfyAudit.History.Confidentiality", name: "Prompts in the history", type: "combo",
    options: ["prompts readable", "prompts as fingerprints only", "prompts encrypted"],
    defaultValue: "prompts readable",
    tooltip: "Fingerprints prove which prompt was used without revealing it. Encryption needs the studio key and the 'cryptography' package on the server.",
    category: ["ComfyAudit", "Shot history", "Confidentiality"],
  },
  {
    id: "ComfyAudit.History.DistributeIn", name: "Distributed in", type: "text", defaultValue: "",
    tooltip: "Territory codes where finished work is made available, comma separated (US, EU, GB, CN...). Decides which disclosure rules apply.",
    category: ["ComfyAudit", "Shot history", "Distribution"],
  },
  {
    id: "ComfyAudit.History.UnionProduction", name: "Union or guild production", type: "boolean", defaultValue: false,
    tooltip: "SAG-AFTRA, DGA or WGA terms apply to this show. Adds their AI provisions to the provenance record's checklist.",
    category: ["ComfyAudit", "Shot history", "Union production"],
  },
  {
    id: "ComfyAudit.History.Advertising", name: "Advertising work", type: "boolean", defaultValue: false,
    tooltip: "Commercials: adds the SAG-AFTRA Commercials, New York synthetic-performer disclosure, FTC and ad-platform rules.",
    category: ["ComfyAudit", "Shot history", "Advertising"],
  },
  {
    id: "ComfyAudit.History.FeatureFilm", name: "Feature film", type: "boolean", defaultValue: false,
    tooltip: "Adds the Academy's AI disclosure rules for awards eligibility.",
    category: ["ComfyAudit", "Shot history", "Feature film"],
  },
  {
    id: "ComfyAudit.History.UnionCrew", name: "Union crew (IATSE / Animation Guild)", type: "boolean", defaultValue: false,
    tooltip: "Crew work under IATSE or Animation Guild terms. Adds their AI notice, consultation and scan-consent rules.",
    category: ["ComfyAudit", "Shot history", "Union crew"],
  },
  {
    id: "ComfyAudit.History.Animation", name: "Animated production", type: "boolean", defaultValue: false,
    tooltip: "An animated production. With union crew, adds the Animation Guild's AI terms.",
    category: ["ComfyAudit", "Shot history", "Animation"],
  },
  {
    id: "ComfyAudit.History.TPNVendor", name: "TPN-assessed vendor", type: "boolean", defaultValue: false,
    tooltip: "Your studio handles MPA member studios' content under TPN content security. Adds the MPA/TPN AI controls: written client approval, an approved-AI inventory and an AI audit log.",
    category: ["ComfyAudit", "Shot history", "TPN"],
  },
  {
    id: "ComfyAudit.History.EOInsurance", name: "E&O insured", type: "boolean", defaultValue: false,
    tooltip: "The production carries errors and omissions insurance. Adds what underwriters ask about AI use.",
    category: ["ComfyAudit", "Shot history", "E&O insurance"],
  },
  {
    id: "ComfyAudit.History.CopyrightClaim", name: "Registering copyright", type: "boolean", defaultValue: false,
    tooltip: "The studio or client will register or enforce copyright in this work. Adds human-authorship rules in more territories.",
    category: ["ComfyAudit", "Shot history", "Copyright"],
  },
  {
    id: "ComfyAudit.History.Sidecars", name: "Provenance file next to every render", type: "boolean", defaultValue: true,
    tooltip: "Write <render>.provenance.json beside each output, holding the shot's history through the entry that made it and the file's fingerprint. EXR, video and 3D outputs cannot carry the workflow themselves; with this, every output can be verified on its own.",
    category: ["ComfyAudit", "Shot history", "Sidecars"],
  },
  {
    id: "ComfyAudit.History.Clients", name: "Clients", type: "text", defaultValue: "",
    tooltip: "Who commissioned the work, comma separated (Netflix, ...). Adds that client's published AI rules for vendors.",
    category: ["ComfyAudit", "Shot history", "Clients"],
  },
];

function getSetting(id, fallback) {
  try {
    const v = app.extensionManager?.setting?.get?.(id);
    if (v !== undefined && v !== null) return v;
  } catch (e) { /* fall through */ }
  try {
    const v = app.ui?.settings?.getSettingValue?.(id);
    if (v !== undefined && v !== null) return v;
  } catch (e) { /* fall through */ }
  return fallback;
}

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

const state = { summary: null, busy: false, error: "", form: null, notice: "", view: "home" };
let mount = null;
/**
 * A Map that survives a reload in the middle of a render. It is kept in
 * ComfyUI's user data on disk (user/<user>/comfyaudit/), which the Desktop app
 * and a browser share and which does not depend on the page's origin: Desktop
 * moves to another port when 8000 is taken, and that would empty the
 * browser's storage. localStorage is only the fallback when the server cannot
 * be reached. Entries older than a week are dropped.
 */
class StoredMap {
  constructor(name) {
    this.name = name;
    this.file = `comfyaudit/${name}.json`;
    this.map = new Map();
    this.timer = null;
    this.early = null;  // keys changed before the disk copy arrived
    try { this.adopt(JSON.parse(localStorage.getItem(name) || "[]")); }
    catch (e) { /* storage unavailable: memory only */ }
    this.early = new Set();
    this.ready = this.load().finally(() => { this.early = null; });
  }
  adopt(raw) {
    const cutoff = Date.now() - 7 * 864e5;
    this.map = new Map();
    for (const [k, v] of raw || []) if ((v?.at || 0) > cutoff) this.map.set(k, v);
  }
  /** The copy on disk wins: the one in this page's storage may be stale or another port's. */
  async load() {
    try {
      const r = await api.getUserData(this.file);
      if (r.status === 200) {
        const mine = new Map([...this.early].map((k) => [k, this.map.get(k)]));
        this.adopt(await r.json());
        for (const [k, v] of mine) v === undefined ? this.map.delete(k) : this.map.set(k, v);
        if (mine.size) this.save(); else this.saveLocal();
      }
      else if (r.status === 404 && this.map.size) this.saveDisk();
    } catch (e) { /* server unreachable: keep the local copy */ }
  }
  saveLocal() { try { localStorage.setItem(this.name, JSON.stringify([...this.map])); } catch (e) { /* memory only */ } }
  saveDisk() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      api.storeUserData(this.file, [...this.map], { overwrite: true, stringify: true })
        .catch((e) => console.warn("[comfyaudit] could not keep pending renders on disk", e));
    }, 200);
  }
  save() { this.saveLocal(); this.saveDisk(); }
  get(k) { return this.map.get(k); }
  has(k) { return this.map.has(k); }
  set(k, v) { this.early?.add(k); this.map.set(k, v); this.save(); return this; }
  delete(k) { this.early?.add(k); const r = this.map.delete(k); this.save(); return r; }
  keys() { return [...this.map.keys()]; }
  get size() { return this.map.size; }
}

/** Queued prompts by the server's prompt_id, so each completion finds its own workflow. */
const jobs = new StoredMap("comfyaudit.history.jobs");
/** The workflow being queued while app.queuePrompt runs; api.queuePrompt reads it. */
let queueContext = null;
/** Outputs gathered per prompt from 'executed' events. */
const outputs = new Map();
/** Renders that finished while a different workflow was open, by shot. */
const pending = new StoredMap("comfyaudit.history.pending");

/**
 * Which shot the open tab is. The record id travels inside the workflow, so a
 * render finds its shot again after a rename, a Save As or a reload; a tab
 * not yet tracked falls back to its file path.
 */
function shotKey() {
  return app.graph?.extra?.[EXTRA_KEY]?.entries?.[0]?.shot?.id || workflowKey();
}

function hold(key, outs) {
  const prev = pending.get(key);
  pending.set(key, { at: Date.now(), renders: [...(prev?.renders || []), outs] });
}

function workflowKey() {
  const wf = app.extensionManager?.workflow?.activeWorkflow;
  return wf?.path || wf?.key || wf?.filename || "unsaved";
}

function graphName() {
  const wf = app.extensionManager?.workflow?.activeWorkflow;
  return wf?.filename || wf?.name || "workflow";
}

function tracked() {
  return !!app.graph?.extra?.[EXTRA_KEY];
}

function markDirty() {
  try { app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.(); }
  catch (e) { /* older frontends track changes on their own */ }
}

async function post(path, payload) {
  const r = await api.fetchApi(`${PREFIX}/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  let data;
  try { data = await r.json(); } catch (e) { throw new Error(`Server returned ${r.status}.`); }
  if (!r.ok || data.error) throw new Error(data.error || `Server returned ${r.status}.`);
  return data;
}

/**
 * Make one entry against the open graph and adopt the result. ``quiet`` leaves
 * the tab unmarked: starting the history is not an edit the artist made.
 * Returns null when the artist switched workflows while the request was in
 * flight, because the answer belongs to the graph that was serialised.
 */
async function stamp(event, extra = {}, { quiet = false } = {}) {
  const graph = app.graph;
  // The frontend reuses one graph object across tabs, but every load replaces
  // its `extra`, so that object identifies the workflow this request was for.
  graph.extra = graph.extra || {};
  const owner = graph.extra;
  const workflow = graph.serialize();
  const data = await post("stamp", { workflow, event, name: shotDefaultName(), ...where(), ...extra });
  if (app.graph !== graph || graph.extra !== owner) return null;
  if (data.history) {
    owner[EXTRA_KEY] = data.history;
    if ((data.recorded && !quiet) || data.forked) markDirty();
  }
  if (data.forked) state.notice = forkNotice(data.forked);
  state.summary = data.summary;
  return data;
}

/** Which workflow file the open tab is ("" for an unsaved one). */
function where() {
  const wf = app.extensionManager?.workflow?.activeWorkflow;
  return { path: wf?.path || "", persisted: !!wf?.isPersisted };
}

function forkNotice() {
  return "This copy of the shot is now its own record, carrying the history it came from. Save it to keep that.";
}

function chainShotId(history) {
  return history?.entries?.[0]?.shot?.id || "";
}

function lastFile(history) {
  for (const e of [...(history?.entries || [])].reverse()) {
    if ("file" in e) return e.file;
  }
  return null;
}

/** In flight, by workflow key, so one tab is checked once at a time. */
const placing = new Set();

/**
 * Is this tab where its history says it was made? Save As, a duplicated tab
 * or a render opened again leave two workflows claiming one shot; the server
 * forks the copy into its own record. A moved or renamed file is noted.
 */
async function checkPlacement() {
  const history = app.graph?.extra?.[EXTRA_KEY];
  if (!history) return;
  const key = workflowKey();
  if (placing.has(key)) return;
  const here = where();
  const now = here.persisted ? here.path : "";
  const before = lastFile(history);
  const id = chainShotId(history);
  let duplicateOf = "";
  if (!here.persisted) {
    for (const wf of app.extensionManager?.workflow?.openWorkflows || []) {
      if (wf === app.extensionManager.workflow.activeWorkflow) continue;
      const other = wf?.changeTracker?.activeState?.extra?.[EXTRA_KEY];
      if (other && chainShotId(other) === id) { duplicateOf = wf.path || "another tab"; break; }
    }
  }
  if (!duplicateOf && before === "" && now) {
    // First save of a tab tracked while unsaved: note the file, and if the
    // shot still carries the tab's temporary name, take the file's.
    placing.add(key);
    try {
      const temporary = /^Unsaved Workflow/i.test(history.shot?.name || "");
      await stamp("moved", { data: { from: "", to: now },
        ...(temporary ? { name: shotDefaultName(), rename: true } : {}) });
      rerender();
    } catch (e) {
      console.warn("[comfyaudit] could not record the first save", e);
    } finally {
      placing.delete(key);
    }
    return;
  }
  if (!duplicateOf && (before === null || before === "" || before === now)) return;
  placing.add(key);
  const graph = app.graph;
  const owner = graph.extra;
  try {
    const data = await post("relocate", { workflow: graph.serialize(), ...here, name: shotDefaultName(),
      ...(duplicateOf ? { duplicate_of: duplicateOf } : {}) });
    if (app.graph !== graph || graph.extra !== owner) return;
    if (data.recorded && data.history) {
      owner[EXTRA_KEY] = data.history;
      markDirty();
    }
    if (data.forked) state.notice = forkNotice(data.forked);
    if (data.summary) state.summary = data.summary;
    rerender();
  } catch (e) {
    console.warn("[comfyaudit] could not check where this workflow came from", e);
  } finally {
    placing.delete(key);
  }
}

function shotDefaultName() {
  return graphName().replace(/\.json$/i, "");
}

let shownTimer = null;

/**
 * A workflow was loaded or switched to. The frontend configures the graph
 * before it updates the active workflow, so wait for that to settle before
 * reading the workflow's name; and never make a load wait on the history.
 */
function onWorkflowShown() {
  clearTimeout(shownTimer);
  shownTimer = setTimeout(async () => {
    await autoStart();
    await checkPlacement();
    // Renders held for this shot are recorded whether or not the panel is open.
    await flushPending();
    if (mount) refresh();
  }, 150);
}

/** In flight, by workflow key, so a burst of load events starts one history. */
const starting = new Set();

/** Track every open workflow from the moment it is open. */
async function autoStart() {
  if (!app.graph || tracked() || !getSetting("ComfyAudit.History.Auto", true)) return;
  // At startup the frontend shows a placeholder graph before it restores the
  // open tabs; starting a history on that would witness a shot that never was.
  const active = app.extensionManager?.workflow?.activeWorkflow;
  if (!active || active.isLoading) return;
  const key = workflowKey();
  if (starting.has(key)) return;
  starting.add(key);
  try {
    const nodes = app.graph._nodes?.length ?? app.graph.nodes?.length ?? 0;
    await stamp(nodes ? "imported" : "created", {}, { quiet: true });
  } catch (e) {
    console.warn("[comfyaudit] could not start the history", e);
  } finally {
    starting.delete(key);
  }
  rerender();
}

async function refresh() {
  if (!app.graph) return;
  if (!tracked()) { state.summary = { present: false }; rerender(); return; }
  try {
    state.summary = await post("summary", { workflow: app.graph.serialize() });
  } catch (e) { state.error = e.message; }
  rerender();
}

async function act(fn) {
  state.busy = true; state.error = ""; rerender();
  try { await fn(); state.form = null; } catch (e) { state.error = e.message; }
  state.busy = false; rerender();
}

/* ------------------------------------------------------------------ */
/* Automatic entries                                                   */
/* ------------------------------------------------------------------ */

async function beforeQueue() {
  if (!tracked() || !getSetting("ComfyAudit.History.Auto", true)) return;
  // A save or Save As since the last entry changes which file this is.
  await checkPlacement();
  await flushPending();
  try { await stamp("edited"); } catch (e) { console.warn("[comfyaudit] history stamp failed", e); }
}

async function flushPending() {
  await pending.ready;
  const key = shotKey();
  const list = pending.get(key)?.renders;
  if (!list?.length || !tracked()) return;
  pending.delete(key);
  for (const outs of list) {
    try {
      if (!(await stamp("rendered", { outputs: outs }))) hold(key, outs);
    } catch (e) { console.warn(e); }
  }
  if (!pending.size) state.notice = "";
}

function wrapQueue() {
  const original = app.queuePrompt?.bind(app);
  if (!original) return false;
  app.queuePrompt = async function (...args) {
    const ctx = { key: shotKey(), tracked: tracked(), at: Date.now() };
    await beforeQueue();
    queueContext = ctx;
    try { return await original(...args); } finally { queueContext = null; }
  };
  // Only a prompt the server accepted gets a prompt_id, so a rejected prompt
  // (a missing model, a bad input) never leaves a job waiting for a render
  // that will not come. Batches call this once per prompt.
  const apiQueue = api.queuePrompt?.bind(api);
  if (apiQueue) {
    api.queuePrompt = async function (...args) {
      const res = await apiQueue(...args);
      if (res?.prompt_id && queueContext) jobs.set(res.prompt_id, queueContext);
      return res;
    };
  }
  return true;
}

let recoverJobs = async () => {};

function listen() {
  api.addEventListener("executed", ({ detail }) => {
    const id = detail?.prompt_id || "current";
    const saved = [];
    for (const list of Object.values(detail?.output || {})) {
      if (!Array.isArray(list)) continue;
      for (const item of list) {
        if (item && item.filename && item.type === "output") saved.push(item);
      }
    }
    if (saved.length) outputs.set(id, [...(outputs.get(id) || []), ...saved]);
  });

  const finish = async (detail, success) => {
    const id = detail?.prompt_id || "current";
    const job = jobs.get(id);
    jobs.delete(id);
    const outs = outputs.get(id) || [];
    outputs.delete(id);
    if (!success || !job?.tracked || !getSetting("ComfyAudit.History.Auto", true)) return;
    if (job.key === shotKey() && tracked()) {
      try {
        if (!(await stamp("rendered", { outputs: outs }))) hold(job.key, outs);
        rerender();
      } catch (e) {
        console.warn("[comfyaudit] render entry failed", e);
        state.error = `A render could not be added to this shot's history: ${e.message}`;
        rerender();
      }
    } else {
      hold(job.key, outs);
      state.notice = "A render finished while another workflow was open. It will be recorded "
        + "when you return to that workflow.";
      rerender();
    }
  };
  api.addEventListener("execution_success", ({ detail }) => finish(detail, true));
  // Jobs that were running when the page was reloaded: ask ComfyUI how they
  // ended, and record the ones that finished from its record of their outputs.
  recoverJobs = async () => {
    await jobs.ready;
    for (const id of jobs.keys()) {
      try {
        const r = await api.fetchApi(`/history/${encodeURIComponent(id)}`);
        const item = (await r.json())?.[id];
        if (!item?.status?.completed && item?.status?.status_str !== "error") continue;
        const saved = [];
        for (const out of Object.values(item.outputs || {})) {
          for (const list of Object.values(out || {})) {
            if (!Array.isArray(list)) continue;
            for (const it of list) if (it?.filename && it.type === "output") saved.push(it);
          }
        }
        outputs.set(id, saved);
        await finish({ prompt_id: id }, item.status.status_str === "success");
      } catch (e) { /* still queued or the server is unreachable: try next load */ }
    }
  };
  api.addEventListener("execution_error", ({ detail }) => finish(detail, false));
  api.addEventListener("execution_interrupted", ({ detail }) => finish(detail, false));
}

/* ------------------------------------------------------------------ */
/* Widgets                                                             */
/* ------------------------------------------------------------------ */

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el[k.toLowerCase()] = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/* ``always``: stays clickable while the panel is busy (the document overlay is
 * built inside a busy action and never redrawn, so it must not inherit that). */
function btn(label, onClick, variant = "ghost", { always = false } = {}) {
  const s = {
    font: "500 12px/1 inherit", padding: "7px 10px", borderRadius: "5px", cursor: "pointer",
    border: `1px solid ${UI.line}`, background: "transparent", color: UI.fg, whiteSpace: "nowrap",
  };
  if (variant === "primary") Object.assign(s, { background: "var(--p-primary-color, #4a7ec4)", borderColor: "transparent", color: "#fff", fontWeight: "600" });
  return h("button", { type: "button", style: s, onclick: onClick, disabled: state.busy && !always ? "disabled" : null }, label);
}

const inputStyle = {
  width: "100%", boxSizing: "border-box", padding: "6px 8px", background: UI.field,
  color: UI.fg, border: `1px solid ${UI.line}`, borderRadius: "4px", fontSize: "12px",
};

function input(value, onInput, placeholder = "", multiline = false) {
  const el = h(multiline ? "textarea" : "input", { style: { ...inputStyle, minHeight: multiline ? "54px" : null }, placeholder });
  el.value = value || "";
  el.oninput = (e) => onInput(e.target.value);
  return el;
}

function choose(options, value, onChange) {
  const el = h("select", { style: inputStyle, onchange: (e) => onChange(e.target.value) });
  for (const [v, label] of options) el.append(h("option", { value: v }, label));
  el.value = value;
  return el;
}

function label(text, control) {
  return h("label", { style: { display: "block", marginBottom: "10px" } },
    h("div", { style: { fontSize: "12px", marginBottom: "4px" } }, text), control);
}

function caption(text) {
  return h("div", { style: { font: "600 11px/1 ui-monospace, monospace", letterSpacing: ".1em", textTransform: "uppercase", color: UI.dim, margin: "16px 0 8px" } }, text);
}

function pill(text, tone) {
  return h("span", { style: { font: "600 10px/1 ui-monospace, monospace", letterSpacing: ".08em", textTransform: "uppercase", padding: "4px 8px", borderRadius: "999px", border: `1px solid ${tone}`, color: tone } }, text);
}

function stat(n, text) {
  return h("div", { style: { border: `1px solid ${UI.line}`, borderRadius: "5px", padding: "8px 10px" } },
    h("div", { style: { font: "700 18px/1 inherit" } }, String(n)),
    h("div", { style: { fontSize: "10.5px", color: UI.dim, marginTop: "4px", textTransform: "uppercase", letterSpacing: ".05em" } }, text));
}

/* ------------------------------------------------------------------ */
/* Working indicator: the Comfy C                                      */
/* ------------------------------------------------------------------ */

/* The C mark from the Comfy motion pack (src/brand.js), extruded the way the
 * pack's logo pieces do it: flat slices stacked along Z, yellow face, plum
 * sides, turning slowly. With reduced motion it holds still and breathes. */
const C_MARK = { w: 521, h: 562, d: "M108.7 560.9C97.9 558.3 86.9 548.6 83.0 538.3C79.1 527.8 79.7 522.9 90.0 486.9C99.8 452.6 100.3 449.5 96.3 444.1C91.2 437.4 91.9 437.5 58.2 437.0L27.5 436.5L21.2 433.6C13.2 429.9 5.0 421.3 2.1 413.5C-2.5 401.2 -3.3 404.9 31.0 284.4C48.1 224.5 63.4 172.6 65.1 169.1C73.0 152.0 87.3 138.2 104.4 131.1C116.5 126.1 125.7 125.0 157.5 125.0C185.1 125.0 185.8 125.0 189.0 122.7C194.7 118.9 195.8 116.3 205.6 82.0C215.9 45.7 218.9 38.5 228.0 27.2C237.1 16.1 252.0 6.5 267.0 2.3C273.1 0.6 280.1 0.5 384.0 0.5L494.5 0.5L500.2 3.3C512.9 9.5 520.2 20.6 520.8 34.5C521.2 42.4 520.8 43.9 507.6 90.0C500.1 116.1 492.6 140.4 491.0 143.9C481.3 164.6 463.9 179.1 441.0 185.6C435.0 187.3 427.8 187.5 343.5 188.0L252.5 188.5L249.2 190.8C247.4 192.1 245.3 194.4 244.5 196.0C242.6 199.7 198.0 355.9 198.0 358.9C198.0 362.6 201.7 369.6 204.8 371.7C207.4 373.4 212.3 373.5 296.5 374.0L385.5 374.5L391.5 376.9C404.5 382.1 413.0 394.5 413.8 409.0C414.2 417.4 414.1 417.8 401.1 463.4C393.8 488.6 386.8 512.0 385.4 515.4C378.6 532.1 363.8 547.2 346.4 555.1C331.8 561.7 338.4 561.4 220.0 561.6C158.5 561.7 110.9 561.5 108.7 560.9Z" };
const BRAND = { yellow: "#F2FF59", plum: "#49378B", lilac: "#DBD6F2" };

function installLoaderStyle() {
  if (document.getElementById("comfyaudit-loader-style")) return;
  document.head.append(h("style", { id: "comfyaudit-loader-style" }, `
@keyframes comfyaudit-c-turn { 0% { transform: rotateX(-12deg) rotateY(-28deg); } 50% { transform: rotateX(8deg) rotateY(28deg); } 100% { transform: rotateX(-12deg) rotateY(-28deg); } }
@keyframes comfyaudit-c-breathe { 0%, 100% { opacity: .65; transform: scale(.96); } 50% { opacity: 1; transform: scale(1); } }
.comfyaudit-c-slab { animation: comfyaudit-c-turn 2.6s cubic-bezier(.45,0,.55,1) infinite; }
@media (prefers-reduced-motion: reduce) { .comfyaudit-c-slab { animation: comfyaudit-c-breathe 2.4s ease-in-out infinite; } }`));
}

function comfyLoader(text, sub = "") {
  installLoaderStyle();
  const H = 56, k = H / C_MARK.h, W = C_MARK.w * k, depth = 14, N = 12;
  const slab = h("div", { class: "comfyaudit-c-slab", style: { position: "absolute", inset: "0", transformStyle: "preserve-3d" } });
  for (let i = 0; i < N; i++) {
    const front = i === N - 1;
    // Sides shade from plum at the back to lilac near the face, so the
    // extrusion reads as depth rather than a flat outline.
    const t = i / (N - 1);
    const mix = (a, b) => Math.round(a + (b - a) * t);
    const side = `rgb(${mix(0x49, 0xa8)}, ${mix(0x37, 0x9c)}, ${mix(0x8b, 0xe0)})`;
    const svgEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svgEl.setAttribute("viewBox", `0 0 ${C_MARK.w} ${C_MARK.h}`);
    svgEl.setAttribute("width", W); svgEl.setAttribute("height", H);
    svgEl.style.cssText = `position:absolute;left:0;top:0;overflow:visible;transform:translateZ(${(-depth / 2 + (i * depth) / (N - 1)).toFixed(2)}px)`;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", C_MARK.d);
    path.setAttribute("fill", front ? BRAND.yellow : side);
    svgEl.append(path);
    slab.append(svgEl);
  }
  const stage = h("div", { "aria-hidden": "true", style: { position: "relative", width: `${W}px`, height: `${H}px`, perspective: "420px", margin: "6px auto 14px" } }, slab);
  return h("div", { role: "status", "aria-live": "polite", style: { textAlign: "center", padding: "18px 6px 8px" } },
    stage,
    h("div", { style: { fontSize: "12.5px", fontWeight: "600", marginBottom: "4px" } }, text),
    sub ? h("div", { style: { fontSize: "11.5px", color: UI.dim, lineHeight: "1.55" } }, sub) : null);
}

/* ------------------------------------------------------------------ */
/* Overlay for the documents                                           */
/* ------------------------------------------------------------------ */

let overlay = null;
function closeOverlay() {
  overlay?.remove();
  overlay = null;
  document.removeEventListener("keydown", overlayKeys, true);
}
function overlayKeys(e) {
  if (e.key === "Escape" && overlay) { e.preventDefault(); e.stopPropagation(); closeOverlay(); }
}

function openDocument(title, html, filename) {
  closeOverlay();
  const iframe = h("iframe", { style: { width: "100%", height: "100%", border: "0", flex: "1 1 auto", background: "#fff" } });
  iframe.srcdoc = html;
  const save = () => saveBlob(html, "text/html", filename);
  const always = { always: true };
  const x = h("button", { type: "button", title: "Close (Esc)", "aria-label": "Close", onclick: closeOverlay,
    style: { font: "400 20px/1 inherit", width: "32px", height: "32px", marginLeft: "4px", borderRadius: "6px",
      border: `1px solid ${UI.line}`, background: "transparent", color: UI.fg, cursor: "pointer" } }, "\u00d7");
  const bar = h("div", { style: { display: "flex", gap: "8px", alignItems: "center", padding: "8px 10px 8px 14px", borderBottom: `1px solid ${UI.line}` } },
    h("strong", { style: { fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: "0" } }, title),
    h("div", { style: { flex: "1" } }),
    btn("Save PDF", () => downloadPdf(html, filename.replace(/\.html$/i, "")), "ghost", always),
    btn("Save HTML", save, "ghost", always), x);
  overlay = h("div", {
    style: { position: "fixed", inset: "0", background: "rgba(0,0,0,.6)", zIndex: "10000", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" },
    onclick: (e) => { if (e.target === overlay) closeOverlay(); },
  }, h("div", { style: { background: UI.bg, color: UI.fg, width: "min(1200px,100%)", height: "min(92vh,100%)", borderRadius: "8px", display: "flex", flexDirection: "column", overflow: "hidden" } }, bar, iframe));
  document.body.append(overlay);
  document.addEventListener("keydown", overlayKeys, true);
  // Esc inside the document (it has focus once clicked) closes it too.
  iframe.addEventListener("load", () => {
    try { iframe.contentDocument.addEventListener("keydown", overlayKeys, true); } catch (e) { /* not same-origin */ }
  });
}

async function openRecord() {
  await act(async () => {
    const data = await post("record", { workflow: app.graph.serialize() });
    openDocument(`${shotName()} - provenance record`, data.html, `${shotName()}.provenance.html`);
  });
}

async function openLife() {
  await act(async () => {
    const data = await post("life", { workflow: app.graph.serialize() });
    openDocument(`${shotName()} - life of the shot`, data.html, `${shotName()}.life.html`);
  });
}

async function openGuide() {
  await act(async () => {
    const r = await api.fetchApi("/comfyaudit/licensing-guide");
    openDocument("Commercial model licensing", await r.text(), "licensing-guide.html");
  });
}

function shotName() {
  return state.summary?.shot?.name || shotDefaultName();
}

/* ------------------------------------------------------------------ */
/* Shot details                                                        */
/* ------------------------------------------------------------------ */

/* Everything else in the record is captured on its own. The shot's name and
 * its ID in the studio's tracking system are the two things only a person
 * knows, so they are the one form outside the delivery flow. */
function detailsForm() {
  const f = state.form;
  const s = state.summary || {};
  const shot = s.shot || {};
  const ids = Object.entries(shot.external_ids || {});
  if (f.name === undefined) f.name = shot.name || "";
  if (f.project === undefined) f.project = shot.project || "";
  if (f.system === undefined) f.system = ids[0]?.[0] || "shotgrid";
  if (f.asset === undefined) f.asset = ids[0]?.[1] || "";
  const submit = () => act(async () => {
    const extIds = f.asset ? { [f.system || "shotgrid"]: f.asset.trim() } : {};
    // A ShotGrid ID is checked against the site when the server is set up for
    // it, so a typo is caught now rather than at delivery.
    if (f.asset && (f.system || "shotgrid") === "shotgrid") {
      const found = await post("shotgrid", { workflow: app.graph.serialize(), value: f.asset.trim().replace(/^shot#/i, "") });
      if (found.configured && found.error) throw new Error(found.error);
      if (found.configured && !found.found) throw new Error(`ShotGrid has no shot ${f.asset.trim()}.`);
      if (found.shot && !f.project) f.project = found.shot.project?.name || "";
    }
    await stamp("note", { note: "Shot details updated", name: f.name || shotDefaultName(),
      project: f.project || "", external_ids: extIds, rename: true });
  });
  return h("div", { style: { border: `1px solid ${UI.line}`, borderRadius: "6px", padding: "12px", margin: "10px 0" } },
    h("div", { style: { fontWeight: "600", marginBottom: "4px" } }, "Shot details"),
    h("div", { style: { fontSize: "11.5px", color: UI.dim, marginBottom: "10px", lineHeight: "1.45" } },
      "Tracking started under the file name. Name the shot and link it to your tracking system; earlier entries keep what they sealed."),
    label("Shot name", input(f.name, (v) => { f.name = v; })),
    label("Project / show", input(f.project, (v) => { f.project = v; }, "optional")),
    label("Studio asset ID", h("div", { style: { display: "flex", gap: "6px" } },
      choose([["shotgrid", "ShotGrid"], ["ftrack", "ftrack"], ["sparky", "Sparky"], ["other", "Other"]], f.system, (v) => { f.system = v; }),
      input(f.asset, (v) => { f.asset = v; }, "e.g. Shot 4411"))),
    h("div", { style: { display: "flex", gap: "8px" } },
      btn("Save", submit, "primary"), btn("Cancel", () => { state.form = null; rerender(); })));
}

/* ------------------------------------------------------------------ */
/* Everyday view                                                       */
/* ------------------------------------------------------------------ */

const COMMERCIAL = {
  cleared: ["Commercial OK", TONE.ok],
  conditional: ["Conditions apply", TONE.warn],
  unknown: ["Licence unclear", TONE.warn],
  blocked: ["Not cleared", TONE.stop],
  none: ["No models yet", TONE.flat],
};
const VERDICT_TONE = { go: TONE.ok, conditions: TONE.warn, "no-go": TONE.stop, unknown: TONE.flat };

function when(at) {
  return String(at || "").slice(0, 16).replace("T", " ");
}

function flagCard(change, tone, title) {
  const models = (change.models || []).map((m) => `${m.filename} (${m.licence || "licence unknown"})`);
  const cause = [
    ...(change.added || []).map((f) => `added ${f}`),
    ...(change.removed || []).map((f) => `removed ${f}`),
  ];
  return h("div", { style: { fontSize: "12px", lineHeight: "1.5", padding: "8px 10px", borderLeft: `3px solid ${tone}`, background: "rgba(255,255,255,.03)", margin: "0 0 8px" } },
    h("div", { style: { fontWeight: "600", color: tone } }, title),
    h("div", { style: { color: UI.dim, fontSize: "11px" } },
      `${when(change.at)} - entry ${change.seq}${change.by ? ` - ${change.by}` : ""}${cause.length ? ` - ${cause.join(", ")}` : ""}`),
    models.length ? h("div", { style: { marginTop: "3px" } }, models.join("; ")) : null);
}

function homeView(s) {
  const wrap = h("div", {});
  const commercial = s.commercial || { status: "none", changes: [] };
  const ids = Object.entries(s.shot?.external_ids || {});
  wrap.append(h("div", { style: { fontSize: "11.5px", color: UI.dim, marginBottom: "10px" } },
    ids.length ? ids.map(([k, v]) => `${k} ${v}`).join(" - ") : "Not linked to a studio asset ID",
    " - ", h("a", { href: "#", style: { color: UI.dim }, onclick: (e) => { e.preventDefault(); state.form = {}; rerender(); } }, "edit")));
  if (s.shot?.parent) {
    const p = s.shot.parent;
    wrap.append(h("div", { style: { fontSize: "11.5px", color: UI.dim, margin: "-4px 0 10px", lineHeight: "1.45" } },
      `Forked from ${p.name || "another record"} at its entry ${p.seq}${p.reason ? ` - ${p.reason}` : ""}. The history it came from travels with this file.`));
  }
  if (state.form) wrap.append(detailsForm());
  for (const p of s.problems || []) wrap.append(h("div", { style: { fontSize: "12px", color: TONE.stop, marginBottom: "4px" } }, p));

  // Where the shot stands on commercial use, and since when.
  const since = commercial.since;
  if (since && commercial.status === "blocked") {
    wrap.append(flagCard(since, TONE.stop, "Not cleared for commercial use"));
  } else if (since && commercial.status === "unknown") {
    wrap.append(flagCard(since, TONE.warn, "A licence here is unclear"));
  }
  if (s.clearance) {
    const c = s.clearance;
    wrap.append(h("div", { style: { fontSize: "12px", lineHeight: "1.5", padding: "8px 10px", borderLeft: `3px solid ${VERDICT_TONE[c.verdict] || TONE.flat}`, margin: "0 0 8px" } },
      h("div", { style: { fontWeight: "600" } }, `Signed off: ${c.verdict_label || c.verdict}`),
      h("div", { style: { color: UI.dim, fontSize: "11px" } }, `${when(c.at)} - ${c.by}${c.exception ? " - delivered as an exception" : ""}`)));
  }

  wrap.append(h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px", margin: "10px 0" } },
    stat(s.entries, "entries"), stat(s.people?.length || 0, "people"),
    stat(s.human_steps, "human decisions"), stat(s.renders, "model runs")));

  if ((commercial.changes || []).length > 1) {
    wrap.append(caption("Commercial standing over time"));
    for (const c of [...commercial.changes].reverse()) {
      const [l, t] = COMMERCIAL[c.status] || COMMERCIAL.none;
      wrap.append(flagCard(c, t, l));
    }
  }

  wrap.append(caption("Recent entries"));
  for (const r of s.recent || []) {
    wrap.append(h("div", { style: { fontSize: "12px", padding: "6px 0", borderBottom: `1px solid ${UI.line}` } },
      h("div", {}, h("strong", {}, `#${r.seq} ${r.label}`)),
      h("div", { style: { color: UI.dim, fontSize: "11px" } }, `${r.who} - ${when(r.at)}`),
      r.note ? h("div", { style: { fontStyle: "italic", marginTop: "2px" } }, `"${r.note}"`) : null));
  }
  if (!getSetting("ComfyAudit.History.UserName", "")) {
    wrap.append(h("div", { style: { fontSize: "11.5px", color: TONE.warn, margin: "10px 0", lineHeight: "1.45" } },
      "Set your name in Settings > ComfyAudit > Shot history, or entries are recorded under your computer account."));
  }
  return wrap;
}

/* ------------------------------------------------------------------ */
/* Package for delivery                                                */
/* ------------------------------------------------------------------ */

/* Check -> make it safe -> sign off -> deliver. The audit runs on the server,
 * and runs again at sign-off, so the verdict sealed into the history is never
 * one the browser supplied. */
const pkg = { step: "profile", result: null, error: "", note: "", exception: "", out: null, open: {}, legalOpen: false };

/* Production facts the rules turn on, asked once alongside the studio profile. */
const DELIVERY_FACTS = [
  "ComfyAudit.History.DistributeIn", "ComfyAudit.History.FeatureFilm",
  "ComfyAudit.History.UnionProduction", "ComfyAudit.History.UnionCrew",
  "ComfyAudit.History.TPNVendor", "ComfyAudit.History.Clients",
];

function openPackage() {
  state.view = "package";
  state.form = null;
  // Every package starts clean: legal's list collapsed, no half-typed evidence.
  pkg.open = {};
  pkg.legalOpen = false;
  pkg.error = "";
  pkg.out = null;
  if (profileIsSet()) runCheck();
  else { pkg.step = "profile"; rerender(); }
}

function leavePackage() {
  state.view = "home";
  rerender();
  refresh();
}

async function runCheck() {
  pkg.step = "checking";
  rerender();
  try {
    pkg.result = await post("check", { workflow: app.graph.serialize(), name: shotName() });
    pkg.step = "fix";
  } catch (e) {
    pkg.error = e.message;
    pkg.step = "error";
  }
  rerender();
}

async function deliverNow() {
  pkg.step = "delivering";
  pkg.error = "";
  rerender();
  const graph = app.graph;
  graph.extra = graph.extra || {};
  const owner = graph.extra;
  try {
    const out = await post("deliver", { workflow: graph.serialize(), name: shotName(),
      note: pkg.note, exception: pkg.exception });
    if (app.graph === graph && graph.extra === owner) {
      owner[EXTRA_KEY] = out.history;
      markDirty();
    }
    state.summary = out.summary;
    pkg.out = out;
    pkg.step = "done";
    downloadZip(out);
  } catch (e) {
    pkg.error = e.message;
    pkg.step = "signoff";
  }
  rerender();
}

function downloadZip(out) {
  const bytes = Uint8Array.from(atob(out.zip_b64), (c) => c.charCodeAt(0));
  const a = h("a", { href: URL.createObjectURL(new Blob([bytes], { type: "application/zip" })), download: out.zip_name });
  document.body.append(a); a.click(); a.remove();
}

function stepsBar() {
  const order = [["check", "Check"], ["fix", "Make it safe"], ["signoff", "Sign off"], ["done", "Deliver"]];
  const at = { profile: "check", checking: "check", error: "check", fix: "fix", signoff: "signoff", delivering: "done", done: "done" }[pkg.step];
  const idx = order.findIndex(([k]) => k === at);
  return h("div", { style: { display: "flex", gap: "4px", flexWrap: "wrap", margin: "0 0 12px" } },
    order.map(([k, l], i) => h("span", { style: {
      font: "600 10px/1 ui-monospace, monospace", letterSpacing: ".06em", textTransform: "uppercase",
      padding: "4px 7px", borderRadius: "4px", border: `1px solid ${i === idx ? "transparent" : UI.line}`,
      background: i === idx ? "var(--p-primary-color, #4a7ec4)" : "transparent",
      color: i === idx ? "#fff" : i < idx ? UI.fg : UI.dim } }, l)));
}

function settingControl(id) {
  const spec = PROFILE_FIELDS.find((f) => f.id === id) || SETTINGS.find((f) => f.id === id);
  const value = getSetting(id, spec.defaultValue);
  const save = async (v) => { await setSetting(id, v); rerender(); };
  if (spec.type === "combo") {
    const opts = (spec.options || []).map((o) => (typeof o === "string" ? [o, o] : [o.value, o.text]));
    return label(spec.name, choose(opts, value, save));
  }
  if (spec.type === "boolean") {
    const box = h("input", { type: "checkbox" });
    box.checked = !!value;
    box.onchange = (e) => save(e.target.checked);
    return h("label", { style: { display: "flex", gap: "8px", alignItems: "center", marginBottom: "8px", fontSize: "12px", cursor: "pointer" } }, box, spec.name);
  }
  const el = input(value, () => {}, spec.tooltip ? "" : "");
  el.onchange = (e) => save(e.target.value);
  return label(spec.name, el);
}

function profileStep() {
  const wrap = h("div", {});
  wrap.append(h("div", { style: { fontSize: "12px", color: UI.dim, lineHeight: "1.5", marginBottom: "10px" } },
    "Who is delivering this, and where it goes. Licences grant rights to particular people in particular places, and these facts decide which rules apply. Asked once; saved in ComfyUI's settings."));
  for (const f of PROFILE_FIELDS) wrap.append(settingControl(f.id));
  wrap.append(caption("This production"));
  for (const id of DELIVERY_FACTS) wrap.append(settingControl(id));
  wrap.append(h("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } },
    btn("Run check", runCheck, "primary"), btn("Cancel", leavePackage)));
  return wrap;
}

function fixCard(f) {
  const tone = VERDICT_TONE[f.verdict] || TONE.flat;
  return h("div", { style: { border: `1px solid ${UI.line}`, borderLeft: `3px solid ${tone}`, borderRadius: "5px", padding: "8px 10px", margin: "0 0 8px", fontSize: "12px", lineHeight: "1.5" } },
    h("div", { style: { display: "flex", gap: "6px", alignItems: "center", marginBottom: "3px", flexWrap: "wrap" } },
      pill(f.verdict === "no-go" ? "blocks delivery" : f.verdict === "conditions" ? "condition" : "unclear", tone),
      f.action_label ? h("span", { style: { fontSize: "11px", color: UI.dim } }, f.action_label) : null),
    h("div", {}, f.problem),
    f.remedy ? h("div", { style: { marginTop: "4px" } }, h("strong", {}, "Fix: "), f.remedy) : null,
    (f.alternatives || []).length ? h("div", { style: { marginTop: "6px" } },
      h("strong", {}, "Or swap for a model licensed for commercial use:"),
      f.alternatives.map((a) => h("div", { style: { marginTop: "3px", paddingLeft: "8px", borderLeft: `2px solid ${a.installed ? TONE.ok : UI.line}` }, title: a.note || "" },
        h("div", {}, a.name, a.installed ? h("span", { style: { color: TONE.ok } }, ` - installed as ${a.filename}`) : h("span", { style: { color: UI.dim } }, " - not installed")),
        h("div", { style: { color: UI.dim, fontSize: "11px" } }, `${a.licence}${a.commercial_use === "conditional" ? " (with conditions)" : ""}. ${a.note || ""}`)))) : null,
    h("div", { style: { marginTop: "4px", font: "11px/1.4 ui-monospace, monospace", color: UI.dim, wordBreak: "break-all" } }, f.subjects.join(", ")),
    f.url ? h("a", { href: f.url, target: "_blank", rel: "noopener", style: { fontSize: "11px", color: UI.dim } }, "Licence terms") : null);
}

function nodeLabel(id) {
  const node = app.graph?._nodes?.find((n) => String(n.id) === String(id));
  const file = node?.widgets?.find((w) => typeof w.value === "string" && /\.\w{2,5}$/.test(w.value))?.value;
  return `${node?.title || node?.type || "node"} ${id}${file ? ` - ${file}` : ""}`;
}

function sourceRow(src) {
  const key = `src:${src.node}`;
  const f = pkg.open[key] || (pkg.open[key] = { origin: "photographed", asset_id: src.asset_id || "" });
  const origins = Object.entries(state.summary?.origins || {}).filter(([k]) => k !== "unknown");
  return h("div", { style: { border: `1px solid ${UI.line}`, borderRadius: "5px", padding: "8px 10px", margin: "0 0 8px", fontSize: "12px" } },
    h("div", { style: { marginBottom: "6px" } }, nodeLabel(src.node)),
    h("div", { style: { display: "flex", gap: "6px", marginBottom: "6px" } },
      choose(origins.map(([k, v]) => [k, typeof v === "string" ? v : k]), f.origin, (v) => { f.origin = v; }),
      input(f.asset_id, (v) => { f.asset_id = v; }, "Asset ID (optional)")),
    btn("Record origin", () => act(async () => {
      await stamp("sourced", { data: { node: src.node, origin: f.origin, asset_id: f.asset_id || "" } });
      await runCheck();
    })));
}

/* One rule legal will check. Read-only for the artist; whoever holds the
 * evidence (legal, production) can record it here when they open the shot. */
function confirmationRule(rule) {
  const box = h("div", { style: { padding: "6px 0", borderTop: `1px solid ${UI.line}`, fontSize: "12px" } },
    h("div", { style: { lineHeight: "1.45" } }, `${rule.title} `,
      h("span", { style: { color: UI.dim, fontSize: "11px" } }, rule.jurisdiction)));
  for (const item of rule.items) {
    const f = pkg.open[item.tag] || (pkg.open[item.tag] = { note: "", open: false });
    const row = h("div", { style: { padding: "4px 0 2px 10px", color: UI.dim, lineHeight: "1.45" } }, item.text, " ");
    if (f.open) {
      row.append(input(f.note, (v) => { f.note = v; }, "What confirms it, e.g. client email of 14 Oct", true));
      row.append(h("div", { style: { display: "flex", gap: "6px", marginTop: "6px" } },
        btn("Record", () => act(async () => {
          if (!f.note.trim()) throw new Error("Say what confirms it; a blank confirmation proves nothing.");
          await stamp("note", { note: f.note.trim(), data: { evidence: item.tag } });
          f.open = false;
          f.note = "";
          await runCheck();
        }), "primary"),
        btn("Cancel", () => { f.open = false; rerender(); })));
    } else {
      row.append(h("a", { href: "#", style: { color: UI.dim, fontSize: "11px" },
        onclick: (e) => { e.preventDefault(); f.open = true; rerender(); } }, "I have the evidence"));
    }
    box.append(row);
  }
  return box;
}

function verdictBox(r) {
  const tone = VERDICT_TONE[r.verdict] || TONE.flat;
  return h("div", { style: { border: `1px solid ${tone}`, borderRadius: "6px", padding: "10px 12px", margin: "0 0 12px" } },
    h("div", { style: { marginBottom: "6px" } }, pill(r.verdict_label || r.verdict, tone)),
    h("div", { style: { fontSize: "12px", lineHeight: "1.55" } }, r.headline || "No determination was made."),
    r.profile ? h("div", { style: { fontSize: "11px", color: UI.dim, marginTop: "6px" } }, `Checked as: ${r.profile} - `,
      h("a", { href: "#", style: { color: UI.dim }, onclick: (e) => { e.preventDefault(); pkg.step = "profile"; rerender(); } }, "change")) : null,
    (r.missing_facts || []).length ? h("div", { style: { fontSize: "11px", color: UI.dim, marginTop: "6px" } },
      `Missing: ${r.missing_facts.join(" ")} `,
      h("a", { href: "#", style: { color: UI.dim }, onclick: (e) => { e.preventDefault(); pkg.step = "profile"; rerender(); } }, "set the studio profile")) : null);
}

function fixStep() {
  const r = pkg.result || {};
  const wrap = h("div", {});
  wrap.append(verdictBox(r));
  const fixes = r.fixes || [];
  wrap.append(caption(fixes.length ? `To make it commercially safe (${fixes.length})` : "Nothing to fix in the licences"));
  for (const f of fixes) wrap.append(fixCard(f));
  if ((r.sources || []).length) {
    wrap.append(caption("Where did these inputs come from?"));
    wrap.append(h("div", { style: { fontSize: "11.5px", color: UI.dim, marginBottom: "8px", lineHeight: "1.45" } },
      "Human-made material that stays in the frame is the strongest evidence of authorship. Only you know where a plate came from."));
    for (const src of r.sources) wrap.append(sourceRow(src));
  }
  if ((r.confirmations || []).length) {
    // These are facts about the production (a client's written approval, a
    // credit disclosure), not about the workflow. The artist is not the person
    // who knows them, so nothing here waits on the artist: they travel with
    // the package as legal's open checklist.
    const n = r.confirmations.reduce((k, rule) => k + rule.items.length, 0);
    wrap.append(caption(`Left for legal (${n})`));
    wrap.append(h("div", { style: { fontSize: "11.5px", color: UI.dim, marginBottom: "6px", lineHeight: "1.45" } },
      "Nothing for you to do here. These are production facts legal checks, such as a client's written approval or a credit disclosure. They go in the package as an open checklist."));
    const list = h("details", { style: { border: `1px solid ${UI.line}`, borderRadius: "5px", padding: "6px 10px", margin: "0 0 8px" } },
      h("summary", { style: { cursor: "pointer", fontSize: "12px" } }, `What legal will check (${r.confirmations.length} rules)`));
    for (const rule of r.confirmations) list.append(confirmationRule(rule));
    // Collapsed unless the person opened it during this package (it is redrawn
    // after every re-check, so it remembers their choice, not a stale form).
    list.open = pkg.legalOpen;
    list.addEventListener("toggle", () => { pkg.legalOpen = list.open; });
    wrap.append(list);
  }
  wrap.append(h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "10px" } },
    btn("Continue to sign-off", () => { pkg.step = "signoff"; rerender(); }, "primary"),
    btn("Re-check", runCheck),
    r.audit_html ? btn("Clearance sheet", () => openDocument(`${shotName()} - clearance sheet`, r.audit_html, `${shotName()}.clearance-sheet.html`)) : null,
    btn("Back", leavePackage)));
  return wrap;
}

function signoffStep() {
  const r = pkg.result || {};
  const needsException = r.verdict === "no-go" || r.verdict === "unknown";
  const wrap = h("div", {});
  wrap.append(verdictBox(r));
  const who = getSetting("ComfyAudit.History.UserName", "");
  const role = getSetting("ComfyAudit.History.Role", "");
  wrap.append(h("div", { style: { fontSize: "12px", marginBottom: "10px", lineHeight: "1.5" } },
    "Signing off as ", h("strong", {}, who || "your computer account"), role ? ` (${role})` : "",
    ". The audit runs again on the server and its verdict is sealed into the history with your sign-off."));
  if (!who) wrap.append(h("div", { style: { fontSize: "11.5px", color: TONE.warn, marginBottom: "8px" } },
    "Set your name in Settings > ComfyAudit > Shot history so legal can see who signed off."));
  wrap.append(label("Approved for", input(pkg.note, (v) => { pkg.note = v; }, "e.g. Final delivery, reel 3", true)));
  if (needsException) {
    wrap.append(h("div", { style: { fontSize: "12px", color: TONE.stop, margin: "4px 0 6px", lineHeight: "1.45" } },
      r.verdict === "no-go"
        ? "The audit says this cannot be used commercially as it stands. To deliver anyway, record why. The reason is sealed with your sign-off and shown to legal."
        : "The audit could not reach a verdict. To deliver anyway, record why. The reason is sealed with your sign-off and shown to legal."));
    wrap.append(label("Reason for delivering anyway", input(pkg.exception, (v) => { pkg.exception = v; }, "", true)));
  }
  if (pkg.error) wrap.append(h("div", { style: { color: TONE.stop, fontSize: "12px", margin: "6px 0" } }, pkg.error));
  wrap.append(h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "8px" } },
    btn("Sign off and package", deliverNow, "primary"),
    btn("Back", () => { pkg.step = "fix"; rerender(); })));
  return wrap;
}

function doneStep() {
  const out = pkg.out || {};
  const c = out.summary?.clearance || {};
  const tone = VERDICT_TONE[c.verdict] || TONE.flat;
  const slug = (out.zip_name || "shot.delivery.zip").replace(/\.delivery\.zip$/, "");
  const wrap = h("div", {});
  wrap.append(h("div", { style: { display: "flex", gap: "6px", marginBottom: "8px", flexWrap: "wrap" } },
    pill("delivered", TONE.accent), pill(c.verdict_label || c.verdict || "", tone),
    pill((ASSURANCE[out.summary?.assurance] || ["?"])[0], (ASSURANCE[out.summary?.assurance] || [0, TONE.flat])[1])));
  const sg = out.shotgrid || {};
  if (sg.note_id || sg.error) {
    wrap.append(h("div", { style: { fontSize: "12px", lineHeight: "1.5", marginBottom: "8px",
      color: sg.error ? TONE.warn : UI.dim } },
      sg.error ? `ShotGrid: ${sg.error} The delivery itself is sealed.`
        : `Posted to ShotGrid as note ${sg.note_id} on ${sg.shot?.code || "the shot"}`
          + (sg.attached ? ", with the package attached" : "")
          + (sg.hash_field ? `; the history head is in ${sg.hash_field}.` : ".")));
  }
  wrap.append(h("div", { style: { fontSize: "12px", lineHeight: "1.5", marginBottom: "10px" } },
    `Signed off by ${c.by || "you"} and sealed into the history. Save the workflow so the delivered history goes with the file.`));
  wrap.append(caption("The package"));
  for (const [file, what] of [
    [`${slug}.json`, "The workflow, with its history, the verdict and the sign-off sealed in"],
    [`${slug}.legal-report.html`, "For legal: clearance, provenance, rules checklist"],
    [`${slug}.clearance-sheet.html`, "The full licence and risk audit"],
    [`${slug}.life.html`, "The shot's life on one timeline: graph, story and log"],
    [`${slug}.omc.json`, "MovieLabs OMC-JSON v2.8, for studio asset systems"],
    [`${slug}.c2pa.json`, "C2PA manifest definition, ready to sign and embed"],
  ]) {
    wrap.append(h("div", { style: { fontSize: "12px", padding: "6px 0", borderBottom: `1px solid ${UI.line}` } },
      h("div", { style: { fontWeight: "600" } }, file), h("div", { style: { color: UI.dim, fontSize: "11px" } }, what)));
  }
  wrap.append(h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "10px" } },
    btn("Download package", () => downloadZip(out), "primary"),
    btn("Legal report", () => openDocument(`${shotName()} - legal report`, out.report_html, `${slug}.legal-report.html`)),
    btn("Back to the shot", leavePackage)));
  return wrap;
}

function packageView() {
  const wrap = h("div", {});
  wrap.append(stepsBar());
  if (pkg.step === "profile") wrap.append(profileStep());
  else if (pkg.step === "checking") wrap.append(comfyLoader("Checking this workflow",
    "Reading the graph, resolving every model and node pack against the licence knowledge base, and checking the rules that apply to this production."));
  else if (pkg.step === "error") wrap.append(h("div", {},
    h("div", { style: { color: TONE.stop, fontSize: "12px", whiteSpace: "pre-wrap", marginBottom: "10px" } }, pkg.error),
    h("div", { style: { display: "flex", gap: "6px" } }, btn("Try again", runCheck, "primary"), btn("Back", leavePackage))));
  else if (pkg.step === "fix") wrap.append(fixStep());
  else if (pkg.step === "signoff") wrap.append(signoffStep());
  else if (pkg.step === "delivering") wrap.append(comfyLoader("Packaging the shot",
    "Re-checking, sealing the sign-off into the history and building the package."));
  else if (pkg.step === "done") wrap.append(doneStep());
  return wrap;
}

/* ------------------------------------------------------------------ */
/* Panel                                                               */
/* ------------------------------------------------------------------ */

function startView() {
  const wrap = h("div", {});
  wrap.append(h("p", { style: { color: UI.dim, fontSize: "12px", lineHeight: "1.55", margin: "0 0 12px" } },
    "Automatic recording is off, so this workflow is not being tracked. Turn on Settings > ComfyAudit > Shot history > Record history automatically, or start this one now."));
  wrap.append(btn("Start tracking this workflow", () => act(() => stamp(app.graph?._nodes?.length ? "imported" : "created")), "primary"));
  return wrap;
}

function draw(el) {
  el.innerHTML = "";
  const root = h("div", { style: { display: "flex", flexDirection: "column", height: "100%", color: UI.fg, font: "13px/1.5 var(--p-font-family, system-ui, sans-serif)" } });
  const head = h("div", { style: { padding: "12px 14px 10px", borderBottom: `1px solid ${UI.line}`, marginBottom: "10px" } },
    h("strong", { style: { fontSize: "13px" } }, state.view === "package" ? "Package for delivery" : "ComfyAudit"),
    h("div", { style: { fontSize: "11px", color: UI.dim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, shotName()));
  const s = state.summary;
  if (state.view === "package") {
    head.append(h("div", { style: { marginTop: "8px" } }, btn("\u2190 Back to the shot", leavePackage)));
  } else if (tracked() && s?.present) {
    // What the shot's standing is, and what you can do with it, stay in view
    // however long its life gets below.
    const [aLabel, aTone] = ASSURANCE[s.assurance] || ["?", TONE.flat];
    const [cLabel, cTone] = COMMERCIAL[(s.commercial || {}).status] || COMMERCIAL.none;
    head.append(h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", margin: "8px 0 10px" } },
      pill(s.shot?.stage || "sandbox", s.shot?.stage === "sandbox" ? TONE.flat : TONE.accent),
      pill(cLabel, cTone), pill(aLabel, aTone)));
    const wide = (b) => { b.style.width = "100%"; b.style.padding = "8px 10px"; return b; };
    head.append(wide(btn("Package for delivery", openPackage, "primary")),
      h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px", marginTop: "6px" } },
        wide(btn("Life of the shot", openLife)), wide(btn("Provenance record", openRecord))));
  }
  const body = h("div", { style: { flex: "1 1 auto", overflowY: "auto", padding: "0 14px 16px", minHeight: "0" } });
  if (state.error) body.append(h("div", { style: { color: TONE.stop, fontSize: "12px", margin: "6px 0 10px" } }, state.error));
  if (state.notice) body.append(h("div", { style: { color: TONE.warn, fontSize: "12px", margin: "6px 0 10px" } }, state.notice));
  if (state.busy) body.append(h("div", { style: { color: UI.dim, fontSize: "12px", margin: "6px 0" } }, "Working..."));
  if (state.view === "package") body.append(packageView());
  else if (!tracked() && getSetting("ComfyAudit.History.Auto", true)) {
    body.append(h("div", { style: { color: UI.dim, fontSize: "12px" } }, "Starting this workflow's history..."));
  } else if (!tracked()) body.append(startView());
  else if (state.summary?.present) body.append(homeView(state.summary));
  else body.append(h("div", { style: { color: UI.dim, fontSize: "12px" } }, "Reading history..."));
  root.append(head, body);
  el.append(root);
}

function rerender() { if (mount) draw(mount); }

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

function openTab() {
  try {
    const tabs = app.extensionManager?.sidebarTab;
    if (tabs?.activeSidebarTabId !== SIDEBAR_ID) tabs?.toggleSidebarTab?.(SIDEBAR_ID);
  } catch (e) { /* noop */ }
}

const COMMANDS = [
  { id: "comfyaudit.history.open", label: "ComfyAudit", icon: "pi pi-history", function: openTab },
  { id: "comfyaudit.history.package", label: "Package for delivery", icon: "pi pi-box",
    function: () => { openTab(); if (tracked()) openPackage(); } },
  { id: "comfyaudit.history.record", label: "Open provenance record", icon: "pi pi-file", function: () => tracked() && openRecord() },
  { id: "comfyaudit.history.guide", label: "Commercial licensing guide", icon: "pi pi-book", function: openGuide },
];

app.registerExtension({
  name: "comfyaudit.history",
  settings: SETTINGS,
  commands: COMMANDS,
  menuCommands: [{ path: ["Extensions", "ComfyAudit"], commands: COMMANDS.map((c) => c.id) }],
  async setup() {
    installIcon();
    if (!wrapQueue()) console.warn("[comfyaudit] could not hook the queue; history will only record manual entries");
    listen();
    setTimeout(() => recoverJobs().then(() => onWorkflowShown()), 2000);
    const manager = app.extensionManager;
    if (typeof manager?.registerSidebarTab === "function") {
      manager.registerSidebarTab({
        id: SIDEBAR_ID, icon: ICON_CLASS, title: "ComfyAudit",
        tooltip: "ComfyAudit - the shot's life, recorded as it happens, and packaging it for delivery",
        type: "custom",
        render: (el) => { mount = el; draw(el); refresh(); flushPending().then(refresh); },
        destroy: () => { mount = null; },
      });
    }
    // Switching workflow tabs swaps app.graph; the panel follows it, and a
    // half-finished package flow belongs to the workflow it was opened on.
    api.addEventListener("graphChanged", () => { state.form = null; onWorkflowShown(); });
    onWorkflowShown();
  },
  // Every workflow load (startup, opening a file, switching tabs) lands here.
  afterConfigureGraph() {
    state.form = null;
    state.view = "home";
    onWorkflowShown();
  },
});
