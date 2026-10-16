// ComfyAudit Review: the legal team's view of a ComfyAudit delivery package.
//
// Three ways to run, shown in the top bar:
//   * on its own: packages are opened and checked in this browser, read-only;
//   * connected to the studio's own ComfyAudit server (python -m comfyaudit.review):
//     the package goes to that server only, which verifies signatures and signs every
//     answer and decision into the shot's history;
//   * demo: two packages of a fictional show, with answers and decisions kept in this
//     tab only, never signed.
// Every value from a package is put on the page with textContent, never as HTML.

import * as P from "./package.js";

const C_MARK = { w: 521, h: 562, d: "M108.7 560.9C97.9 558.3 86.9 548.6 83.0 538.3C79.1 527.8 79.7 522.9 90.0 486.9C99.8 452.6 100.3 449.5 96.3 444.1C91.2 437.4 91.9 437.5 58.2 437.0L27.5 436.5L21.2 433.6C13.2 429.9 5.0 421.3 2.1 413.5C-2.5 401.2 -3.3 404.9 31.0 284.4C48.1 224.5 63.4 172.6 65.1 169.1C73.0 152.0 87.3 138.2 104.4 131.1C116.5 126.1 125.7 125.0 157.5 125.0C185.1 125.0 185.8 125.0 189.0 122.7C194.7 118.9 195.8 116.3 205.6 82.0C215.9 45.7 218.9 38.5 228.0 27.2C237.1 16.1 252.0 6.5 267.0 2.3C273.1 0.6 280.1 0.5 384.0 0.5L494.5 0.5L500.2 3.3C512.9 9.5 520.2 20.6 520.8 34.5C521.2 42.4 520.8 43.9 507.6 90.0C500.1 116.1 492.6 140.4 491.0 143.9C481.3 164.6 463.9 179.1 441.0 185.6C435.0 187.3 427.8 187.5 343.5 188.0L252.5 188.5L249.2 190.8C247.4 192.1 245.3 194.4 244.5 196.0C242.6 199.7 198.0 355.9 198.0 358.9C198.0 362.6 201.7 369.6 204.8 371.7C207.4 373.4 212.3 373.5 296.5 374.0L385.5 374.5L391.5 376.9C404.5 382.1 413.0 394.5 413.8 409.0C414.2 417.4 414.1 417.8 401.1 463.4C393.8 488.6 386.8 512.0 385.4 515.4C378.6 532.1 363.8 547.2 346.4 555.1C331.8 561.7 338.4 561.4 220.0 561.6C158.5 561.7 110.9 561.5 108.7 560.9Z" };

const LEGAL_TONE = { cleared: "ok", "sent-back": "stop", awaiting: "warn" };
const VERDICT_TONE = { go: "ok", conditions: "warn", "no-go": "stop", unknown: "warn" };
const ASSURANCE_TONE = { witnessed: "ok", signed: "ok", consistency: "lilac", none: "stop", absent: "stop" };
const SCOPE_WORDS = { shot: "This shot", show: "The whole show", studio: "The whole studio" };
const DONE = {
  opened: "Package opened and checked.",
  "answered-shot": "Recorded in this shot's history.",
  "answered-show": "Recorded once for the whole show. Every shot of the show inherits it.",
  "answered-studio": "Recorded once for the whole studio. Every shot inherits it.",
  clear: "Shot cleared.", "send-back": "Shot sent back.",
};

const store = {
  get(k, d = "") { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window: settings last for this visit */ } },
};

const state = {
  rules: { rules: {} },
  packages: new Map(),   // id -> package opened in this tab
  studioIds: new Map(),  // local id -> id on the studio server
  demo: false,
  demoAnswers: [],
  api: store.get("comfyaudit.review.api"),
  reviewer: store.get("comfyaudit.review.reviewer"),
  status: null,          // the studio server's /status, when connected
  statusError: "",
  flash: "", error: "",
};

// ---------------------------------------------------------------- tiny DOM helper
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
  return el;
}
const pill = (text, tone = "", note = "") => h("span", { class: `pill ${tone}` }, text, note ? h("small", { text: note }) : null);
const go = (label, onclick, attrs = {}) => h("button", { class: "go", type: "button", onclick, ...attrs }, h("span", { text: label }));
const ghost = (label, onclick, attrs = {}) => h("button", { class: `ghost ${attrs.tone || ""}`, type: "button", onclick, ...attrs }, h("span", { text: label }));
const date = (v) => { const t = String(v || ""); return t.length >= 16 ? t.slice(0, 16).replace("T", " ") : t; };
const card = (title, ...kids) => h("section", { class: "card" }, title ? h("h2", { text: title }) : null, ...kids);

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 3200);
}

