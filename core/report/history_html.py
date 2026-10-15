"""Render a shot history as a provenance record a legal team can read.

The audit report answers *what is in this workflow*. This one answers *how did
this shot come to be*: who made it, who changed it and when, which models came
and went, who wrote each prompt, where the source media came from, where a
person - not a model - made the decisions, and whether the record itself can be
trusted.

It leads with the record's own reliability, because a provenance document is
only worth what its weakest link is worth, and then draws the life of the shot
as a graph before any table appears.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .. import __version__
from .. import history as H
from . import compliance as compliance_mod
from .html import CSS, FONTS, _e

#: Glyph tone per event. Human events are filled; the machine step is hollow.
EVENT_TONE = {"created": "accent", "imported": "flat", "edited": "accent",
              "rendered": "machine", "reviewed": "accent", "approved": "ok",
              "rejected": "stop", "note": "accent", "handoff": "accent",
              "sourced": "accent", "promoted": "accent"}

STAGE_TONE = {"sandbox": "flat", "production": "accent", "cleared": "ok",
              "delivered": "ok"}

ASSURANCE = {
    "witnessed": ("ok", "Witnessed"),
    "signed": ("ok", "Signed"),
    "consistency": ("warn", "Unverified"),
    "none": ("stop", "Altered"),
}

COMMERCIAL_TONE = {"yes": "ok", "conditional": "warn", "no": "stop", "unknown": "warn"}

HISTORY_CSS = """
.assure{display:flex;gap:18px;align-items:flex-start;margin:26px 0 8px;
  padding:18px 22px;border:1px solid var(--rule);border-left-width:5px;border-radius:3px;
  background:var(--surface)}
.assure.ok{border-left-color:var(--ok)} .assure.warn{border-left-color:var(--warn)}
.assure.stop{border-left-color:var(--stop)}
.assure .badge{font-family:var(--display);font-weight:900;font-size:22px;
  letter-spacing:-.01em;white-space:nowrap}
.assure.ok .badge{color:var(--ok)} .assure.warn .badge{color:var(--warn)}
.assure.stop .badge{color:var(--stop)}
.assure p{margin:0;font-size:15px;max-width:72ch}
.assure ul{margin:8px 0 0;padding-left:18px;font-size:14px;color:var(--stop)}
.hil{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.hil div{background:var(--surface);border:1px solid var(--rule);border-radius:3px;padding:12px 14px}
.hil b{display:block;font-family:var(--display);font-size:26px;font-weight:900;line-height:1}
.hil span{font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.graphwrap{overflow-x:auto;margin:14px 0 6px;border:1px solid var(--rule);border-radius:3px;
  background:var(--surface);-webkit-overflow-scrolling:touch}
.graphwrap svg{display:block}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-family:var(--mono);font-size:11.5px;
  color:var(--ink-2);margin:8px 0 22px}
.legend i{display:inline-block;width:11px;height:11px;margin-right:6px;vertical-align:-1px;
  border-radius:50%;background:var(--accent)}
.legend i.machine{border-radius:2px;background:transparent;border:2px solid var(--ink-3)}
.legend i.ok{background:var(--ok)} .legend i.stop{background:var(--stop)}
.legend i.flag{border-radius:0;width:3px;background:var(--warn)}
.gaps{margin:10px 0 20px;padding:0;list-style:none}
.gaps li{padding:10px 14px;margin:6px 0;background:var(--warn-bg);border-radius:3px;
  font-size:14.5px;color:var(--ink)}
.gaps li::before{content:"Gap  ";font-family:var(--mono);font-size:11px;letter-spacing:.1em;
  color:var(--warn);font-weight:600}
table.log{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0 24px}
table.log th{text-align:left;font-family:var(--mono);font-weight:500;font-size:11px;
  letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);
  border-bottom:1px solid var(--rule-2);padding:8px 10px 6px}
table.log td{border-bottom:1px solid var(--rule);padding:8px 10px;vertical-align:top}
table.log td.mono,table.log code{font-family:var(--mono);font-size:12px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-family:var(--mono);
  font-size:11px;background:var(--sunk);color:var(--ink-2);white-space:nowrap}
