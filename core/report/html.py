"""Render an audit as a self-contained HTML report.

The page is one file with no external assets beyond a webfont link, so it can be
emailed, archived next to the show, or opened on a machine with no network.  It
is laid out as a document rather than a dashboard: what the workflow is made of
comes first, and everything after it is the evidence for that.

``render(report)`` produces a whole document.  ``render(report,
standalone=False)`` produces just the style block and body content, for hosts
that supply their own document skeleton.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
from typing import Any, Iterable

from .. import __version__
from ..audit import AuditReport
from . import narrative as narrative_section
from . import review as review_section
from ..score import clearance, licensing

FONTS = ("https://fonts.googleapis.com/css2?"
         "family=Chivo:wght@600;700;900&"
         "family=IBM+Plex+Mono:wght@400;500;600&"
         "family=Source+Sans+3:wght@400;600&display=swap")

#: Tone per licence position. These colour the *terms*, not a judgement: a
#: non-commercial licence is a fact about the model, not a fault in the reader.
POSITION_TONE = {"permissive": "ok", "conditional": "warn",
                 "non-commercial": "stop", "unstated": "flat"}

SEVERITY_TONE = {"critical": "stop", "high": "stop", "medium": "warn",
                 "low": "ok", "info": "flat"}

COMMERCIAL_TONE = {"yes": "ok", "conditional": "warn", "no": "stop", "unknown": "warn"}

CSS = """
:root{
  --paper:#f3f3ef; --surface:#ffffff; --sunk:#ebebe5;
  --ink:#15191c; --ink-2:#586066; --ink-3:#868d90;
  --rule:#dcddd6; --rule-2:#c7c9c0;
  --accent:#0e6e78; --accent-soft:#e2eff0;
  --ok:#2f6b3f; --ok-bg:#e6efe6;
  --warn:#8a5a12; --warn-bg:#f6ebd6;
  --stop:#a32e22; --stop-bg:#f6e3e0;
  --stamp-shadow:rgba(21,25,28,.10);
  --display:"Chivo","Helvetica Neue",Helvetica,Arial,sans-serif;
  --body:"Source Sans 3","Segoe UI",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#101416; --surface:#171c1f; --sunk:#0b0e10;
    --ink:#e6e8e4; --ink-2:#9aa3a6; --ink-3:#767e81;
    --rule:#282f33; --rule-2:#394247;
    --accent:#4fb3bf; --accent-soft:#12292c;
    --ok:#74c48c; --ok-bg:#152219;
    --warn:#e0ac5b; --warn-bg:#28200f;
    --stop:#e88a7d; --stop-bg:#2a1815;
    --stamp-shadow:rgba(0,0,0,.45);
  }
}
:root[data-theme="dark"]{
  --paper:#101416; --surface:#171c1f; --sunk:#0b0e10;
  --ink:#e6e8e4; --ink-2:#9aa3a6; --ink-3:#767e81;
  --rule:#282f33; --rule-2:#394247;
  --accent:#4fb3bf; --accent-soft:#12292c;
  --ok:#74c48c; --ok-bg:#152219;
  --warn:#e0ac5b; --warn-bg:#28200f;
  --stop:#e88a7d; --stop-bg:#2a1815;
  --stamp-shadow:rgba(0,0,0,.45);
}

*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:var(--body); font-size:16px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.sheet{max-width:1020px;margin:0 auto;padding:0 24px 96px}

/* -- masthead ---------------------------------------------------------- */
.masthead{
  display:flex; gap:28px; align-items:flex-start; justify-content:space-between;
  flex-wrap:wrap; padding:40px 0 24px; border-bottom:2px solid var(--ink);
}
.title-block{flex:1 1 320px;min-width:0}
.eyebrow{
  font-family:var(--mono); font-size:11px; font-weight:500;
  letter-spacing:.16em; text-transform:uppercase; color:var(--accent);
}
h1{
  font-family:var(--display); font-weight:900; font-size:clamp(30px,5vw,44px);
  line-height:1.04; letter-spacing:-.02em; margin:10px 0 0; text-wrap:balance;
  overflow-wrap:anywhere;
}
.slate{
  margin-top:16px; display:grid; gap:2px 20px;
  grid-template-columns:auto 1fr; font-family:var(--mono); font-size:12.5px;
}
.slate dt{color:var(--ink-3);letter-spacing:.06em;text-transform:uppercase;font-size:11px;padding-top:2px}
.slate dd{margin:0;color:var(--ink-2);overflow-wrap:anywhere}

/* -- plain-language summary --------------------------------------------- */
.plainly{
  margin:26px 0 30px; padding:22px 26px; border:1px solid var(--rule);
  border-radius:3px; background:var(--paper-2,transparent);
}
.plainly h2{
  font:600 11px/1 ui-monospace,monospace; letter-spacing:.16em;
  text-transform:uppercase; color:var(--ink-3); margin:0 0 14px;
  border:0; padding:0;
}
.plainly p{ margin:0 0 12px; font-size:15px; line-height:1.62; max-width:74ch; }
.plainly p:last-child{ margin-bottom:0; }

/* -- determination ------------------------------------------------------ */
/* -- outstanding work --------------------------------------------------- */
/* Deliberately quieter than the old verdict stamp: this is the fragment that
   gets cropped and forwarded, so it should read as a work list, not a ruling. */
