/**
 * comfyaudit - a guided audit of the graph on the canvas.
 *
 * This is the sidebar tab. It walks the three questions an audit actually needs
 * answered, in the order they matter:
 *
 *   1. Who is asking?  The studio profile. Licences grant rights to particular
 *                      people in particular places doing particular things, so
 *                      without this the report can only describe terms - it
 *                      cannot reach a verdict.
 *   2. How deep?       Scope. Online lookups and hashing cost time; a Claude
 *                      review costs money. All optional.
 *   3. What now?       The report, and something you can hand to someone else.
 *
 * The node path (Audit This Workflow) remains the right flow for a pipeline,
 * where the audit is part of a queued run. This is for the other case: a
 * workflow is open, someone asks "can we deliver this?", and the answer is
 * wanted before an hour of rendering is spent finding out.
 *
 * Everything is rendered with plain DOM. ComfyUI's frontend is Vue, but an
 * extension that reaches into it inherits its release cycle; plain elements and
 * CSS custom properties survive frontend upgrades.
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const PREFIX = "/comfyaudit";
const SIDEBAR_ID = "comfyaudit.sidebar";

/* Operational risk is graded because it is a property of the graph. Licence
 * position never is - "non-commercial" is not a worse licence than "permissive",
 * it is a different one - so no colour is assigned to it anywhere below. */
const RISK_TONE = {
  Severe: "#c4483a",
  High: "#c4483a",
  Elevated: "#c08a2e",
  Moderate: "#c08a2e",
  Low: "#4a9c63",
};

const VERDICT_TONE = {
  "go": "#4a9c63",
  "conditions": "#c08a2e",
  "no-go": "#c4483a",
  "unknown": "#8a929b",
};

const SEVERITY_TONE = {
  critical: "#c4483a",
  high: "#c4483a",
  medium: "#c08a2e",
  low: "#4a9c63",
  info: "#8a929b",
};

/* ComfyUI theme variables, with fallbacks for when this runs against a frontend
 * that has renamed one of them. */
const UI = {
  fg: "var(--fg-color, #e6e6e6)",
  dim: "var(--descrip-text, #9aa0a6)",
  bg: "var(--comfy-menu-bg, #202020)",
  field: "var(--comfy-input-bg, #2a2a2a)",
  line: "var(--border-color, #3a3a3a)",
};

/* ------------------------------------------------------------------ */
/* Tiny DOM helper                                                     */
/* ------------------------------------------------------------------ */

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "style") Object.assign(el.style, value);
    else if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") el[key.toLowerCase()] = value;
    else el.setAttribute(key, value);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid && kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ------------------------------------------------------------------ */
/* Studio profile - one definition, two surfaces                       */
/* ------------------------------------------------------------------ */

/**
 * The facts about a facility that licence terms actually turn on.
 *
 * Defined once here and used twice: to register ComfyUI's own settings entries,
 * and to build the wizard's first step. The ids must match server/settings.py,
 * which is what reads them back on the Python side.
 */
const PROFILE_FIELDS = [
  {
    id: "ComfyAudit.Studio.Territory",
    name: "Territory",
    type: "combo",
    options: ["not set", "United States", "European Union", "United Kingdom",
              "South Korea", "Canada", "Australia", "Japan", "China", "India",
              "elsewhere"],
    defaultValue: "not set",
    tooltip:
      "Where work is rendered and deployed. Several open-weight licences "
      + "exclude regions outright - MiniMax H3 excludes the US, EU, UK and "
      + "South Korea - and no fee lifts a territory exclusion.",
    hint: "Often the fact that decides everything.",
  },
  {
    id: "ComfyAudit.Studio.Revenue",
    name: "Annual revenue",
    type: "combo",
    options: ["not set", "under $1M", "$1M - $10M", "$10M - $20M",
              "$20M - $100M", "over $100M"],
    defaultValue: "not set",
    tooltip:
      "Total company revenue, not AI-derived revenue. Free use is capped at "
      + "$1M by Stability and $20M by MiniMax; above a cap you need an "
      + "agreement, which is a budget line rather than a blocker.",
    hint: "Total company revenue, not AI-derived.",
  },
  {
    id: "ComfyAudit.Studio.Ships",
    name: "What ships",
    type: "combo",
    options: ["not set", "finished frames to a client",
              "nothing leaves the building", "software containing this workflow",
              "a network service"],
    defaultValue: "not set",
    tooltip:
      "Copyleft only reaches your own code when something is distributed, so "
      + "this decides whether an AGPL node pack is a non-issue or a serious "
      + "problem.",
    hint: "Decides whether copyleft reaches you at all.",
  },
  {
    id: "ComfyAudit.Studio.TrainsModels",
    name: "Outputs train other models",
    type: "boolean",
    defaultValue: false,
    tooltip:
      "Several licences forbid this outright and worldwide, with no fee that "
      + "lifts it.",
  },
  {
    id: "ComfyAudit.Studio.Likeness",
    name: "Real performers involved",
    type: "boolean",
    defaultValue: false,
    tooltip:
      "No model licence grants rights in a performer's face - that comes from "
      + "their contract and, increasingly, their union agreement.",
  },
  {
    id: "ComfyAudit.Studio.Label",
    name: "Studio name",
    type: "text",
    defaultValue: "",
    tooltip: "A label for the report: a facility, a show or a client.",
  },
];