function cLoader(text, sub) {
  const H = 64, W = C_MARK.w * (H / C_MARK.h), depth = 16, N = 12;
  const slab = h("div", { class: "c-slab" });
  for (let i = 0; i < N; i++) {
    const t = i / (N - 1), mix = (a, b) => Math.round(a + (b - a) * t);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${C_MARK.w} ${C_MARK.h}`);
    svg.setAttribute("width", W); svg.setAttribute("height", H);
    svg.style.transform = `translateZ(${(-depth / 2 + (i * depth) / (N - 1)).toFixed(2)}px)`;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", C_MARK.d);
    // Plum at the back to lilac near the face, yellow on the face: the C extruded.
    path.setAttribute("fill", i === N - 1 ? "#f2ff59" : `rgb(${mix(0x49, 0xdb)}, ${mix(0x37, 0xd6)}, ${mix(0x8b, 0xf2)})`);
    svg.append(path); slab.append(svg);
  }
  return h("div", { class: "loader", role: "status", "aria-live": "polite" },
    h("div", { class: "c-stage", "aria-hidden": "true", style: { width: `${W}px`, height: `${H}px` } }, slab),
    h("div", { style: { fontWeight: "600" }, text }), sub ? h("div", { class: "dim small", text: sub }) : null);
}

// ---------------------------------------------------------------- the studio server
const mode = () => (state.demo ? "demo" : state.status ? "studio" : "local");
const base = () => state.api.replace(/\/+$/, "");

async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(base() + path, { credentials: "include", ...opts,
      headers: { Accept: "application/json", ...(opts.headers || {}) } });
  } catch {
    throw new Error(`Could not reach your studio's ComfyAudit server at ${base()}. Check the address, that it ` +
      "lists this page in COMFYAUDIT_REVIEW_CORS_ORIGINS, and that it is served over HTTPS.");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `The studio server answered ${r.status}.`);
  return data;
}

async function connect() {
  state.status = null; state.statusError = "";
  if (!state.api) return;
  try {
    const s = await api("/status");
    if (s.app !== "ComfyAudit Review API") throw new Error("That address is not a ComfyAudit Review server.");
    state.status = s;
  } catch (e) { state.statusError = e.message; }
}

function whereLabel() {
  const el = document.getElementById("where");
  el.replaceChildren();
  const m = mode();
  el.className = `where ${m === "studio" ? "on" : m === "demo" ? "demo" : ""}`;
  el.append(h("span", { class: "dot" }),
    m === "demo" ? "Demo: nothing is signed or saved" :
      m === "studio" ? `Connected to your studio server${state.status.you?.name ? ` as ${state.status.you.name}` : ""}` :
        "On its own: read-only");
}

// ---------------------------------------------------------------- opening packages
async function openFiles(files) {
  const view = document.getElementById("view");
  for (const file of files) {
    view.replaceChildren(cLoader(`Checking ${file.name}`,
      "Reading the zip in this browser, fingerprinting every file and re-hashing the shot's history."));
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const pkg = await P.openPackage(bytes, file.name);
      state.packages.set(pkg.id, pkg);
      if (mode() === "studio") {
        const out = await api(`/open?filename=${encodeURIComponent(file.name)}`, { method: "POST",
          headers: { "Content-Type": "application/zip" }, body: bytes });
        state.studioIds.set(pkg.id, out.id);
      }
      state.flash = DONE.opened;
      location.hash = `#/p/${pkg.id}`;
    } catch (e) {
      state.error = e.message;
      location.hash = "#/";
    }
  }
  render();
}

async function startDemo() {
  const view = document.getElementById("view");
  view.replaceChildren(cLoader("Opening the demo show", "Two packages of a fictional show, checked in this browser."));
  try {
    const idx = await (await fetch("demo/index.json")).json();
    state.demo = true; state.status = null; state.demoAnswers = []; state.packages.clear();
    for (const name of idx.packages) {
      const bytes = new Uint8Array(await (await fetch(`demo/${name}`)).arrayBuffer());
      const pkg = await P.openPackage(bytes, name);
      state.packages.set(pkg.id, pkg);
    }
    location.hash = `#/show/${P.productionKey(idx.show)}`;
  } catch (e) { state.error = `The demo could not be opened: ${e.message}`; location.hash = "#/"; }
  render();
}

async function leaveDemo() {
  state.demo = false; state.packages.clear(); state.demoAnswers = [];
  await connect();
  location.hash = "#/";
  render();
}

// ---------------------------------------------------------------- documents
function openDocument(title, html) {
  // A sandboxed frame without allow-same-origin: the report runs in an origin of its own,
  // so whatever it contains cannot read this page, its settings or the studio server.
  const frame = h("iframe", { sandbox: "allow-scripts allow-popups", title, referrerpolicy: "no-referrer" });
  frame.srcdoc = html;
  const close = () => { overlay.remove(); removeEventListener("keydown", keys, true); };
  const keys = (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } };
  const overlay = h("div", { class: "overlay", role: "dialog", "aria-label": title },
    h("div", { class: "bar" }, h("strong", { text: title }),
      h("div", { class: "row", style: { marginTop: 0 } },
        ghost("Download", () => download(new Blob([html], { type: "text/html" }), `${title}.html`)),
        go("Close", close))),
    frame);
  addEventListener("keydown", keys, true);
  document.body.append(overlay);
}

