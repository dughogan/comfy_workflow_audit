"""The pages of ComfyAudit Review, drawn from the data ``app.Review`` returns.

Written for people who clear shots, not for people who build them: plain
words, the decision first, nothing that needs ComfyUI to understand. Every
page also carries the data it was drawn from (``core/report/machine.py``), so
an agent reading the same page gets the same facts; ``?format=json`` returns
that data on its own.
"""

from __future__ import annotations

import base64
import html
import os
from typing import Any
from urllib.parse import quote

from ..core.report import machine

e = html.escape

_BRAND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand")


def _asset(name: str) -> str:
    with open(os.path.join(_BRAND, name), encoding="utf-8") as fh:
        return fh.read().strip()


#: Inlined, so a studio server with no internet access still shows them.
LOGO = _asset("comfy-logo.svg").replace("<svg ", "<svg role='img' aria-label='Comfy' ", 1)
FAVICON = "data:image/svg+xml;base64," + base64.b64encode(_asset("comfy-icon.svg").encode()).decode()

#: Comfy brand (comfy.org/brand, read 2026-10-16): Comfy Ink #211927, Comfy Yellow
#: #F2FF59, Comfy Plum #49378B, Warm White #F0EFED, Canvas #C2BFB9, Warm Gray
#: #7E7C78, Cool Gray #3C3C3C, Mauve #4D3762. The logo is used as provided
#: (yellow), always on an Ink bar so it stays legible in light and dark.
CSS = """
:root{--bg:#F0EFED;--card:#fff;--ink:#211927;--soft:#6b6964;--line:#dcd9d3;--accent:#49378B;
--bar:#211927;--yellow:#F2FF59;--canvas:#C2BFB9;
--ok:#2b6e45;--ok-bg:#e3f0e6;--warn:#7a5200;--warn-bg:#f8eed6;--stop:#9b2f26;--stop-bg:#f6e2df;
--muted-bg:#e6e4df;--focus:#49378B55}
@media (prefers-color-scheme:dark){:root{--bg:#1a1420;--card:#211927;--ink:#F0EFED;--soft:#a9a6a0;
--line:#3a2f45;--accent:#F2FF59;--bar:#140f18;--ok:#8fd3a8;--ok-bg:#1d2e24;--warn:#e9c46a;--warn-bg:#33291a;
--stop:#f0968b;--stop-bg:#3a1f22;--muted-bg:#2d2436;--focus:#F2FF5966}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "PP Formula",Inter,system-ui,
-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
a{color:var(--accent)}
header.top{background:var(--bar);color:#F0EFED;border-bottom:3px solid var(--yellow)}
header.top .in{max-width:980px;margin:0 auto;padding:12px 16px;display:flex;gap:16px;align-items:center;
flex-wrap:wrap}
header.top .brand{display:flex;align-items:center;gap:12px;text-decoration:none;color:#F0EFED}
header.top .brand svg{height:28px;width:auto;display:block}
header.top .brand .app{font-weight:600;letter-spacing:.01em;padding-left:12px;border-left:1px solid #4D3762;
font-size:15px}
header.top .who{margin-left:auto;font-size:14px;color:#C2BFB9}
header.top .who a{color:var(--yellow)}header.top .who strong{color:#F0EFED}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:30px;line-height:1.2;margin:4px 0 6px;font-weight:650}
h2{font-size:19px;margin:0 0 12px;font-weight:650}
h3{font-size:16px;margin:0 0 6px;font-weight:600}
.sub{color:var(--soft);margin:0 0 18px}
.crumbs{font-size:14px;color:var(--soft)}
.crumbs a{color:var(--soft)}
section.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;
margin:0 0 18px}
.band{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 20px}
.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:4px 12px;font-size:14px;
font-weight:600;background:var(--muted-bg);color:var(--soft);white-space:nowrap}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.stop{background:var(--stop-bg);color:var(--stop)}
.pill small{font-weight:500;opacity:.85}
dl.facts{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:0}
dl.facts dt{color:var(--soft)}dl.facts dd{margin:0}
.callout{border-left:4px solid var(--warn);background:var(--warn-bg);padding:10px 14px;border-radius:6px;
margin:12px 0}
.callout.stop{border-color:var(--stop);background:var(--stop-bg)}
.callout.ok{border-color:var(--ok);background:var(--ok-bg)}
.flash{border-radius:8px;padding:12px 16px;margin:0 0 18px;background:var(--ok-bg);color:var(--ok);
font-weight:550}
.flash.err{background:var(--stop-bg);color:var(--stop)}
ul.plain{list-style:none;padding:0;margin:0}
.rule{border-top:1px solid var(--line);padding:14px 0}.rule:first-of-type{border-top:0;padding-top:4px}
.rule .meta{font-size:13px;color:var(--soft);font-weight:400}
.rule>summary{color:var(--ink);padding:2px 0}
.item{margin:10px 0 0;padding:12px;border:1px solid var(--line);border-radius:8px}
.item.done{background:var(--ok-bg);border-color:transparent}
.item .scope{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--soft)}
.item .answer{font-size:14px;margin-top:6px}
textarea,input[type=text]{width:100%;font:inherit;padding:8px 10px;border:1px solid var(--line);
border-radius:6px;background:var(--bg);color:var(--ink)}
textarea{min-height:64px;resize:vertical}
textarea:focus,input:focus,button:focus{outline:3px solid var(--focus);outline-offset:1px}
button,.button{font:inherit;font-weight:600;border:1px solid var(--line);background:var(--card);
color:var(--ink);border-radius:6px;padding:8px 14px;cursor:pointer;text-decoration:none;display:inline-block}
button.primary{background:var(--yellow);border-color:#211927;color:#211927}
button.go{background:var(--ok);border-color:var(--ok);color:#fff}
button.back{background:var(--card);border-color:var(--stop);color:var(--stop)}
@media (prefers-color-scheme:dark){button.go{color:#211927}}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:10px}
table{width:100%;border-collapse:collapse;font-size:15px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:13px;color:var(--soft);font-weight:600}
.scroll{overflow-x:auto}
.small{font-size:14px}.soft{color:var(--soft)}
code,.mono{font-family:ui-monospace,Consolas,monospace;font-size:13px;word-break:break-all}
details summary{cursor:pointer;color:var(--accent)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media (max-width:720px){.grid2{grid-template-columns:1fr}h1{font-size:25px}
dl.facts{grid-template-columns:1fr}dl.facts dt{margin-top:6px}}
.count{font-size:28px;font-weight:650;line-height:1}
footer{max-width:980px;margin:0 auto;padding:0 16px 40px;font-size:13px;color:var(--soft)}
"""

