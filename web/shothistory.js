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

const state = { summary: null, busy: false, error: "", form: null, notice: "" };
let mount = null;
/** Queued prompts by the server's prompt_id, so each completion finds its own workflow. */
const jobs = new Map();
/** The workflow being queued while app.queuePrompt runs; api.queuePrompt reads it. */
let queueContext = null;
/** Outputs gathered per prompt from 'executed' events. */
const outputs = new Map();
/** Renders that finished while a different workflow was open, by workflow key. */
const pending = new Map();

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
  const data = await post("stamp", { workflow, event, name: shotDefaultName(), ...extra });
  if (app.graph !== graph || graph.extra !== owner) return null;
  if (data.history) {
    owner[EXTRA_KEY] = data.history;
    if (data.recorded && !quiet) markDirty();
  }
  state.summary = data.summary;
  return data;
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
    if (mount) flushPending().then(refresh);
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
  await flushPending();
  try { await stamp("edited"); } catch (e) { console.warn("[comfyaudit] history stamp failed", e); }
}

async function flushPending() {
  const key = workflowKey();
  const list = pending.get(key);
  if (!list?.length || !tracked()) return;
  pending.delete(key);
  for (const outs of list) {
    try {
      if (!(await stamp("rendered", { outputs: outs }))) {
        pending.set(key, [...(pending.get(key) || []), outs]);
      }
    } catch (e) { console.warn(e); }
  }
  if (!pending.size) state.notice = "";
}

function wrapQueue() {
  const original = app.queuePrompt?.bind(app);
  if (!original) return false;
  app.queuePrompt = async function (...args) {
    const ctx = { key: workflowKey(), tracked: tracked() };
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
    if (job.key === workflowKey() && tracked()) {
      try {
        if (!(await stamp("rendered", { outputs: outs }))) {
          pending.set(job.key, [...(pending.get(job.key) || []), outs]);
        }
        rerender();
      } catch (e) { console.warn("[comfyaudit] render entry failed", e); }
    } else {
      pending.set(job.key, [...(pending.get(job.key) || []), outs]);
      state.notice = "A render finished while another workflow was open. It will be recorded "
        + "when you return to that workflow.";
      rerender();
    }
  };
  api.addEventListener("execution_success", ({ detail }) => finish(detail, true));
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

function btn(label, onClick, variant = "ghost") {
  const s = {
    font: "500 12px/1 inherit", padding: "7px 10px", borderRadius: "5px", cursor: "pointer",
    border: `1px solid ${UI.line}`, background: "transparent", color: UI.fg, whiteSpace: "nowrap",
  };
  if (variant === "primary") Object.assign(s, { background: "var(--p-primary-color, #4a7ec4)", borderColor: "transparent", color: "#fff", fontWeight: "600" });
  return h("button", { type: "button", style: s, onclick: onClick, disabled: state.busy ? "disabled" : null }, label);
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
/* Overlay for the documents                                           */
/* ------------------------------------------------------------------ */

let overlay = null;
function closeOverlay() { overlay?.remove(); overlay = null; }

function openDocument(title, html, filename) {
  closeOverlay();
  const iframe = h("iframe", { style: { width: "100%", height: "100%", border: "0", flex: "1 1 auto", background: "#fff" } });
  iframe.srcdoc = html;
  const save = () => {
    const a = h("a", { href: URL.createObjectURL(new Blob([html], { type: "text/html" })), download: filename });
    document.body.append(a); a.click(); a.remove();
  };
  const print = () => { try { iframe.contentWindow.focus(); iframe.contentWindow.print(); } catch (e) { console.warn(e); } };
  const bar = h("div", { style: { display: "flex", gap: "8px", alignItems: "center", padding: "10px 14px", borderBottom: `1px solid ${UI.line}` } },
    h("strong", { style: { fontSize: "13px" } }, title), h("div", { style: { flex: "1" } }),
    btn("PDF", print), btn("HTML", save), btn("Close", closeOverlay));
  overlay = h("div", {
    style: { position: "fixed", inset: "0", background: "rgba(0,0,0,.6)", zIndex: "10000", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" },
    onclick: (e) => { if (e.target === overlay) closeOverlay(); },
  }, h("div", { style: { background: UI.bg, color: UI.fg, width: "min(1200px,100%)", height: "min(92vh,100%)", borderRadius: "8px", display: "flex", flexDirection: "column", overflow: "hidden" } }, bar, iframe));
  document.body.append(overlay);
}

async function openRecord() {
  await act(async () => {
    const data = await post("record", { workflow: app.graph.serialize() });
    openDocument(`${graphName()} - provenance record`, data.html, `${graphName()}.provenance.html`);
  });
}

async function openGuide() {
  await act(async () => {
    const r = await api.fetchApi("/comfyaudit/licensing-guide");
    openDocument("Commercial model licensing", await r.text(), "licensing-guide.html");
  });
}

async function exportManifest() {
  await act(async () => {
    const data = await post("manifest", { workflow: app.graph.serialize(), title: graphName() });
    const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(data.manifest, null, 2)], { type: "application/json" })), download: `${graphName()}.c2pa.json` });
    document.body.append(a); a.click(); a.remove();
  });
}