.pill.ok{background:var(--ok-bg);color:var(--ok)} .pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.stop{background:var(--stop-bg);color:var(--stop)} .pill.accent{background:var(--accent-soft);color:var(--accent)}
.prompt{font-size:14px;max-width:62ch;overflow-wrap:anywhere}
.prompt.hidden{font-family:var(--mono);font-size:12px;color:var(--ink-3)}
.rule-card{border:1px solid var(--rule);border-radius:3px;padding:14px 18px;margin:10px 0;
  background:var(--surface)}
.rule-card h4{margin:0 0 4px;font-family:var(--display);font-size:17px;font-weight:700;
  color:var(--ink);text-transform:none;letter-spacing:-.005em}
.rule-card .meta{font-family:var(--mono);font-size:11px;color:var(--ink-3);letter-spacing:.04em}
.rule-card ul{margin:6px 0 0;padding-left:0;list-style:none;font-size:14px}
.rule-card ul li{padding-left:20px;text-indent:-20px;margin:3px 0}
.rule-card ul.asks{padding-left:18px;list-style:disc}
.rule-card code.tag{font-family:var(--mono);font-size:10.5px;color:var(--ink-3);
  background:var(--sunk);padding:1px 5px;border-radius:3px;white-space:nowrap}
.rule-card ul.asks li{padding-left:0;text-indent:0}
.rule-card li.have{color:var(--ok)} .rule-card li.need{color:var(--stop)}
.rule-card li.na{color:var(--ink-3)}
.rule-card ul.asks li{color:var(--ink);list-style:disc}
h2.sec{font-family:var(--display);font-weight:900;font-size:24px;letter-spacing:-.01em;
  margin:44px 0 6px;padding-top:18px;border-top:1px solid var(--rule-2)}
h2.sec small{font-family:var(--mono);font-size:12px;font-weight:500;color:var(--ink-3);
  margin-left:10px;letter-spacing:.04em}