LEGAL_PILL = {"cleared": "ok", "sent-back": "stop", "awaiting": "warn"}
VERDICT_PILL = {"go": "ok", "go-with-conditions": "warn", "conditions": "warn", "no-go": "stop",
                "unknown": "warn"}
ASSURANCE_PILL = {"witnessed": "ok", "signed": "ok", "consistency": "warn", "none": "stop",
                  "absent": "stop"}
SCOPE_WORDS = {"shot": "This shot", "show": "The whole show", "studio": "The whole studio"}


def _date(value: Any) -> str:
    """'2026-10-16T14:03:00-07:00' -> '2026-10-16 14:03'."""
    text = str(value or "")
    return text[:16].replace("T", " ") if len(text) >= 16 else text


def _pill(text: str, tone: str = "", note: str = "") -> str:
    return (f"<span class='pill {e(tone)}'>{e(text)}"
            + (f" <small>{e(note)}</small>" if note else "") + "</span>")


def _csrf(token: str) -> str:
    return f"<input type='hidden' name='csrf' value='{e(token)}'>"


def layout(title: str, body: str, *, kind: str, data: dict[str, Any], me: dict[str, Any],
           flash: str = "", error: str = "", crumbs: list[tuple[str, str]] | None = None) -> str:
    who = ""
    if me.get("mode") == "sign-in":
        who = (f"Signed in as <strong>{e(me['name'])}</strong>" if me.get("can_sign")
               else f"<span style='color:var(--stop)'>{e(me.get('problem', ''))}</span>")
    elif me.get("name"):
        who = f"Reviewing as <strong>{e(me['name'])}</strong> · <a href='/me'>change</a>"
    else:
        who = "<a href='/me'>Say who you are</a> to record decisions"
    trail = ""
    if crumbs:
        trail = "<div class='crumbs'>" + " › ".join(
            f"<a href='{e(href)}'>{e(label)}</a>" if href else e(label) for label, href in crumbs) + "</div>"
    note = (f"<div class='flash' role='status'>{e(flash)}</div>" if flash else "") + \
           (f"<div class='flash err' role='alert'>{e(error)}</div>" if error else "")
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{e(title)} · ComfyAudit Review</title>
<link rel='alternate' type='application/json' href='?format=json'>
<link rel='icon' type='image/svg+xml' href='{FAVICON}'>
<style>{CSS}</style></head><body>
<header class='top'><div class='in'><a class='brand' href='/'>{LOGO}<span class='app'>ComfyAudit Review</span></a>
<span class='who'>{who}</span></div></header>
<main>{trail}{note}{body}</main>
<footer>{e(machine.DISCLAIMER)} This page's data is also available as JSON:
<a href='?format=json'>?format=json</a>.</footer>
{machine.block(kind, data)}
</body></html>"""


# ---------------------------------------------------------------------------

def home(data: dict[str, Any], me: dict[str, Any], csrf: str, **msg: str) -> str:
    inbox = ""
    if data["inbox_folder"]:
        rows = "".join(
            f"<tr><td>{e(r['name'])}</td><td class='soft'>{e(_date(r['modified']))}</td>"
            f"<td class='soft'>{r['bytes'] / 1e6:.1f} MB</td><td>"
            + (f"<a href='/p/{e(r['opened'])}'>Already open · view</a>" if r.get("opened") else
               f"<form method='post' action='/open'>{_csrf(csrf)}"
               f"<input type='hidden' name='inbox' value='{e(r['name'])}'><button>Open</button></form>")
            + "</td></tr>" for r in data["inbox"])
        inbox = ("<h3 style='margin-top:20px'>Waiting in the inbox</h3>" + (
            f"<div class='scroll'><table><tr><th>Package</th><th>Arrived</th><th>Size</th><th></th></tr>"
            f"{rows}</table></div>" if rows else "<p class='soft'>Nothing in the inbox folder.</p>"))
    shows = "".join(
        f"<tr><td><a href='/show/{quote(s['production'])}'>{e(s['name'] or 'No show given')}</a></td>"
        f"<td>{s['packages']}</td><td>{s['awaiting']}</td></tr>" for s in data["shows"])
    pkgs = "".join(
        f"<tr><td><a href='/p/{r['id']}'>{e(r['name'] or r['filename'])}</a>"
        f"<div class='small soft'>{e(r['project'])}</div></td>"
        f"<td>{_pill(r['legal_state_label'], LEGAL_PILL.get(r['legal_state'], ''))}</td>"
        f"<td>{_pill(r['verdict_label'] or r['verdict'] or 'No verdict', VERDICT_PILL.get(r['verdict'], 'warn'))}</td>"
        f"<td>{r['open_count']}</td><td class='soft'>{e(_date(r['opened_at']))}</td></tr>"
        for r in data["packages"][:30])
    body = f"""<h1>Shots for legal review</h1>
