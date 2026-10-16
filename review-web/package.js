// Reading and checking a ComfyAudit delivery package, entirely in the browser.
//
// The zip is read here (no library: the central directory, and DecompressionStream for
// deflate), every file is checked against the SHA-256 that <shot>.package.json gives it,
// and the shot history's hash chain is recomputed. Signatures need the studio's key, so
// they are checked by the studio's own ComfyAudit server when one is connected.
//
// buildView() returns the same shape as the Python API's shot view (review/app.py), so
// one page draws a shot whether it was read here or by the studio server.

const MAX_MEMBER = 512 * 1024 * 1024;

export const EVENTS = {
  created: "Workflow created", imported: "Brought under history tracking", edited: "Workflow changed",
  rendered: "Output generated", reviewed: "Output reviewed by a person", approved: "Output approved for use",
  rejected: "Output rejected", note: "Note recorded", handoff: "Handed to another person or vendor",
  sourced: "Source asset declared", promoted: "Moved to a new production stage",
  forked: "Branched into a new record from a copy of the shot", moved: "Workflow file moved, renamed or opened elsewhere",
};
export const STATES = { cleared: "Cleared by legal", "sent-back": "Sent back", awaiting: "Waiting for legal review" };
const DOC_ROLES = ["legal-report", "licence-audit", "life-of-the-shot"];

// ---------------------------------------------------------------- zip
export function readZip(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("That file is not a zip. Open the .delivery.zip that Package for delivery made.");
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  if (p === 0xffffffff) throw new Error("This zip is larger than the browser reader handles (ZIP64).");
  const entries = new Map();
  const utf8 = new TextDecoder();
  for (let k = 0; k < count; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("The zip's directory is damaged.");
    const flags = dv.getUint16(p + 8, true), method = dv.getUint16(p + 10, true);
    const csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
    const nl = dv.getUint16(p + 28, true), xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true);
    const offset = dv.getUint32(p + 42, true);
    const name = utf8.decode(bytes.subarray(p + 46, p + 46 + nl));
    if (!name.endsWith("/")) entries.set(name, { name, flags, method, csize, usize, offset });
    p += 46 + nl + xl + cl;
  }
  return { bytes, entries };
}

