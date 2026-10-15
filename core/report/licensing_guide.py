"""A plain-language guide to commercial model licensing, built from the knowledge base.

Production teams keep asking the same three things: *which models can we put in
final pixels, what do the "it depends" ones depend on, and how do we actually
get a commercial licence when we need one?* The answers already live in
``licences.json`` - commercial position, fee shape, licence holder, territory
and revenue conditions, a source for each - so this renders them as a document
rather than hand-writing one that drifts out of date the week a licence moves.

Every tier, every condition and every link below is read from the same file the
auditor uses, and the page states the date that file was checked.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .. import __version__
from ..knowledge import freshness as freshness_mod
from ..knowledge import licences as licences_mod
from ..score.clearance import TERRITORIES
from .html import CSS, FONTS, _e

TIERS = [
    ("yes", "ok", "Commercial use permitted",
     "Free to use in commercial work, including final pixels. Usually the only duty "
     "is to keep a copyright or licence notice with anything you redistribute."),
    ("conditional", "warn", "Permitted, with conditions",
     "Commercial use is allowed for some studios and not others. The conditions are "
     "facts about you - where you are, what you earn, how many users a product has - "
     "so the same file can be fine for one facility and off-limits for another."),
    ("no", "stop", "Not without a separate licence",
     "The published licence does not allow commercial use. Several of these are "
     "written to cover the outputs, not just the weights, so a frame made with them "
     "carries the restriction. A commercial licence may be available from the holder."),
]

GUIDE_CSS = """
.steps{counter-reset:s;list-style:none;padding:0;margin:14px 0 26px}
.steps>li{counter-increment:s;position:relative;padding:14px 18px 14px 64px;margin:10px 0;
  background:var(--surface);border:1px solid var(--rule);border-radius:3px}
.steps>li::before{content:counter(s);position:absolute;left:18px;top:12px;
  font-family:var(--display);font-weight:900;font-size:28px;color:var(--accent);line-height:1}
.steps h4{margin:0 0 4px;font-family:var(--display);font-size:17px}
.steps p{margin:0;font-size:15px;color:var(--ink-2);max-width:72ch}
.steps ul{margin:8px 0 0;padding-left:18px;font-size:14.5px}
.concepts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin:14px 0 24px}
.concepts div{background:var(--surface);border:1px solid var(--rule);border-radius:3px;padding:14px 16px}
.concepts h4{margin:0 0 6px;font-family:var(--display);font-size:15.5px}
.concepts p{margin:0;font-size:14.5px;color:var(--ink-2)}
.tier{border-left:5px solid var(--rule-2);padding:4px 0 4px 18px;margin:28px 0 8px}
.tier.ok{border-color:var(--ok)} .tier.warn{border-color:var(--warn)} .tier.stop{border-color:var(--stop)}
.tier h3{margin:0;font-family:var(--display);font-weight:900;font-size:21px}
.tier.ok h3{color:var(--ok)} .tier.warn h3{color:var(--warn)} .tier.stop h3{color:var(--stop)}
.tier p{margin:4px 0 0;color:var(--ink-2);font-size:15px;max-width:74ch}
table.lic{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0 10px}
table.lic th{text-align:left;font-family:var(--mono);font-weight:500;font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-3);border-bottom:1px solid var(--rule-2);padding:8px 10px 6px}
table.lic td{border-bottom:1px solid var(--rule);padding:10px;vertical-align:top}
table.lic td.fam{font-weight:600;max-width:240px}
table.lic ul{margin:0;padding-left:16px}
table.lic .src{font-family:var(--mono);font-size:11.5px}
h2.sec{font-family:var(--display);font-weight:900;font-size:26px;letter-spacing:-.01em;
  margin:46px 0 6px;padding-top:18px;border-top:1px solid var(--rule-2)}