<p class='sub'>Open a delivery package from ComfyUI to see what was decided, answer what is left, and
clear the shot or send it back. Every decision is signed into the shot's own history.</p>
<section class='card'><h2>Open a package</h2>
<form method='post' action='/open' enctype='multipart/form-data'>{_csrf(csrf)}
<p class='small soft' style='margin-top:0'>The <code>.delivery.zip</code> that <em>Package for delivery</em>
made in ComfyUI.</p>
<div class='row'><input type='file' name='package' accept='.zip' required aria-label='Delivery package'>
<button class='primary'>Open package</button></div></form>{inbox}</section>
<div class='grid2'>
<section class='card'><h2>Shows</h2>{f"<div class='scroll'><table><tr><th>Show</th><th>Packages</th><th>Waiting</th></tr>{shows}</table></div>" if shows else "<p class='soft'>None yet. Shows appear here as their packages are opened.</p>"}</section>
<section class='card'><h2>How this works</h2><ol class='small' style='padding-left:18px;margin:0'>
<li>Open the package. It is checked file by file, and the shot's history is verified.</li>
<li>Read the decision made at sign-off and what it left for legal.</li>
<li>Answer what you can. Items for a whole show or the studio are answered once and every shot inherits them.</li>
<li>Clear the shot, or send it back with a reason. Download the updated workflow for the artist.</li></ol></section>
</div>
<section class='card'><h2>Recently opened</h2>{f"<div class='scroll'><table><tr><th>Shot</th><th>Legal</th><th>Verdict at sign-off</th><th>Open</th><th>Opened</th></tr>{pkgs}</table></div>" if pkgs else "<p class='soft'>No packages opened yet.</p>"}</section>"""
    return layout("Shots", body, kind="review-home", data=data, me=me, **msg)


# ---------------------------------------------------------------------------

def _answer_form(pid: str, item: dict[str, Any], csrf: str, show: str, can: bool) -> str:
    if not can:
        return ""
    where = {"shot": "Record for this shot", "show": f"Record for the whole show{(' ' + show) if show else ''}",
             "studio": "Record for the whole studio"}[item["scope"]]
    return (f"<form method='post' action='/p/{pid}/answer' class='row' style='display:block'>{_csrf(csrf)}"
            f"<input type='hidden' name='tag' value='{e(item['tag'])}'>"
            f"<input type='hidden' name='scope' value='{e(item['scope'])}'>"
            f"<label class='small soft' for='a-{e(item['tag'])}'>What confirms it (a document, an email, "
            f"where it is filed)</label>"
            f"<textarea id='a-{e(item['tag'])}' name='note' required></textarea>"
            f"<div class='row'><button>{e(where)}</button></div></form>")


def _items(view: dict[str, Any], csrf: str, can: bool) -> str:
    if not view["decision"]:
        return "<p class='soft'>This package carries no sign-off, so nothing was left for legal.</p>"
    if not view["open_items"]:
        return "<p>The sign-off left nothing for legal to confirm.</p>"
    show = view["shot"].get("project", "")
    levels = []
    for level, heading, hint in (
            ("studio", "For the whole studio", "Answered once; every shot of every show inherits it."),
            ("show", f"For the whole show{(' ' + show) if show else ''}",
             "Answered once; every shot of this show inherits it."),
            ("shot", "For this shot only", "Recorded in this shot's own history.")):
        groups = [{**g, "items": [i for i in g["items"] if i["scope"] == level]}
                  for g in view["open_items"]]
        groups = [g for g in groups if g["items"]]
        if not groups:
            continue
        total = sum(len(g["items"]) for g in groups)
        open_n = sum(1 for g in groups for i in g["items"] if i["state"] != "answered")
        levels.append(f"<h3 style='margin:18px 0 2px'>{e(heading)} · "
                      f"{open_n} open of {total}</h3><p class='small soft' style='margin:0 0 6px'>{e(hint)}</p>"
                      + _rule_groups(view, groups, csrf, show, can))
    return "".join(levels)


def _rule_groups(view: dict[str, Any], groups: list[dict[str, Any]], csrf: str, show: str,
                 can: bool) -> str:
    out = []
    for g in groups:
        items = []
        for i in g["items"]:
            scope = SCOPE_WORDS.get(i["scope"], i["scope"])
            if i["state"] == "answered":
                a = i["answer"] or {}
                if a.get("scope") == "shot":
                    said = f"Answered for this shot by {e(str(a.get('by', '')))}"
                else:
                    where = "the studio" if a.get("scope") == "studio" else f"the show {a.get('production_name', '')}"
                    sig = "" if a.get("signature") == "signed" else f" ({e(str(a.get('signature', '')))})"
                    said = (f"Answered once for {e(where)} by {e(str(a.get('by', '')))}, "
                            f"{e(_date(a.get('at')))}{sig}: “{e(str(a.get('note', '')))}”")
                items.append(f"<div class='item done'><div class='scope'>{e(scope)} · answered</div>"
                             f"<div>{e(i['text'])}</div><div class='answer'>✓ {said}</div></div>")
            else:
                items.append(f"<div class='item'><div class='scope'>{e(scope)} · open</div>"
                             f"<div>{e(i['text'])}</div>"
                             + (_answer_form(view['package']['id'], i, csrf, show, can) if i["tag"] else "")
                             + "</div>")
        open_n = sum(1 for i in g["items"] if i["state"] != "answered")
        state = f"{open_n} open" if open_n else "all answered"
        out.append(f"<details class='rule'><summary><strong>{e(g['title'])}</strong> "
                   f"<span class='meta'>· {e(g['jurisdiction'])} · {state}</span></summary>"
                   + (f"<p class='small soft' style='margin:6px 0 0'>{e(g['why'])}"
                      + (f" <a href='{e(g['source'])}' rel='noreferrer' target='_blank'>Source</a>"
                         if str(g.get("source", "")).startswith("http") else "") + "</p>" if g["why"] else "")
                   + "".join(items) + "</details>")
    return "".join(out)


def _decision_form(view: dict[str, Any], csrf: str, me: dict[str, Any],
                   draft: dict[str, str] | None = None) -> str:
    draft = draft or {}
    pid = view["package"]["id"]
    last = view["last_review"]
    done = ""
    if last:
        tone = "ok" if last["decision"] == "cleared" else "stop"
        done = (f"<div class='callout {tone}'><strong>{'Cleared' if last['decision'] == 'cleared' else 'Sent back'}</strong> by {e(last['by'])}"
                f" ({e(last['role'])}), {e(_date(last['at']))}<br>“{e(last['reason'])}”"
                + (f"<div class='small'>Cleared with {len(last.get('open_items') or [])} item(s) still open.</div>"
                   if last["decision"] == "cleared" and last.get("open_items") else "") + "</div>")
    download = (f"<p class='small'><a class='button' href='/p/{pid}/workflow.json' download>Download the "
                f"updated workflow</a> <span class='soft'>Give this to the artist: it carries every "
                f"decision made here. Opening their old copy instead starts a new record of the shot."
                f"</span></p>") if view["since_packaging"] else ""
    if not view["writable"]:
        return done + f"<div class='callout stop'>{e(view['why_not_writable'])}</div>" + download
    if not me.get("can_sign"):
        return done + f"<div class='callout'>{e(me.get('problem', ''))}</div>" + download
    open_n = view["open_count"]
    ack = (f"<label class='row small'><input type='checkbox' name='accept_open' value='1'"
           f"{' checked' if draft.get('accept_open') else ''}> Clear it with "
           f"{open_n} item(s) still open (they are recorded with the decision)</label>") if open_n else ""
    return done + f"""<form method='post' action='/p/{pid}/decide'>{_csrf(csrf)}