function download(blob, name) {
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

// ---------------------------------------------------------------- writing
function nowLocal() {
  // Local time with its offset, as the server writes times (2026-10-16T15:06:00-05:00).
  const d = new Date(), off = -d.getTimezoneOffset(), pad = (n) => String(Math.abs(n)).padStart(2, "0");
  const local = new Date(d.getTime() + off * 60000).toISOString().slice(0, 19);
  return `${local}${off >= 0 ? "+" : "-"}${pad(Math.trunc(off / 60))}:${pad(off % 60)}`;
}

function demoActor() {
  return { name: state.reviewer || "Demo reviewer", role: "legal", declared_by: "demo" };
}

async function recordAnswer(ctx, item, note) {
  note = note.trim();
  if (!note) throw new Error("Say what confirms it; a blank answer proves nothing.");
  if (ctx.kind === "studio") {
    const out = await api(`/p/${ctx.studioId}/answer`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag: item.tag, scope: item.scope, note, reviewer: state.reviewer }) });
    return `answered-${out.scope}`;
  }
  // Demo: kept in this tab, marked as demo, never signed.
  const pkg = ctx.pkg, at = nowLocal();
  if (item.scope === "shot") {
    pkg.extra.push({ seq: pkg.history.entries.length + pkg.extra.length + 1, at, event: "note", note,
      actor: demoActor(), data: { evidence: item.tag }, demo: true });
  } else {
    const view = ctx.view;
    state.demoAnswers.push({ tag: item.tag, scope: item.scope, note, at, demo: true, by: demoActor(),
      production: item.scope === "studio" ? "*studio*" : view.shot.production,
      production_name: item.scope === "studio" ? "" : view.shot.project });
  }
  return `answered-${item.scope}`;
}

async function recordDecision(ctx, decision, reason, acceptOpen) {
  reason = reason.trim();
  if (!reason) throw new Error("Give a reason. It is sealed with the decision and is what the artist and the record will show.");
  if (ctx.kind === "studio") {
    await api(`/p/${ctx.studioId}/decide`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, reason, accept_open: !!acceptOpen, reviewer: state.reviewer }) });
    return decision;
  }
  const view = ctx.view, pkg = ctx.pkg;
  const open = view.open_items.flatMap((g) => g.items.filter((i) => i.state !== "answered").map((i) => i.tag || i.text));
  if (decision === "clear" && open.length && !acceptOpen) {
    throw new Error(`${open.length} item(s) are still open. Answer them, or confirm you are clearing the shot with them open.`);
  }
  const at = nowLocal(), seq = () => pkg.history.entries.length + pkg.extra.length + 1;
  pkg.extra.push({ seq: seq(), at, event: decision === "clear" ? "approved" : "rejected", note: reason, actor: demoActor(), demo: true,
    data: { legal_review: { decision, label: decision === "clear" ? "Cleared by legal" : "Sent back by legal",
      package: pkg.sha256, open_items: open } } });
  const stage = view.shot.stage;
  const target = decision === "clear" && ["sandbox", "production"].includes(stage) ? "cleared"
    : decision === "send-back" && stage !== "production" ? "production" : "";
  if (target) pkg.extra.push({ seq: seq(), at, event: "promoted", note: "", actor: demoActor(), demo: true, data: { stage: target } });
  return decision;
}

// ---------------------------------------------------------------- pages
function home() {
  const input = h("input", { type: "file", accept: ".zip,application/zip", multiple: true, id: "pick",
    onchange: (e) => openFiles([...e.target.files]) });
  const drop = h("div", { class: "drop" },
    h("p", { class: "eyebrow", text: "Open a delivery package" }),
    h("p", { text: "Drop the .delivery.zip that Package for delivery made in ComfyUI, or choose it." }),
    input,
    h("div", { class: "row", style: { justifyContent: "center" } },
      go("Choose a package", () => input.click()), ghost("Try the demo show", startDemo)),
    h("div", { class: "privacy" }, h("b", { text: "Private by design." }),
      h("span", { text: mode() === "studio"
        ? "The package is read and checked in this browser, then sent to your studio's own ComfyAudit server, and nowhere else, so your answers and decisions can be signed into the shot."
        : "The package is read and checked in this browser. Nothing is uploaded or stored. To record answers and decisions, connect your studio's own ComfyAudit server." })));
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => openFiles([...e.dataTransfer.files]));

  const opened = [...state.packages.values()].map((pkg) => P.buildView(pkg, state.rules, state.demoAnswers));
  const shows = {};
  for (const v of opened) (shows[v.shot.production] ||= { name: v.shot.project, n: 0, waiting: 0 }) && (shows[v.shot.production].n++, shows[v.shot.production].waiting += v.legal_state === "awaiting");

  const hero = h("section", { class: "card hero" },
    h("p", { class: "eyebrow", text: "For studio legal and production" }),
    h("h1", { text: "Review a shot before it ships" }),
    h("p", { class: "lede", text: "Open a ComfyAudit delivery package to see what the artist's sign-off decided, what it left for legal, and whether the record holds. Answer what is yours to answer, then clear the shot or send it back." }),
    h("div", { style: { marginTop: "18px" } }, drop));
  const kids = [hero];
  if (state.demo) {
    kids.push(h("div", { class: "callout plum" }, h("span", { class: "demo-badge", text: "Demo" }), " ",
      "A fictional show, studio and people. Answers and decisions stay in this tab and are not signed. ",
      h("a", { href: "#/", onclick: (e) => { e.preventDefault(); leaveDemo(); }, text: "Leave the demo" })));
  }
  if (opened.length) {
    kids.push(card("Opened in this tab", table(["Shot", "Legal", "Verdict at sign-off", "Open"],
      opened.map((v) => [h("a", { href: `#/p/${v.package.id}`, text: v.shot.name || v.package.filename }),
        pill(v.legal_state_label, LEGAL_TONE[v.legal_state]),
        pill(v.decision?.verdict_label || "No sign-off", VERDICT_TONE[v.decision?.verdict] || "warn"), String(v.open_count)])),
      h("p", { class: "small dim", text: "Kept only while this tab is open." })));
    kids.push(card("Shows", h("ul", {}, Object.entries(shows).map(([k, s]) =>
      h("li", {}, h("a", { href: `#/show/${k}`, text: s.name || "No show given" }), h("span", { class: "dim", text: ` · ${s.n} package(s), ${s.waiting} waiting` }))))));
  }
  kids.push(h("div", { class: "grid2" },
    card("How it works", h("ol", { class: "small", style: { paddingLeft: "18px", margin: 0 } },
      h("li", { text: "Open the package. Every file is fingerprinted against its index, and the shot's history is re-hashed in this browser." }),
      h("li", { text: "Read the decision made at sign-off in ComfyUI, and what it left for legal." }),
      h("li", { text: "Answer what you can. Items for a whole show or the studio are answered once, and every shot of that show inherits them." }),
      h("li", { text: "Clear the shot, or send it back with a reason. The decision is signed into the shot's history by your studio's server." }))),
    card("Your studio's server", h("p", { class: "small", text: mode() === "studio"
      ? `Connected. Decisions are signed ${state.status.signing === "per-person" ? "with each person's own key, from the studio sign-in" : "with the studio key"}.`
      : "Recording answers and decisions needs your studio's ComfyAudit server (python -m comfyaudit.review), run on your own network. Without it, this page is read-only." }),
      h("div", { class: "row" }, h("a", { class: "ghost", href: "#/settings" }, h("span", { text: "Studio server settings" }))))));
  return kids;
}