.outstanding{
  border:1px solid var(--rule); border-left:3px solid currentColor;
  border-radius:3px; padding:14px 18px; min-width:210px; align-self:flex-start;
}
.outstanding .head{
  font:600 10px/1 ui-monospace,monospace; letter-spacing:.16em;
  text-transform:uppercase; color:var(--ink-3);
}
.outstanding .lead{
  font:600 15px/1.2 inherit; margin:7px 0 10px; color:currentColor;
}
.outstanding .row{
  display:flex; gap:9px; align-items:baseline; font-size:12.5px;
  color:var(--ink-2); padding:1px 0;
}
.outstanding .row .n{
  font:600 12px ui-monospace,monospace; min-width:1.4em; text-align:right;
  color:var(--ink-1);
}
.outstanding .who{
  font-size:11px; line-height:1.5; margin-top:11px; padding-top:9px;
  border-top:1px solid var(--rule); color:var(--ink-3);
}
.outstanding.ok{ color:var(--ok); }
.outstanding.warn{ color:var(--warn); }
.outstanding.stop{ color:var(--warn); }
.outstanding.flat{ color:var(--ink-3); }

.det{ border-left:3px solid var(--rule); padding:2px 0 2px 16px; margin:18px 0; }
.det.stop{ border-left-color:var(--stop); }
.det.warn{ border-left-color:var(--warn); }
.det.ok{ border-left-color:var(--ok); }
.det .subj{ font:600 13px/1.4 ui-monospace,monospace; margin-bottom:6px; }
.det p{ margin:6px 0; }
.det .lift{ font-size:12.5px; color:var(--ink-2); }
.det .lift b{ font-weight:600; color:var(--ink-1); }
.det .later{ color:var(--ink-2); }

/* -- licence composition ------------------------------------------------- */
.composition{
  flex:0 0 auto; min-width:260px; border:1px solid var(--rule);
  background:var(--surface); box-shadow:0 6px 18px -14px var(--stamp-shadow);
}
.composition .head{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.13em;
  text-transform:uppercase; color:var(--ink-3);
  padding:10px 14px 8px; border-bottom:1px solid var(--rule);
}
.composition .row{
  display:flex; align-items:baseline; gap:10px; padding:7px 14px;
  border-bottom:1px solid var(--rule);
}
.composition .row:last-child{border-bottom:none}
.composition .n{
  font-family:var(--display); font-weight:700; font-size:19px;
  font-variant-numeric:tabular-nums; min-width:1.6em; text-align:right;
}
.composition .lbl{font-size:13.5px}
.composition .row.ok .n{color:var(--ok)}
.composition .row.warn .n{color:var(--warn)}
.composition .row.stop .n{color:var(--stop)}
.composition .row.flat .n{color:var(--ink-3)}
.composition .none{padding:12px 14px;color:var(--ink-3);font-size:13.5px}

.verdict-note{
  margin:22px 0 0; font-size:17px; line-height:1.5; max-width:62ch;
  border-left:3px solid currentColor; padding-left:16px;
}
.verdict-note.stop{color:var(--stop)} .verdict-note.warn{color:var(--warn)}
.verdict-note.ok{color:var(--ok)} .verdict-note.flat{color:var(--ink-2)}
.verdict-note span{color:var(--ink)}

/* -- instrument readouts ------------------------------------------------ */
.readouts{
  display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
  gap:1px; background:var(--rule); border:1px solid var(--rule);
  margin:32px 0 8px;
}
.readout{background:var(--surface);padding:16px 18px 15px}
.readout .k{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.13em;
  text-transform:uppercase; color:var(--ink-3);
}
.readout .v{
  font-family:var(--display); font-weight:700; font-size:32px; line-height:1;
  margin-top:9px; letter-spacing:-.02em; font-variant-numeric:tabular-nums;
}
.readout .v small{font-size:15px;font-weight:600;color:var(--ink-3);letter-spacing:0}
.readout .d{font-size:13px;color:var(--ink-2);margin-top:5px;line-height:1.35}
.gauge{height:4px;background:var(--sunk);margin-top:11px;overflow:hidden}
.gauge>i{display:block;height:100%}

/* -- sections ----------------------------------------------------------- */
h2{
  font-family:var(--display); font-weight:700; font-size:13px;
  letter-spacing:.15em; text-transform:uppercase; color:var(--ink);
  margin:52px 0 0; padding-bottom:9px; border-bottom:1px solid var(--ink);
  display:flex; align-items:baseline; gap:12px;
}
h2 .num{font-family:var(--mono);font-weight:500;color:var(--accent);font-size:12px}
h2 .count{margin-left:auto;font-family:var(--mono);font-weight:400;
  font-size:11.5px;letter-spacing:.08em;color:var(--ink-3);text-transform:none}
h3{
  font-family:var(--display); font-weight:700; font-size:15px;
  letter-spacing:-.005em; margin:26px 0 8px;
}
h4{
  font-family:var(--mono); font-weight:500; font-size:11px; letter-spacing:.13em;
  text-transform:uppercase; color:var(--ink-3); margin:26px 0 8px;
}
p{margin:10px 0;max-width:70ch}
ul{margin:9px 0;padding-left:19px} li{margin:3px 0;max-width:70ch}
a{color:var(--accent);text-underline-offset:2px}
a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}

/* -- tables ------------------------------------------------------------- */
.scroll{overflow-x:auto;margin:14px 0;border:1px solid var(--rule);background:var(--surface)}
table{border-collapse:collapse;width:100%;min-width:600px;font-size:14px}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--rule);vertical-align:top}
th{
  font-family:var(--mono); font-size:10.5px; font-weight:500; letter-spacing:.11em;
  text-transform:uppercase; color:var(--ink-3); background:var(--sunk);
  white-space:nowrap; position:sticky; top:0;
}
tbody tr:last-child td{border-bottom:none}
td.num{font-variant-numeric:tabular-nums;font-family:var(--mono);font-size:13px;white-space:nowrap}
.file{font-family:var(--mono);font-size:12.5px;overflow-wrap:anywhere}
.sub{display:block;font-size:11.5px;color:var(--ink-3);margin-top:3px;font-family:var(--mono)}