<label for='reason' class='small soft'>Reason (sealed with the decision; the artist sees it)</label>
<textarea id='reason' name='reason' required>{e(draft.get('reason', ''))}</textarea>{ack}
<div class='row'><button class='go' name='decision' value='clear'>Clear the shot</button>
<button class='back' name='decision' value='send-back'>Send it back</button></div></form>""" + download


def shot(view: dict[str, Any], me: dict[str, Any], csrf: str, draft: dict[str, str] | None = None,
         **msg: str) -> str:
    s, d, i = view["shot"], view["decision"], view["integrity"]
    pid = view["package"]["id"]
    band = (_pill(view["legal_state_label"], LEGAL_PILL[view["legal_state"]])
            + (_pill(d["verdict_label"] or d["verdict"], VERDICT_PILL.get(d["verdict"], "warn"), "at sign-off")
               if d else _pill("No sign-off", "stop"))
            + _pill(i["label"], ASSURANCE_PILL.get(i["assurance"], "warn"), "history")
            + _pill("Package intact" if i["package_files"] else "Package altered",
                    "ok" if i["package_files"] else "stop")
            + (_pill(f"{view['open_count']} open", "warn") if view["open_count"] else "")
            + (_pill("Older copy", "warn", "shot carried on") if view.get("old_copy") else ""))
    ids = " · ".join(e(f"{k}: {v}") for k, v in (s.get("external_ids") or {}).items())
    subtitle = " · ".join(x for x in (e(s.get("project", "")) or "No show given", ids,
                                      f"stage: {e(s.get('stage', ''))}") if x)
    signoff = "<p class='soft'>This package has no sign-off entry.</p>"
    if d:
        signoff = f"""<dl class='facts'>