/* ------------------------------------------------------------------ */
/* Forms                                                               */
/* ------------------------------------------------------------------ */

const FORMS = {
  reviewed: { title: "Record a review", hint: "What you looked at and what you asked for. Feedback is part of the creative record.", note: true },
  approved: { title: "Approve the output", hint: "Your sign-off on the current render. Name what it was approved for.", note: true },
  rejected: { title: "Reject the output", hint: "Why it was not used.", note: true },
  note: { title: "Add a note", hint: "Intent, a decision, or evidence for a rule. To attest evidence, enter the rule id shown in the provenance record.", note: true, evidence: true },
  handoff: { title: "Take over the shot", hint: "Record that responsibility moved to you.", note: true },
  sourced: { title: "Declare a source", hint: "Where an input image, plate or clip came from.", source: true, note: true },
  promoted: { title: "Change stage", hint: "Sandbox work never ships. Moving to production is where clearance starts.", stage: true, note: true },
  details: { title: "Shot details", hint: "Tracking started under the file name. Name the shot and link it to your tracking system; the change is recorded, and earlier entries keep what they sealed.", details: true },
};

function formView() {
  const f = state.form;
  const spec = FORMS[f.event];
  const s = state.summary || {};
  const wrap = h("div", { style: { border: `1px solid ${UI.line}`, borderRadius: "6px", padding: "12px", margin: "12px 0" } },
    h("div", { style: { fontWeight: "600", marginBottom: "4px" } }, spec.title),
    h("div", { style: { fontSize: "11.5px", color: UI.dim, marginBottom: "10px", lineHeight: "1.45" } }, spec.hint));
  if (spec.source) {
    const nodes = (s.sources || []).map((x) => [x.node, `node ${x.node}${x.origin ? " - " + x.origin : " - undeclared"}`]);
    if (!f.node && nodes.length) f.node = nodes[0][0];
    wrap.append(label("Input", nodes.length ? choose(nodes, f.node, (v) => { f.node = v; }) : h("div", { style: { color: UI.dim, fontSize: "12px" } }, "No supplied inputs in this graph.")));
    wrap.append(label("Origin", choose(Object.entries(s.origins || {}), f.origin || "artist", (v) => { f.origin = v; })));
    wrap.append(label("Asset ID", input(f.asset_id, (v) => { f.asset_id = v; }, "e.g. ShotGrid Version 88213")));
    wrap.append(label("Owner / rights holder", input(f.owner, (v) => { f.owner = v; }, "optional")));
  }
  if (spec.details) {
    const shot = s.shot || {};
    const ids = Object.entries(shot.external_ids || {});
    if (f.name === undefined) f.name = shot.name || "";
    if (f.project === undefined) f.project = shot.project || "";
    if (f.system === undefined) f.system = ids[0]?.[0] || "shotgrid";
    if (f.asset === undefined) f.asset = ids[0]?.[1] || "";
    wrap.append(label("Shot name", input(f.name, (v) => { f.name = v; })));
    wrap.append(label("Project / show", input(f.project, (v) => { f.project = v; }, "optional")));
    wrap.append(label("Studio asset ID", h("div", { style: { display: "flex", gap: "6px" } },
      choose([["shotgrid", "ShotGrid"], ["ftrack", "ftrack"], ["sparky", "Sparky"], ["other", "Other"]], f.system, (v) => { f.system = v; }),
      input(f.asset, (v) => { f.asset = v; }, "e.g. Shot 4411"))));
  }
  if (spec.stage) {
    wrap.append(label("Move to", choose((s.stages || []).map((x) => [x, x]), f.stage || "production", (v) => { f.stage = v; })));
  }
  if (spec.evidence) wrap.append(label("Evidence for rule (optional)", input(f.evidence, (v) => { f.evidence = v; }, "rule-id:item, e.g. eu-ai-act-art50-4-deployer-deepfake:1")));
  if (spec.note) wrap.append(label("Note", input(f.note, (v) => { f.note = v; }, "", true)));
  const submit = () => act(async () => {
    const data = {};
    if (spec.source) Object.assign(data, { node: f.node, origin: f.origin || "artist", asset_id: f.asset_id || "", owner: f.owner || "" });
    if (spec.stage) data.stage = f.stage || "production";
    if (spec.evidence && f.evidence) data.evidence = f.evidence.trim();
    if (spec.details) {
      const ids = f.asset ? { [f.system || "shotgrid"]: f.asset.trim() } : {};
      await stamp("note", { note: "Shot details updated", name: f.name || shotDefaultName(),
        project: f.project || "", external_ids: ids, rename: true });
      return;
    }
    await stamp(f.event, { note: f.note || "", data });
  });
  wrap.append(h("div", { style: { display: "flex", gap: "8px" } }, btn("Record", submit, "primary"), btn("Cancel", () => { state.form = null; rerender(); })));
  return wrap;
}