const STUDIO_SETTINGS = PROFILE_FIELDS.map((field) => ({
  id: field.id,
  name: field.name,
  tooltip: field.tooltip,
  type: field.type,
  options: field.options,
  defaultValue: field.defaultValue,
  category: ["ComfyAudit", "Studio profile", field.name],
}));

/**
 * The facility's record of what it has already decided about.
 *
 * Not part of the profile - that says what the studio is, this says what it has
 * concluded - but it belongs in the same panel for the same reason: it is a
 * property of the installation, not of any one workflow.
 */
const REGISTRY_SETTINGS = [
  {
    id: "ComfyAudit.Registry.Path",
    name: "Registry file",
    tooltip:
      "Path to a JSON file recording what this facility has already approved, "
      + "rejected or parked. Optional.",
    type: "text",
    defaultValue: "",
    category: ["ComfyAudit", "Registry", "Registry file"],
  },
];

/* ComfyUI has moved its settings API once already, so both spellings are tried
 * before giving up. Failing to read a setting must never break the panel: an
 * unreadable profile is the same as an unset one, which is a supported state. */
function getSetting(id, fallback) {
  try {
    const store = app.extensionManager?.setting;
    if (store?.get) {
      const value = store.get(id);
      if (value !== undefined && value !== null) return value;
    }
  } catch (err) { /* fall through */ }
  try {
    const value = app.ui?.settings?.getSettingValue?.(id);
    if (value !== undefined && value !== null) return value;
  } catch (err) { /* fall through */ }
  return fallback;
}

async function setSetting(id, value) {
  try {
    const store = app.extensionManager?.setting;
    if (store?.set) return await store.set(id, value);
  } catch (err) { /* fall through */ }
  try {
    return await app.ui?.settings?.setSettingValue?.(id, value);
  } catch (err) {
    console.warn("[comfyaudit] could not persist", id, err);
  }
}

function profileIsSet() {
  const territory = getSetting("ComfyAudit.Studio.Territory", "not set");
  const revenue = getSetting("ComfyAudit.Studio.Revenue", "not set");
  return territory !== "not set" && revenue !== "not set";
}

/* ------------------------------------------------------------------ */
/* Wizard state                                                        */
/* ------------------------------------------------------------------ */

/* Module level, so switching to another sidebar tab and back does not throw a
 * finished report away. Re-running is cheap but never free, and losing a Claude
 * review to a stray click would not be. */
const state = {
  step: "profile",
  busy: false,
  error: "",
  data: null,
  html: "",
  markdown: "",
  name: "workflow",
  status: null,
  options: { check_local_models: true, online: false, hash_models: false },
  claude: { enabled: false, mode: "full", model: "" },
};

let mount = null;

function rerender() {
  if (mount) draw(mount);
}

function go(step) {
  state.step = step;
  rerender();
}

/* ------------------------------------------------------------------ */
/* Requests                                                            */
/* ------------------------------------------------------------------ */