.intro{color:var(--ink-2);max-width:74ch;margin:0 0 10px;font-size:15.5px}
"""

CONCEPTS = [
    ("The licence can reach the pixels",
     "Most licences govern the weights: copying, modifying, redistributing them. Some "
     "also address what they make, and not always consistently. The FLUX.1 [dev] text "
     "lets you use outputs commercially in one clause and bars production use of "
     "anything the model produces in another, so treat its outputs as needing a "
     "licence until Black Forest Labs or counsel says otherwise. The CC BY-NC "
     "upscalers and CodeFormer are non-commercial and how far that reaches into "
     "outputs is unsettled: a risk to clear, not a safe default."),
    ("Every model in the graph counts",
     "Clearance is decided by the most restrictive weight that touched the frame, "
     "not by the hero checkpoint. Upscalers, face detectors, background removers and "
     "depth models are where non-commercial terms usually hide."),
    ("Fine-tunes inherit their base",
     "A LoRA or merge cannot grant more than the model it was trained from. A LoRA on "
     "FLUX.1 [dev] marked 'commercial OK' by its uploader is still non-commercial."),
    ("'Conditional' means: it depends on you",
     "Territory carve-outs (MiniMax, Hunyuan, Kolors), revenue caps (Stability $1M, "
     "MiniMax $20M), user caps (Llama) and visible-attribution duties. The open "
     "licence does not reach an excluded territory at all - working there takes a "
     "separate agreement with the holder, if one is offered. A revenue cap is a "
     "budget line, not a blocker."),
    ("Hosted models run on someone else's terms",
     "API nodes are governed by the provider's terms of service, not a model licence. "
     "Your inputs leave your network, terms can change without a new version number, "
     "and output ownership and indemnity vary by provider and plan."),
    ("A commercial licence is not indemnity",
     "Being allowed to use a model is separate from the rights holder standing behind "
     "it if a claim arrives about what it was trained on. Feature and broadcast legal "
     "teams ask for both; ask for indemnity and warranties explicitly."),
    ("Code licences rarely touch frames",
     "Custom node packs are software. GPL and AGPL obligations bite when you "
     "distribute the software - a product, a plug-in, a hosted tool - not when you "
     "deliver images. Ultralytics' published position on internal use is the exception "
     "worth reading."),
]

STEPS = [
    ("Audit the workflow",
     "Run ComfyAudit on the graph that will make final pixels. It lists every weight - "
     "including the ones nobody remembers adding - with its licence, a source link "
     "and how confident the match is.",
     []),
    ("Sort every model into the three tiers below",
     "Permitted models need nothing more than their notice kept. Conditional ones need "
     "your facts applied: fill in the studio profile (territory, revenue, what ships) "
     "and the audit works out which conditions you meet.",
     []),
    ("Swap what you can",
     "Most blockers have a permissive equivalent: an Apache upscaler for a "
     "non-commercial one, FLUX.1 [schnell] or Qwen-Image for FLUX.1 [dev], a "
     "permissive matte model for BRIA RMBG. Re-audit after each swap.",
     []),
    ("License what you can't swap",
     "Contact the licence holder the audit names. Ask, in writing, for:",
     ["commercial use of outputs in the media you deliver (broadcast, theatrical, "
      "streaming, advertising, games)",
      "the territories you render in and distribute to",
      "the term, and what happens to delivered work if it ends",
      "the right to sublicense to your client and your vendors, so the deliverable "
      "is usable downstream",
      "any attribution, usage reporting or content-filtering duties",
      "indemnification and warranties about training data and third-party claims",
      "the fee basis: flat, per-seat, revenue share or usage-metered"]),
    ("Record the decision",
     "Add it to the studio registry with the contract reference "
     "(<code>comfyaudit registry add studio-cleared.json shot.json --reference "
     "CONTRACT-123</code>). Future audits stop re-raising it - and reopen it "
     "automatically if the file's hash or the licence it reads as changes.",
     []),
    ("Keep the shot history running",
     "Turn on shot history so the workflow carries who built it, which models and "
     "prompts produced the delivered frames, where people made the decisions, and "
     "who signed off. Export the provenance record for legal with the delivery.",
     []),
    ("Re-check at delivery",
     "Licences move - Stability relicensed SD3 after release - and some are "
     "internally inconsistent. Refresh the knowledge base, confirm anything that "
     "gates delivery at its source, and re-audit before final delivery, not only at "
     "the start of the show.",
     []),
]


def _conditions(lic: dict[str, Any]) -> list[str]:
    c = lic.get("conditions", {}) or {}
    out = []
    if c.get("territory_excluded"):
        places = ", ".join(TERRITORIES.get(t, t).replace("the ", "") for t in c["territory_excluded"])
        out.append(f"Not licensed in: {places}")
    if c.get("revenue_cap_usd"):
        out.append(f"Free below USD ${c['revenue_cap_usd'] / 1e6:g}M "
                   f"{c.get('revenue_basis', 'annual revenue')}; above it, an agreement")
    if c.get("monthly_active_users_cap"):
        out.append(f"Cap of {c['monthly_active_users_cap']:,} monthly active users")
    if c.get("attribution_visible"):
        out.append(f"Must visibly credit “{c['attribution_visible']}”")
    if c.get("no_competing_training"):
        out.append("Outputs may not train competing models")
    if c.get("outputs_commercial") == "no":
        out.append("Restriction covers outputs, not only weights")
    if c.get("copyleft_reach"):
        out.append("Copyleft: obligations attach when you distribute")
    if c.get("hosted"):
        out.append("Runs on the provider's servers; their terms of service apply")
    if c.get("prohibited_uses"):
        out.append("Use-based restrictions (acceptable-use list)")
    if not out:
        out = [r for r in lic.get("restrictions", [])[:3]]
    return out


def render(kb_path: str | None = None, standalone: bool = True) -> str:
    kb = licences_mod.load_kb(kb_path)
    meta = licences_mod.kb_metadata(kb_path)
    fresh = freshness_mod.assess(meta)
    families: dict[str, list[str]] = {}
    for m in kb.get("models", []):
        families.setdefault(m["licence"], []).append(m["family"])

    out: list[str] = []
    w = out.append
    w("<div class='sheet'>")
    w("<header class='masthead'><div class='title-block'>")
    w("<div class='eyebrow'>Guide for production &amp; legal</div>")
    w("<h1>Which models can make final pixels</h1><dl class='slate'>")
    w(f"<dt>Knowledge</dt><dd>{len(kb.get('licences', {}))} licences, "
      f"{len(kb.get('models', []))} model families, checked {_e(kb.get('checked', '?'))}</dd>")
    w(f"<dt>Prepared</dt><dd>{_dt.datetime.now().astimezone().strftime('%Y-%m-%d')} "
      f"&middot; comfyaudit {__version__}</dd></dl></div></header>")
    if fresh.state in ("old", "unknown"):
        w(f"<p class='verdict-note warn'><span><strong>Check at source.</strong> "
          f"{_e(fresh.message)}</span></p>")

    w("<h2 class='sec'>How it works</h2>")
    w("<p class='intro'>Seven ideas cover nearly every licensing question a production "
      "runs into. Read these once; the tables below apply them.</p>")
    w("<div class='concepts'>")
    for title, text in CONCEPTS:
        w(f"<div><h4>{_e(title)}</h4><p>{_e(text)}</p></div>")
    w("</div>")

    w("<h2 class='sec'>Getting to commercial use, step by step</h2>")
    w("<ol class='steps'>")
    for title, text, bullets in STEPS:
        w(f"<li><h4>{_e(title)}</h4><p>{text}</p>")
        if bullets:
            w("<ul>" + "".join(f"<li>{_e(b)}</li>" for b in bullets) + "</ul>")
        w("</li>")
    w("</ol>")

    w("<h2 class='sec'>Model licences at a glance</h2>")
    w("<p class='intro'>Grouped by what the published terms allow. Model families are "
      "matched from filenames, which anyone can change, so confirm anything that "
      "gates a delivery against the source linked on its row.</p>")
    for key, tone, title, blurb in TIERS:
        rows = [(lid, lic) for lid, lic in kb.get("licences", {}).items()
                if lic.get("commercial_use") == key and lid != "unknown"]
        if not rows:
            continue
        rows.sort(key=lambda r: (-len(families.get(r[0], [])), r[1]["name"]))
        w(f"<div class='tier {tone}'><h3>{_e(title)}</h3><p>{_e(blurb)}</p></div>")
        w("<table class='lic'><thead><tr><th>Models</th><th>Licence</th><th>Terms that matter</th>"
          "<th>Commercial licence from</th></tr></thead><tbody>")
        for lid, lic in rows:
            fam = families.get(lid, [])
            fam_cell = (", ".join(_e(f) for f in fam[:8])
                        + (f" and {len(fam) - 8} more" if len(fam) > 8 else "")) if fam else \
                "<span style='color:var(--ink-3)'>any model under this licence</span>"
            if key == "yes":
                conds = (["Keep the licence and copyright notice"]
                         if lic.get("attribution_required") else [])
                if (lic.get("conditions") or {}).get("prohibited_uses"):
                    conds.append("Use-based restrictions apply (acceptable-use list)")
            else:
                conds = _conditions(lic)
            holder = (lic.get("conditions") or {}).get("separate_licence_holder", "")
            if key == "yes":
                holder = "not needed"
            src = (f"<div class='src'><a href='{_e(lic['url'])}'>source</a></div>"
                   if lic.get("url") else "")
            w(f"<tr><td class='fam'>{fam_cell}</td><td>{_e(lic['name'])}{src}</td>"
              f"<td><ul>{''.join(f'<li>{_e(c)}</li>' for c in conds)}</ul></td>"
              f"<td>{_e(holder or '-')}</td></tr>")
        w("</tbody></table>")

    w("<footer style='margin-top:40px;padding-top:16px;border-top:1px solid var(--rule);"
      "font-size:13px;color:var(--ink-3);max-width:80ch'><p>Generated from the ComfyAudit "
      "licence knowledge base. Every entry links the licence it summarises; summaries "
      "are a reading of published terms, not legal advice, and licences change. Studios "
      "can extend or override the knowledge base with their own entries and negotiated "
      "terms.</p></footer></div>")
    body = "\n".join(out)
    style = f"<style>{CSS}{GUIDE_CSS}</style>"
    if not standalone:
        return f"<link rel='stylesheet' href='{FONTS}'>\n{style}\n{body}"
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Commercial model licensing guide</title>"
            "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
            f"<link rel='stylesheet' href='{FONTS}'>{style}</head><body>{body}</body></html>")