function table(head, rows) {
  return h("div", { class: "scroll" }, h("table", {}, h("tr", {}, head.map((x) => h("th", { text: x }))),
    rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c))))));
}

function itemsSection(view, ctx, can) {
  if (!view.decision) return [h("p", { class: "dim", text: "This package carries no sign-off, so nothing was left for legal." })];
  if (!view.open_items.length) return [h("p", { text: "The sign-off left nothing for legal to confirm." })];
  const show = view.shot.project;
  const out = [];
  for (const [level, heading, hint] of [
    ["studio", "For the whole studio", "Answered once; every shot of every show inherits it."],
    ["show", `For the whole show${show ? " " + show : ""}`, "Answered once; every shot of this show inherits it."],
    ["shot", "For this shot only", "Recorded in this shot's own history."]]) {
    const groups = view.open_items.map((g) => ({ ...g, items: g.items.filter((i) => i.scope === level) })).filter((g) => g.items.length);
    if (!groups.length) continue;
    const total = groups.reduce((n, g) => n + g.items.length, 0);
    const open = groups.reduce((n, g) => n + g.items.filter((i) => i.state !== "answered").length, 0);
    out.push(h("div", { class: "level" }, h("h3", { text: `${heading} · ${open} open of ${total}` }),
      h("p", { class: "small dim", style: { margin: "2px 0 6px" }, text: hint }),
      groups.map((g) => {
        const openN = g.items.filter((i) => i.state !== "answered").length;
        return h("details", { class: "rule" },
          h("summary", {}, h("strong", { text: g.title }), h("span", { class: "meta", text: `${g.jurisdiction} · ${openN ? openN + " open" : "all answered"}` })),
          g.why ? h("p", { class: "small dim", style: { margin: "6px 0 0 18px" } }, g.why, " ",
            /^https?:/.test(g.source) ? h("a", { href: g.source, target: "_blank", rel: "noreferrer", text: "Source" }) : null) : null,
          g.items.map((i) => itemCard(i, view, ctx, can)));
      })));
  }
  return out;
}

function itemCard(item, view, ctx, can) {
  const scope = SCOPE_WORDS[item.scope] || item.scope;
  if (item.state === "answered") {
    const a = item.answer || {};
    const where = a.scope === "shot" ? "for this shot" : a.scope === "studio" ? "once for the studio" : `once for the show ${a.production_name || ""}`;
    const sig = a.signature && a.signature !== "signed" ? ` (${a.signature})` : "";
    return h("div", { class: "item done" }, h("div", { class: "scope", text: `${scope} · answered` }), h("div", { text: item.text }),
      h("div", { class: "answer", text: `✓ Answered ${where} by ${a.by || ""}${a.at ? ", " + date(a.at) : ""}${sig}${a.note ? `: “${a.note}”` : ""}` }));
  }
  const box = h("div", { class: "item" }, h("div", { class: "scope", text: `${scope} · open` }), h("div", { text: item.text }));
  if (can && item.tag) {
    const ta = h("textarea", { "aria-label": "What confirms it" });
    const label = { shot: "Record for this shot", show: `Record for the whole show${view.shot.project ? " " + view.shot.project : ""}`, studio: "Record for the whole studio" }[item.scope];
    box.append(h("label", { class: "small", text: "What confirms it (a document, an email, where it is filed)" }), ta,
      h("div", { class: "row" }, ghost(label, async (e) => {
        e.currentTarget.disabled = true;
        try { const done = await recordAnswer(ctx, item, ta.value); state.flash = DONE[done]; }
        catch (err) { state.error = err.message; }
        render();
      })));
  }
  return box;
}