/* ------------------------------------------------------------------ */
/* Panel                                                               */
/* ------------------------------------------------------------------ */

function startView() {
  const f = state.form || (state.form = { event: "start", name: graphName() });
  const wrap = h("div", {});
  wrap.append(h("p", { style: { color: UI.dim, fontSize: "12px", lineHeight: "1.55", margin: "0 0 12px" } },
    "Shot history records who made this workflow, every substantive change, the models "
    + "and prompts behind each render, and every point where a person made the call. "
    + "It is stored inside the workflow and signed, so it travels with the file and "
    + "into every image it renders."));
  wrap.append(label("Shot name", input(f.name, (v) => { f.name = v; })));
  wrap.append(label("Project / show", input(f.project, (v) => { f.project = v; }, "optional")));
  wrap.append(label("Studio asset ID", h("div", { style: { display: "flex", gap: "6px" } },
    choose([["shotgrid", "ShotGrid"], ["ftrack", "ftrack"], ["sparky", "Sparky"], ["other", "Other"]], f.system || "shotgrid", (v) => { f.system = v; }),
    input(f.asset, (v) => { f.asset = v; }, "e.g. Shot 4411"))));
  wrap.append(h("div", { style: { fontSize: "11px", color: UI.dim, margin: "-4px 0 12px", lineHeight: "1.45" } },
    "Provenance should start where the asset was born. Linking the ID ties this record to your tracking system."));
  wrap.append(h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
    btn("Start tracking (new work)", () => act(() => stamp("created", startPayload(f))), "primary"),
    btn("Track existing workflow", () => act(() => stamp("imported", startPayload(f))))));
  return wrap;
}

function startPayload(f) {
  const ids = f.asset ? { [f.system || "shotgrid"]: f.asset } : {};
  return { name: f.name || graphName(), project: f.project || "", external_ids: ids };
}