export async function member(zip, name) {
  const e = zip.entries.get(name);
  if (!e) throw new Error(`${name} is not in the package.`);
  if (e.usize > MAX_MEMBER) throw new Error(`${name} is larger than the browser reads.`);
  if (e.flags & 1) throw new Error(`${name} is encrypted.`);
  const dv = new DataView(zip.bytes.buffer, zip.bytes.byteOffset, zip.bytes.byteLength);
  if (dv.getUint32(e.offset, true) !== 0x04034b50) throw new Error(`${name}: damaged entry.`);
  const start = e.offset + 30 + dv.getUint16(e.offset + 26, true) + dv.getUint16(e.offset + 28, true);
  const data = zip.bytes.subarray(start, start + e.csize);
  if (e.method === 0) return data.slice();
  if (e.method !== 8) throw new Error(`${name} uses a compression this reader does not know.`);
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function sha256(data) {
  const buf = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------- canonical JSON, as Python writes it
// Hashes in the history are taken over json.dumps(sort_keys=True, separators=(",", ":"),
// ensure_ascii=False). JSON.parse would turn 7.0 into 7 and change those bytes, so this
// parser keeps every number exactly as it was written (Python wrote it with repr, and
// repr reproduces it), and objects as Maps so key order and odd keys survive.
class Num { constructor(raw) { this.raw = raw; } }

export function parseRaw(text) {
  let i = 0;
  const ws = () => { while (i < text.length && " \t\n\r".includes(text[i])) i++; };
  const value = () => {
    ws();
    const c = text[i];
    if (c === "{") {
      i++; const m = new Map(); ws();
      if (text[i] === "}") { i++; return m; }
      for (;;) {
        ws(); const k = str(); ws();
        if (text[i++] !== ":") throw new Error("bad JSON");
        m.set(k, value()); ws();
        const n = text[i++];
        if (n === "}") return m;
        if (n !== ",") throw new Error("bad JSON");
      }
    }
    if (c === "[") {
      i++; const a = []; ws();
      if (text[i] === "]") { i++; return a; }
      for (;;) {
        a.push(value()); ws();
        const n = text[i++];
        if (n === "]") return a;
        if (n !== ",") throw new Error("bad JSON");
      }
    }
    if (c === '"') return str();
    for (const [lit, v] of [["true", true], ["false", false], ["null", null]]) {
      if (text.startsWith(lit, i)) { i += lit.length; return v; }
    }
    const m = /^-?(?:Infinity|NaN|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(i, i + 64)) ||
      (text.startsWith("NaN", i) ? ["NaN"] : null);
    if (!m) throw new Error("bad JSON");
    i += m[0].length;
    return new Num(m[0]);
  };
  const str = () => {
    if (text[i] !== '"') throw new Error("bad JSON");
    let j = i + 1;
    while (text[j] !== '"') { if (text[j] === "\\") j++; j++; }
    const s = JSON.parse(text.slice(i, j + 1));
    i = j + 1;
    return s;
  };
  const out = value();
  return out;
}

const cpCompare = (a, b) => {
  const x = [...a], y = [...b];
  for (let k = 0; k < Math.min(x.length, y.length); k++) {
    const d = x[k].codePointAt(0) - y[k].codePointAt(0);
    if (d) return d;
  }
  return x.length - y.length;
};

export function canon(v) {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (v instanceof Num) return v.raw;
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  const keys = [...v.keys()].sort(cpCompare);
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canon(v.get(k))).join(",") + "}";
}

/** Recompute the chain: each entry's hash, its link to the one before, and its snapshot. */
export async function checkChain(rawWorkflow) {
  const hist = rawWorkflow instanceof Map && rawWorkflow.get("extra") instanceof Map
    ? rawWorkflow.get("extra").get("comfyaudit_history") : null;
  if (!(hist instanceof Map)) return { ok: false, problems: ["The workflow carries no shot history."], checked: 0 };
  const entries = hist.get("entries") || [];
  const problems = [];
  let prev = "";
  for (const e of entries) {
    const seq = e.get("seq") instanceof Num ? e.get("seq").raw : "?";
    if ((e.get("prev") || "") !== prev) problems.push(`entry ${seq} does not follow the entry before it`);
    const body = new Map([...e].filter(([k]) => k !== "hash" && k !== "sig"));
    if (await sha256(canon(body)) !== e.get("hash")) problems.push(`entry ${seq} has been changed since it was written`);
    if (e.has("snapshot") && await sha256(canon(e.get("snapshot"))) !== e.get("snapshot_sha256")) {
      problems.push(`the graph recorded in entry ${seq} has been changed`);
    }
    prev = e.get("hash") || "";
  }
  return { ok: problems.length === 0 && entries.length > 0, problems, checked: entries.length,
    signed: entries.filter((e) => e.has("sig")).length, hasParent: hist.has("parent") };
}

// ---------------------------------------------------------------- opening
export async function openPackage(bytes, filename) {
  const zip = readZip(bytes);
  const indexNames = [...zip.entries.keys()].filter((n) => n.endsWith(".package.json") && !n.includes("/"));
  if (indexNames.length !== 1) {
    throw new Error("This is not a ComfyAudit delivery package: it needs exactly one <shot>.package.json " +
      "index. Packages are made by Package for delivery in ComfyUI.");
  }
  const indexName = indexNames[0];
  const index = JSON.parse(new TextDecoder().decode(await member(zip, indexName)));
  if (!String(index.schema || "").startsWith("comfyaudit/delivery-package/")) {
    throw new Error(`${indexName} is not a ComfyAudit delivery package index.`);
  }
  const files = [];
  for (const f of index.files || []) {
    const row = { path: f.path, role: f.role, media_type: f.media_type, description: f.description, bytes: f.bytes };
    if (!zip.entries.has(f.path)) row.state = "missing";
    else row.state = (await sha256(await member(zip, f.path))) === f.sha256 ? "ok" : "changed";
    files.push(row);
  }
  const listed = new Set((index.files || []).map((f) => f.path));
  const unlisted = [...zip.entries.keys()].filter((n) => !listed.has(n) && n !== indexName);
  const problems = files.filter((r) => r.state !== "ok").map((r) =>
    `${r.path} ${r.state === "missing" ? "is missing" : "has changed since it was packaged"}`)
    .concat(unlisted.map((n) => `${n} is in the package but not in its index`));
  const wfPath = (index.files || []).find((f) => f.role === "workflow")?.path;
  if (!wfPath || !zip.entries.has(wfPath)) throw new Error("The package index names no workflow, or it is missing.");
  const wfText = new TextDecoder().decode(await member(zip, wfPath));
  const workflow = JSON.parse(wfText);
  const history = workflow?.extra?.comfyaudit_history;
  if (!history?.entries?.length) throw new Error("The workflow in this package carries no shot history.");
  const chain = await checkChain(parseRaw(wfText));
  const digest = await sha256(bytes);
  return { id: digest.slice(0, 16), sha256: digest, filename, zip, index, indexName, files, unlisted,
    problems, workflow, wfPath, history, chain, openedAt: new Date().toISOString(), extra: [] };
}

// ---------------------------------------------------------------- the view
export const productionKey = (p) => String(p || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

function sealedClearance(entries) {
  for (let k = entries.length - 1; k >= 0; k--) {
    const c = entries[k].data?.clearance;
    if (c && typeof c === "object") {
      const a = entries[k].actor || {};
      return { ...c, entry: entries[k].seq, at: entries[k].at || "", by: a.name || "", role: a.role || "",
        note: entries[k].note || "" };
    }
  }
  return null;
}

export function legalReviews(entries) {
  // Legal's decision: an approved / rejected entry carrying data.legal (history.legal_decision).
  const event = { cleared: "approved", "sent back": "rejected" };
  return entries.filter((e) => e.data?.legal && event[e.data.legal.decision] === e.event).map((e) => ({
    ...e.data.legal, seq: e.seq, at: e.at || "", reason: e.note || "", by: e.actor?.name || "",
    role: e.actor?.role || "", demo: !!e.demo }));
}

/**
 * A shot view from a package opened here. ``answers`` are show and studio answers known to
 * this page (demo mode); ``pkg.extra`` are entries recorded in demo mode, never signed.
 */
export function buildView(pkg, rules, answers = []) {
  const entries = [...pkg.history.entries, ...pkg.extra];
  const top = entries[entries.length - 1].shot || pkg.history.shot || {};
  const promoted = entries.filter((e) => e.event === "promoted" && e.data?.stage);
  const stage = promoted.length ? promoted[promoted.length - 1].data.stage : (pkg.history.shot?.stage || "sandbox");
  const project = top.project || "";
  const prod = productionKey(project);
  const sealed = sealedClearance(pkg.history.entries);
  const known = {};
  for (const a of answers) {
    if (a.production !== "*studio*" && a.production !== prod) continue;
    const prev = known[a.tag];
    if (prev && prev.production !== "*studio*" && a.production === "*studio*") continue;
    known[a.tag] = a;
  }
  const attested = {};
  for (const e of entries) {
    const tag = String(e.data?.evidence || "");
    if (e.event === "note" && String(e.note || "").trim() && tag) attested[tag] = e;
  }
  const items = [];
  for (const g of sealed?.for_legal || []) {
    const rule = rules.rules[g.rule] || { evidence: [] };
    const texts = g.items || [];
    let tags = g.tags || [];
    if (tags.length !== texts.length) {
      tags = texts.map((t) => { const i = rule.evidence.findIndex((ev) => ev.text === t); return i < 0 ? "" : `${g.rule}:${i}`; });
    }
    items.push({ rule: g.rule, title: g.title, jurisdiction: g.jurisdiction, why: rule.summary || "",
      source: rule.source || "", items: texts.map((text, k) => {
        const tag = tags[k], idx = Number(String(tag).split(":")[1]);
        const scope = rule.evidence[idx]?.scope || "shot";
        const row = { text, tag, scope, state: tag ? "open" : "unknown", answer: null };
        if (attested[tag]) {
          const e = attested[tag];
          row.state = "answered";
          row.answer = { scope: "shot", by: `${e.actor?.name || ""}, ${String(e.at || "").slice(0, 10)}`, note: e.note,
            demo: !!e.demo };
        } else if (known[tag]) {
          const a = known[tag];
          row.state = "answered";
          row.answer = { scope: a.scope, note: a.note, by: a.by?.name || "", at: a.at, signature: a.demo ? "demo" : "unsigned",
            production_name: a.production_name || "" };
        }
        return row;
      }) });
  }
  const openCount = items.reduce((n, g) => n + g.items.filter((i) => i.state !== "answered").length, 0);
  const reviews = legalReviews(entries);
  const last = reviews[reviews.length - 1] || null;
  const state = last ? (last.decision === "cleared" ? "cleared" : "sent-back") : "awaiting";
  const flat = Object.fromEntries(Object.entries(rules.rules).flatMap(([id, r]) => r.evidence.map((ev, i) => [`${id}:${i}`, { text: ev.text, title: r.title }])));
  const inherited = (sealed?.answers_relied_on || []).map((a) => ({ tag: a.tag, scope: a.scope,
    production_name: a.production_name || "", note: a.note, at: a.at, by: a.by?.name || "",
    signature: "sealed", when: "sealed at sign-off" }));
  const seen = new Set(inherited.map((a) => a.tag));
  for (const g of items) for (const i of g.items) {
    if (i.answer && i.answer.scope !== "shot" && !seen.has(i.tag)) {
      inherited.push({ tag: i.tag, scope: i.answer.scope, production_name: i.answer.production_name, note: i.answer.note,
        at: i.answer.at, by: i.answer.by, signature: i.answer.signature, when: "answered since the sign-off" });
    }
  }
  for (const a of inherited) { a.text = flat[a.tag]?.text || a.tag; a.rule_title = flat[a.tag]?.title || ""; }
  const atPackaging = pkg.history.entries.length;
  const since = entries.slice(atPackaging).map((e) => ({ seq: e.seq, at: e.at, event: e.event,
    label: EVENTS[e.event] || e.event, who: e.actor?.name || "", role: e.actor?.role || "", note: e.note || "", demo: !!e.demo }));
  const chainOk = pkg.chain.ok;
  const assurance = !chainOk ? "none" : "consistency";
  return {
    source: "browser",
    package: { id: pkg.id, filename: pkg.filename, sha256: pkg.sha256, opened_at: pkg.openedAt,
      made_with: pkg.index.comfyaudit_version || "", made_at: pkg.index.generated_at || "", index: pkg.indexName },
    shot: { id: top.id || "", name: top.name || "", project, stage, external_ids: top.external_ids || {},
      production: prod, shotgrid: String(top.external_ids?.shotgrid || "") },
    legal_state: state, legal_state_label: STATES[state],
    decision: sealed ? { verdict: sealed.verdict || "", verdict_label: sealed.verdict_label || "", headline: sealed.headline || "",
      checked_as: sealed.profile || "", signed_off_by: sealed.by, role: sealed.role, signed_off_at: sealed.at, note: sealed.note,
      exception: sealed.exception || "", blockers: sealed.blockers || [], conditions: sealed.conditions || [],
      outstanding: sealed.outstanding || [], index_agrees: (pkg.index.decision || {}).verdict === sealed.verdict } : null,
    integrity: {
      assurance,
      label: chainOk ? "Chain intact" : "Altered",
      detail: chainOk
        ? `Every one of the ${pkg.chain.checked} entries was re-hashed in this browser and links to the one before. ` +
          `${pkg.chain.signed} are signed; signatures need the studio's key, so your studio's ComfyAudit server checks them.`
        : "The shot history does not match what was recorded. Do not rely on it; ask for the package again.",
      problems: pkg.chain.problems, notes: pkg.chain.hasParent ? ["This shot is a copy of an earlier record; the earlier record it carries was not re-checked here."] : [],
      package_files: pkg.problems.length === 0, package_problems: pkg.problems,
      head_at_packaging: pkg.history.entries[atPackaging - 1]?.hash || "", entries: entries.length,
    },
    open_items: items, open_count: openCount, inherited, reviews, last_review: last,
    since_packaging: since,
    documents: pkg.files.filter((f) => DOC_ROLES.includes(f.role)),
    other_files: pkg.files.filter((f) => !DOC_ROLES.includes(f.role)),
    content_credentials: pkg.index.content_credentials || [],
    writable: chainOk && pkg.problems.length === 0,
    why_not_writable: chainOk && pkg.problems.length === 0 ? "" :
      "The package or its history does not check out, so nothing can be recorded against it: " +
      [...pkg.problems, ...pkg.chain.problems].slice(0, 3).join("; "),
  };
}