function decisionSection(view, ctx, can, why) {
  const out = [];
  const last = view.last_review;
  if (last) {
    out.push(h("div", { class: `callout ${last.decision === "clear" ? "ok" : "stop"}` },
      h("strong", { text: last.decision === "clear" ? "Cleared" : "Sent back" }),
      ` by ${last.by} (${last.role}), ${date(last.at)}${last.demo ? " (demo)" : ""}`, h("br"), `“${last.reason}”`,
      last.decision === "clear" && last.open_items?.length ? h("div", { class: "small", text: `Cleared with ${last.open_items.length} item(s) still open.` }) : null));
  }
  if (!can) { out.push(h("div", { class: "callout plum", text: why })); return out; }
  const reason = h("textarea", { id: "reason", "aria-label": "Reason" });
  reason.value = ctx.draft || "";
  const ack = view.open_count ? h("input", { type: "checkbox", id: "ack" }) : null;
  const act = (decision) => async (e) => {
    e.currentTarget.disabled = true;
    ctx.draft = reason.value;
    try { const done = await recordDecision(ctx, decision, reason.value, ack?.checked); state.flash = DONE[done]; ctx.draft = ""; drafts.delete(ctx.key); }
    catch (err) { state.error = err.message; drafts.set(ctx.key, reason.value); }
    render();
  };
  out.push(h("label", { class: "small", for: "reason", text: "Reason (sealed with the decision; the artist sees it)" }), reason,
    ack ? h("label", { class: "check" }, ack, `Clear it with ${view.open_count} item(s) still open (they are recorded with the decision)`) : null,
    h("div", { class: "row" }, go("Clear the shot", act("clear")), ghost("Send it back", act("send-back"), { tone: "stop" })));
  return out;
}
const drafts = new Map();