/* -- chips -------------------------------------------------------------- */
.chip{
  display:inline-block; font-family:var(--mono); font-size:10.5px; font-weight:500;
  letter-spacing:.09em; text-transform:uppercase; padding:2px 7px;
  border:1px solid currentColor; white-space:nowrap;
}
.chip.ok{color:var(--ok);background:var(--ok-bg)}
.chip.warn{color:var(--warn);background:var(--warn-bg)}
.chip.stop{color:var(--stop);background:var(--stop-bg)}
.chip.flat{color:var(--ink-3);background:var(--sunk)}

/* -- blocks ------------------------------------------------------------- */
.block{
  background:var(--surface); border:1px solid var(--rule);
  border-left:3px solid var(--rule-2); padding:15px 18px; margin:13px 0;
}
.block.stop{border-left-color:var(--stop)}
.block.warn{border-left-color:var(--warn)}
.block.ok{border-left-color:var(--ok)}
.block.flat{border-left-color:var(--rule-2)}
.block > :first-child{margin-top:0} .block > :last-child{margin-bottom:0}
.block-head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:6px}
.block-head .name{font-family:var(--display);font-weight:700;font-size:15px;overflow-wrap:anywhere}
.block-head .tag{font-family:var(--mono);font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3);margin-left:auto}
pre{
  background:var(--sunk); border:1px solid var(--rule); padding:12px 14px;
  margin:9px 0; font-family:var(--mono); font-size:12.5px; line-height:1.55;
  white-space:pre-wrap; overflow-wrap:anywhere;
}
code{font-family:var(--mono);font-size:.88em;background:var(--sunk);padding:1px 5px}
.block code{background:var(--paper)}
.action{
  margin-top:11px; padding-top:10px; border-top:1px dashed var(--rule-2);
  font-size:14.5px;
}
.action b{font-family:var(--mono);font-size:10.5px;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3);display:block;margin-bottom:3px}
.evidence{margin:9px 0 0;padding:0;list-style:none;display:grid;gap:3px}
.evidence li{font-family:var(--mono);font-size:12.5px;color:var(--ink-2);
  overflow-wrap:anywhere;max-width:none}
.evidence li::before{content:"\\2014\\00a0";color:var(--ink-3)}
.footnote{font-family:var(--mono);font-size:11.5px;color:var(--ink-3);margin-top:9px}
.footnote a{color:var(--ink-3)}
.lede{font-size:15px;color:var(--ink-2);margin:12px 0 0;max-width:70ch}
.empty{color:var(--ink-3);font-style:italic}

/* -- priority list ------------------------------------------------------ */
.priority{counter-reset:p;margin:16px 0 0;padding:0;list-style:none;display:grid;gap:10px}
.priority li{
  counter-increment:p; position:relative; padding-left:40px; max-width:74ch;
  min-height:26px; display:flex; align-items:center;
}
.priority li::before{
  content:counter(p); position:absolute; left:0; top:0;
  width:26px; height:26px; display:grid; place-items:center;
  font-family:var(--mono); font-size:12px; font-weight:600;
  color:var(--paper); background:var(--ink);
}

.colophon{
  margin-top:56px; padding-top:20px; border-top:2px solid var(--ink);
  font-size:13.5px; color:var(--ink-2);
}
.colophon .disclaimer{
  margin-top:16px; padding:14px 16px; background:var(--sunk);
  border:1px solid var(--rule); font-size:13px;
}

