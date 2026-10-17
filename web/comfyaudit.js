/**
 * comfyaudit - studio profile, knowledge-base status and report exports.
 *
 * The sidebar lives in shothistory.js: one Comfy Workflow Tracker tab that records a
 * workflow's life on its own and, when someone chooses "Package for
 * delivery", runs this audit against the studio profile, lists what would make
 * the workflow commercially safe, and packages it for legal. This file holds
 * what that flow shares with the rest of the pack: the studio profile (one
 * definition, used for ComfyUI's settings and for the package flow's first
 * step), the server's status, and the browser-side exports.
 *
 * Everything is rendered with plain DOM. ComfyUI's frontend is Vue, but an
 * extension that reaches into it inherits its release cycle; plain elements and
 * CSS custom properties survive frontend upgrades.
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const PREFIX = "/comfyaudit";

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

export function escapeHtml(text) {
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
export const PROFILE_FIELDS = [
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
  category: ["Comfy Workflow Tracker", "Studio profile", field.name],
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
    category: ["Comfy Workflow Tracker", "Registry", "Registry file"],
  },
];

/* ComfyUI has moved its settings API once already, so both spellings are tried
 * before giving up. Failing to read a setting must never break the panel: an
 * unreadable profile is the same as an unset one, which is a supported state. */
export function getSetting(id, fallback) {
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

export async function setSetting(id, value) {
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

export function profileIsSet() {
  const territory = getSetting("ComfyAudit.Studio.Territory", "not set");
  const revenue = getSetting("ComfyAudit.Studio.Revenue", "not set");
  return territory !== "not set" && revenue !== "not set";
}


/* ------------------------------------------------------------------ */
/* Downloads                                                           */
/* ------------------------------------------------------------------ */

export function saveBlob(text, type, filename) {
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
  const title = `<title>${escapeHtml(name)}</title>`;
  out = /<title>[\s\S]*?<\/title>/i.test(out)
    ? out.replace(/<title>[\s\S]*?<\/title>/i, title)
    : out.replace(/<head>/i, `<head>${title}`);
  return out.includes("</head>") ? out.replace("</head>", `${extra}</head>`) : extra + out;
}

export function downloadPdf(html, name) {
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
/* Status                                                              */
/* ------------------------------------------------------------------ */

let status = null;

/** What the server knows: knowledge-base versions, the studio profile, Claude. */
export function serverStatus() {
  return status;
}

export async function loadStatus() {
  try {
    const response = await api.fetchApi(`${PREFIX}/status`);
    status = await response.json();
  } catch (err) {
    status = null;
  }
  return status;
}

/* ------------------------------------------------------------------ */
/* Registration                                                        */
/* ------------------------------------------------------------------ */

/* The audit used to be its own sidebar tab. These commands now open the one
 * Comfy Workflow Tracker tab, registered by shothistory.js, at its package flow. */
const run = (id) => () => app.extensionManager?.command?.execute?.(id);

const COMMANDS = [
  { id: "comfyaudit.open", label: "Open Comfy Workflow Tracker", icon: "pi pi-verified", function: run("comfyaudit.history.open") },
  { id: "comfyaudit.audit", label: "Package for delivery", icon: "pi pi-verified", function: run("comfyaudit.history.package") },
];

app.registerExtension({
  name: "comfyaudit.panel",
  settings: [...STUDIO_SETTINGS, ...REGISTRY_SETTINGS],
  commands: COMMANDS,
  menuCommands: [{ path: ["Extensions", "Comfy Workflow Tracker"], commands: COMMANDS.map((c) => c.id) }],

  async setup() {
    await loadStatus();
    const live = status?.knowledge?.live_introspection ? "live node schemas" : "bundled catalog";
    const studio = status?.studio_profile?.profile_set
      ? status.studio_profile.profile
      : "none set - delivery checks will describe the licences without reaching a verdict";
    console.log(
      `[comfyaudit] ready - ${live}, licence KB v${status?.knowledge?.licences?.version}`
      + `
[comfyaudit] studio profile: ${studio}`
    );
  },
});
