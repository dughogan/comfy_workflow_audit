"""The life of a shot: its story, its log and its graph, on one timeline.

One self-contained HTML page, generated from the history alone: the same page
opens over the ComfyUI canvas, ships in the delivery package, and works on an
air-gapped network. Three views share a scrubber:

* **Graph** - the workflow redrawn as it stood at each entry, with what was
  added or changed at that moment marked, and models that bar commercial use
  outlined.
* **Story** - the life told in sentences, built from the entries (never from
  a model), so every claim in it points at a signed entry.
* **Log** - every entry, timestamped.

Prompts protected by the studio's confidentiality setting stay protected: the
story says a prompt was rewritten, never what it says.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

from .. import __version__
from .. import history as H
from .html import _e

#: How each event reads in the story, when nothing more specific applies.
_VERB = {
    "reviewed": "reviewed the work",
    "approved": "approved the output",
    "rejected": "rejected the output",
    "handoff": "took the shot over",
    "promoted": "moved the shot to a new stage",
    "moved": "moved the workflow file",
}


def _when(at: str) -> str:
    try:
        return _dt.datetime.fromisoformat(at).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return str(at)[:16].replace("T", " ")


def _states(snap: dict[str, Any]) -> dict[str, str]:
    """A signature per node of what a person can change about it."""
    sig: dict[str, list[str]] = {}
    for node, params in (snap.get("params") or {}).items():
        sig.setdefault(str(node), []).append(json.dumps(
            {k: v for k, v in params.items() if k not in ("seed", "noise_seed")}, sort_keys=True))
    for p in snap.get("prompts", []):
        sig.setdefault(str(p["node"]), []).append(f"p:{p.get('polarity')}:{p.get('sha256')}")
    for i in snap.get("inputs", []):
        sig.setdefault(str(i["node"]), []).append(f"i:{i.get('sha256')}")
    for m in snap.get("models", []):
        sig.setdefault(str(m.get("node")), []).append(
            f"m:{m.get('filename')}:{m.get('strength')}:{m.get('enabled', True)}")
    for node, mode in (snap.get("bypassed") or {}).items():
        sig.setdefault(str(node), []).append(f"b:{mode}")
    return {k: "|".join(sorted(v)) for k, v in sig.items()}


def _sentence(e: dict[str, Any], who: str, flag: dict[str, Any] | None) -> str:
    """One entry, told plainly. Built only from what the entry records."""
    ev = e.get("event", "")
    ch = e.get("changes") or {}
    data = e.get("data") or {}
    note = (e.get("note") or "").strip()
    parts: list[str] = []
    if ev == "created":
        parts.append(f"{who} started the workflow")
    elif ev == "imported":
        parts.append(f"Tracking began when {who} opened an existing workflow; what it "
                     "held before then is not attributed to anyone")
    elif ev == "forked":
        link = data.get("parent") or {}
        parts.append(f"{who}'s copy became its own record, branched from "
                     f"{link.get('name') or 'another record'} at its entry {link.get('seq')}"
                     + (f" ({data['reason']})" if data.get("reason") else ""))
    elif ev in ("edited", "rendered"):
        did: list[str] = []
        if ch.get("prompts_changed") or ch.get("prompts_added"):
            did.append("rewrote the prompt" if len((ch.get("prompts_changed") or [])
                                                     + (ch.get("prompts_added") or [])) == 1
                       else "rewrote the prompts")
        if ch.get("models_added") and ch.get("models_removed"):
            did.append(f"swapped {', '.join(ch['models_removed'])} for {', '.join(ch['models_added'])}")
        elif ch.get("models_added") and not ch.get("initial"):
            did.append(f"added {', '.join(ch['models_added'])}")
        elif ch.get("models_removed"):
            did.append(f"removed {', '.join(ch['models_removed'])}")
        if ch.get("model_strength_changed"):
            did.append("changed a model's strength")
        if ch.get("models_disabled") or ch.get("branches_toggled"):
            did.append("switched part of the graph off or on")
        if ch.get("selected"):
            picks = ", ".join(f"the output of entry {s['from_entry']}" for s in ch["selected"])
            did.append(f"chose {picks} and brought it back in")
        elif ch.get("inputs_changed"):
            did.append("loaded new source material")
        if ch.get("params_changed"):
            did.append("adjusted " + ", ".join(p.split(":")[0].split(".", 1)[-1]
                                                for p in ch["params_changed"][:3]))
        if ch.get("node_count") or ch.get("packs_added"):
            did.append("restructured the graph")
        if ev == "rendered":
            n = len(e.get("outputs") or [])
            did.append(f"rendered {n} output{'s' if n != 1 else ''}" if n else "ran the workflow")
        if not did:
            did.append("saved a change")
        parts.append(f"{who} " + (", ".join(did[:-1]) + " and " + did[-1] if len(did) > 1 else did[0]))
    elif ev == "sourced":
        origin = H.ORIGINS.get(data.get("origin", ""), data.get("origin", "")).lower()
        parts.append(f"{who} declared where an input came from: {origin}"
                     + (f" (asset {data['asset_id']})" if data.get("asset_id") else ""))
    elif ev == "promoted":
        parts.append(f"{who} moved the shot from {data.get('from', 'sandbox')} to {data.get('stage')}")
    elif ev == "approved" and isinstance(data.get("clearance"), dict):
        c = data["clearance"]
        parts.append(f"{who} signed it off for delivery: {c.get('verdict_label') or c.get('verdict')}"
                     + (f", as an exception ({c['exception']})" if c.get("exception") else ""))
    elif ev == "note" and data.get("evidence"):
        parts.append(f"{who} confirmed evidence for {data['evidence'].split(':')[0]}")
    elif ev == "note":
        parts.append(f"{who} added a note")
    elif ev == "moved":
        parts.append(f"The workflow was saved as {data.get('to')}" if not data.get("from")
                     else f"The workflow file moved from {data.get('from')} to {data.get('to') or 'an unsaved tab'}")
    else:
        parts.append(f"{who} {_VERB.get(ev, H.EVENTS.get(ev, ev).lower())}")
    text = parts[0]
    if note and ev not in ("moved",):
        text += f": “{note}”"
    text += "."
    if flag:
        models = ", ".join(f"{m['filename']} ({m.get('licence') or 'licence unknown'})"
                           for m in flag["models"])
        if flag["status"] == "blocked":
            text += f" From here the workflow could not be used commercially: {models}."
        elif flag["status"] == "cleared" and flag.get("from") == "blocked":
            text += " Commercial use was possible again from here."
        elif flag["status"] == "unknown":
            text += f" A licence here could not be identified: {models}."
    return text


def data(history: dict[str, Any]) -> dict[str, Any]:
    """Everything the page draws, derived from the chain."""
    entries = history.get("entries", [])
    lin = H.lineage(history)
    roles = {p["name"]: p.get("role", "") for p in lin["people"]}
    flags = {c["seq"]: c for c in H.commercial_timeline(history)}
    graphs: list[dict[str, Any]] = []
    graph_of: dict[str, int] = {}
    rows = []
    prev_states: dict[str, str] | None = None
    prev_nodes: set[str] = set()
    for e in entries:
        snap = H.snapshot_of(entries, e) or {}
        topo = snap.get("topology") or {"nodes": [], "links": []}
        key = e.get("snapshot_sha256", "") or str(e.get("seq"))
        if key not in graph_of:
            graph_of[key] = len(graphs)
            graphs.append(topo)
        states = _states(snap)
        ids = {n["id"] for n in topo["nodes"]}
        added = sorted(ids - prev_nodes) if prev_states is not None else []
        removed = sorted(prev_nodes - ids)
        changed = sorted(i for i in ids & prev_nodes
                         if prev_states is not None and states.get(i) != prev_states.get(i))
        blocking = sorted({str(m.get("node")) for m in snap.get("models", [])
                           if m.get("commercial_use") == "no" and m.get("enabled", True)})
        actor = e.get("actor") or {}
        who = actor.get("name") or "someone"
        role = actor.get("role") or roles.get(who, "")
        flag = flags.get(e.get("seq"))
        empty = not topo["nodes"] and not snap.get("node_count")
        rows.append({
            "empty": empty, "outputs": len(e.get("outputs") or []),
            "seq": e.get("seq"), "at": e.get("at", ""), "when": _when(e.get("at", "")),
            "day": str(e.get("at", ""))[:10], "event": e.get("event", ""),
            "label": H.EVENTS.get(e.get("event", ""), e.get("event", "")),
            "who": who, "role": role, "note": e.get("note", ""),
            "story": _sentence(e, f"{who}" + (f" ({role})" if role and e.get("seq") == 1 else ""), flag),
            "graph": graph_of[key], "added": added, "removed": removed, "changed": changed,
            "blocking": blocking, "signed": "sig" in e,
            "flag": ({"status": flag["status"], "from": flag["from"]} if flag else None),
            "breakpoints": e.get("breakpoints", []),
        })
        prev_states, prev_nodes = states, ids
    return {"shot": lin["shot"], "rows": rows, "graphs": graphs}


def render(history: dict[str, Any], *, verification: Any = None) -> str:
    """The page. ``verification`` is a ``history.Verification`` to show its seal."""
    payload = data(history)
    shot = payload["shot"]
    seal = ""
    if verification is not None:
        seal = (f"<span class='seal {verification.assurance}'>"
                f"{_e(H.ASSURANCE_LABELS.get(verification.assurance, ''))}</span>")
    ids = " &middot; ".join(f"{_e(k)} {_e(v)}" for k, v in (shot.get("external_ids") or {}).items())
    # Everything in the payload came from the workflow, so it reaches the page
    # only as JSON and goes on screen through textContent, never as markup.
    blob = json.dumps(payload, default=str).replace("</", "<\\/").replace("<!--", "<\\!--")
    values = {"TITLE": _e(shot.get("name") or "Shot"),
              "IDS": ids or "not linked to a studio asset ID",
              "STAGE": _e(shot.get("stage", "sandbox")), "SEAL": seal,
              "VERSION": _e(__version__), "DATA": blob}
    # One pass, so a value that happens to contain a placeholder stays text.
    return re.sub(r"%(TITLE|IDS|STAGE|SEAL|VERSION|DATA)%", lambda m: values[m.group(1)], PAGE)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%TITLE% - life of the shot</title>
<style>
:root{--bg:#f6f4ef;--panel:#fff;--ink:#1d1d1b;--ink2:#5c5b56;--ink3:#8c8a83;--rule:#e2dfd6;
--add:#1d9e75;--chg:#c27a17;--stop:#b23a2e;--node:#efede6;--wire:#b8b5ab;--accent:#2f6fb3}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--panel:#1f1f1d;--ink:#ecebe6;--ink2:#b3b1aa;
--ink3:#7d7b75;--rule:#33322f;--node:#2a2a27;--wire:#57554f;--accent:#6aa5e6}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:16px 20px 8px;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
h1{font-size:20px;margin:0;font-weight:600}.meta{color:var(--ink2);font-size:12.5px}
.seal,.stage{font:600 10.5px/1 ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase;
padding:4px 8px;border-radius:99px;border:1px solid var(--ink3);color:var(--ink2)}
.seal.witnessed,.seal.signed{border-color:var(--add);color:var(--add)}.seal.none{border-color:var(--stop);color:var(--stop)}
.bar{display:flex;gap:10px;align-items:center;padding:8px 20px;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);background:var(--panel);position:sticky;top:0;z-index:2}
.track{flex:1;position:relative;padding:6px 0 10px}.track input{width:100%;margin:0;display:block;accent-color:var(--accent)}
#ticks{position:absolute;left:8px;right:8px;bottom:0;height:8px;pointer-events:none}
#ticks i{position:absolute;top:0;width:2px;height:6px;margin-left:-1px;background:var(--ink3);border-radius:1px}
#ticks i.blk{background:var(--stop);width:4px;height:8px;margin-left:-2px}#ticks i.ren{background:var(--accent)}
.bar select{font:inherit;font-size:12px;padding:4px;border-radius:6px;border:1px solid var(--rule);background:var(--panel);color:var(--ink)}
.bar button:disabled{opacity:.4;cursor:default}
#cap{position:absolute;left:14px;right:14px;top:12px;z-index:1;background:var(--panel);
border:1px solid var(--rule);border-left:4px solid var(--accent);border-radius:6px;padding:8px 12px;font-size:13.5px;line-height:1.45;
min-height:62px;box-shadow:0 2px 10px rgba(0,0,0,.08)}#cap.blk{border-left-color:var(--stop)}#cap b{font:600 10.5px/1 ui-monospace,monospace;
letter-spacing:.08em;text-transform:uppercase;color:var(--ink3);display:block;margin-bottom:4px}
#graphwrap svg{margin-top:92px;min-height:380px;height:calc(100% - 130px)}
.bar button{font:inherit;padding:5px 10px;border-radius:6px;border:1px solid var(--rule);background:var(--panel);color:var(--ink);cursor:pointer}
#now{min-width:230px;text-align:right;color:var(--ink2);font-size:12.5px}
main{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:0;min-height:70vh}
@media (max-width:900px){main{grid-template-columns:1fr}}
#graphwrap{border-right:1px solid var(--rule);position:relative;padding:10px}
svg{width:100%;height:100%;min-height:440px;display:block}
.n rect{fill:var(--node);stroke:var(--wire);stroke-width:1.2}.n text{fill:var(--ink);font-size:var(--fs,11px)}
.n .t2{fill:var(--ink3);font-size:calc(var(--fs,11px) * .85)}.n.add rect{stroke:var(--add);stroke-width:2.6}
.n.chg rect{stroke:var(--chg);stroke-width:2.6}.n.byp{opacity:.4}.n.blk rect{fill:color-mix(in srgb,var(--stop) 14%,var(--node))}
.n.gone rect{fill:none;stroke-dasharray:4 3;stroke:var(--ink3)}.n.gone{opacity:.55}
.empty{fill:var(--ink3);font-size:calc(var(--fs,11px) * 1.3)}
.w{fill:none;stroke:var(--wire);stroke-width:1.4}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--ink2);padding:6px 4px}
.legend b{display:inline-block;width:11px;height:11px;border-radius:2px;vertical-align:-1px;margin-right:4px;border:2px solid}
#side{display:flex;flex-direction:column;min-width:0}.tabs{display:flex;gap:4px;padding:10px 14px 0}
.tabs button{font:inherit;font-size:12.5px;padding:6px 12px;border:1px solid var(--rule);border-bottom:0;border-radius:6px 6px 0 0;background:transparent;color:var(--ink2);cursor:pointer}
.tabs button.on{background:var(--panel);color:var(--ink);font-weight:600}
.pane{background:var(--panel);border-top:1px solid var(--rule);padding:14px 18px;overflow:auto;flex:1;max-height:calc(100vh - 160px)}
.day{font:600 11px/1 ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3);margin:14px 0 6px}
.s{margin:0 0 8px;font-family:Georgia,"Times New Roman",serif;font-size:15px;line-height:1.6;transition:opacity .15s}
.s.future{opacity:.28}.s.now{background:color-mix(in srgb,var(--accent) 12%,transparent);border-radius:4px;padding:2px 4px;margin-left:-4px}
.s.flag{border-left:3px solid var(--stop);padding-left:8px}
table{border-collapse:collapse;width:100%;font-size:12.5px}td{padding:6px 6px;border-bottom:1px solid var(--rule);vertical-align:top}
tr.future{opacity:.35}tr.now td{background:color-mix(in srgb,var(--accent) 12%,transparent)}
td.m{font-family:ui-monospace,monospace;white-space:nowrap;color:var(--ink2)}.flagdot{color:var(--stop);font-weight:700}
footer{padding:12px 20px;color:var(--ink3);font-size:11.5px}
</style></head><body>
<header><h1>%TITLE%</h1><span class="stage">%STAGE%</span>%SEAL%<span class="meta">%IDS%</span></header>
<div class="bar"><button id="play" aria-label="Play the life of the shot">&#9654; Play</button>
<button id="prev" aria-label="Previous entry" title="Previous entry (left arrow)">&#8249;</button><button id="next" aria-label="Next entry" title="Next entry (right arrow)">&#8250;</button>
<div class="track"><input id="t" type="range" min="0" value="0" step="1" aria-label="Entry"><div id="ticks"></div></div>
<select id="speed" aria-label="Playback speed"><option value="2600">0.5&times;</option><option value="1600" selected>1&times;</option><option value="800">2&times;</option></select>
<span id="now"></span></div>
<main><div id="graphwrap"><div id="cap" aria-live="polite"></div><svg id="g" role="img" aria-label="The workflow graph at the selected entry"></svg>
<div class="legend"><span><b style="border-color:var(--add)"></b>added here</span><span><b style="border-color:var(--chg)"></b>changed here</span>
<span><b style="border-color:var(--ink3);border-style:dashed"></b>removed here</span><span><b style="border-color:var(--wire);background:color-mix(in srgb,var(--stop) 14%,var(--node))"></b>bars commercial use</span></div></div>
<div id="side"><div class="tabs"><button data-p="story" class="on">Story</button><button data-p="log">Log</button></div>
<div class="pane" id="story"></div><div class="pane" id="log" hidden></div></div></main>
<footer>Generated by comfyaudit %VERSION% from the shot's history. Every sentence and row is one entry in the signed chain; protected prompts stay protected.</footer>
<script>
const D=%DATA%;const R=D.rows,G=D.graphs;const $=s=>document.getElementById(s);const NS="http://www.w3.org/2000/svg";
let b={x0:1e9,y0:1e9,x1:-1e9,y1:-1e9};for(const g of G)for(const n of g.nodes){b.x0=Math.min(b.x0,n.pos[0]);b.y0=Math.min(b.y0,n.pos[1]);b.x1=Math.max(b.x1,n.pos[0]+n.size[0]);b.y1=Math.max(b.y1,n.pos[1]+n.size[1])}
if(b.x0>b.x1)b={x0:0,y0:0,x1:800,y1:400};const pad=40;
/* Labels scale with the graph, so a wide workflow stays readable. */
const FS=Math.max(11,(b.x1-b.x0+2*pad)/85);$("g").style.setProperty("--fs",FS+"px");
$("g").setAttribute("viewBox",`${b.x0-pad} ${b.y0-pad-FS*2} ${b.x1-b.x0+2*pad} ${b.y1-b.y0+2*pad+FS*2}`);
const el=(t,a,p)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);p&&p.append(e);return e};
/* Where each node is on screen now, so a change of layout moves rather than jumps. */
let shown={},cur=-1,anim=0;
function nodesAt(i){const r=R[i],g=G[r.graph];const out=g.nodes.map(n=>({...n}));
if(i>0){const prev=G[R[i-1].graph];for(const id of r.removed){const n=prev.nodes.find(x=>x.id===id);if(n)out.push({...n,gone:1})}}return out}
function paint(i,pos,fade){const r=R[i],g=G[r.graph],svg=$("g");svg.innerHTML="";
if(!g.nodes.length){const t=el("text",{x:b.x0,y:b.y0+FS,class:"empty"},svg);
t.textContent=r.empty?"The canvas was empty at this point.":"The graph's layout was not recorded for this entry (it predates graph capture, or the workflow is in API format).";return}
const nodes=nodesAt(i),by={};for(const n of nodes){const p=pos[n.id];if(p&&!n.gone)n.pos=p;by[n.id]=n}
const ws=el("g",{},svg);
for(const [a,as,c,cs] of g.links){const A=by[a],C=by[c];if(!A||!C)continue;const x1=A.pos[0]+A.size[0],y1=A.pos[1]+14+as*18,x2=C.pos[0],y2=C.pos[1]+14+cs*18,dx=Math.max(40,Math.abs(x2-x1)/2);
el("path",{d:`M${x1} ${y1} C${x1+dx} ${y1},${x2-dx} ${y2},${x2} ${y2}`,class:"w"},ws)}
for(const n of nodes){const c=["n"];if(n.gone)c.push("gone");else{if(r.added.includes(n.id))c.push("add");else if(r.changed.includes(n.id))c.push("chg");if(n.mode===4||n.mode===2)c.push("byp");if(r.blocking.includes(n.id))c.push("blk")}
const gg=el("g",{class:c.join(" ")},svg);if(fade!==undefined&&(n.gone||r.added.includes(n.id)))gg.style.opacity=n.gone?Math.max(.15,.55*(1-fade)):fade;
el("rect",{x:n.pos[0],y:n.pos[1]-FS*2,width:n.size[0],height:n.size[1]+FS*2,rx:6},gg);
const t=el("text",{x:n.pos[0]+8,y:n.pos[1]-FS*.6},gg);t.textContent=(n.title||n.type).slice(0,40);
if(n.title){const t2=el("text",{x:n.pos[0]+8,y:n.pos[1]+FS,class:"t2"},gg);t2.textContent=n.type.slice(0,44)}
const tip=el("title",{},gg);tip.textContent=`${n.type} (node ${n.id})${n.gone?" - removed here":""}`}}
let done=0;function draw(i,move){cancelAnimationFrame(anim);clearTimeout(done);const target={};for(const n of G[R[i].graph].nodes)target[n.id]=n.pos;
const from={...shown};const moving=move&&Object.keys(target).some(id=>from[id]&&(from[id][0]!==target[id][0]||from[id][1]!==target[id][1]));
const fresh=move&&(R[i].added.length||R[i].removed.length);
if(!moving&&!fresh){shown=target;paint(i,target);return}
const t0=performance.now(),dur=Math.min(600,+$("speed").value*.4);
const step=now=>{const k=Math.min(1,(now-t0)/dur),e=k<.5?2*k*k:1-Math.pow(-2*k+2,2)/2,pos={};
for(const id in target){const a=from[id],z=target[id];pos[id]=a?[a[0]+(z[0]-a[0])*e,a[1]+(z[1]-a[1])*e]:z}
paint(i,pos,e);if(k<1)anim=requestAnimationFrame(step);else finish()};
/* Animation frames stop while the page is hidden; the end state must land regardless. */
const finish=()=>{cancelAnimationFrame(anim);clearTimeout(done);shown=target;paint(i,target)};
step(t0);done=setTimeout(finish,dur+80)}
function caption(i){const r=R[i],c=$("cap");c.className=r.flag&&r.flag.status==="blocked"?"blk":"";c.textContent="";
const h=document.createElement("b");h.textContent=`${r.label} · entry ${r.seq} of ${R.length} · ${r.when}`;c.append(h,document.createTextNode(r.story))}
function show(i,move){i=Math.max(0,Math.min(R.length-1,i));$("t").value=i;const r=R[i];$("now").textContent=r.who;
draw(i,move&&cur>=0&&Math.abs(i-cur)===1);caption(i);cur=i;
document.querySelectorAll(".s").forEach(s=>{const k=+s.dataset.i;s.className="s"+(k>i?" future":"")+(k===i?" now":"")+(R[k].flag&&R[k].flag.status==="blocked"?" flag":"")});
document.querySelectorAll("tr[data-i]").forEach(t=>{const k=+t.dataset.i;t.className=k>i?"future":k===i?"now":""});
/* Keep the current line in view by scrolling its own pane only, never the page around it. */
const pane=!$("story").hidden?$("story"):$("log"),now=pane.querySelector(".now");
if(now){const pr=pane.getBoundingClientRect(),nr=now.getBoundingClientRect();
if(nr.top<pr.top||nr.bottom>pr.bottom)pane.scrollTop+=nr.top-pr.top-pane.clientHeight/3}}
let day="";const st=$("story");R.forEach((r,i)=>{if(r.day!==day){day=r.day;const d=document.createElement("div");d.className="day";d.textContent=r.when.split(",")[0];st.append(d)}
const p=document.createElement("p");p.className="s";p.dataset.i=i;p.textContent=r.story;p.onclick=()=>{stop();show(i,true)};st.append(p)});
const tb=document.createElement("table");R.forEach((r,i)=>{const tr=document.createElement("tr");tr.dataset.i=i;tr.onclick=()=>{stop();show(i,true)};
const td=(text,cls)=>{const c=document.createElement("td");if(cls)c.className=cls;if(text!==undefined)c.textContent=text;tr.append(c);return c};
td(r.when,"m");td("#"+r.seq,"m");const what=td();
if(r.flag){const f=document.createElement("span");f.className="flagdot";f.title="commercial standing changed: "+r.flag.status;f.textContent="● ";what.append(f)}
what.append(document.createTextNode(r.label));if(r.note){const em=document.createElement("em");em.textContent=" “"+r.note+"”";what.append(em)}
td(r.who);td(r.signed?"signed":"","m");tb.append(tr)});$("log").append(tb);
/* A tick per entry: red where the shot stopped being usable commercially, blue for renders. */
R.forEach((r,i)=>{const t=document.createElement("i");t.style.left=(R.length>1?i/(R.length-1)*100:0)+"%";
if(r.flag&&r.flag.status==="blocked"){t.className="blk";t.title=`${r.when}: not cleared for commercial use`}else if(r.event==="rendered")t.className="ren";$("ticks").append(t)});
$("t").max=Math.max(0,R.length-1);$("t").oninput=e=>{stop();show(+e.target.value,false)};
document.querySelectorAll(".tabs button").forEach(x=>x.onclick=()=>{document.querySelectorAll(".tabs button").forEach(y=>y.classList.toggle("on",y===x));$("story").hidden=x.dataset.p!=="story";$("log").hidden=x.dataset.p!=="log";show(+$("t").value,false)});
let tm=null;
function stop(){if(tm){clearTimeout(tm);tm=null}$("play").innerHTML="&#9654; Play"}
function tick(){const i=+$("t").value+1;if(i>=R.length){stop();return}show(i,true);tm=setTimeout(tick,+$("speed").value)}
function play(){if(tm){stop();return}if(R.length<2)return;if(+$("t").value>=R.length-1)show(0,false);$("play").innerHTML="&#10074;&#10074; Pause";tm=setTimeout(tick,+$("speed").value)}
$("play").onclick=play;$("prev").onclick=()=>{stop();show(+$("t").value-1,true)};$("next").onclick=()=>{stop();show(+$("t").value+1,true)};
document.addEventListener("keydown",e=>{if(e.target.tagName==="SELECT"||e.target.tagName==="BUTTON"&&e.key===" ")return;
if(e.key===" "){e.preventDefault();play()}else if(e.key==="ArrowRight"&&e.target.id!=="t"){e.preventDefault();stop();show(+$("t").value+1,true)}
else if(e.key==="ArrowLeft"&&e.target.id!=="t"){e.preventDefault();stop();show(+$("t").value-1,true)}
else if(e.key==="Home"){stop();show(0,false)}else if(e.key==="End"){stop();show(R.length-1,false)}});
if(R.length<2){$("play").disabled=true;$("prev").disabled=true;$("next").disabled=true}
show(R.length-1,false);
</script></body></html>"""