<dt>Verdict</dt><dd><strong>{e(d['verdict_label'] or d['verdict'])}</strong>{(' - ' + e(d['headline'])) if d['headline'] else ''}</dd>
<dt>Signed off by</dt><dd>{e(d['signed_off_by'])}{(' (' + e(d['role']) + ')') if d['role'] else ''}, {e(_date(d['signed_off_at']))}</dd>
{f"<dt>Note</dt><dd>{e(d['note'])}</dd>" if d['note'] else ''}
{f"<dt>Checked as</dt><dd>{e(d['checked_as'])}</dd>" if d['checked_as'] else ''}
</dl>""" + (f"<div class='callout'><strong>Delivered as an exception.</strong> Reason given: “{e(d['exception'])}”</div>"
            if d["exception"] else "") + (
            "<h3 style='margin-top:14px'>What blocked a clean yes</h3><ul>" + "".join(
                f"<li>{e(b if isinstance(b, str) else str(b))}</li>" for b in d["blockers"]) + "</ul>"
            if d["blockers"] else "") + (
            "<h3 style='margin-top:14px'>Conditions</h3><ul>" + "".join(
                f"<li>{e(c if isinstance(c, str) else str(c))}</li>" for c in d["conditions"]) + "</ul>"
            if d["conditions"] else "") + (
            "" if d["index_agrees"] else
            "<div class='callout stop'>The package index states a different verdict from the signed "
            "sign-off. The signed one is shown here.</div>")
    inherited = "".join(
        f"<tr><td>{e(a['text'])}<div class='small soft'>{e(a['rule_title'])}</div></td>"
        f"<td>{e('the studio' if a['scope'] == 'studio' else 'show ' + (a.get('production_name') or ''))}</td>"
        f"<td>“{e(str(a.get('note', '')))}”<div class='small soft'>{e(str(a.get('by', '')))}, "
        f"{e(_date(a.get('at')))} · {e(a['when'])}</div></td>"
        f"<td>{_pill({'signed': 'Signature checks', 'unsigned': 'Unsigned', 'altered': 'Altered'}.get(a['signature'], a['signature']), {'signed': 'ok', 'unsigned': 'warn'}.get(a['signature'], 'stop'))}</td></tr>"
        for a in view["inherited"])
    docs = "".join(
        f"<li><a href='/p/{pid}/file/{quote(f['path'])}' target='_blank' rel='noopener'>"
        f"{e({'legal-report': 'Legal report (provenance record)', 'licence-audit': 'Licence audit', 'life-of-the-shot': 'Life of the shot'}[f['role']])}</a>"
        f"<div class='small soft'>{e(f['description'])}</div></li>" for f in view["documents"])
    others = "".join(
        f"<tr><td><a href='/p/{pid}/file/{quote(f['path'])}' download>{e(f['path'])}</a></td>"
        f"<td class='small soft'>{e(f['role'])}</td><td>{_pill('matches' if f['state'] == 'ok' else f['state'], 'ok' if f['state'] == 'ok' else 'stop')}</td></tr>"
        for f in view["documents"] + view["other_files"])
    since = "".join(f"<li><strong>{e(r['label'])}</strong> - {e(r['who'])}"
                    f"{(' (' + e(r['role']) + ')') if r['role'] else ''}, {e(_date(r['at']))}"
                    f"{(': “' + e(r['note']) + '”') if r['note'] else ''}</li>" for r in view["since_packaging"])
    sg = view["shotgrid"]
    sg_html = ""
    if sg["configured"] and s.get("shotgrid"):
        posts = "".join(f"<li>{e(_date(p['at']))}: " + (e(p['error']) if p.get('error') else
                        f"Note {e(str(p.get('note_id')))} posted") + "</li>" for p in sg["posts"])
        sg_html = (f"<p class='small'>Decisions are posted to ShotGrid shot {e(s['shotgrid'])} as a Note.</p>"
                   + (f"<ul class='small'>{posts}</ul>" if posts else ""))
    elif s.get("shotgrid"):
        sg_html = f"<p class='small soft'>ShotGrid {e(s['shotgrid'])}. ShotGrid is not set up on this server, so nothing is posted there.</p>"
    problems = "".join(f"<li>{e(p)}</li>" for p in i["problems"] + i["package_problems"])
    notes = "".join(f"<li>{e(n)}</li>" for n in i["notes"])
    body = f"""<h1>{e(s.get('name') or 'Unnamed shot')}</h1>