async function shotPage(id, studioOnly) {
  const pkg = studioOnly ? null : state.packages.get(id);
  if (!studioOnly && !pkg) return [card("This package is not open", h("p", { text: "Packages are kept only while this tab is open. Open it again from the start page." }), h("a", { href: "#/", text: "Back to the start" }))];
  const studioId = studioOnly ? id : state.studioIds.get(id);
  const kind = state.demo ? "demo" : studioId && mode() === "studio" ? "studio" : "local";
  let view;
  if (kind === "studio") view = await api(`/p/${studioId}?format=json`);
  else view = P.buildView(pkg, state.rules, state.demoAnswers);
  if (pkg && kind === "studio") {
    // The browser's own check stands beside the server's.
    const mine = P.buildView(pkg, state.rules);
    view.browser_check = { chain: mine.integrity.label, package_files: mine.integrity.package_files };
  }
  const key = `${kind}:${id}`;
  const ctx = { kind, pkg, studioId, view, key, draft: drafts.get(key) || "" };
  const you = state.status?.you || {};
  const signing = state.status?.signing;
  const can = kind === "demo" || (kind === "studio" && view.writable &&
    (signing === "per-person" ? you.can_sign : signing === "studio-key" && !!state.reviewer));
  const why = kind === "local" ? "Connect your studio's ComfyAudit server (Studio server, top right) to record answers and decisions. On its own, this page reads packages but writes nothing."
    : !view.writable ? view.why_not_writable
      : state.status?.signing === "studio-key" && !state.reviewer ? "Give your name in Studio server settings first. It is recorded with what you decide."
        : you.problem || "This server cannot record decisions.";
  const s = view.shot, d = view.decision, i = view.integrity;
  const ids = Object.entries(s.external_ids || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
  const band = h("div", { class: "band" },
    pill(view.legal_state_label, LEGAL_TONE[view.legal_state]),
    d ? pill(d.verdict_label || d.verdict, VERDICT_TONE[d.verdict] || "warn", "at sign-off") : pill("No sign-off", "stop"),
    pill(i.label, ASSURANCE_TONE[i.assurance] || "warn", "history"),
    pill(i.package_files ? "Package intact" : "Package altered", i.package_files ? "ok" : "stop"),
    view.open_count ? pill(`${view.open_count} open`, "warn") : null,
    kind === "demo" ? h("span", { class: "demo-badge", text: "Demo" }) : null,
    h("a", { class: "pill", href: "#decide", onclick: (e) => { e.preventDefault(); document.getElementById("decide")?.scrollIntoView({ behavior: "smooth" }); }, text: "Go to your decision ↓" }));
  const signoff = d ? [h("dl", { class: "facts" },
    h("dt", { text: "Verdict" }), h("dd", {}, h("strong", { text: d.verdict_label || d.verdict }), d.headline ? ` - ${d.headline}` : ""),
    h("dt", { text: "Signed off by" }), h("dd", { text: `${d.signed_off_by}${d.role ? ` (${d.role})` : ""}, ${date(d.signed_off_at)}` }),
    d.note ? [h("dt", { text: "Note" }), h("dd", { text: d.note })] : null,
    d.checked_as ? [h("dt", { text: "Checked as" }), h("dd", { text: d.checked_as })] : null),
  d.exception ? h("div", { class: "callout" }, h("strong", { text: "Delivered as an exception. " }), `Reason given: “${d.exception}”`) : null,
  d.blockers?.length ? [h("h3", { style: { marginTop: "14px" }, text: "What blocked a clean yes" }), h("ul", {}, d.blockers.map((b) => h("li", { text: String(b) })))] : null,
  d.conditions?.length ? [h("h3", { style: { marginTop: "14px" }, text: "Conditions" }), h("ul", {}, d.conditions.map((c) => h("li", { text: String(c) })))] : null,
  d.index_agrees === false ? h("div", { class: "callout stop", text: "The package index states a different verdict from the signed sign-off. The signed one is shown here." }) : null]
    : [h("p", { class: "dim", text: "This package has no sign-off entry." })];
  const docNames = { "legal-report": "Legal report (provenance record)", "licence-audit": "Licence audit", "life-of-the-shot": "Life of the shot" };
  const docs = h("ul", { class: "list-docs" }, view.documents.map((f) => h("li", {},
    h("div", {}, h("strong", { text: docNames[f.role] || f.path }), h("div", { class: "small dim", text: f.description })),
    pkg ? go("Open", async () => openDocument(docNames[f.role] || f.path, new TextDecoder().decode(await P.member(pkg.zip, f.path))))
      : h("a", { class: "go", href: `${base()}/p/${studioId}/file/${encodeURIComponent(f.path)}`, target: "_blank", rel: "noopener" }, h("span", { text: "Open" })))));
  const inherited = view.inherited.length ? table(["Item", "Answered for", "Answer", "Signature"], view.inherited.map((a) => [
    h("div", {}, a.text, h("div", { class: "small dim", text: a.rule_title })),
    a.scope === "studio" ? "the studio" : `show ${a.production_name || ""}`,
    h("div", {}, `“${a.note || ""}”`, h("div", { class: "small dim", text: `${a.by || ""}, ${date(a.at)} · ${a.when}` })),
    pill({ signed: "Signature checks", unsigned: "Unsigned", altered: "Altered", sealed: "Sealed in sign-off", demo: "Demo" }[a.signature] || a.signature,
      { signed: "ok", sealed: "lilac", demo: "warn", unsigned: "warn" }[a.signature] || "stop")])) : h("p", { class: "dim", text: "Nothing inherited." });
  const files = table(["File", "Role", "Fingerprint"], [...view.documents, ...view.other_files].map((f) => [
    h("span", { class: "mono", text: f.path }), h("span", { class: "small dim", text: f.role }),
    pill(f.state === "ok" ? "matches" : f.state, f.state === "ok" ? "ok" : "stop")]));
  const download = h("div", { class: "row" },
    kind === "studio" ? ghost("Download the updated workflow", async () => {
      const r = await fetch(`${base()}/p/${studioId}/workflow.json`, { credentials: "include" });
      download2(await r.blob(), s.name);
    }) : pkg ? ghost("Download the workflow as packaged", () => download(new Blob([JSON.stringify(pkg.workflow, null, 1)], { type: "application/json" }), pkg.wfPath)) : null,
    h("span", { class: "small dim", text: kind === "studio" ? "Give this to the artist: it carries every decision made here." : kind === "demo" ? "Demo entries are not added to downloads." : "" }));
  const crumbs = h("div", { class: "crumbs" }, h("a", { href: "#/", text: "Start" }), " › ",
    s.production ? h("a", { href: `#/show/${s.production}`, text: s.project || "Show" }) : "No show", " › ", s.name || "Shot");
  return [crumbs, h("h1", { text: s.name || "Unnamed shot" }),
    h("p", { class: "lede", text: [s.project || "No show given", ids, `stage: ${s.stage}`].filter(Boolean).join(" · ") }),
    band,
    card("Signed off in ComfyUI", ...signoff),
    card("Documents", docs, h("p", { class: "small dim", text: "They open as they were packaged, in a sandbox that cannot reach this page or your studio server." })),
    card(`Left for legal${view.open_count ? ` · ${view.open_count} open` : ""}`,
      h("p", { class: "small dim", style: { marginTop: 0 }, text: "Production facts the record cannot prove itself. Items for a whole show or the studio are answered once, and every shot of that show inherits the answer." }),
      ...itemsSection(view, ctx, can)),
    card("Inherited from the show and studio", inherited),
    h("section", { class: "card", id: "decide" }, h("h2", { text: "Your decision" }), ...decisionSection(view, ctx, can, why), download),
    card("Can this record be trusted?",
      h("p", {}, h("strong", { text: `${i.label}. ` }), i.detail),
      view.browser_check ? h("p", { class: "small dim", text: `Checked again in this browser: ${view.browser_check.chain}; package ${view.browser_check.package_files ? "intact" : "altered"}.` }) : null,
      [...(i.problems || []), ...(i.package_problems || [])].length ? h("div", { class: "callout stop" }, h("strong", { text: "Problems" }),
        h("ul", {}, [...i.problems, ...i.package_problems].map((p) => h("li", { text: p })))) : null,
      (i.notes || []).length ? h("details", {}, h("summary", { text: `Notes (${i.notes.length})` }), h("ul", { class: "small" }, i.notes.map((n) => h("li", { text: n })))) : null,
      h("details", {}, h("summary", { text: "Every file in the package, checked against its fingerprint" }), files,
        h("p", { class: "small dim" }, "Package ", h("span", { class: "mono", text: view.package.filename }), ", SHA-256 ",
          h("span", { class: "mono", text: view.package.sha256 }), `, made with ComfyAudit ${view.package.made_with} on ${date(view.package.made_at)}.`))),
    card("Recorded since the package was made", view.since_packaging.length
      ? h("ul", {}, view.since_packaging.map((r) => h("li", {}, h("strong", { text: r.label }),
        ` - ${r.who}${r.role ? ` (${r.role})` : ""}, ${date(r.at)}${r.note ? `: “${r.note}”` : ""}${r.demo ? " (demo)" : ""}`)))
      : h("p", { class: "dim", text: "Nothing yet." }))];
}
const download2 = (blob, name) => download(blob, `${name || "shot"}.json`);

async function showPage(key) {
  let shots, answers = [], studioAnswers = [], name = key;
  if (mode() === "studio") {
    const d = await api(`/show/${encodeURIComponent(key)}?format=json`);
    name = d.name; answers = d.answers; studioAnswers = d.studio_answers;
    const local = new Map([...state.studioIds].map(([l, s]) => [s, l]));
    shots = d.shots.map((r) => ({ ...r, href: local.has(r.id) ? `#/p/${local.get(r.id)}` : `#/s/${r.id}` }));
  } else {
    const views = [...state.packages.values()].map((p) => P.buildView(p, state.rules, state.demoAnswers)).filter((v) => v.shot.production === key);
    name = views[0]?.shot.project || key;
    shots = views.map((v) => ({ name: v.shot.name, href: `#/p/${v.package.id}`, legal_state: v.legal_state, legal_state_label: v.legal_state_label,
      verdict: v.decision?.verdict, verdict_label: v.decision?.verdict_label, open_count: v.open_count, assurance: v.integrity.assurance,
      assurance_label: v.integrity.label, stage: v.shot.stage }));
    const flat = Object.fromEntries(Object.entries(state.rules.rules).flatMap(([id, r]) => r.evidence.map((ev, i) => [`${id}:${i}`, { text: ev.text, title: r.title }])));
    const row = (a) => ({ ...a, text: flat[a.tag]?.text || a.tag, rule_title: flat[a.tag]?.title || "", by: a.by?.name || a.by || "", signature: a.demo ? "demo" : a.signature || "sealed" });
    answers = state.demoAnswers.filter((a) => a.production === key).map(row);
    studioAnswers = state.demoAnswers.filter((a) => a.production === "*studio*").map(row);
    const sealed = new Map();
    for (const p of state.packages.values()) {
      const v = P.buildView(p, state.rules);
      if (v.shot.production !== key) continue;
      for (const a of v.inherited) sealed.set(a.tag, { ...a, signature: "sealed" });
    }
    for (const a of sealed.values()) (a.scope === "studio" ? studioAnswers : answers).push(a);
  }
  const counts = { awaiting: 0, cleared: 0, "sent-back": 0 };
  for (const s of shots) counts[s.legal_state]++;
  const answersTable = (rows) => rows.length ? table(["Item", "Answer", "By", "Signature"], rows.map((a) => [
    h("div", {}, a.text, h("div", { class: "small dim", text: a.rule_title })), `“${a.note}”`,
    h("div", {}, a.by, h("div", { class: "small dim", text: date(a.at) })),
    pill({ signed: "Signature checks", unsigned: "Unsigned", altered: "Altered", sealed: "Sealed in a sign-off", demo: "Demo" }[a.signature] || a.signature,
      { signed: "ok", sealed: "lilac", demo: "warn", unsigned: "warn" }[a.signature] || "stop")])) : h("p", { class: "dim", text: "Nothing answered yet." });
  return [h("div", { class: "crumbs" }, h("a", { href: "#/", text: "Start" }), " › ", name),
    h("h1", { text: name || "No show given" }),
    h("p", { class: "lede", text: mode() === "studio" ? "Every shot of this show your studio server has seen, newest package of each, and what has been answered once for the whole show."
      : "Every shot of this show opened in this tab, and what has been answered once for the whole show." }),
    state.demo ? h("div", { class: "callout plum" }, h("span", { class: "demo-badge", text: "Demo" }), " A fictional show. Open a shot, answer an item, then clear it or send it back.") : null,
    h("div", { class: "stats", style: { marginTop: "18px" } },
      [["awaiting", "waiting for legal"], ["cleared", "cleared"], ["sent-back", "sent back"]].map(([k, l]) =>
        h("section", { class: "card" }, h("div", { class: "count", text: String(counts[k]) }), h("div", { class: "small dim", text: l })))),
    card("Shots", shots.length ? table(["Shot", "Legal", "Verdict at sign-off", "Open", "History", "Stage"], shots.map((r) => [
      h("a", { href: r.href, text: r.name || "Shot" }), pill(r.legal_state_label, LEGAL_TONE[r.legal_state]),
      pill(r.verdict_label || r.verdict || "No verdict", VERDICT_TONE[r.verdict] || "warn"), String(r.open_count),
      pill(r.assurance_label, ASSURANCE_TONE[r.assurance] || "warn"), h("span", { class: "small dim", text: r.stage })])) : h("p", { class: "dim", text: "No shots of this show yet." })),
    card("Answered for the whole show", answersTable(answers)),
    card("Answered for the whole studio", answersTable(studioAnswers))];
}

function settingsPage() {
  const url = h("input", { type: "url", id: "api", placeholder: "https://comfyaudit.your-studio.internal", value: state.api });
  const name = h("input", { type: "text", id: "reviewer", autocomplete: "name", value: state.reviewer });
  const s = state.status;
  const status = state.demo ? h("div", { class: "callout plum", text: "You are in the demo. Leave it to connect a studio server." })
    : s ? h("div", { class: "callout ok" }, h("strong", { text: "Connected. " }),
      s.signing === "per-person" ? `Signed in as ${s.you?.name || "?"}${s.you?.role ? ` (${s.you.role})` : ""}; your decisions are signed with your own key.`
        : s.signing === "studio-key" ? "Decisions are signed with the studio key, under the name you give below."
          : "This server has no signing key, so it is read-only.",
      s.you?.problem && s.signing === "per-person" ? h("div", { text: s.you.problem }) : null)
      : state.statusError ? h("div", { class: "callout stop", text: state.statusError })
        : h("div", { class: "callout plum", text: "Not connected. This page reads packages on its own; it records nothing." });
  return [h("div", { class: "crumbs" }, h("a", { href: "#/", text: "Start" }), " › Studio server"),
    h("h1", { text: "Your studio's ComfyAudit server" }),
    h("p", { class: "lede", text: "Answers and decisions are signed into each shot's history by your studio's own ComfyAudit server, on your network. This page only talks to the address you give here." }),
    card("Connection", status,
      h("label", { class: "small", for: "api", text: "Server address" }), url,
      h("label", { class: "small", for: "reviewer", text: "Your full name (used when your studio signs with one studio key; ignored when it uses your sign-in)" }), name,
      h("div", { class: "row" }, go("Save and connect", async () => {
        state.api = url.value.trim(); state.reviewer = name.value.trim().replace(/\s+/g, " ");
        store.set("comfyaudit.review.api", state.api); store.set("comfyaudit.review.reviewer", state.reviewer);
        if (state.demo) { state.demo = false; state.packages.clear(); }
        await connect(); render();
      }), state.api ? ghost("Disconnect", async () => { state.api = ""; store.set("comfyaudit.review.api", ""); await connect(); render(); }) : null)),
    card("Setting up the server", h("p", { class: "small", text: "On a machine on your studio network, with the same signing key, keyring, ledger and answers file as your ComfyUI machines:" }),
      h("pre", { class: "mono", style: { whiteSpace: "pre-wrap", background: "var(--ink)", padding: "12px", borderRadius: "9px" },
        text: `COMFYAUDIT_REVIEW_CORS_ORIGINS=${location.origin}\nCOMFYAUDIT_HISTORY_KEYRING=/secure/keyring.json\nCOMFYAUDIT_TRUSTED_USER_HEADER=X-Studio-User\nCOMFYAUDIT_ANSWERS_FILE=/shared/comfyaudit/answers.json\npython -m comfyaudit.review` }),
      h("p", { class: "small dim", text: "Serve it over HTTPS behind your sign-in proxy (browsers block an https page from calling plain http, except on localhost). The proxy must replace the sign-in header, and the server must be reachable only through it." }))];
}

// ---------------------------------------------------------------- router
async function render() {
  whereLabel();
  const view = document.getElementById("view");
  const [, route, arg] = (location.hash || "#/").split("/");
  let kids;
  try {
    if (route === "p" && arg) kids = await shotPage(arg, false);
    else if (route === "s" && arg && mode() === "studio") kids = await shotPage(arg, true);
    else if (route === "show" && arg) kids = await showPage(decodeURIComponent(arg));
    else if (route === "settings") kids = settingsPage();
    else kids = home();
  } catch (e) {
    kids = [card("That did not work", h("div", { class: "callout stop", text: e.message }), h("a", { href: "#/", text: "Back to the start" }))];
  }
  const notes = [state.flash ? h("div", { class: "flash", role: "status", text: state.flash }) : null,
    state.error ? h("div", { class: "flash err", role: "alert", text: state.error }) : null];
  // Said where the person is looking too: they may be far down the page.
  if (state.flash || state.error) toast(state.error || state.flash);
  state.flash = ""; state.error = "";
  view.replaceChildren(...notes.filter(Boolean), ...kids.flat().filter(Boolean));
}

addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
(async () => {
  try { state.rules = await (await fetch("data/rules.json")).json(); } catch { /* items show without their rule summaries */ }
  await connect();
  if (new URLSearchParams(location.search).has("demo")) return startDemo();
  render();
})();
