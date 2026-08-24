"""Render the Claude review section of a report.

Takes the plain dict the agent produces rather than the agent's own types, so
the core reporting layer stays independent of the Anthropic integration and a
report loaded back from JSON renders identically to a freshly generated one.

The section is deliberately fenced off and labelled. A reader has to be able to
tell, at a glance, which half of the report is deterministic rule output and
which half is a model's opinion.
"""

from __future__ import annotations

import html as _html
from typing import Any

DISCLAIMER = ("Everything in this section was produced by Claude reading this "
              "workflow, not by a rule. It is a starting point for verification, "
              "not a finding.")


def has_review(report: Any) -> bool:
    review = (getattr(report, "diagnostics", None) or {}).get("claude_review")
    return bool(isinstance(review, dict) and review.get("ran"))


def get_review(report: Any) -> dict[str, Any]:
    return (getattr(report, "diagnostics", None) or {}).get("claude_review") or {}


def _meta(review: dict[str, Any]) -> str:
    usage = review.get("usage") or {}
    bits = [f"mode {review.get('mode', '?')}", str(review.get("model", "?")),
            f"{usage.get('turns', 0)} turns",
            f"{len(review.get('tool_calls') or [])} tool calls"]
    if review.get("web_search_enabled"):
        bits.append("web search on")
    return " · ".join(bits)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def markdown(review: dict[str, Any]) -> str:
    if not review.get("ran"):
        return f"## 7. Claude review\n\n**Not run.** {review.get('error', '')}\n"

    out = ["## 7. Claude review", "", f"*{_meta(review)}*", "", f"> {DISCLAIMER}", ""]

    if review.get("summary"):
        out += [review["summary"], ""]

    identifications = review.get("identifications") or []
    if identifications:
        out += ["### Models identified", "",
                "| Model | Believed to be | Base | Commercial | Confidence |",
                "|---|---|---|---|---|"]
        for i in identifications:
            out.append(f"| `{i.get('filename', '?')}` | {i.get('family', '?')} | "
                       f"{i.get('base_model', '?')} | {i.get('commercial_use', '?')} | "
                       f"{i.get('confidence', '?')} |")
        out.append("")
        for i in identifications:
            link = f" [verify]({i['verify_at']})" if i.get("verify_at") else ""
            out.append(f"- **{i.get('filename', '?')}** — {i.get('reasoning', '')}{link}")
        out.append("")

    risks = review.get("content_risks") or []
    if risks:
        out += ["### Clearance risks in the prompt text", ""]
        for r in risks:
            out += [f"**[{r.get('severity', '?')}] {r.get('kind', 'other')}** — "
                    f"{r.get('where', '?')}", "",
                    f"> {r.get('excerpt', '')}", "",
                    f"{r.get('detail', '')} **{r.get('recommendation', '')}**", ""]

    subs = review.get("substitutions") or []
    if subs:
        out += ["### Proposed substitutions", "",
                "| Replace | With | Licence | Installed | Visual impact |",
                "|---|---|---|---|---|"]
        for s in subs:
            out.append(f"| `{s.get('replace', '?')}` | {s.get('replace_with', '?')} | "
                       f"{s.get('licence', '?')} | "
                       f"{'yes' if s.get('available_locally') else 'no'} | "
                       f"{s.get('quality_impact', '?')} |")
        out.append("")
        for s in subs:
            out.append(f"- **{s.get('replace', '?')}** — {s.get('rationale', '')}")
        out.append("")

    actions = review.get("actions") or []
    if actions:
        out += ["### Remediation plan", ""]
        for a in actions:
            owner = f" *({a['owner']})*" if a.get("owner") else ""
            out.append(f"{a.get('order', '?')}. **{a.get('title', '')}**{owner} — "
                       f"{a.get('detail', '')}")
        out.append("")

    if review.get("error"):
        out += [f"*Ended early: {review['error']}*", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def html(review: dict[str, Any]) -> str:
    def e(text: Any) -> str:
        return _html.escape(str(text if text is not None else ""), quote=True)

    if not review.get("ran"):
        return ("<h2><span class='num'>7</span>Claude review"
                "<span class='count'>not run</span></h2>"
                f"<div class='block flat'><p>{e(review.get('error', ''))}</p></div>")

    out = [f"<h2><span class='num'>7</span>Claude review"
           f"<span class='count'>{e(_meta(review))}</span></h2>",
           f"<div class='block warn'><p style='margin-top:0'>"
           f"<strong>Model-derived.</strong> {e(DISCLAIMER)}</p></div>"]

    if review.get("summary"):
        out.append(f"<div class='block flat'><p>{e(review['summary'])}</p></div>")

    identifications = review.get("identifications") or []
    if identifications:
        rows = "".join(
            f"<tr><td><span class='file'>{e(i.get('filename'))}</span></td>"
            f"<td>{e(i.get('family'))}<span class='sub'>{e(i.get('reasoning', ''))[:180]}</span></td>"
            f"<td>{e(i.get('base_model'))}</td>"
            f"<td><span class='chip "
            f"{ {'no': 'stop', 'conditional': 'warn', 'yes': 'ok'}.get(i.get('commercial_use'), 'warn') }'>"
            f"{e(i.get('commercial_use'))}</span></td>"
            f"<td>{e(i.get('confidence'))}</td></tr>"
            for i in identifications)
        out.append("<h4>Models identified</h4>")
        out.append("<div class='scroll'><table><thead><tr>"
                   "<th>Model</th><th>Believed to be</th><th>Base</th>"
                   "<th>Commercial</th><th>Confidence</th>"
                   f"</tr></thead><tbody>{rows}</tbody></table></div>")

    for risk in review.get("content_risks") or []:
        tone = {"critical": "stop", "high": "stop", "medium": "warn"}.get(
            risk.get("severity"), "ok")
        out.append(
            f"<div class='block {tone}'><div class='block-head'>"
            f"<span class='chip {tone}'>{e(risk.get('severity'))}</span>"
            f"<span class='name'>{e(risk.get('kind'))}</span>"
            f"<span class='tag'>{e(risk.get('where'))}</span></div>"
            f"<pre>{e(risk.get('excerpt'))}</pre><p>{e(risk.get('detail'))}</p>"
            f"<div class='action'><b>What to do</b>{e(risk.get('recommendation'))}</div>"
            "</div>")

    subs = review.get("substitutions") or []
    if subs:
        rows = "".join(
            f"<tr><td><span class='file'>{e(s.get('replace'))}</span></td>"
            f"<td>{e(s.get('replace_with'))}<span class='sub'>{e(s.get('rationale', ''))[:180]}</span></td>"
            f"<td>{e(s.get('licence'))}</td>"
            f"<td>{'yes' if s.get('available_locally') else 'no'}</td>"
            f"<td>{e(s.get('quality_impact'))}</td></tr>"
            for s in subs)
        out.append("<h4>Proposed substitutions</h4>")
        out.append("<div class='scroll'><table><thead><tr>"
                   "<th>Replace</th><th>With</th><th>Licence</th>"
                   "<th>Installed</th><th>Visual impact</th>"
                   f"</tr></thead><tbody>{rows}</tbody></table></div>")

    actions = review.get("actions") or []
    if actions:
        items = "".join(
            f"<li><span><strong>{e(a.get('title'))}</strong>"
            + (f" <em>({e(a.get('owner'))})</em>" if a.get("owner") else "")
            + f" — {e(a.get('detail'))}</span></li>"
            for a in actions)
        out.append(f"<h4>Remediation plan</h4><ol class='priority'>{items}</ol>")

    if review.get("error"):
        out.append(f"<div class='footnote'>Ended early: {e(review['error'])}</div>")
    return "".join(out)
