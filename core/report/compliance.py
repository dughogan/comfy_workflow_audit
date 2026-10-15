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

#: Model families named in pending litigation. They raise the case as a risk
#: signal for the shot; the case says nothing yet about the shot itself.
MODEL_FAMILY_RE = {
    "model-stable-diffusion": re.compile(
        r"stable.?diffusion|stability.?ai|sdxl|sd_?xl|\bsd[_-]?(1\.?5|2\.?1|3(\.5)?)(?![0-9])|"
        r"\bv1-5-pruned|pony.?diffusion", re.IGNORECASE),
    "model-midjourney": re.compile(r"midjourney", re.IGNORECASE),
    "model-minimax": re.compile(r"minimax|hailuo", re.IGNORECASE),
}

#: Studio and shot facts the file cannot know, set in settings or per record.
FLAG_FACTS = {
    "union_performers": "union-performers",
    "union_crew": "union-crew",
    "animation": "animation",
    "advertising": "advertising",
    "copyright_claim": "copyright-claim",
    "tpn_vendor": "tpn-vendor",
    "eo_insurance": "eo-insurance",
    "third_party_characters": "third-party-characters",
    "ai_music": "ai-music",
    "intimate_imagery": "intimate-imagery",
}


def client_fact(name: str) -> str:
    """'Netflix' -> 'client-netflix'."""
    return "client-" + re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")

#: Evidence checks the history can answer by itself. Each one is mapped only to
#: evidence items it actually proves (see tools/build_regulations.py); anything
#: it cannot prove - a consent rider, a disclosure in the credits, a marking that
#: survived delivery - is a manual attestation instead.
CHECKS = {
    "external_id": "Linked to the studio's own asset ID",
    "human_breakpoints": "Human decisions recorded between model runs",
    "human_inputs": "Human-made source material recorded with its origin",
    "prompt_authorship": "Each prompt attributed to the person who wrote it",
    "sources_declared": "Origin of every supplied input declared",
    "final_reviewed": "A person reviewed the final output",
    "approved": "Sign-off recorded after the final render",
    "signed": "Record intact and signed with the studio key (verified)",
    "verifiable_process": "Signed record linking prompts, settings and fingerprinted outputs",
    "outputs_fingerprinted": "Delivered outputs fingerprinted",
    "production_stage": "Shot marked as production work, not sandbox",
    "models_recorded": "Every model used is named in the record, with its licence",
}

#: EU member states, so distribution to one of them counts as distribution in the EU.
EU_MEMBERS = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
              "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
              "SI", "ES", "SE"}


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
    for fact, rx in MODEL_FAMILY_RE.items():
        if rx.search(blob):
            out.add(fact)
    for t in [context.get("territory", "")] + list(context.get("distribute_in", [])):
        t = (t or "").strip().lower()
        if t:
            out.add(t)
            if t.startswith("us-"):
                out.add("us")
            if t.upper() in EU_MEMBERS:
                out.add("eu")
            if t in ("youtube", "yt"):
                out.add("youtube")
    for flag in ("feature", "youtube"):
        if context.get(flag):
            out.add(flag)
    if lineage["shot"].get("stage") in ("production", "cleared", "delivered"):
        out.add("production")
    if lineage["sources"]:
        out.add("source-media")
    for flag, fact in FLAG_FACTS.items():
        if context.get(flag):
            out.add(fact)
    for client in context.get("clients", []) or []:
        if str(client).strip():
            out.add(client_fact(str(client)))
    return out