async function post(path, payload) {
  const response = await api.fetchApi(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data;
  try {
    data = await response.json();
  } catch (err) {
    throw new Error(`Server returned ${response.status} with no JSON body.`);
  }
  if (!response.ok || data.error) {
    throw new Error(data.error || `Server returned ${response.status}.`);
  }
  return data;
}

async function loadStatus() {
  try {
    const response = await api.fetchApi(`${PREFIX}/status`);
    state.status = await response.json();
    if (!state.claude.model && state.status?.claude?.models?.length) {
      state.claude.model = state.status.claude.models[0];
    }
  } catch (err) {
    state.status = null;
  }
  rerender();
}

/**
 * What to call this workflow in the report and in the saved filenames.
 *
 * The frontend has kept the active workflow in three different places across
 * releases and returns null from the older two on current builds, so all of
 * them are tried. Getting this wrong is not fatal but it is annoying: every
 * download lands as "workflow.audit.md" and a folder of them is unreadable.
 */
function graphName() {
  const active = app.extensionManager?.workflow?.activeWorkflow;
  const raw = active?.filename
    || (active?.path ? active.path.split(/[\\/]/).pop() : null)
    || app.graph?.extra?.workflowName
    || app.workflowManager?.activeWorkflow?.filename;
  return (raw || "workflow").replace(/\.json$/i, "").trim() || "workflow";
}

async function runAudit() {
  let workflow;
  try {
    workflow = app.graph.serialize();
  } catch (err) {
    state.error = `Could not serialise the graph: ${err.message}`;
    go("error");
    return;
  }

  state.busy = true;
  state.error = "";
  state.name = graphName();
  go("running");

  const withClaude = state.claude.enabled && state.status?.claude?.available;

  try {
    const path = withClaude ? `${PREFIX}/review` : `${PREFIX}/audit`;
    // The name rides along in options so the report heading matches the file
    // it gets saved as; the server has no other way to know it.
    const options = { ...state.options, name: state.name };
    const payload = withClaude
      ? { workflow, mode: state.claude.mode, model: state.claude.model, options }
      : { workflow, options };

    const data = await post(path, payload);

    let html = data.html;
    let markdown = data.markdown || "";
    if (withClaude && data.review_markdown) {
      html = injectReview(html, data.review_markdown, data.review);
      // The downloaded markdown has to carry the review too, or the file says
      // less than the panel did and nobody can tell why.
      markdown = `${markdown}\n\n## Claude review\n\n${data.review_markdown}\n`;
    }

    state.data = data;
    state.html = html;
    state.markdown = markdown;
    state.busy = false;
    go("report");
  } catch (err) {
    state.busy = false;
    state.error = `${err.message}\n\nCheck the ComfyUI server console for the traceback.`;
    go("error");
  }
}

/**
 * Fold the Claude section into the report page as plain, readable HTML.
 * Deliberately minimal - it inherits the report's own stylesheet.
 */
function injectReview(html, markdown, review) {
  const label = review && review.ran
    ? `Mode ${escapeHtml(review.mode)} - ${escapeHtml(review.model)} - ${review.usage?.turns ?? 0} turns`
    : "Not run";

  const section = `
    <div class="sheet">
      <h2><span class="num">7</span>Claude review<span class="count">${label}</span></h2>
      <div class="block warn">
        <p style="margin-top:0"><strong>Model-derived, not rule-derived.</strong>
        Everything below was produced by Claude reading this workflow. Verify before
        acting on it.</p>
        <pre style="white-space:pre-wrap">${escapeHtml(markdown)}</pre>
      </div>
    </div>`;

  return html.includes("</body>")
    ? html.replace("</body>", `${section}</body>`)
    : html + section;
}

/* ------------------------------------------------------------------ */
/* Downloads                                                           */
/* ------------------------------------------------------------------ */

function saveBlob(text, type, filename) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = h("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

/**
 * Hand the report to the browser's print pipeline so it can be saved as PDF.
 *
 * There is no PDF library here on purpose. This pack declares no dependencies -
 * that is what makes it safe to drop into someone's ComfyUI environment - and
 * pulling in a renderer to lay out a document the browser can already lay out
 * would trade that away for nothing. The report stylesheet already carries an
 * @media print block; this adds the page box and a filename and gets out of the
 * way. Every browser's print dialog offers "Save as PDF".
 */
function printable(html, name) {
  const extra = `<style>
    @page { size: A4; margin: 14mm; }
    html, body { background: #fff !important; }
    @media print { .sheet { max-width: none !important; } }
  </style>`;
  let out = html;
  // Browsers name the saved PDF after the document title.
  const title = `<title>${escapeHtml(name)} - clearance sheet</title>`;
  out = /<title>[\s\S]*?<\/title>/i.test(out)
    ? out.replace(/<title>[\s\S]*?<\/title>/i, title)
    : out.replace(/<head>/i, `<head>${title}`);
  return out.includes("</head>") ? out.replace("</head>", `${extra}</head>`) : extra + out;
}

function downloadPdf(html, name) {
  const frame = h("iframe", {
    style: {
      position: "fixed", right: "0", bottom: "0",
      width: "0", height: "0", border: "0", visibility: "hidden",
    },
  });
  document.body.append(frame);

  const win = frame.contentWindow;
  const doc = win.document;
  doc.open();
  doc.write(printable(html, name));
  doc.close();

  const fire = () => {
    try {
      win.focus();
      win.print();
    } catch (err) {
      console.warn("[comfyaudit] print failed", err);
    }
    // Kept alive well past print(): removing the frame while the dialog is open
    // cancels the job in Chromium.
    setTimeout(() => frame.remove(), 120000);
  };

  // The report links a webfont. Printing before it resolves gives a fallback
  // face in the PDF, so the load event is waited on where there is one.
  if (doc.readyState === "complete") setTimeout(fire, 400);
  else win.addEventListener("load", () => setTimeout(fire, 400));
}

/* ------------------------------------------------------------------ */
/* Full-report overlay                                                 */
/* ------------------------------------------------------------------ */

let overlay = null;

function closeOverlay() {
  if (overlay) {
    overlay.remove();
    overlay = null;
    document.removeEventListener("keydown", onKeydown);
  }
}

function onKeydown(event) {
  if (event.key === "Escape") closeOverlay();
}

/* The sidebar is a column a few hundred pixels wide. The report is a document
 * with wide tables in it. Rendering it in place would mean choosing which
 * columns to lose, so the full thing opens over the canvas instead, in an
 * iframe that keeps its stylesheet away from ComfyUI's. */
function openFullReport() {
  closeOverlay();

  overlay = h("div", {
    style: {
      position: "fixed", inset: "0", background: "rgba(0,0,0,.6)",
      zIndex: "10000", display: "flex", alignItems: "center",
      justifyContent: "center", padding: "24px",
    },
    onclick: (event) => { if (event.target === overlay) closeOverlay(); },
  });

  const bar = h("div", {
    style: {
      display: "flex", alignItems: "center", gap: "10px",
      padding: "10px 14px", borderBottom: `1px solid ${UI.line}`, flex: "0 0 auto",
    },
  },
    h("strong", { style: { fontSize: "13px" } }, `${state.name} - clearance sheet`),
    h("div", { style: { flex: "1" } }),
    button("Markdown", () => saveBlob(state.markdown, "text/markdown", `${state.name}.audit.md`), "ghost"),
    button("PDF", () => downloadPdf(state.html, state.name), "ghost"),
    button("HTML", () => saveBlob(state.html, "text/html", `${state.name}.audit.html`), "ghost"),
    button("Close", closeOverlay, "ghost"),
  );

  const iframe = h("iframe", { style: { width: "100%", height: "100%", border: "0", flex: "1 1 auto" } });
  iframe.srcdoc = state.html;

  const frame = h("div", {
    style: {
      background: UI.bg, color: UI.fg, width: "min(1200px, 100%)",
      height: "min(92vh, 100%)", borderRadius: "8px", display: "flex",
      flexDirection: "column", overflow: "hidden",
      boxShadow: "0 24px 64px -24px rgba(0,0,0,.85)",
    },
  }, bar, iframe);

  overlay.append(frame);
  document.body.append(overlay);
  document.addEventListener("keydown", onKeydown);
}

/* ------------------------------------------------------------------ */
/* Widgets                                                             */
/* ------------------------------------------------------------------ */

function button(label, onClick, variant = "primary") {
  const base = {
    font: "500 12px/1 inherit",
    padding: "7px 11px",
    borderRadius: "5px",
    cursor: "pointer",
    border: `1px solid ${UI.line}`,
    background: "transparent",
    color: UI.fg,
    whiteSpace: "nowrap",
  };
  if (variant === "primary") {
    base.background = "var(--p-primary-color, #4a7ec4)";
    base.borderColor = "transparent";
    base.color = "#fff";
    base.fontWeight = "600";
  }
  if (variant === "ghost") base.color = UI.dim;
  return h("button", { style: base, onclick: onClick, type: "button" }, label);
}

function sectionTitle(text, note) {
  return h("div", { style: { margin: "0 0 10px" } },
    h("div", {
      style: {
        font: "600 11px/1 ui-monospace, monospace", letterSpacing: ".1em",
        textTransform: "uppercase", color: UI.dim,
      },
    }, text),
    note ? h("div", { style: { fontSize: "12px", color: UI.dim, marginTop: "6px", lineHeight: "1.5" } }, note) : null,
  );
}

function field(label, control, hint) {
  return h("label", { style: { display: "block", marginBottom: "12px" } },
    h("div", { style: { fontSize: "12px", marginBottom: "4px", color: UI.fg } }, label),
    control,
    hint ? h("div", { style: { fontSize: "11px", color: UI.dim, marginTop: "4px", lineHeight: "1.45" } }, hint) : null,
  );
}

const inputStyle = {
  width: "100%", boxSizing: "border-box", padding: "6px 8px",
  background: UI.field, color: UI.fg, border: `1px solid ${UI.line}`,
  borderRadius: "4px", fontSize: "12px",
};

function select(options, value, onChange) {
  const el = h("select", { style: inputStyle, onchange: (e) => onChange(e.target.value) });
  for (const option of options) {
    el.append(h("option", { value: option }, option));
  }
  el.value = value;
  return el;
}

function checkbox(label, checked, onChange, hint) {
  const box = h("input", { type: "checkbox", style: { marginTop: "2px", flex: "0 0 auto" } });
  box.checked = !!checked;
  box.onchange = (e) => onChange(e.target.checked);
  return h("label", {
    style: { display: "flex", gap: "8px", alignItems: "flex-start", marginBottom: "10px", cursor: "pointer" },
  },
    box,
    h("div", {},
      h("div", { style: { fontSize: "12px", color: UI.fg } }, label),
      hint ? h("div", { style: { fontSize: "11px", color: UI.dim, marginTop: "2px", lineHeight: "1.45" } }, hint) : null,
    ),
  );
}

function pill(text, tone) {
  return h("span", {
    style: {
      font: "600 10px/1 ui-monospace, monospace", letterSpacing: ".08em",
      textTransform: "uppercase", padding: "4px 8px", borderRadius: "999px",
      border: `1px solid ${tone}`, color: tone, whiteSpace: "nowrap",
    },
  }, text);
}

function steps(current) {
  const order = [
    ["profile", "Profile"],
    ["scope", "Scope"],
    ["report", "Report"],
  ];
  const active = current === "running" || current === "error" ? "report" : current;
  const row = h("div", {
    style: { display: "flex", gap: "6px", alignItems: "center", marginBottom: "16px", flexWrap: "wrap" },
  });
  order.forEach(([key, label], index) => {
    const isNow = key === active;
    const done = order.findIndex(([k]) => k === active) > index;
    row.append(h("span", {
      style: {
        font: "600 10px/1 ui-monospace, monospace", letterSpacing: ".08em",
        textTransform: "uppercase", padding: "4px 8px", borderRadius: "4px",
        background: isNow ? "var(--p-primary-color, #4a7ec4)" : "transparent",
        color: isNow ? "#fff" : done ? UI.fg : UI.dim,
        border: `1px solid ${isNow ? "transparent" : UI.line}`,
      },
    }, `${index + 1} ${label}`));
    if (index < order.length - 1) {
      row.append(h("span", { style: { color: UI.dim, fontSize: "10px" } }, ">"));
    }
  });
  return row;
}

/* ------------------------------------------------------------------ */
/* Steps                                                               */
/* ------------------------------------------------------------------ */

function stepProfile() {
  const wrap = h("div", {});
  wrap.append(sectionTitle(
    "Who is asking",
    "A licence grants rights to particular people, in particular places, doing "
    + "particular things. Set these and the report reaches a verdict; leave them "
    + "and it describes the terms without ruling on them. Saved in ComfyUI's "
    + "settings, so this is a one-off.",
  ));

  for (const spec of PROFILE_FIELDS) {
    const value = getSetting(spec.id, spec.defaultValue);
    if (spec.type === "combo") {
      wrap.append(field(spec.name, select(spec.options, value, async (next) => {
        await setSetting(spec.id, next);
        rerender();
      }), spec.hint));
    } else if (spec.type === "boolean") {
      wrap.append(checkbox(spec.name, value, async (next) => {
        await setSetting(spec.id, next);
        rerender();
      }, spec.tooltip));
    } else {
      const input = h("input", { type: "text", style: inputStyle });
      input.value = value || "";
      input.onchange = async (e) => { await setSetting(spec.id, e.target.value); };
      wrap.append(field(spec.name, input, spec.tooltip));
    }
  }

  const ready = profileIsSet();
  wrap.append(h("div", {
    style: {
      fontSize: "11px", lineHeight: "1.5", padding: "9px 10px", borderRadius: "5px",
      border: `1px solid ${ready ? VERDICT_TONE.go : UI.line}`,
      color: ready ? UI.fg : UI.dim, marginBottom: "14px",
    },
  }, ready
    ? "Territory and revenue are set, so this audit can reach a go / no-go."
    : "Territory and revenue are the minimum for a verdict. Without them the report still lists every model, licence and risk - it just will not rule on them."));

  wrap.append(h("div", { style: { display: "flex", gap: "8px" } },
    button("Next: scope", () => go("scope")),
    button(ready ? "Skip" : "Continue without", () => go("scope"), "secondary"),
  ));
  return wrap;
}

function stepScope() {
  const wrap = h("div", {});
  wrap.append(sectionTitle(
    "How deep",
    "The defaults are the fast path and answer most questions. The rest cost time or money.",
  ));

  wrap.append(checkbox(
    "Check models on disk",
    state.options.check_local_models,
    (v) => { state.options.check_local_models = v; },
    "Match the graph against what is actually installed. Fast, local, catches the missing weight before the render does.",
  ));
  wrap.append(checkbox(
    "Look models up online",
    state.options.online,
    (v) => { state.options.online = v; },
    "Query Hugging Face and Civitai for licences the bundled knowledge base does not have. Slower, and needs network.",
  ));
  wrap.append(checkbox(
    "Hash model files",
    state.options.hash_models,
    (v) => { state.options.hash_models = v; },
    "Identify weights by content rather than filename - the only thing that survives a rename. Reads every model file, so it is slow.",
  ));

  const claude = state.status?.claude;
  wrap.append(h("div", { style: { height: "1px", background: UI.line, margin: "14px 0" } }));

  if (claude?.available) {
    wrap.append(checkbox(
      "Add a Claude review",
      state.claude.enabled,
      (v) => { state.claude.enabled = v; rerender(); },
      "Claude reads the audit and investigates what the rules could not settle. Several model calls; takes about a minute.",
    ));
    if (state.claude.enabled) {
      wrap.append(field("Mode", select(claude.modes || ["full"], state.claude.mode, (v) => { state.claude.mode = v; })));
      wrap.append(field("Model", select(claude.models || [], state.claude.model, (v) => { state.claude.model = v; })));
    }
  } else {
    wrap.append(h("div", {
      style: {
        fontSize: "11px", lineHeight: "1.5", color: UI.dim, padding: "9px 10px",
        border: `1px solid ${UI.line}`, borderRadius: "5px", marginBottom: "12px",
      },
    },
      h("div", { style: { color: UI.fg, marginBottom: "3px" } }, "Claude review unavailable"),
      claude?.reason || "The server did not report on it.",
    ));
  }

  wrap.append(h("div", { style: { display: "flex", gap: "8px", marginTop: "6px" } },
    button("Run audit", runAudit),
    button("Back", () => go("profile"), "secondary"),
  ));
  return wrap;
}

function stepRunning() {
  return h("div", { style: { padding: "8px 0" } },
    sectionTitle("Auditing"),
    h("div", { style: { fontSize: "12px", color: UI.dim, lineHeight: "1.6" } },
      state.claude.enabled && state.status?.claude?.available
        ? "Reading the graph, then asking Claude to investigate what the rules could not settle. This takes about a minute."
        : "Reading the graph, resolving models and scoring risk."),
  );
}

function stepError() {
  return h("div", {},
    sectionTitle("That did not work"),
    h("div", {
      style: {
        fontSize: "12px", lineHeight: "1.6", whiteSpace: "pre-wrap",
        color: "#e88a7d", marginBottom: "14px",
      },
    }, state.error),
    h("div", { style: { display: "flex", gap: "8px" } },
      button("Try again", runAudit),
      button("Back", () => go("scope"), "secondary"),
    ),
  );
}

function statRow(label, value, tone) {
  return h("div", {
    style: {
      display: "flex", justifyContent: "space-between", gap: "10px",
      padding: "6px 0", borderBottom: `1px solid ${UI.line}`, fontSize: "12px",
    },
  },
    h("span", { style: { color: UI.dim } }, label),
    h("span", { style: { color: tone || UI.fg, textAlign: "right", fontWeight: "500" } }, value),
  );
}

function stepReport() {
  const data = state.data || {};
  const summary = data.summary || {};
  const clearance = data.report?.clearance || {};
  const findings = data.report?.risk?.findings || [];

  const wrap = h("div", {});

  /* The verdict leads, because it is the question that was asked. When there is
   * no profile there is no verdict, and saying so plainly beats an empty space. */
  const verdict = clearance.verdict || "unknown";
  const tone = VERDICT_TONE[verdict] || VERDICT_TONE.unknown;
  wrap.append(h("div", {
    style: {
      border: `1px solid ${tone}`, borderRadius: "6px", padding: "11px 12px",
      marginBottom: "14px",
    },
  },
    h("div", { style: { display: "flex", alignItems: "center", gap: "8px", marginBottom: "7px", flexWrap: "wrap" } },
      pill(clearance.verdict_label || "Needs more information", tone),
      summary.risk_band ? pill(`risk ${summary.risk} ${summary.risk_band}`, RISK_TONE[summary.risk_band] || UI.dim) : null,
    ),
    h("div", { style: { fontSize: "12px", lineHeight: "1.55", color: UI.fg } },
      clearance.headline || "No determination was made."),
    (clearance.missing_facts || []).length
      ? h("div", { style: { fontSize: "11px", color: UI.dim, marginTop: "6px" } },
          `Missing: ${clearance.missing_facts.join(" ")}`)
      : null,
  ));

  wrap.append(sectionTitle("At a glance"));
  const counts = summary.licence_counts || {};
  const composition = ["permissive", "conditional", "non-commercial", "unstated"]
    .filter((k) => counts[k]).map((k) => `${counts[k]} ${k}`).join(", ") || "no models";
  wrap.append(h("div", { style: { marginBottom: "14px" } },
    statRow("Models", summary.models ?? "-"),
    statRow("Licences", composition),
    statRow("Custom packs", summary.packs ?? "-"),
    statRow("Operational risk", `${summary.risk ?? "-"} ${summary.risk_band || ""}`.trim(),
            RISK_TONE[summary.risk_band]),
    statRow("Automation", `${summary.automation ?? "-"} ${summary.automation_band || ""}`.trim()),
  ));

  if (clearance.blockers?.length) {
    wrap.append(sectionTitle("Blockers"));
    const list = h("div", { style: { marginBottom: "14px" } });
    for (const item of clearance.blockers) {
      list.append(h("div", {
        style: {
          fontSize: "12px", lineHeight: "1.5", padding: "7px 9px", marginBottom: "6px",
          borderLeft: `2px solid ${VERDICT_TONE["no-go"]}`, background: "rgba(196,72,58,.08)",
        },
      }, typeof item === "string" ? item : (item.text || item.subject || JSON.stringify(item))));
    }
    wrap.append(list);
  }

  if (findings.length) {
    wrap.append(sectionTitle("Findings", `${findings.length} in total, worst first.`));
    const list = h("div", { style: { marginBottom: "14px" } });
    for (const finding of findings) {
      const dot = SEVERITY_TONE[finding.severity] || UI.dim;
      list.append(h("details", {
        style: {
          borderLeft: `2px solid ${dot}`, padding: "6px 0 6px 9px", marginBottom: "6px",
        },
      },
        h("summary", {
          style: { fontSize: "12px", cursor: "pointer", color: UI.fg, lineHeight: "1.45" },
        }, finding.title),
        h("div", { style: { fontSize: "11px", color: UI.dim, lineHeight: "1.55", marginTop: "5px" } },
          h("div", {
            style: {
              color: dot, textTransform: "uppercase",
              font: "600 10px/1 ui-monospace, monospace", letterSpacing: ".08em",
              marginBottom: "5px",
            },
          }, `${finding.severity} - ${finding.category}`),
          finding.detail || "",
          finding.recommendation
            ? h("div", { style: { marginTop: "6px", color: UI.fg } }, `Fix: ${finding.recommendation}`)
            : null,
        ),
      ));
    }
    wrap.append(list);
  }

  wrap.append(sectionTitle("Take it with you"));
  wrap.append(h("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "8px" } },
    button("Markdown", () => saveBlob(state.markdown, "text/markdown", `${state.name}.audit.md`)),
    button("PDF", () => downloadPdf(state.html, state.name)),
    button("HTML", () => saveBlob(state.html, "text/html", `${state.name}.audit.html`), "secondary"),
  ));
  wrap.append(h("div", { style: { fontSize: "11px", color: UI.dim, lineHeight: "1.5", marginBottom: "14px" } },
    "PDF opens your browser's print dialog - choose \"Save as PDF\" as the destination. "
    + "The report carries its own print stylesheet, so it lays out as a document rather than a screenshot."));

  wrap.append(h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
    button("Open full report", openFullReport),
    button("Run again", runAudit, "secondary"),
    button("Change scope", () => go("scope"), "secondary"),
  ));
  return wrap;
}

/* ------------------------------------------------------------------ */
/* Shell                                                               */
/* ------------------------------------------------------------------ */

function draw(el) {
  el.innerHTML = "";

  const root = h("div", {
    style: {
      display: "flex", flexDirection: "column", height: "100%",
      color: UI.fg, font: "13px/1.5 var(--p-font-family, system-ui, sans-serif)",
      boxSizing: "border-box",
    },
  });

  const head = h("div", { style: { padding: "12px 14px 0", flex: "0 0 auto" } },
    h("div", { style: { display: "flex", alignItems: "baseline", gap: "8px", marginBottom: "3px" } },
      h("strong", { style: { fontSize: "13px" } }, "Workflow audit"),
      h("span", { style: { fontSize: "11px", color: UI.dim } },
        state.status?.knowledge?.licences?.version ? `KB ${state.status.knowledge.licences.version}` : ""),
    ),
    h("div", { style: { fontSize: "11px", color: UI.dim, marginBottom: "12px" } }, state.name),
    steps(state.step),
  );

  const body = h("div", {
    style: {
      flex: "1 1 auto", overflowY: "auto", overflowX: "hidden",
      padding: "0 14px 16px", minHeight: "0",
    },
  });

  if (state.step === "profile") body.append(stepProfile());
  else if (state.step === "scope") body.append(stepScope());
  else if (state.step === "running") body.append(stepRunning());
  else if (state.step === "error") body.append(stepError());
  else if (state.step === "report") body.append(stepReport());

  root.append(head, body);
  el.append(root);
}

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

function openSidebar() {
  try {
    const tabs = app.extensionManager?.sidebarTab;
    if (tabs?.toggleSidebarTab) tabs.toggleSidebarTab(SIDEBAR_ID);
    else if (tabs) tabs.activeSidebarTabId = SIDEBAR_ID;
  } catch (err) {
    console.warn("[comfyaudit] could not open the sidebar tab", err);
  }
}

const COMMANDS = [
  {
    id: "comfyaudit.open",
    label: "Open workflow audit",
    icon: "pi pi-verified",
    function: openSidebar,
  },
  {
    id: "comfyaudit.audit",
    label: "Audit this workflow",
    icon: "pi pi-verified",
    function: () => { openSidebar(); state.claude.enabled = false; runAudit(); },
  },
  {
    id: "comfyaudit.review",
    label: "Audit + Claude review",
    icon: "pi pi-sparkles",
    function: () => {
      openSidebar();
      state.claude.enabled = true;
      if (state.status?.claude?.available) runAudit();
      else go("scope");
    },
  },
];

app.registerExtension({
  name: "comfyaudit.panel",
  settings: [...STUDIO_SETTINGS, ...REGISTRY_SETTINGS],
  commands: COMMANDS,
  menuCommands: [{ path: ["Extensions", "ComfyAudit"], commands: COMMANDS.map((c) => c.id) }],

  async setup() {
    // The sidebar is the whole point, but a frontend old enough to lack the API
    // should still get the commands rather than a broken extension.
    const manager = app.extensionManager;
    if (typeof manager?.registerSidebarTab === "function") {
      try {
        manager.registerSidebarTab({
          id: SIDEBAR_ID,
          icon: "pi pi-verified",
          title: "Audit",
          tooltip: "ComfyAudit - models, licences, automation and production risk",
          type: "custom",
          render: (el) => {
            mount = el;
            if (!state.data) state.name = graphName();
            draw(el);
          },
          destroy: () => { mount = null; },
        });
      } catch (err) {
        console.warn("[comfyaudit] sidebar tab registration failed", err);
      }
    } else {
      console.warn("[comfyaudit] this frontend has no sidebar tab API; use Extensions > ComfyAudit");
    }

    await loadStatus();

    const status = state.status;
    const live = status?.knowledge?.live_introspection ? "live node schemas" : "bundled catalog";
    const studio = status?.studio_profile?.profile_set
      ? status.studio_profile.profile
      : "none set - reports will describe the licences without reaching a verdict";
    console.log(
      `[comfyaudit] ready - ${live}, licence KB v${status?.knowledge?.licences?.version}, `
      + `Claude ${status?.claude?.available ? "available" : "unavailable: " + status?.claude?.reason}`
      + `\n[comfyaudit] studio profile: ${studio}`
    );
  },
});