@media (max-width:640px){
  .masthead{padding-top:28px}
  .composition{width:100%}
  h2{margin-top:40px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media print{
  body{background:#fff}
  .sheet{max-width:none;padding:0}
  .block,.readout,.scroll{break-inside:avoid}
  h2{break-after:avoid}
  th{position:static}
}
"""


# --------------------------------------------------------------------------


def render(report: AuditReport, standalone: bool = True) -> str:
    body = _body(report)
    style = f"<style>{CSS}</style>"
    if not standalone:
        return f"<link rel='stylesheet' href='{FONTS}'>\n{style}\n{body}"
    src_name = _e(report.source.get("name", "workflow"))
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Clearance sheet - {src_name}</title>"
        f"<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        f"<link rel='stylesheet' href='{FONTS}'>"
        f"{style}</head><body>{body}</body></html>"
    )


def _body(report: AuditReport) -> str:
    src = report.source
    risk, auto = report.risk, report.automation
    out: list[str] = []
    w = out.append
    section = _Sections(w)

    w("<div class='sheet'>")

    # -- masthead ----------------------------------------------------------
    w("<header class='masthead'>")
    w("<div class='title-block'>")
    w("<div class='eyebrow'>ComfyUI workflow report</div>")
    w(f"<h1>{_e(src.get('name', 'workflow'))}</h1>")
    w("<dl class='slate'>")
    w(f"<dt>Graph</dt><dd>{src.get('nodes_total', 0)} nodes"
      + (f", {src.get('nodes_disabled')} disabled" if src.get("nodes_disabled") else "")
      + (f", {src.get('subgraphs')} subgraphs" if src.get("subgraphs") else "")
      + f" &middot; {_e(src.get('format', '?'))} format</dd>")
    w(f"<dt>Contents</dt><dd>{len(report.models)} models &middot; "
      f"{len(report.prompts)} prompts &middot; "
      f"{len([p for p in report.packs if p.identified])} custom packs</dd>")
    w(f"<dt>Audited</dt><dd>{_dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
      f"&middot; comfyaudit {__version__}</dd>")
    w("</dl></div>")
    if report.clearance.determined:
        w(_stamp(report.clearance))
    else:
        w(_composition(report.licensing))
    w("</header>")

    if report.clearance.determined:
        w(f"<p class='verdict-note {_verdict_tone(report.clearance.verdict)}'>"
          f"<span><strong>{_e(clearance.VERDICT_LABELS[report.clearance.verdict])}.</strong> "
          f"{_e(report.clearance.headline)} This applies the licence terms to the "
          "profile above and nothing else; change the profile and it changes. It is "
          "a reading of published terms with the reasoning shown, not legal advice."
          "</span></p>")
        # The narrative is what makes the verdict legible when the reasoning
        # gets skimmed, which it will be.
        paragraphs = narrative_section.summarise(report)
        if paragraphs:
            w("<div class='plainly'><h2>In plain terms</h2>")
            for paragraph in paragraphs:
                w(f"<p>{_e(paragraph)}</p>")
            w("</div>")
    else:
        w(f"<p class='verdict-note flat'><span><strong>Licences.</strong> "
          f"{_e(report.licensing.headline)} This report describes what those licences "
          "say; it does not decide whether they suit your job, which depends on the "
          "client, the territory and any agreements you already hold.</span></p>")
        # The tables below assume a reader who knows how to read them. This does
        # not, and it is what most people who open the file will actually read.
        paragraphs = narrative_section.summarise(report)
        if paragraphs:
            w("<div class='plainly'><h2>In plain terms</h2>")
            for paragraph in paragraphs:
                w(f"<p>{_e(paragraph)}</p>")
            w("</div>")

    if report.freshness.state in ("old", "unknown"):
        w(f"<p class='verdict-note warn'><span><strong>Check this at source.</strong> "
          f"{_e(report.freshness.message)}</span></p>")

    # -- readouts ----------------------------------------------------------
    counts = risk.counts()
    w("<div class='readouts'>")
    w(_readout("Operational risk", f"{risk.score}<small>/100</small>", risk.band,
               _risk_tone(risk.score), risk.score))
    w(_readout("Human touchpoints", str(len(auto.per_run_touchpoints)),
               f"per run, plus {len(auto.setup_touchpoints)} one-off at setup"
               if auto.setup_touchpoints else "per run",
               _auto_tone(auto.index)))
    w(_readout("Findings", str(sum(counts.values())),
               ", ".join(f"{counts[s]} {s}" for s in
                         ("critical", "high", "medium", "low", "info") if counts.get(s))
               or "none",
               "stop" if counts.get("critical") else ("warn" if counts.get("high") else "ok")))
    w("</div>")
    w(f"<p class='lede'>{_e(risk.band_detail)} {_e(auto.band_detail)}</p>")

    top = [f for f in risk.findings if f.severity in ("critical", "high")][:3]
    if top:
        w(_h2("", "Most likely to bite first"))
        w("<ol class='priority'>")
        for finding in top:
            w(f"<li><span><strong>{_e(finding.title)}</strong> &mdash; "
              f"{_e(finding.recommendation or finding.detail)}</span></li>")
        w("</ol>")

    _registry_section(w, section, report)
    _clearance_section(w, section, report)
    _licence_section(w, section, report)
    _models_section(w, section, report)
    _prompts_section(w, section, report)
    _assets_section(w, section, report)
    _dependencies_section(w, section, report)
    _automation_section(w, section, report)
    _risk_section(w, section, report)

    review = review_section.get_review(report)
    if review:
        w(review_section.html(review))

    _colophon(w, report)

    w("</div>")
    return "\n".join(out)


# --------------------------------------------------------------------------


def _registry_section(w, section, report: AuditReport) -> None:
    """What is new since the facility last looked.

    Leads the document when a registry exists, because by the second workflow it
    is the only part anyone reads: the licences were settled in March.
    """
    reg = report.registry
    if not reg.loaded:
        return

    outstanding = len(reg.matches) - len(reg.known)
    section("New since last cleared",
            "nothing new" if reg.clean else f"{outstanding} to look at")
    w(f"<p class='lede'>{_e(reg.headline())}</p>")

    if reg.clean:
        w("<div class='block ok'><p>Nothing here requires a fresh decision. The "
          "sections below restate what those decisions were, for the record.</p>"
          "</div>")
        return

    for state, heading, lead, tone in (
        ("rejected", "Previously rejected",
         "Someone has already decided these should not be used. If that has "
         "changed, the registry entry is what to update.", "stop"),
        ("changed", "Changed since they were cleared",
         "These were signed off, but not in the state they are in now. A "
         "decision is about a specific file and a specific licence.", "warn"),
        ("pending", "Still awaiting an answer",
         "Raised before and never resolved.", "warn"),
        ("new", "Not seen before",
         "Nobody has decided about these yet. This is the list that needs "
         "someone's time.", "flat"),
    ):
        items = [m for m in reg.matches if m.state == state]
        if not items:
            continue
        w(f"<h3>{_e(heading)}</h3>")
        w(f"<p class='muted'>{_e(lead)}</p>")
        for match in items:
            w(f"<div class='det {tone}'>"
              f"<div class='subj'>{_e(match.subject)}"
              f"<span class='sub'> {_e(match.kind)}</span></div>"
              f"<p>{_e(match.detail)}</p></div>")

    if reg.known:
        w(f"<p class='muted'>{len(reg.known)} other item(s) were already cleared "
          "and are not repeated here.</p>")


def _clearance_section(w, section, report: AuditReport) -> None:
    """What the licences mean for this studio, and how that was worked out."""
    clr = report.clearance
    if not clr.determined:
        return

    section("What has to happen", clearance.VERDICT_LABELS[clr.verdict])
    w(f"<p class='lede'>{_e(clr.headline)}</p>")
    w(f"<p class='muted'>Assessed for {_e(clr.profile.describe())}"
      + (f" &middot; {_e(clr.profile.label)}" if clr.profile.label else "")
      + "</p>")

    for verdict, heading, lead in (
        ("no-go", "Has to change",
         "These need resolving before the workflow goes on a paid job. Each "
         "names what lifts it, and most are one swap or one purchase."),
        ("conditions", "Conditions to meet",
         "None of these block. Each is something to do, budget for or confirm "
         "before delivery."),
        ("unknown", "Unresolved",
         "Not enough is known about these to say either way."),
    ):
        items = clr.by_verdict(verdict)
        if not items:
            continue
        tone = _verdict_tone(verdict)
        w(f"<h3>{_e(heading)}</h3>")
        w(f"<p class='muted'>{_e(lead)}</p>")
        for reasons, subjects, also in _group_determinations(items, verdict):
            w(f"<div class='det {tone}'>")
            w(f"<div class='subj'>{_e(_subjects(subjects))}</div>")
            for reason in reasons:
                w(f"<p>{_e(reason.text)}</p>")
                if reason.remedy:
                    w(f"<p class='lift'><b>What lifts it:</b> {_e(reason.remedy)}</p>")
            for reason in also:
                w(f"<p class='later'>Once that is resolved: {_e(reason.text)}</p>")
                if reason.remedy:
                    w(f"<p class='lift'><b>What lifts it:</b> {_e(reason.remedy)}</p>")
            w("</div>")

    cleared = clr.by_verdict("go")
    if cleared:
        w("<h3>Clear</h3>")
        w("<p class='muted'>No condition in these licences is triggered by this "
          "studio's circumstances.</p>")
        w(_table(("Item", "Licence"),
                 [f"<tr><td>{_e(d.subject)}</td><td>{_e(d.licence)}</td></tr>"
                  for d in cleared]))


def _group_determinations(items, verdict):
    """Collapse determinations that turn on identical reasoning.

    Four weights that all fail the same territory clause are one thing to fix,
    and printing the clause four times buries that.
    """
    def split(det):
        primary = [r for r in det.reasons if r.verdict == verdict]
        also = [r for r in det.reasons if r.verdict not in (verdict, "go")]
        return primary, also

    buckets: dict[tuple, list[str]] = {}
    for det in items:
        primary, also = split(det)
        buckets.setdefault(tuple((r.text, r.remedy) for r in primary + also),
                           []).append(det.subject)
    out = []
    for det in items:
        primary, also = split(det)
        key = tuple((r.text, r.remedy) for r in primary + also)
        if key in buckets:
            out.append((primary, buckets.pop(key), also))
    return out


def _subjects(subjects: list[str]) -> str:
    if len(subjects) <= 3:
        return ", ".join(subjects)
    return ", ".join(subjects[:3]) + f" and {len(subjects) - 3} more"


def _stamp(clr: Any) -> str:
    """The masthead block, sized to the work rather than to the judgement.

    An earlier version put NOT USABLE AS-IS in a large red box, which is exactly
    the fragment that gets screenshotted and forwarded without the reasoning
    underneath it. Most "no" answers here are one model swap or one licence
    away, so what leads now is the count of jobs outstanding and their shape.
    """
    tone = _verdict_tone(clr.verdict)
    total = sum(clr.actions.values())
    rows = []
    for action in clearance.ACTION_ORDER:
        count = clr.actions.get(action, 0)
        if count:
            rows.append(f"<div class='row'><span class='n'>{count}</span>"
                        f"<span class='lbl'>{_e(_action_noun(action, count))}</span>"
                        "</div>")

    head = ("Outstanding" if total else "Assessed")
    lead = (f"{total} to resolve" if total else
            clearance.VERDICT_LABELS[clr.verdict])
    return ("<div class='outstanding " + tone + "'>"
            f"<div class='head'>{_e(head)}</div>"
            f"<div class='lead'>{_e(lead)}</div>"
            + "".join(rows)
            + f"<div class='who'>{_e(clr.profile.describe())}</div></div>")


def _action_noun(action: str, count: int) -> str:
    """The job's name without its article, since the count supplies the number."""
    if count == 1:
        label = clearance.ACTIONS[action]
        return label[2:] if label.startswith("a ") else label
    return clearance._plural_action(action)


def _verdict_tone(verdict: str) -> str:
    return {"no-go": "stop", "conditions": "warn", "go": "ok"}.get(verdict, "flat")


def _licence_section(w, section, report: AuditReport) -> None:
    """What the licences say, grouped, with a source for each claim."""
    lic = report.licensing
    section("Licence summary", f"{len(lic.groups)} distinct")
    if not lic.groups:
        w("<p class='empty'>No models were found, so there is nothing to report.</p>")
        return

    rows = []
    for group in lic.groups:
        tone = POSITION_TONE.get(group.position, "flat")
        models = ", ".join(group.models[:4]) + (
            f" and {len(group.models) - 4} more" if len(group.models) > 4 else "")
        rows.append(
            f"<tr><td>{_e(group.licence)}"
            f"<span class='sub'>{_e(models)}</span></td>"
            f"<td class='num'>{group.count}</td>"
            f"<td><span class='chip {tone}'>{_e(group.position)}</span></td>"
            f"<td>{_e(licensing.describe_fee(group.fee))}</td>"
            f"<td>{_e(group.confidence)}</td></tr>")
    w(_table(["Licence", "Models", "Commercial use", "Fee", "Confidence"], rows))

    for group in lic.groups:
        tone = POSITION_TONE.get(group.position, "flat")
        w(f"<div class='block {tone}'><div class='block-head'>"
          f"<span class='name'>{_e(group.licence)}</span>"
          f"<span class='chip {tone}'>{_e(group.position)}</span>"
          f"<span class='tag'>{group.count} model(s)</span></div>")
        if group.summary:
            w(f"<p>{_e(group.summary)}</p>")
        if group.restrictions:
            w("<ul>" + "".join(f"<li>{_e(r)}</li>"
                               for r in group.restrictions[:10]) + "</ul>")
        if group.url:
            w(f"<div class='footnote'><a href='{_e(group.url)}'>Licence terms</a></div>")
        w("</div>")

    if lic.obligations:
        w("<h4>Obligations that come with these licences</h4><ul>")
        for obligation in lic.obligations:
            w(f"<li>{_e(obligation)}</li>")
        w("</ul>")

    if lic.to_verify:
        w("<h4>Worth confirming at source</h4>")
        w("<p>The entries the tool is least sure about. A licence is matched from a "
          "filename, and filenames can be changed by anyone.</p><ul>")
        for item in lic.to_verify:
            w(f"<li>{_e(item)}</li>")
        w("</ul>")

    if lic.hosted_api_types:
        w("<h4>Hosted models</h4>")
        w(f"<p>{len(lic.hosted_api_types)} node type(s) call a vendor API rather than "
          "loading local weights. Their terms come from that vendor's contract, which "
          "is not visible in the workflow.</p><ul>"
          + "".join(f"<li><code>{_e(t)}</code></li>" for t in lic.hosted_api_types)
          + "</ul>")


def _models_section(w, section, report: AuditReport) -> None:
    section("Models", f"{len(report.models)} referenced")
    if not report.models:
        w("<p class='empty'>No model references found.</p>")
        return

    rows = []
    for model in report.models:
        lic = model.license
        comm = lic.commercial_use if lic else "unknown"
        name = (f"<span class='file'>{_e(model.filename)}</span>"
                + ("" if model.enabled
                   else " <span class='chip flat'>disabled</span>"))
        if model.strength is not None:
            name += f"<span class='sub'>strength {model.strength}</span>"
        licence_cell = _e(lic.name if lic else "Unknown")
        if lic and (lic.matched_on or lic.confidence):
            bits = []
            if lic.matched_on:
                bits.append(f"matched: {lic.matched_on}")
            bits.append(f"confidence {lic.confidence}")
            licence_cell += f"<span class='sub'>{_e(' &middot; '.join(bits))}</span>"
        rows.append(
            f"<tr><td>{name}</td><td>{_e(model.role)}</td><td>{licence_cell}</td>"
            f"<td><span class='chip {COMMERCIAL_TONE.get(comm, 'warn')}'>{_e(comm)}</span></td>"
            f"<td>{_e(_fee(lic.fee if lic else 'unknown'))}</td>"
            f"<td>{_source(model.provenance)}</td></tr>"
        )
    w(_table(["Model", "Role", "Licence", "Commercial", "Fee", "Source"], rows))

    detail = [m for m in report.models
              if m.license and (m.license.restrictions
                                or m.license.commercial_use in ("no", "conditional", "unknown"))]
    if not detail:
        return
    w("<h4>Licence detail</h4>")
    for model in detail:
        lic = model.license
        tone = COMMERCIAL_TONE.get(lic.commercial_use, "warn")
        w(f"<div class='block {tone}'>")
        w(f"<div class='block-head'><span class='name'>{_e(model.filename)}</span>"
          f"<span class='chip {tone}'>{_e(lic.commercial_use)}</span>"
          f"<span class='tag'>{_e(lic.name)}</span></div>")
        if lic.summary:
            w(f"<p>{_e(lic.summary)}</p>")
        if lic.restrictions:
            w("<ul>" + "".join(f"<li>{_e(r)}</li>" for r in lic.restrictions) + "</ul>")
        links = []
        if lic.url:
            links.append(f"<a href='{_e(lic.url)}'>licence terms</a>")
        if model.provenance and model.provenance.url:
            links.append(f"<a href='{_e(model.provenance.url)}'>model source</a>")
        if links:
            w(f"<div class='footnote'>{' &middot; '.join(links)}</div>")
        w("</div>")


def _prompts_section(w, section, report: AuditReport) -> None:
    section("Prompts", f"{len(report.prompts)} found")
    if not report.prompts:
        w("<p class='empty'>No prompt text found.</p>")
    for polarity in ("positive", "negative", "both", "system", "unknown"):
        group = [p for p in report.prompts if p.polarity == polarity]
        if not group:
            continue
        w(f"<h4>{_e(polarity)} &middot; {len(group)}</h4>")
        for prompt in group:
            flags = []
            if prompt.driven_by_link:
                flags.append("driven from upstream")
            if prompt.dynamic_syntax:
                flags.append("random alternation")
            if prompt.wildcards:
                flags.append(f"wildcards: {', '.join(prompt.wildcards)}")
            if not prompt.enabled:
                flags.append("node disabled")
            w("<div class='block flat'>")
            w(f"<div class='block-head'><span class='name'>{_e(prompt.node_label)}</span>"
              f"<code>{_e(prompt.widget)}</code>"
              f"<span class='tag'>~{prompt.token_estimate} tokens</span></div>")
            w(f"<pre>{_e(prompt.text.strip()[:1600])}</pre>")
            meta = []
            if flags:
                meta.append("; ".join(flags))
            if prompt.consumers:
                meta.append("consumed by " + ", ".join(prompt.consumers))
            if meta:
                w(f"<div class='footnote'>{_e(' &middot; '.join(meta))}</div>")
            w("</div>")

    if report.notes:
        w("<h4>Notes left in the graph</h4>")
        for note in report.notes:
            w(f"<div class='block flat'><div class='block-head'>"
              f"<span class='name'>{_e(note.node_label)}</span></div>"
              f"<pre>{_e(note.text.strip()[:900])}</pre></div>")


def _assets_section(w, section, report: AuditReport) -> None:
    section("Assets", f"{len(report.inputs)} in, {len(report.outputs)} out")
    if report.inputs:
        rows = []
        for asset in report.inputs:
            how = "upload widget &mdash; a person picks it" if asset.upload_widget \
                else "path in the graph"
            if asset.kind == "url":
                how = "downloaded at run time"
            if asset.absolute_path:
                how += " <span class='chip stop'>absolute</span>"
            rows.append(f"<tr><td><span class='file'>{_e(asset.value)}</span></td>"
                        f"<td>{_e(asset.kind)}</td><td>{_e(asset.node_label)}</td>"
                        f"<td>{how}</td></tr>")
        w(_table(["Asset", "Kind", "Node", "Supplied how"], rows))
    else:
        w("<p class='empty'>No external inputs &mdash; this workflow generates from scratch.</p>")

    if report.outputs:
        w("<h4>Writes to</h4><ul>")
        for out in report.outputs:
            w(f"<li><code>{_e(out.value)}</code> &mdash; {_e(out.node_label)}</li>")
        w("</ul>")


def _dependencies_section(w, section, report: AuditReport) -> None:
    installable = [p for p in report.packs if p.identified]
    section("Node dependencies",
            f"{len(report.core_node_types)} core &middot; {len(installable)} custom")

    if not report.packs:
        w("<div class='block ok'><p>Core ComfyUI nodes only &mdash; the strongest "
          "position for portability and long-term maintenance. Nothing to install "
          "beyond ComfyUI itself.</p></div>")
    else:
        rows = []
        for pack in report.packs:
            if not pack.identified:
                rows.append(f"<tr><td><span class='file'>{_e(pack.title)}</span>"
                            f"<span class='sub'>no known install source</span></td>"
                            f"<td>&mdash;</td><td class='num'>{pack.node_count}</td>"
                            f"<td><span class='chip stop'>unidentified</span></td>"
                            f"<td class='num'>&mdash;</td><td class='num'>&mdash;</td></tr>")
                continue
            ver = (f"<span class='chip ok'>{_e(pack.pinned_version)}</span>"
                   if pack.pinned_version else "<span class='chip warn'>not pinned</span>")
            licence = _pack_licence_cell(pack)
            rows.append(
                f"<tr><td><a href='{_e(pack.reference or '#')}'>{_e(pack.title)}</a>"
                f"<span class='sub'>{_e(', '.join(pack.node_types[:3]))}</span></td>"
                f"<td>{_e(pack.author or '-')}</td><td>{licence}</td>"
                f"<td class='num'>{pack.node_count}</td><td>{ver}</td>"
                f"<td class='num'>{pack.stars if pack.stars is not None else '-'}</td>"
                f"<td class='num'>{_e((pack.last_update or '-').split(' ')[0])}</td></tr>"
            )
        w(_table(["Pack", "Author", "Licence", "Nodes", "Version", "Stars",
                  "Last commit"], rows))
        notes = [(p.title, n) for p in report.packs for n in p.notes]
        if notes:
            w("<ul>" + "".join(f"<li><strong>{_e(t)}</strong>: {_e(n)}</li>"
                               for t, n in notes) + "</ul>")

    if report.api_node_types:
        w("<h4>Hosted API nodes</h4>")
        w("<p>Billed per call and executed on vendor infrastructure.</p><ul>")
        for node_type in report.api_node_types:
            w(f"<li><code>{_e(node_type)}</code></li>")
        w("</ul>")


def _automation_section(w, section, report: AuditReport) -> None:
    auto = report.automation
    section("Where a human has to be", f"{len(auto.per_run_touchpoints)} per run")
    w(f"<div class='block {_auto_tone(auto.index)}'>"
      f"<div class='block-head'><span class='name'>{_e(auto.band)}</span>"
      f"<span class='tag'>index {auto.index}/100</span></div>"
      f"<p>{_e(auto.band_detail)}</p></div>")

    per_run = auto.per_run_touchpoints
    if per_run:
        rows = [f"<tr><td class='num'>{t.cost:.1f}</td><td>{_e(t.stage)}</td>"
                f"<td>{_e(t.label)}</td><td>{_e(t.detail)}</td></tr>" for t in per_run]
        w("<h4>Human touchpoints on every run</h4>")
        w(_table(["Weight", "When", "Touchpoint", "Why"], rows))
    else:
        w("<div class='block ok'><p>No per-run human touchpoints detected. This graph "
          "can be queued and left alone.</p></div>")

    if auto.setup_touchpoints:
        w("<h4>One-off setup before it runs anywhere else</h4>")
        for tp in auto.setup_touchpoints:
            w(f"<div class='block warn'><div class='block-head'>"
              f"<span class='name'>{_e(tp.label)}</span>"
              f"<span class='tag'>weight {tp.cost:.1f}</span></div>"
              f"<p>{_e(tp.detail)}</p></div>")

    if auto.automation_signals:
        w("<h4>Already automated</h4><ul>")
        for sig in auto.automation_signals:
            w(f"<li>{_e(sig)}</li>")
        w("</ul>")


def _risk_section(w, section, report: AuditReport) -> None:
    risk = report.risk
    section("Operational risks", f"{len(risk.findings)} findings")
    if not risk.findings:
        w("<div class='block ok'><p>No risks identified.</p></div>")
        return

    if risk.by_category:
        rows = [f"<tr><td>{_e(cat)}</td><td class='num'>{value}</td></tr>"
                for cat, value in sorted(risk.by_category.items(), key=lambda kv: -kv[1])]
        w(_table(["Category", "Weighted score"], rows))

    for finding in risk.findings:
        tone = SEVERITY_TONE.get(finding.severity, "warn")
        w(f"<div class='block {tone}'>")
        w(f"<div class='block-head'><span class='chip {tone}'>{_e(finding.severity)}</span>"
          f"<span class='name'>{_e(finding.title)}</span>"
          f"<span class='tag'>{_e(finding.category)}</span></div>")
        w(f"<p>{_e(finding.detail)}</p>")
        if finding.evidence:
            w("<ul class='evidence'>"
              + "".join(f"<li>{_e(str(e))}</li>" for e in finding.evidence[:12]) + "</ul>")
            if len(finding.evidence) > 12:
                w(f"<div class='footnote'>and {len(finding.evidence) - 12} more</div>")
        if finding.recommendation:
            w(f"<div class='action'><b>What to do</b>{_e(finding.recommendation)}</div>")
        w("</div>")


def _colophon(w, report: AuditReport) -> None:
    know = report.knowledge
    lic_meta = know.get("licences", {})
    diag = report.diagnostics
    w("<div class='colophon'>")
    w("<h4 style='margin-top:0'>How this was determined</h4><ul>")
    w(f"<li>Core node schemas from ComfyUI {_e(know.get('comfyui_catalog_version', '?'))}</li>")
    w(f"<li>{know.get('node_packs_indexed', 0)} custom node packs indexed from the "
      "ComfyUI-Manager registry</li>")
    w(f"<li>Licence knowledge base v{_e(lic_meta.get('version', '?'))}, "
      f"{lic_meta.get('model_rules', 0)} model rules, verified "
      f"{_e(lic_meta.get('checked', '?'))}</li>")
    w("<li>Online lookups: "
      + ("enabled" if diag.get("online") else "disabled, offline knowledge base only")
      + "</li>")
    w("<li>Local models directory: "
      + (f"<code>{_e(diag['models_dir'])}</code>, "
         f"{diag.get('models_scanned_locally', 0)} weights indexed"
         if diag.get("models_dir")
         else "not supplied, so model presence was not verified") + "</li>")
    if diag.get("parser_warning_count"):
        w(f"<li>{diag['parser_warning_count']} node(s) whose stored widget values did "
          "not match a known schema, read positionally</li>")
    w("</ul>")
    w("<div class='disclaimer'><p style='margin-top:0'>Licence findings are derived by "
      "matching model filenames against a curated knowledge base. Filenames are not "
      "authoritative &mdash; anyone can rename a weight &mdash; so every verdict reports "
      "the pattern it matched and a confidence level. Treat a low-confidence verdict as a "
      "prompt to check the source page, not as an answer.</p>"
      "<p style='margin-bottom:0'>This is an engineering tool, not legal advice. It exists "
      "to surface the questions worth putting to your legal or production team before a "
      "delivery, and to make the answers reproducible six months later.</p></div>")
    w("</div>")


# --------------------------------------------------------------------------


def _h2(num: str, title: str, count: str = "") -> str:
    tail = f"<span class='count'>{_e(count)}</span>" if count else ""
    return (f"<h2><span class='num'>{_e(num)}</span>{_e(title)}{tail}</h2>")


class _Sections:
    """Numbers sections in the order they are written, not by hand.

    The determination section exists only when a studio profile was supplied,
    so any fixed numbering would leave a hole in every report without one.
    """

    def __init__(self, write):
        self._write = write
        self._n = 0

    def __call__(self, title: str, count: str = "") -> None:
        self._n += 1
        self._write(_h2(str(self._n), title, count))


def _composition(lic: Any) -> str:
    """Licence positions as counts. Information, not a ruling."""
    if not lic.counts:
        return ("<div class='composition'><div class='head'>Licences</div>"
                "<div class='none'>No models found</div></div>")
    rows = []
    for position in ("permissive", "conditional", "non-commercial", "unstated"):
        count = lic.counts.get(position)
        if not count:
            continue
        tone = POSITION_TONE.get(position, "flat")
        rows.append(f"<div class='row {tone}'><span class='n'>{count}</span>"
                    f"<span class='lbl'>{_e(position)}</span></div>")
    return ("<div class='composition'><div class='head'>Licences &middot; "
            f"{lic.total_models} model(s)</div>" + "".join(rows) + "</div>")


def _readout(key: str, value: str, detail: str, tone: str, pct: int | None = None) -> str:
    colour = {"ok": "var(--ok)", "warn": "var(--warn)", "stop": "var(--stop)"}.get(
        tone, "var(--ink-3)")
    gauge = ""
    if pct is not None:
        gauge = (f"<div class='gauge'><i style='width:{max(2, min(100, pct))}%;"
                 f"background:{colour}'></i></div>")
    return (f"<div class='readout'><div class='k'>{_e(key)}</div>"
            f"<div class='v' style='color:{colour}'>{value}</div>"
            f"<div class='d'>{_e(detail)}</div>{gauge}</div>")


def _table(headers: Iterable[str], rows: Iterable[str]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    return ("<div class='scroll'><table><thead><tr>" + head
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _pack_licence_cell(pack: Any) -> str:
    """A node pack's licence, toned by how far it reaches into your own code."""
    from ..resolve.sources import COPYLEFT_SPDX, WEAK_COPYLEFT_SPDX

    if not pack.licence:
        return "<span class='muted'>not checked</span>"
    tone = ("stop" if pack.licence in COPYLEFT_SPDX
            else "warn" if pack.licence in WEAK_COPYLEFT_SPDX else "ok")
    label = f"<span class='chip {tone}'>{_e(pack.licence)}</span>"
    if pack.licence_url:
        return f"<a href='{_e(pack.licence_url)}'>{label}</a>"
    return label


def _source(prov: Any) -> str:
    if prov is None or prov.source == "unknown":
        return "<span class='chip warn'>unresolved</span>"
    label = _e(prov.source)
    if prov.gated:
        label += " <span class='chip warn'>gated</span>"
    if prov.url:
        return f"<a href='{_e(prov.url)}'>{label}</a>"
    return label


def _fee(fee: str) -> str:
    return {"none": "None", "revenue-threshold": "Above revenue cap",
            "paid": "Licence required", "unknown": "Unknown"}.get(fee, fee)


def _risk_tone(score: int) -> str:
    return "stop" if score >= 50 else ("warn" if score >= 25 else "ok")


def _auto_tone(index: int) -> str:
    return "ok" if index >= 65 else ("warn" if index >= 40 else "stop")


def _e(text: Any) -> str:
    return _html.escape(str(text if text is not None else ""), quote=True)