def _check(name: str, lineage: dict[str, Any], history: dict[str, Any],
           verification: dict[str, Any]) -> bool | None:
    assurance = verification.get("assurance", "")
    # "Signed" here means the whole record. A history signed only from some
    # entry on still holds unsigned entries anyone could have written, so it
    # does not prove what those entries claim.
    whole_signed = (assurance in ("signed", "witnessed")
                    and verification.get("signed") == "yes")
    if name == "external_id":
        return bool(lineage["shot"].get("external_ids"))
    if name == "human_breakpoints":
        return lineage["human_steps"] > 0 and not any(
            "followed another render" in g for g in lineage["gaps"])
    if name == "human_inputs":
        return any(s.get("origin") in H.HUMAN_ORIGINS for s in lineage["sources"])
    if name == "prompt_authorship":
        return bool(lineage["prompts"]) and all(
            p.get("authored_by") not in ("", "unknown", "before tracking")
            for p in lineage["prompts"])
    if name == "sources_declared":
        if not lineage["sources"]:
            return None
        return all(s.get("origin") and s["origin"] != "unknown" for s in lineage["sources"])
    if name == "final_reviewed":
        return lineage["final_reviewed"] if lineage["renders"] else None
    if name == "approved":
        return lineage["approved"] is not None
    if name == "signed":
        return whole_signed
    if name == "outputs_fingerprinted":
        if not lineage["renders"]:
            return None
        outs = lineage["final_outputs"]
        return bool(outs) and all(o.get("sha256") for o in outs)
    if name == "verifiable_process":
        if not lineage["renders"]:
            return None
        outs = lineage["final_outputs"]
        return (whole_signed and bool(lineage["prompts"])
                and bool(outs) and all(o.get("sha256") for o in outs))
    if name == "models_recorded":
        return bool(lineage["models"]) if lineage["renders"] else None
    if name == "production_stage":
        return lineage["shot"].get("stage") in ("production", "cleared", "delivered")
    return False


def _attested(rule_id: str, history: dict[str, Any]) -> dict[int, str]:
    """Manual evidence recorded in the chain as notes tagged ``<rule-id>:<index>``.

    The index is required: one note must not tick every item on a rule. The
    note has to say something, and whether it was signed is reported with it.
    """
    got: dict[int, str] = {}
    for e in history.get("entries", []):
        if e.get("event") != "note" or not str(e.get("note", "")).strip():
            continue
        tag = str((e.get("data") or {}).get("evidence", ""))
        rid, _, idx = tag.partition(":")
        if rid != rule_id or not idx.isdigit():
            continue
        who = e.get("actor", {}).get("name", "")
        got[int(idx)] = f"{who}, {e.get('at', '')[:10]}" + ("" if "sig" in e else ", unsigned")
    return got


def assess(lineage: dict[str, Any], history: dict[str, Any],
           context: dict[str, Any]) -> dict[str, Any]:
    kb = load()
    verification = context.get("verification") or {}
    facts = signals(lineage, history, context)
    rules = []
    clarifications = []
    for rule in kb.get("rules", []):
        applies = rule.get("applies", {})
        if applies.get("clarification"):
            clarifications.append({k: rule.get(k, "") for k in (
                "id", "title", "jurisdiction", "authority", "summary", "human_in_loop",
                "source", "source_title", "source_date", "verified_on", "basis", "check_result")})
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
                if i in attested:
                    state, note = "have", f" (attested by {attested[i]})"
                else:
                    state, note = "need", ""
            else:
                ok = _check(check, lineage, history, verification)
                state = "na" if ok is None else ("have" if ok else "need")
                note = ""
            evidence.append({"text": item["text"] + note, "state": state,
                             "tag": f"{rule['id']}:{i}" if check == "manual" else ""})
        rules.append({**{k: rule.get(k, "") for k in (
            "id", "title", "jurisdiction", "authority", "status", "source",
            "source_title", "source_date", "confidence", "notes", "human_in_loop",
            "verified_on", "basis", "check_result", "sources")},
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
                 "A tick means the record holds something that speaks to the item; "
                 "whether it is enough is for counsel. A cross means the record holds "
                 "nothing for it yet. Where a rule turns on a fact the file cannot know - "
                 "where the work is distributed, whether it is a union production - set "
                 "it in the studio settings. This is a checklist to take to counsel, "
                 "not a ruling.")
    return {"intro": intro, "rules": rules, "clarifications": clarifications,
            "corrections": kb.get("corrections", []), "signals": known,
            "as_of": kb.get("as_of", ""), "research": kb.get("research", {})}
