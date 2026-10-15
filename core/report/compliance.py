"""Which published rules a shot plausibly has to meet, and what the record shows.

This does not decide compliance. It does two narrower things a legal team can
check line by line:

1. **Which rules are in play** - from facts the workflow and its history reveal
   (identity or voice work, where the studio operates and distributes, whether
   the shot has crossed into production) and facts the studio supplies.
2. **What evidence the history already holds** for each rule's record-keeping
   expectations - human break points, declared sources, a recorded review of
   the final output, a sign-off, a provenance manifest - and what is missing.

Every rule comes from ``knowledge/data/regulations.json``, and every entry there
carries its source and the date of that source, because these rules move: the
EU marking obligations, union AI terms and US state likeness laws all changed
within the last two years. Evidence a person attests to (performer consent was
obtained, a disclosure was made) is recorded as a ``note`` entry tagged with the
rule id, so it lands in the signed chain rather than in an email.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .. import history as H
from ..score.clearance import IDENTITY_RE

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge", "data",
                    "regulations.json")

VOICE_RE = re.compile(r"\btts\b|text.?to.?speech|voice|rvc|xtts|f5.?tts|elevenlabs|"
                      r"speech|vocal|singing|lip.?sync|audio.?to.?face", re.IGNORECASE)

LLM_RE = re.compile(r"ollama|\bllm\b|llm_|chatgpt|openai.?chat|claude|gemini.?text|"
                    r"textgen|text.?generat|qwen.?\d*.?(chat|instruct)|llama", re.IGNORECASE)
TRAINING_RE = re.compile(r"train|fine.?tune|dataset|kohya|musubi|lora.?trainer|dreambooth",
                         re.IGNORECASE)

#: Evidence checks the history can answer by itself.
CHECKS = {
    "external_id": "Linked to the studio's own asset ID",
    "human_breakpoints": "Human decisions recorded between model runs",
    "prompt_authorship": "Each prompt attributed to the person who wrote it",
    "sources_declared": "Origin of every supplied input declared",
    "final_reviewed": "A person reviewed the final output",
    "approved": "Sign-off recorded",
    "signed": "Record signed with the studio key",
    "outputs_fingerprinted": "Delivered outputs fingerprinted",
    "manifest": "Content-provenance manifest can be exported",
    "production_stage": "Shot marked as production work, not sandbox",
}


def load() -> dict[str, Any]:
    try:
        with open(DATA, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"as_of": "", "rules": []}


def signals(lineage: dict[str, Any], history: dict[str, Any],
            context: dict[str, Any]) -> set[str]:
    """Facts that decide which rules apply."""
    out = {"always"}
    entries = history.get("entries", [])
    names: list[str] = list(context.get("node_types", []))
    for e in entries:
        snap = H.snapshot_of(entries, e)
        nodes = snap.get("nodes", {})
        names += nodes.get("core", []) + nodes.get("api", [])
        for pack in nodes.get("custom", []):
            names += pack.get("nodes", [])
        names += [m["filename"] for m in snap.get("models", [])]
    blob = " ".join(names)
    if context.get("likeness") or IDENTITY_RE.search(blob):
        out.add("likeness")
    if context.get("voice") or VOICE_RE.search(blob):
        out.add("voice")
    if LLM_RE.search(blob):
        out.add("llm")
    if TRAINING_RE.search(blob):
        out.add("training")
    for t in [context.get("territory", "")] + list(context.get("distribute_in", [])):
        t = (t or "").strip().lower()
        if t:
            out.add(t)
            if t.startswith("us-"):
                out.add("us")
            if t in ("youtube", "yt"):
                out.add("youtube")
    for flag in ("feature", "youtube"):
        if context.get(flag):
            out.add(flag)
    if lineage["shot"].get("stage") in ("production", "cleared", "delivered"):
        out.add("production")
    if lineage["sources"]:
        out.add("source-media")
    if context.get("union_performers"):
        out.add("union-performers")
    if context.get("advertising"):
        out.add("advertising")
    return out


def _check(name: str, lineage: dict[str, Any], history: dict[str, Any]) -> bool | None:
    entries = history.get("entries", [])
    if name == "external_id":
        return bool(lineage["shot"].get("external_ids"))
    if name == "human_breakpoints":
        return lineage["human_steps"] > 0 and not any(
            "followed another render" in g for g in lineage["gaps"])
    if name == "prompt_authorship":
        return bool(lineage["prompts"]) and all(p.get("authored_by") not in ("", "unknown")
                                                 for p in lineage["prompts"])
    if name == "sources_declared":
        if not lineage["sources"]:
            return None
        return all(s.get("origin") for s in lineage["sources"])
    if name == "final_reviewed":
        return lineage["final_reviewed"] if lineage["renders"] else None
    if name == "approved":
        return lineage["approved"] is not None
    if name == "signed":
        return bool(entries) and all("sig" in e for e in entries)
    if name == "outputs_fingerprinted":
        outs = lineage["final_outputs"]
        if not lineage["renders"]:
            return None
        return bool(outs) and all(o.get("sha256") for o in outs)
    if name == "manifest":
        return bool(entries)
    if name == "production_stage":
        return lineage["shot"].get("stage") in ("production", "cleared", "delivered")
    return False


def _attested(rule_id: str, history: dict[str, Any]) -> dict[int, str]:
    """Manual evidence recorded in the chain as notes tagged with this rule."""
    got: dict[int, str] = {}
    for e in history.get("entries", []):
        tag = str((e.get("data") or {}).get("evidence", ""))
        if not tag:
            continue
        rid, _, idx = tag.partition(":")
        if rid != rule_id:
            continue
        who = e["actor"].get("name", "")
        when = e["at"][:10]
        if idx.isdigit():
            got[int(idx)] = f"{who}, {when}"
        else:
            got[-1] = f"{who}, {when}"
    return got


def assess(lineage: dict[str, Any], history: dict[str, Any],
           context: dict[str, Any]) -> dict[str, Any]:
    kb = load()
    facts = signals(lineage, history, context)
    rules = []
    clarifications = []
    for rule in kb.get("rules", []):
        applies = rule.get("applies", {})
        if applies.get("clarification"):
            clarifications.append({k: rule.get(k, "") for k in (
                "id", "title", "jurisdiction", "authority", "summary", "human_in_loop",
                "source", "source_title", "source_date")})
            continue
        any_of = set(applies.get("any", []))
        all_of = set(applies.get("all", []))
        if any_of and not (any_of & facts):
            continue
        if all_of and not all_of <= facts:
            continue
        attested = _attested(rule["id"], history)
        evidence = []
        for i, item in enumerate(rule.get("evidence", [])):
            check = item.get("check", "manual")
            if check == "manual":
                if i in attested or -1 in attested:
                    state, note = "have", f" (attested by {attested.get(i, attested.get(-1))})"
                else:
                    state, note = "need", ""
            else:
                ok = _check(check, lineage, history)
                state = "na" if ok is None else ("have" if ok else "need")
                note = ""
            evidence.append({"text": item["text"] + note, "state": state,
                             "tag": f"{rule['id']}:{i}" if check == "manual" else ""})
        rules.append({**{k: rule.get(k, "") for k in (
            "id", "title", "jurisdiction", "authority", "status", "source",
            "source_title", "source_date", "confidence", "notes", "human_in_loop")},
            "why": rule.get("summary", ""), "requires": rule.get("requires", []),
            "evidence": evidence})

    order = {"Global": 0, "Industry": 1, "US": 2, "EU": 3}
    rules.sort(key=lambda r: (order.get(r["jurisdiction"], 5), r["jurisdiction"], r["title"]))
    known = sorted(f for f in facts if f != "always")
    if not kb.get("rules"):
        intro = "No regulatory knowledge base is installed, so no rules were checked."
    else:
        intro = (f"Selected from {len(kb['rules'])} published rules and agreements "
                 f"(knowledge as of {kb.get('as_of') or 'an unknown date'}) using what "
                 f"this record shows: {', '.join(known) or 'no special signals'}. "
                 "Green items the record already evidences; red items it does not yet. "
                 "Where a rule turns on a fact the file cannot know - where the work is "
                 "distributed, whether performers are union members - set it in the "
                 "studio profile. This is a checklist to take to counsel, not a ruling.")
    return {"intro": intro, "rules": rules, "clarifications": clarifications,
            "corrections": kb.get("corrections", []), "signals": known,
            "as_of": kb.get("as_of", "")}