.intro{color:var(--ink-2);max-width:74ch;margin:0 0 10px;font-size:15px}
@media print{.graphwrap{overflow:visible} .graphwrap svg{width:100%;height:auto}}
"""


def render(history: dict[str, Any], *, data: dict[str, Any] | None = None,
           keys: H.Keys | None = None, ledger: str = "", reveal: bool = False,
           context: dict[str, Any] | None = None, standalone: bool = True) -> str:
    """A whole provenance record.

    ``data`` is the workflow the history came from; without it the record can
    still be read, but it cannot say whether the workflow moved on since.
    ``context`` carries the audit facts the compliance checklist reasons from
    (node types, territory) when a caller has them.
    """
    keys = keys or H.Keys.from_environment()
    v = H.verify(data or {}, keys=keys, ledger=ledger, history=history)
    if data is None:
        v.current = None
        v.problems = [p for p in v.problems if "changed since the last entry" not in p]
    lin = H.lineage(history)
    rows = H.timeline(history, keys=keys, reveal=reveal)
    checks = compliance_mod.assess(lin, history, context or {})
    body = _body(history, lin, rows, v, checks)
    style = f"<style>{CSS}{HISTORY_CSS}</style>"
    if not standalone:
        return f"<link rel='stylesheet' href='{FONTS}'>\n{style}\n{body}"
    title = _e(lin["shot"].get("name") or "Shot")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Provenance record - {title}</title>"
            "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
            f"<link rel='stylesheet' href='{FONTS}'>{style}</head><body>{body}</body></html>")


def _body(history, lin, rows, v, checks) -> str:
    shot = lin["shot"]
    out: list[str] = []
    w = out.append
    w("<div class='sheet'>")

    # -- masthead ------------------------------------------------------------
    w("<header class='masthead'><div class='title-block'>")
    w("<div class='eyebrow'>Shot provenance record</div>")
    w(f"<h1>{_e(shot.get('name') or 'Untitled shot')}</h1><dl class='slate'>")
    if shot.get("project"):
        w(f"<dt>Project</dt><dd>{_e(shot['project'])}</dd>")
    ext = shot.get("external_ids") or {}
    w("<dt>Studio IDs</dt><dd>" + (", ".join(f"{_e(k)} {_e(val)}" for k, val in ext.items())
                                  or "<span style='color:var(--warn)'>not linked</span>")
      + "</dd>")
    w(f"<dt>Record</dt><dd><code>{_e(shot.get('id', ''))}</code></dd>")
    span = ""
    if rows:
        span = f"{rows[0]['at'][:10]} to {rows[-1]['at'][:10]}"
    w(f"<dt>Life</dt><dd>{lin['entries']} entries &middot; {len(lin['people'])} "
      f"people &middot; {span}</dd>")
    w(f"<dt>Stage</dt><dd><span class='pill {STAGE_TONE.get(shot.get('stage'), 'flat')}'>"
      f"{_e(shot.get('stage', 'sandbox'))}</span></dd>")
    w(f"<dt>Prepared</dt><dd>{_dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}"
      f" &middot; comfyaudit {__version__}</dd>")
    w("</dl></div></header>")

    # -- can this record be relied on? --------------------------------------
    tone, label = ASSURANCE[v.assurance]
    w(f"<div class='assure {tone}'><div class='badge'>{label}</div><div>")
    w(f"<p>{_e(v.assurance_detail)}</p>")
    if v.current is False:
        w("<p style='margin-top:8px;color:var(--warn)'>The workflow has been changed "
          "since its last entry; those edits are not part of this record.</p>")
    if v.problems:
        w("<ul>" + "".join(f"<li>{_e(p)}</li>" for p in v.problems) + "</ul>")
    w("</div></div>")

    # -- human in the loop ----------------------------------------------------
    bp = lin["breakpoints"]
    w("<h2 class='sec'>Human in the loop</h2>")
    w("<p class='intro'>Every place a person, rather than a model, made a decision. "
      "Copyright offices and studio legal teams look for this pattern: a person "
      "originating the idea, choosing the inputs, directing changes and selecting "
      "the result, with the model executing in between.</p>")
    w("<div class='hil'>")
    w(f"<div><b>{lin['human_steps']}</b><span>human decisions</span></div>")
    w(f"<div><b>{lin['renders']}</b><span>model runs</span></div>")
    w(f"<div><b>{bp.get('prompt', 0)}</b><span>prompt revisions</span></div>")
    w(f"<div><b>{bp.get('review', 0) + bp.get('selection', 0)}</b><span>reviews &amp; selections</span></div>")
    w(f"<div><b>{len(lin['people'])}</b><span>people</span></div>")
    w("</div>")
    if lin["gaps"]:
        w("<ul class='gaps'>" + "".join(f"<li>{_e(g)}</li>" for g in lin["gaps"]) + "</ul>")

    # -- the graph -------------------------------------------------------------
    w("<h2 class='sec'>Life of the shot</h2>")
    w("<p class='intro'>One lane per person, in the order they first touched the shot. "
      "Each mark is one entry in the record; the line between them is the chain, "
      "so a line crossing lanes is a handoff. Hover any mark for detail.</p>")
    w("<div class='graphwrap'>" + flow_svg(lin, rows) + "</div>")
    w("<div class='legend'><span><i></i>person acted</span>"
      "<span><i class='machine'></i>model generated output</span>"
      "<span><i class='ok'></i>approved</span><span><i class='stop'></i>rejected</span>"
      "<span><i class='flag'></i>human break point</span>"
      "<span>+ model added &nbsp; &minus; model removed &nbsp; P prompt &nbsp; S source</span></div>")

    # -- model lifeline -------------------------------------------------------
    w("<h2 class='sec'>Models over time<small>" + f"{len(lin['models'])} ever used</small></h2>")
    w("<p class='intro'>Every weight that was ever in the graph, from the entry that "
      "introduced it to the one that removed it. Solid bars reached the final render; "
      "the licence is as the bundled knowledge base read it at each entry.</p>")
    if lin["models"]:
        w("<div class='graphwrap'>" + lifeline_svg(lin, rows) + "</div>")
        w("<div class='legend'><span><i class='ok'></i>commercial use permitted</span>"
          "<span><i style='background:var(--warn)'></i>conditional or not stated</span>"
          "<span><i class='stop'></i>non-commercial</span>"
          "<span><i style='opacity:.3'></i>pale: removed or bypassed before the final render</span></div>")

    # -- prompts --------------------------------------------------------------
    w("<h2 class='sec'>Prompts and authorship<small>" + f"{len(lin['prompts'])} versions</small></h2>")
    w("<p class='intro'>Each distinct prompt text, the person who first wrote it into "
      "the graph, and whether it produced the final render. Protected prompts are "
      "shown by fingerprint: the SHA-256 proves which text was used without "
      "revealing it.</p>")
    w("<table class='log'><thead><tr><th>Written by</th><th>When</th><th>Kind</th>"
      "<th>Prompt</th><th>Runs</th><th>Final</th></tr></thead><tbody>")
    for p in lin["prompts"]:
        text = p.get("text")
        cell = (f"<div class='prompt'>{_e(text)}</div>" if text is not None else
                f"<div class='prompt hidden'>protected &middot; sha256 {_e(p['sha256'][:16])}&hellip;</div>")
        w(f"<tr><td>{_e(p['authored_by'])}</td><td class='mono'>{_e(p['authored_at'][:16].replace('T', ' '))}</td>"
          f"<td>{_e(p['polarity'])}</td><td>{cell}</td><td class='mono'>{p['renders']}</td>"
          f"<td>{'<span class=\"pill ok\">yes</span>' if p['in_final'] else ''}</td></tr>")
    w("</tbody></table>")

    # -- sources --------------------------------------------------------------
    w("<h2 class='sec'>Source material<small>" + f"{len(lin['sources'])} inputs</small></h2>")
    w("<p class='intro'>Every image, plate or clip a person supplied to the graph, and "
      "where it came from. An undeclared source is the first question legal will ask.</p>")
    if lin["sources"]:
        w("<table class='log'><thead><tr><th>Input node</th><th>Origin</th><th>Asset ID</th>"
          "<th>Owner</th><th>Declared by</th><th>Fingerprint</th></tr></thead><tbody>")
        for s in lin["sources"]:
            origin = s.get("origin") or ""
            tone = "warn" if not origin or origin in ("unknown", "generated") else "ok"
            w(f"<tr><td class='mono'>{_e(s['node'])}</td>"
              f"<td><span class='pill {tone}'>{_e(H.ORIGINS.get(origin, 'not declared') if origin else 'not declared')}</span></td>"
              f"<td class='mono'>{_e(s.get('asset_id', ''))}</td><td>{_e(s.get('owner', ''))}</td>"
              f"<td>{_e(s.get('declared_by', ''))}</td>"
              f"<td class='mono'>{_e((s.get('sha256') or '')[:12])}</td></tr>")
        w("</tbody></table>")
    else:
        w("<p class='intro'>No supplied inputs: the shot was generated from prompts alone.</p>")

    # -- stages & sign-off ----------------------------------------------------
    w("<h2 class='sec'>Stages and sign-off</h2>")
    if lin["stages"]:
        w("<table class='log'><thead><tr><th>Moved to</th><th>From</th><th>By</th>"
          "<th>When</th><th>Note</th></tr></thead><tbody>")
        for s in lin["stages"]:
            w(f"<tr><td><span class='pill {STAGE_TONE.get(s['stage'], 'flat')}'>{_e(s['stage'])}</span></td>"
              f"<td>{_e(s['from'])}</td><td>{_e(s['by'])}</td>"
              f"<td class='mono'>{_e(s['at'][:16].replace('T', ' '))}</td><td>{_e(s['note'])}</td></tr>")
        w("</tbody></table>")
    else:
        w("<p class='intro'>Still in sandbox: never promoted toward production.</p>")
    if lin["approved"]:
        a = lin["approved"]
        w(f"<p class='intro'><strong>Approved</strong> by {_e(a['by'])}"
          + (f" ({_e(a['role'])})" if a.get("role") else "")
          + f" on {_e(a['at'][:10])}" + (f": {_e(a['note'])}" if a.get("note") else "") + "</p>")

    # -- compliance -----------------------------------------------------------
    w("<h2 class='sec'>Rules this shot may need to meet<small>"
      + f"{len(checks['rules'])} apply</small></h2>")
    w(f"<p class='intro'>{_e(checks['intro'])} Items only a person can attest (a "
      "signed consent rider, a disclosure placed in the credits) are recorded as a note "
      "carrying the grey tag shown beside them, so the attestation is signed into the "
      "chain like everything else.</p>")
    if checks["rules"]:
        w("<table class='log'><thead><tr><th>Rule</th><th>Where</th><th>Status</th>"
          "<th>Evidence on record</th></tr></thead><tbody>")
        for r in checks["rules"]:
            have = sum(1 for e in r["evidence"] if e["state"] == "have")
            need = sum(1 for e in r["evidence"] if e["state"] == "need")
            tone = "ok" if not need else ("warn" if have else "stop")
            w(f"<tr><td><a href='#rule-{_e(r['id'])}'>{_e(r['title'])}</a></td>"
              f"<td class='mono'>{_e(r['jurisdiction'])}</td><td>{_e(_short_status(r['status']))}</td>"
              f"<td><span class='pill {tone}'>{have} of {have + need}</span></td></tr>")
        w("</tbody></table>")
    for r in checks["rules"]:
        w(f"<div class='rule-card' id='rule-{_e(r['id'])}'>")
        w(f"<h4>{_e(r['title'])}</h4><div class='meta'>{_e(r['jurisdiction'])} &middot; "
          f"{_e(r['authority'])} &middot; {_e(r['status'])} &middot; source dated "
          f"{_e(r.get('source_date') or 'undated - see note')} &middot; {_e(r.get('confidence', ''))} confidence</div>")
        w(f"<p style='margin:8px 0 0;font-size:14.5px'>{_e(r['why'])}</p>")
        if r.get("requires"):
            w("<div class='meta' style='margin-top:10px'>WHAT IT ASKS</div><ul class='asks'>")
            for item in r["requires"][:4]:
                w(f"<li>{_e(item)}</li>")
            if len(r["requires"]) > 4:
                w(f"<li class='na'>and {len(r['requires']) - 4} more at the source</li>")
            w("</ul>")
        w("<div class='meta' style='margin-top:10px'>WHAT THIS RECORD SHOWS</div><ul>")
        for item in r["evidence"]:
            mark = {"have": "&#10003; ", "need": "&#10007; ", "na": "&ndash; "}[item["state"]]
            tag = (f" <code class='tag'>{_e(item['tag'])}</code>"
                   if item.get("tag") and item["state"] == "need" else "")
            w(f"<li class='{item['state']}'>{mark}{_e(item['text'])}{tag}</li>")
        w("</ul>")
        if r.get("human_in_loop"):
            w(f"<p style='margin:8px 0 0;font-size:13.5px;color:var(--ink-2)'><strong>Human "
              f"involvement:</strong> {_e(r['human_in_loop'])}</p>")
        if r.get("notes"):
            w(f"<p style='margin:8px 0 0;font-size:12.5px;color:var(--ink-3)'>"
              f"<strong>Caveats:</strong> {_e(r['notes'])}</p>")
        if r.get("source"):
            w(f"<div class='meta' style='margin-top:8px'><a href='{_e(r['source'])}'>"
              f"{_e(r.get('source_title') or r['source'])}</a></div>")
        w("</div>")

    if checks.get("clarifications"):
        w("<h3 style='font-family:var(--display);font-size:17px;margin:26px 0 6px'>Often "
          "assumed, but does not apply here</h3>")
        for c in checks["clarifications"]:
            w("<div class='rule-card' style='opacity:.85'>")
            w(f"<h4>{_e(c['title'])}</h4><div class='meta'>{_e(c['jurisdiction'])} &middot; "
              f"{_e(c['authority'])} &middot; source dated {_e(c.get('source_date') or 'n/a')}</div>")
            w(f"<p style='margin:8px 0 0;font-size:14px'>{_e(c['summary'])} "
              f"{_e(c.get('human_in_loop', ''))}</p>")
            if c.get("source"):
                w(f"<div class='meta' style='margin-top:8px'><a href='{_e(c['source'])}'>"
                  f"{_e(c.get('source_title') or c['source'])}</a></div>")
            w("</div>")

    # -- the log --------------------------------------------------------------
    w("<h2 class='sec'>Complete record<small>" + f"{len(rows)} entries</small></h2>")
    w("<table class='log'><thead><tr><th>#</th><th>When</th><th>Who</th><th>What</th>"
      "<th>Changes</th><th>Hash</th></tr></thead><tbody>")
    for r in rows:
        changes = _describe_changes(r["changes"])
        if r.get("note"):
            changes = f"<em>&ldquo;{_e(r['note'])}&rdquo;</em><br>" + changes
        if r.get("outputs"):
            changes += "<br>Outputs: " + ", ".join(
                f"{_e(o.get('file', ''))}" + (f" <code>{_e(o['sha256'][:10])}</code>" if o.get("sha256") else "")
                for o in r["outputs"])
        who = _e(r["actor"].get("name", "unknown"))
        if r["actor"].get("role"):
            who += f"<br><span style='color:var(--ink-3);font-size:12px'>{_e(r['actor']['role'])}</span>"
        tone = {"ok": "ok", "stop": "stop", "machine": "flat"}.get(EVENT_TONE.get(r["event"], ""), "accent")
        w(f"<tr><td class='mono'>{r['seq']}</td><td class='mono'>{_e(r['at'][:16].replace('T', ' '))}</td>"
          f"<td>{who}</td><td><span class='pill {tone}'>{_e(r['label'])}</span></td>"
          f"<td>{changes or '<span style=\"color:var(--ink-3)\">no change to the graph</span>'}</td>"
          f"<td class='mono'>{_e(r['hash'][:10])}{' &#10003;' if r['signed'] else ''}</td></tr>")
    w("</tbody></table>")

    # -- colophon -------------------------------------------------------------
    w("<footer style='margin-top:40px;padding-top:16px;border-top:1px solid var(--rule);"
      "font-size:13px;color:var(--ink-3);max-width:80ch'>")
    w("<p>Names are recorded as each person declared them. A signature shows an entry "
      "was written by someone holding the studio key, not which individual that was; "
      "per-person keys narrow it. Licences are matched from filenames by the bundled "
      "knowledge base and should be confirmed at source. Regulatory entries summarise "
      "published sources as of the date shown on each. This record documents what "
      "happened; it is not legal advice and does not by itself establish authorship "
      "or clearance.</p></footer>")
    w("</div>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# the flow graph
# ---------------------------------------------------------------------------

def flow_svg(lin: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """The shot's life as a lane graph. Pure SVG with CSS variables for theme."""
    n = len(rows)
    if not n:
        return "<p style='padding:16px'>No entries yet.</p>"
    people = [p["name"] for p in lin["people"]]
    lane_of = {name: i for i, name in enumerate(people)}
    left, top, lane_h = 168, 46, 58
    col = max(26, min(96, int((972 - left - 30) / max(n, 1))))
    width = left + col * n + 30
    height = top + lane_h * len(people) + 18
    show_tags = col >= 40
    s: list[str] = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' "
                    f"width='{width}' height='{height}' role='img' "
                    f"aria-label='Shot history graph: {n} entries by {len(people)} people'>"]
    s.append("<style>.t{font:500 11px var(--mono,monospace);fill:var(--ink-3,#888)}"
             ".n{font:600 13px var(--body,sans-serif);fill:var(--ink,#111)}"
             ".r{font:400 11px var(--body,sans-serif);fill:var(--ink-3,#888)}"
             ".tag{font:600 10px var(--mono,monospace)}</style>")

    # stage band
    stage = "sandbox"
    stage_at: list[str] = []
    for r in rows:
        if r["event"] == "promoted":
            for c in lin["stages"]:
                if c["seq"] == r["seq"]:
                    stage = c["stage"]
        stage_at.append(stage)
    start = 0
    for i in range(1, n + 1):
        if i == n or stage_at[i] != stage_at[start]:
            x0 = left + col * start
            tone = STAGE_TONE.get(stage_at[start], "flat")
            fill = {"accent": "var(--accent-soft,#e2eff0)", "ok": "var(--ok-bg,#e6efe6)"}.get(
                tone, "var(--sunk,#eee)")
            s.append(f"<rect x='{x0}' y='8' width='{col * (i - start)}' height='18' fill='{fill}'/>")
            s.append(f"<text class='t' x='{x0 + 6}' y='21'>{_e(stage_at[start])}</text>")
            start = i

    # lanes
    for name, i in lane_of.items():
        y = top + lane_h * i + lane_h / 2
        if i % 2 == 0:
            s.append(f"<rect x='0' y='{top + lane_h * i}' width='{width}' height='{lane_h}' "
                     "fill='var(--paper,#f6f6f2)' opacity='.6'/>")
        role = next((p.get("role") for p in lin["people"] if p["name"] == name), "")
        s.append(f"<text class='n' x='14' y='{y + (0 if role else 4)}'>{_e(_clip(name, 20))}</text>")
        if role:
            s.append(f"<text class='r' x='14' y='{y + 15}'>{_e(_clip(role, 24))}</text>")
        s.append(f"<line x1='{left - 8}' y1='{y}' x2='{width - 10}' y2='{y}' "
                 "stroke='var(--rule,#ddd)' stroke-dasharray='2 4'/>")

    # chain
    pts = []
    for i, r in enumerate(rows):
        x = left + col * i + col / 2
        y = top + lane_h * lane_of.get(r["actor"].get("name") or "unknown", 0) + lane_h / 2
        pts.append((x, y))
    s.append("<polyline fill='none' stroke='var(--ink-3,#999)' stroke-width='1.4' points='"
             + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "'/>")

    # entries
    for i, (r, (x, y)) in enumerate(zip(rows, pts)):
        tone = EVENT_TONE.get(r["event"], "accent")
        tip = [f"#{r['seq']} {r['label']}", f"{r['actor'].get('name', '')} · {r['at'][:16].replace('T', ' ')}"]
        if r.get("note"):
            tip.append(f"“{r['note']}”")
        tip.extend(_describe_changes(r["changes"], plain=True).split("\n"))
        title = "<title>" + _e("\n".join(t for t in tip if t)) + "</title>"
        if r["breakpoints"] and r["event"] != "rendered":
            s.append(f"<line x1='{x}' y1='{y + 11}' x2='{x}' y2='{y + 22}' stroke='var(--warn,#a60)' stroke-width='3'/>")
        if tone == "machine":
            s.append(f"<g>{title}<rect x='{x - 8}' y='{y - 8}' width='16' height='16' rx='2' "
                     "fill='var(--surface,#fff)' stroke='var(--ink-2,#555)' stroke-width='2'/></g>")
        else:
            fill = {"ok": "var(--ok,#2f6b3f)", "stop": "var(--stop,#a32e22)",
                    "flat": "var(--ink-3,#888)"}.get(tone, "var(--accent,#0e6e78)")
            radius = 9 if r["event"] in ("approved", "rejected", "created") else 7
            s.append(f"<g>{title}<circle cx='{x}' cy='{y}' r='{radius}' fill='{fill}' "
                     "stroke='var(--surface,#fff)' stroke-width='2'/></g>")
            if r["event"] == "approved":
                s.append(f"<path d='M{x - 4} {y} l3 3 l5 -6' fill='none' stroke='var(--surface,#fff)' stroke-width='2'/>")
        if show_tags:
            tags = _tags(r["changes"], r["event"])
            for k, (txt, color) in enumerate(tags[:3]):
                s.append(f"<text class='tag' x='{x}' y='{y - 14 - 11 * k}' text-anchor='middle' "
                         f"fill='{color}'>{_e(txt)}</text>")
    s.append("</svg>")
    return "".join(s)