<p class='sub'>{subtitle}</p>
<div class='band'>{band}<a class='pill' href='#decide'>Go to your decision ↓</a></div>
<section class='card'><h2>Signed off in ComfyUI</h2>{signoff}</section>
<section class='card'><h2>Documents</h2><ul>{docs}</ul>
<p class='small soft'>They open as they were packaged. Use your browser's print to save one as PDF.</p></section>
<section class='card'><h2>Left for legal{f" · {view['open_count']} open" if view['open_count'] else ''}</h2>
<p class='small soft' style='margin-top:0'>Production facts the record cannot prove itself. Items for a whole
show or the studio are answered once, and every shot of that show inherits the answer.</p>
{_items(view, csrf, me.get('can_sign') and view['writable'])}</section>
<section class='card'><h2>Inherited from the show and studio</h2>
{f"<div class='scroll'><table><tr><th>Item</th><th>Answered for</th><th>Answer</th><th>Signature</th></tr>{inherited}</table></div>" if inherited else "<p class='soft'>Nothing inherited.</p>"}</section>
<section class='card' id='decide'><h2>Your decision</h2>{_decision_form(view, csrf, me, draft)}{sg_html}</section>
<section class='card'><h2>Can this record be trusted?</h2>
<p><strong>{e(i['label'])}.</strong> {e(i['detail'])}</p>
{f"<div class='callout stop'><strong>Problems</strong><ul>{problems}</ul></div>" if problems else ''}
{f"<details><summary>Notes ({len(i['notes'])})</summary><ul class='small'>{notes}</ul></details>" if notes else ''}
<details><summary>Every file in the package, checked against its fingerprint</summary>
<div class='scroll'><table>{others}</table></div>
<p class='small soft'>Package <code>{e(view['package']['filename'])}</code>, SHA-256 <code>{e(view['package']['sha256'])}</code>,
made with ComfyAudit {e(view['package']['made_with'])} on {e(_date(view['package']['made_at']))}.
History head at packaging <code>{e(i['head_at_packaging'][:16])}</code>.</p></details></section>
<section class='card'><h2>Recorded here since the package was made</h2>
{f"<ul>{since}</ul>" if since else "<p class='soft'>Nothing yet.</p>"}</section>"""
    crumbs = [("Shots", "/")]
    if s.get("production"):
        crumbs.append((s.get("project") or "Show", f"/show/{quote(s['production'])}"))
    crumbs.append((s.get("name") or "Shot", ""))
    return layout(s.get("name") or "Shot", body, kind="review-shot", data=view, me=me, crumbs=crumbs, **msg)


# ---------------------------------------------------------------------------

def _answers_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class='soft'>Nothing answered yet.</p>"
    body = "".join(
        f"<tr><td>{e(a['text'])}<div class='small soft'>{e(a['rule_title'])}</div></td>"
        f"<td>“{e(a['note'])}”</td><td>{e(a['by'])}<div class='small soft'>{e(_date(a['at']))}"
        f"{(' · replaces ' + str(a['superseded']) + ' earlier') if a['superseded'] else ''}</div></td>"
        f"<td>{_pill({'signed': 'Signature checks', 'unsigned': 'Unsigned', 'altered': 'Altered'}.get(a['signature'], a['signature']), {'signed': 'ok', 'unsigned': 'warn'}.get(a['signature'], 'stop'))}</td></tr>"
        for a in rows)
    return (f"<div class='scroll'><table><tr><th>Item</th><th>Answer</th><th>By</th><th>Signature</th></tr>"
            f"{body}</table></div>")


def show(data: dict[str, Any], me: dict[str, Any], csrf: str, **msg: str) -> str:
    c = data["counts"]
    rows = "".join(
        f"<tr><td><a href='/p/{r['id']}'>{e(r['name'] or r['filename'])}</a>"
        + (f"<div class='small soft'>{len(r['earlier_packages'])} earlier package(s)</div>" if r["earlier_packages"] else "")
        + f"</td><td>{_pill(r['legal_state_label'], LEGAL_PILL.get(r['legal_state'], ''))}</td>"
        f"<td>{_pill(r['verdict_label'] or r['verdict'] or 'No verdict', VERDICT_PILL.get(r['verdict'], 'warn'))}</td>"
        f"<td>{r['open_count']}</td>"
        f"<td>{_pill(r['assurance_label'], ASSURANCE_PILL.get(r['assurance'], 'warn'))}"
        + ("" if r["package_ok"] else " " + _pill("Package altered", "stop")) + "</td>"
        f"<td class='soft small'>{e(r['stage'])}</td></tr>" for r in data["shots"])
    body = f"""<h1>{e(data['name'] or 'No show given')}</h1>
