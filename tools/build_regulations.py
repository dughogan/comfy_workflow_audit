#!/usr/bin/env python3
"""Build core/knowledge/data/regulations.json from the researched source file.

The research file (tools/research/regulations.research.json) is the sourced
record: every rule's obligations, the evidence a studio should keep, the source
it came from and the date of that source. This script adds the two things the
tool needs on top, both decided here by hand rather than guessed:

* ``applies`` - which facts about a shot put the rule in play. Facts come from
  the history (identity or voice nodes, training nodes, LLM nodes, production
  stage, supplied media) and from the studio profile (territories, advertising,
  union/guild production, feature film).
* ``check`` on each evidence item - which of the history's own checks answers
  it, or ``manual`` when only a person can attest it (a signed consent rider,
  a disclosure placed in the credits). Manual items are attested in the chain
  with a note tagged ``<rule-id>:<index>``.

Rerun after refreshing the research:

    python tools/build_regulations.py
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "research", "regulations.research.json")
OUT = os.path.join(HERE, "..", "core", "knowledge", "data", "regulations.json")

LIKENESS = ["likeness", "voice"]

#: rule id -> (applies, {evidence index: check}). Unlisted indices are manual.
MAP: dict[str, tuple[dict, dict[int, str]]] = {
    "sag-aftra-tvt-2023-employment-based-replica": ({"any": LIKENESS, "all": ["union-performers"]}, {2: "external_id"}),
    "sag-aftra-tvt-2023-independent-replica-and-alteration": ({"any": LIKENESS, "all": ["union-performers"]}, {1: "sources_declared"}),
    "sag-aftra-tvt-2023-synthetic-performers": ({"any": ["union-performers"]}, {1: "prompt_authorship"}),
    "sag-aftra-tvt-2026": ({"any": LIKENESS + ["training"], "all": ["union-performers"]}, {}),
    "sag-aftra-commercials-2025": ({"any": LIKENESS, "all": ["advertising"]}, {}),
    "sag-aftra-interactive-media-2025": ({"any": ["voice"], "all": ["union-performers"]}, {}),
    "wga-mba-ai": ({"any": ["llm"]}, {}),
    "dga-ai": ({"any": ["union-performers"]}, {0: "final_reviewed", 1: "approved"}),
    "ampas-99th-oscars-ai-rules": ({"any": ["feature"]}, {0: "human_breakpoints"}),
    "usco-part2-copyrightability": ({"any": ["always"]}, {0: "sources_declared", 1: "human_breakpoints",
                                                          2: "final_reviewed", 3: "prompt_authorship"}),
    "usco-registration-ai-disclosure": ({"any": ["production"]}, {1: "human_breakpoints"}),
    "thaler-v-perlmutter": ({"any": ["production"]}, {0: "prompt_authorship"}),
    "eu-ai-act-art50-2-provider-marking": ({"any": ["eu"]}, {1: "manifest"}),
    "eu-ai-act-art50-4-deployer-deepfake": ({"any": ["eu"]}, {}),
    "eu-code-of-practice-ai-content-transparency": ({"any": ["eu"]}, {1: "manifest"}),
    # Kept in the knowledge base because it is the rule people assume applies.
    "eu-ai-act-art14-human-oversight": ({"clarification": True}, {}),
    "eu-cjeu-originality-standard": ({"any": ["eu"]}, {0: "human_breakpoints", 1: "sources_declared"}),
    "de-ag-munich-ai-logos-2026": ({"any": ["eu"]}, {0: "prompt_authorship", 1: "human_breakpoints"}),
    "cz-prague-municipal-court-ai-image": ({"any": ["eu"]}, {0: "signed"}),
    "de-gema-v-openai": ({"any": ["eu"]}, {0: "outputs_fingerprinted"}),
    "de-kneschke-v-laion": ({"any": ["training"]}, {}),
    "uk-cdpa-s9-3-computer-generated-works": ({"any": ["gb"]}, {0: "prompt_authorship", 1: "human_breakpoints"}),
    "cn-ai-content-labeling-measures": ({"any": ["cn"]}, {0: "manifest"}),
    "cn-li-v-liu-beijing-internet-court": ({"any": ["cn"]}, {0: "signed"}),
    "us-ca-ab2602-digital-replica-contracts": ({"any": LIKENESS, "all": ["us"]}, {2: "external_id"}),
    "us-ca-ab1836-deceased-personality-replicas": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-ca-sb942-ai-transparency-act": ({"any": ["us-ca"]}, {1: "signed"}),
    "us-tn-elvis-act": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-ny-synthetic-performer-ad-disclosure": ({"any": ["us", "us-ny"], "all": ["advertising"]}, {}),
    "us-ny-post-mortem-digital-replica": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-ny-gol-5-302-digital-replica-contracts": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-no-fakes-act": ({"any": LIKENESS, "all": ["us"]}, {}),
    # Concerns nonconsensual intimate imagery only; a studio sets the fact explicitly.
    "us-take-it-down-act": ({"all": ["intimate-imagery"]}, {}),
    "c2pa-content-credentials": ({"any": ["always"]}, {0: "manifest"}),
    "youtube-altered-synthetic-disclosure": ({"any": ["youtube"]}, {}),
    "meta-ai-labels-ads": ({"any": ["advertising"]}, {1: "manifest"}),
    "us-ftc-reviews-testimonials-rule": ({"all": ["advertising", "us"]}, {}),
}


def main() -> int:
    with open(SRC, encoding="utf-8") as fh:
        src = json.load(fh)
    rules = []
    missing = []
    for r in src["rules"]:
        if r["id"] not in MAP:
            missing.append(r["id"])
            continue
        applies, checks = MAP[r["id"]]
        when = r["applies_when"].rstrip(".")
        lead = "Applies"
        if when.lower().startswith("only when "):
            lead, when = "Applies only", when[5:]
        rules.append({
            **{k: r.get(k, "") for k in ("id", "jurisdiction", "authority", "title", "kind",
                                          "status", "effective", "source", "source_title",
                                          "source_date", "confidence", "notes")},
            "summary": f"{lead} {when if when.lower().startswith('when ') else 'when ' + when}.",
            "requires": r["requires"],
            "human_in_loop": r.get("human_in_loop", ""),
            "applies": applies,
            "evidence": [{"text": text, "check": checks.get(i, "manual")}
                         for i, text in enumerate(r["evidence"])],
        })
    if missing:
        raise SystemExit(f"no mapping for: {', '.join(missing)}")
    out = {
        "_comment": [
            "Regulatory knowledge base for comfyaudit shot history.",
            "Built by tools/build_regulations.py from tools/research/regulations.research.json.",
            "Data, not law: every rule carries its source and that source's date. Re-verify",
            "anything that gates a delivery; union terms and EU guidance changed in 2026.",
        ],
        "as_of": src["as_of"],
        "corrections": src.get("corrections", []),
        "not_found": src.get("not_found", []),
        "rules": rules,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {OUT}: {len(rules)} rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