def lifeline_svg(lin: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    n = max(len(rows), 1)
    models = lin["models"]
    left, row_h, top = 330, 36, 8
    col = max(26, min(96, int((972 - left - 30) / n)))
    width = left + col * n + 30
    height = top + row_h * len(models) + 26
    s = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='{width}' "
         f"height='{height}' role='img' aria-label='Model lifeline: {len(models)} models'>"
         "<style>.f{font:500 11.5px var(--mono,monospace);fill:var(--ink,#111)}"
         ".l{font:400 10.5px var(--body,sans-serif)}"
         ".t{font:500 10px var(--mono,monospace);fill:var(--ink-3,#888)}</style>"]
    for i, m in enumerate(models):
        y = top + row_h * i
        tone = COMMERCIAL_TONE.get(m["commercial_use"], "warn")
        color = {"ok": "var(--ok,#2f6b3f)", "warn": "var(--warn,#8a5a12)",
                 "stop": "var(--stop,#a32e22)"}[tone]
        start = m["introduced"] - 1
        end = (m["removed"] - 1) if m["removed"] else n
        x0, x1 = left + col * start + 4, left + col * end - 4
        tip = (f"{m['filename']}\n{m['licence']}\ncommercial use: {m['commercial_use']}\n"
               + ("bypassed - did not contribute to the final render\n"
                  if not m.get("enabled", True) and not m["removed"] else "")
               + ""
               f"added #{m['introduced']} by {m['introduced_by']}"
               + (f"\nremoved #{m['removed']} by {m['removed_by']}" if m["removed"] else ""))
        s.append(f"<g><title>{_e(tip)}</title>")
        s.append(f"<text class='f' x='10' y='{y + 14}'>{_e(_clip(m['filename'], 40))}</text>")
        s.append(f"<text class='l' x='10' y='{y + 28}' fill='{color}'>"
                 f"{_e(_clip(m['licence'], 52))}</text>")
        s.append(f"<rect x='{x0}' y='{y + 10}' width='{max(x1 - x0, 6)}' height='12' rx='6' "
                 f"fill='{color}' fill-opacity='{0.9 if m['in_final'] else 0.25}' stroke='{color}'/></g>")
    y = top + row_h * len(models) + 14
    for i in range(n):
        if n <= 30 or i % max(1, n // 15) == 0:
            s.append(f"<text class='t' x='{left + col * i + col / 2}' y='{y}' text-anchor='middle'>{i + 1}</text>")
    s.append("</svg>")
    return "".join(s)


def _tags(changes: dict[str, Any], event: str) -> list[tuple[str, str]]:
    tags = []
    if changes.get("initial"):
        return [("new", "var(--accent,#0e6e78)")]
    if changes.get("models_added"):
        tags.append((f"+{len(changes['models_added'])}M", "var(--ok,#2f6b3f)"))
    if changes.get("models_removed"):
        tags.append((f"−{len(changes['models_removed'])}M", "var(--stop,#a32e22)"))
    if changes.get("models_disabled"):
        tags.append((f"off {len(changes['models_disabled'])}M", "var(--ink-3,#888)"))
    if changes.get("prompts_changed") or changes.get("prompts_added"):
        tags.append(("P", "var(--accent,#0e6e78)"))
    if event == "sourced" or changes.get("inputs_changed"):
        tags.append(("S", "var(--warn,#8a5a12)"))
    return tags


_CHANGE_LABELS = [
    ("models_added", "Models added"), ("models_removed", "Models removed"),
    ("model_strength_changed", "Model strength"), ("models_disabled", "Models bypassed"),
    ("models_enabled", "Models re-enabled"), ("licence_changed", "Licence reading changed"),
    ("prompts_changed", "Prompt revised"), ("prompts_added", "Prompt added"),
    ("prompts_removed", "Prompt removed"), ("inputs_changed", "Source media changed"),
    ("packs_added", "Node packs added"), ("packs_removed", "Node packs removed"),
    ("pack_versions_changed", "Pack versions"), ("custom_nodes_added", "Custom nodes added"),
    ("core_nodes_added", "Core nodes added"), ("core_nodes_removed", "Core nodes removed"),
    ("api_nodes_added", "Hosted-model nodes added"), ("api_nodes_removed", "Hosted-model nodes removed"),
    ("seeds_changed", "Seed changed"), ("params_changed", "Settings"),
    ("branches_toggled", "Branches toggled"), ("node_count", "Node count"),
]


def _describe_changes(changes: dict[str, Any], plain: bool = False) -> str:
    if changes.get("initial"):
        text = f"Initial state: {len(changes.get('models_added', []))} models, " \
               f"{len(changes.get('prompts_added', []))} prompts"
        return text if plain else _e(text)
    lines = []
    for key, label in _CHANGE_LABELS:
        val = changes.get(key)
        if not val:
            continue
        items = val if isinstance(val, list) else [val]
        shown = ", ".join(str(x) for x in items[:4]) + (f" +{len(items) - 4} more" if len(items) > 4 else "")
        lines.append(f"{label}: {shown}")
    if plain:
        return "\n".join(lines)
    return "<br>".join(_e(line) for line in lines)


def _short_status(status: str) -> str:
    """The leading clause of a status: 'in force', 'proposed', 'in force from 2026-07-01'."""
    head = status.split("(")[0].split(";")[0].strip(" ,")
    return head or status


def _clip(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"