<p class='sub'>Every shot of this show opened here, newest package of each, and what has been answered
once for the whole show.</p>
<div class='band'>
<section class='card' style='margin:0;flex:1;min-width:150px'><div class='count'>{c['awaiting']}</div><div class='soft small'>waiting for legal</div></section>
<section class='card' style='margin:0;flex:1;min-width:150px'><div class='count'>{c['cleared']}</div><div class='soft small'>cleared</div></section>
<section class='card' style='margin:0;flex:1;min-width:150px'><div class='count'>{c['sent-back']}</div><div class='soft small'>sent back</div></section></div>
<section class='card'><h2>Shots</h2>
{f"<div class='scroll'><table><tr><th>Shot</th><th>Legal</th><th>Verdict at sign-off</th><th>Open</th><th>History</th><th>Stage</th></tr>{rows}</table></div>" if rows else "<p class='soft'>No shots of this show have been opened.</p>"}</section>
<section class='card'><h2>Answered for the whole show</h2>
<p class='small soft' style='margin-top:0'>Every shot whose show is “{e(data['name'])}” inherits these, however the
name is typed. A newer answer for the same item replaces the older one; both stay in the log.</p>
{_answers_table(data['answers'])}</section>
<section class='card'><h2>Answered for the whole studio</h2>{_answers_table(data['studio_answers'])}</section>"""
    return layout(data["name"] or "Show", body, kind="review-show", data=data, me=me,
                  crumbs=[("Shots", "/"), (data["name"] or "Show", "")], **msg)


def me_page(me: dict[str, Any], csrf: str, per_person: bool, **msg: str) -> str:
    if per_person:
        body = ("<h1>Who you are</h1><p>This server takes who you are from the studio's sign-in, and "
                "signs what you decide with your own key.</p>"
                + (f"<p>Signed in as <strong>{e(me['name'])}</strong> ({e(me['role'])}).</p>" if me.get("can_sign")
                   else f"<div class='callout stop'>{e(me.get('problem', ''))}</div>"))
    else:
        body = f"""<h1>Who you are</h1>
<section class='card'><p style='margin-top:0'>Your name is recorded with every answer and decision you make here,
which are signed with the studio's key. The signature proves they were recorded through this server;
your name is as you give it.</p>
<form method='post' action='/me'>{_csrf(csrf)}<label for='name' class='small soft'>Your full name</label>
<input type='text' id='name' name='name' value='{e(me.get('name') or '')}' required autocomplete='name'>
<div class='row'><button class='primary'>Save</button></div></form>
{f"<div class='callout'>{e(me['problem'])}</div>" if me.get('problem') and me.get('name') else ''}</section>"""
    return layout("Who you are", body, kind="review-identity", data=me, me=me,
                  crumbs=[("Shots", "/"), ("Who you are", "")], **msg)


def error_page(message: str, me: dict[str, Any], status: int) -> str:
    body = f"<h1>{'Not found' if status == 404 else 'That did not work'}</h1><div class='callout stop'>{e(message)}</div><p><a href='/'>Back to the shots</a></p>"
    return layout("Error", body, kind="review-error", data={"error": message, "status": status}, me=me)