function trackedView(s) {
  const wrap = h("div", {});
  const [aLabel, aTone] = ASSURANCE[s.assurance] || ["?", TONE.flat];
  wrap.append(h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center", marginBottom: "8px" } },
    pill(s.shot?.stage || "sandbox", s.shot?.stage === "sandbox" ? TONE.flat : TONE.accent),
    pill(aLabel, aTone),
    ...Object.entries(s.shot?.external_ids || {}).map(([k, v]) => h("span", { style: { fontSize: "11px", color: UI.dim } }, `${k} ${v}`))));
  wrap.append(h("div", { style: { fontSize: "11.5px", color: UI.dim, lineHeight: "1.45", marginBottom: "10px" } }, s.assurance_detail));
  for (const p of s.problems || []) wrap.append(h("div", { style: { fontSize: "12px", color: TONE.stop, marginBottom: "4px" } }, p));
  for (const n of s.notes || []) wrap.append(h("div", { style: { fontSize: "12px", color: TONE.warn, marginBottom: "4px" } }, n));

  wrap.append(h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px", margin: "10px 0" } },
    stat(s.entries, "entries"), stat(s.people?.length || 0, "people"),
    stat(s.human_steps, "human decisions"), stat(s.renders, "model runs")));

  if (!getSetting("ComfyAudit.History.UserName", "")) {
    wrap.append(h("div", { style: { fontSize: "12px", color: TONE.warn, margin: "6px 0", lineHeight: "1.45" } },
      "Set your name in Settings > ComfyAudit > Shot history, or entries are recorded under your computer account."));
  }

  if (state.form) wrap.append(formView());
  else {
    wrap.append(caption("Record a human step"));
    const row = h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } });
    for (const [event, text] of [["details", "Shot details"], ["reviewed", "Review"], ["approved", "Approve"], ["rejected", "Reject"], ["sourced", "Declare source"], ["promoted", "Change stage"], ["note", "Note / evidence"], ["handoff", "Take over"]]) {
      row.append(btn(text, () => { state.form = { event }; rerender(); }, event === "approved" ? "primary" : "ghost"));
    }
    wrap.append(row);
  }

  if (s.gaps?.length) {
    wrap.append(caption("Gaps legal will ask about"));
    for (const g of s.gaps) wrap.append(h("div", { style: { fontSize: "12px", lineHeight: "1.45", padding: "7px 9px", borderLeft: `3px solid ${TONE.warn}`, margin: "0 0 6px", background: "rgba(192,138,46,.08)" } }, g));
  }

  wrap.append(caption("Recent"));
  for (const r of s.recent || []) {
    wrap.append(h("div", { style: { fontSize: "12px", padding: "6px 0", borderBottom: `1px solid ${UI.line}` } },
      h("div", {}, h("strong", {}, `#${r.seq} ${r.label}`)),
      h("div", { style: { color: UI.dim, fontSize: "11px" } }, `${r.who} - ${r.at.slice(0, 16).replace("T", " ")}`),
      r.note ? h("div", { style: { fontStyle: "italic", marginTop: "2px" } }, `"${r.note}"`) : null));
  }

  wrap.append(caption("Documents"));
  wrap.append(h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
    btn("Provenance record", openRecord, "primary"), btn("C2PA manifest", exportManifest), btn("Licensing guide", openGuide)));
  return wrap;
}

function draw(el) {
  el.innerHTML = "";
  const root = h("div", { style: { display: "flex", flexDirection: "column", height: "100%", color: UI.fg, font: "13px/1.5 var(--p-font-family, system-ui, sans-serif)" } });
  const head = h("div", { style: { padding: "12px 14px 6px" } },
    h("strong", { style: { fontSize: "13px" } }, "Shot history"),
    h("div", { style: { fontSize: "11px", color: UI.dim } }, state.summary?.shot?.name || graphName()));
  const body = h("div", { style: { flex: "1 1 auto", overflowY: "auto", padding: "0 14px 16px", minHeight: "0" } });
  if (state.error) body.append(h("div", { style: { color: TONE.stop, fontSize: "12px", margin: "6px 0 10px" } }, state.error));
  if (state.notice) body.append(h("div", { style: { color: TONE.warn, fontSize: "12px", margin: "6px 0 10px" } }, state.notice));
  if (state.busy) body.append(h("div", { style: { color: UI.dim, fontSize: "12px", margin: "6px 0" } }, "Working..."));
  if (!tracked() && getSetting("ComfyAudit.History.Auto", true)) {
    body.append(h("div", { style: { color: UI.dim, fontSize: "12px" } }, "Starting this workflow's history..."));
  } else if (!tracked()) body.append(startView());
  else if (state.summary?.present) body.append(trackedView(state.summary));
  else body.append(h("div", { style: { color: UI.dim, fontSize: "12px" } }, "Reading history..."));
  root.append(head, body);
  el.append(root);
}

function rerender() { if (mount) draw(mount); }

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

const COMMANDS = [
  { id: "comfyaudit.history.open", label: "Shot history", icon: "pi pi-history",
    function: () => { try { app.extensionManager?.sidebarTab?.toggleSidebarTab?.(SIDEBAR_ID); } catch (e) { /* noop */ } } },
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
    const manager = app.extensionManager;
    if (typeof manager?.registerSidebarTab === "function") {
      manager.registerSidebarTab({
        id: SIDEBAR_ID, icon: ICON_CLASS, title: "History",
        tooltip: "Shot history - who made this, what changed, and where people made the decisions",
        type: "custom",
        render: (el) => { mount = el; state.form = tracked() ? null : state.form; draw(el); refresh(); flushPending().then(refresh); },
        destroy: () => { mount = null; },
      });
    }
    // Switching workflow tabs swaps app.graph; the panel follows it.
    // A half-filled form belongs to the workflow it was opened on.
    api.addEventListener("graphChanged", () => { state.form = null; onWorkflowShown(); });
    onWorkflowShown();
  },
  // Every workflow load (startup, opening a file, switching tabs) lands here.
  afterConfigureGraph() {
    state.form = null;
    onWorkflowShown();
  },
});
