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
    "sag-aftra-tvt-2023-employment-based-replica": ({"any": LIKENESS, "all": ["union-performers"]}, {}),
    "sag-aftra-tvt-2023-independent-replica-and-alteration": ({"any": LIKENESS, "all": ["union-performers"]}, {1: "sources_declared"}),
    "sag-aftra-tvt-2023-synthetic-performers": ({"any": ["union-performers"]}, {}),
    "sag-aftra-tvt-2026": ({"any": LIKENESS + ["training"], "all": ["union-performers"]}, {}),
    "sag-aftra-commercials-2025": ({"any": LIKENESS, "all": ["advertising"]}, {}),
    "sag-aftra-interactive-media-2025": ({"any": ["voice"], "all": ["union-performers"]}, {}),
    "wga-mba-ai": ({"any": ["llm"]}, {}),
    # A reviewer can be anyone; "the director was consulted" is attested, not inferred.
    "dga-ai": ({"any": ["union-performers"]}, {1: "approved"}),
    "ampas-99th-oscars-ai-rules": ({"any": ["feature"]}, {0: "human_breakpoints", 2: "models_recorded"}),
    # Human edits after generation (comp, paint, roto) mostly happen outside
    # ComfyUI, so item 1 is attested; a seed or parameter tweak is not one.
    "usco-part2-copyrightability": ({"any": ["always"]}, {0: "human_inputs", 2: "final_reviewed",
                                                          3: "prompt_authorship"}),
    "usco-registration-ai-disclosure": ({"any": ["production"]}, {1: "human_breakpoints"}),
    "thaler-v-perlmutter": ({"any": ["production"]}, {0: "prompt_authorship"}),
    # The tool exports an unsigned manifest definition; whether marking was
    # applied and survived delivery is attested.
    "eu-ai-act-art50-2-provider-marking": ({"any": ["eu"]}, {1: "final_deliverable"}),
    "eu-ai-act-art50-4-deployer-deepfake": ({"any": ["eu"]}, {}),
    "eu-code-of-practice-ai-content-transparency": ({"any": ["eu"]}, {}),
    # Kept in the knowledge base because it is the rule people assume applies.
    "eu-ai-act-art14-human-oversight": ({"clarification": True}, {}),
    # Item 0 asks for creative choices visible in the frame (paintovers, layout),
    # which the history cannot see; only the decision record is answered.
    "eu-cjeu-originality-standard": ({"any": ["eu"]}, {2: "human_breakpoints"}),
    # The ruling itself says selection and parameter iteration are not enough,
    # so "human edits beyond selection" is attested, never inferred from them.
    "de-ag-munich-ai-logos-2026": ({"any": ["eu"]}, {}),
    "cz-prague-municipal-court-ai-image": ({"any": ["eu"]}, {0: "verifiable_process"}),
    # Item 0 is the model per shot; the similarity check (1) is attested.
    "de-gema-v-openai": ({"any": ["eu"]}, {0: "models_recorded"}),
    "de-kneschke-v-laion": ({"any": ["training"]}, {}),
    "uk-cdpa-s9-3-computer-generated-works": ({"any": ["gb"]}, {0: "prompt_authorship", 1: "human_breakpoints"}),
    "cn-ai-content-labeling-measures": ({"any": ["cn"]}, {}),
    "cn-li-v-liu-beijing-internet-court": ({"any": ["cn"]}, {0: "verifiable_process", 1: "models_recorded"}),
    "us-ca-ab2602-digital-replica-contracts": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-ca-ab1836-deceased-personality-replicas": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-ca-sb942-ai-transparency-act": ({"any": ["us-ca"]}, {2: "models_recorded"}),
    "us-tn-elvis-act": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-ny-synthetic-performer-ad-disclosure": ({"any": ["us", "us-ny"], "all": ["advertising"]}, {}),
    "us-ny-post-mortem-digital-replica": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-ny-gol-5-302-digital-replica-contracts": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-no-fakes-act": ({"any": LIKENESS, "all": ["us"]}, {}),
    # Concerns nonconsensual intimate imagery only; a studio sets the fact explicitly.
    "us-take-it-down-act": ({"all": ["intimate-imagery"]}, {}),
    # Signed at delivery by the studio's c2patool, and recorded in the history
    # with the signer; the ai-disclosure assertion (1) is not written yet.
    "c2pa-content-credentials": ({"any": ["always"]}, {0: "content_credentials",
                                                        2: "content_credentials"}),
    "youtube-altered-synthetic-disclosure": ({"any": ["youtube"]}, {}),
    "meta-ai-labels-ads": ({"any": ["advertising"]}, {1: "content_credentials"}),
    "us-ftc-reviews-testimonials-rule": ({"all": ["advertising", "us"]}, {}),

    # -- added 2026-10-15 ------------------------------------------------------
    # Unions and guilds.
    "iatse-basic-agreement-2024-ai": ({"any": ["union-crew"]}, {}),
    "tag-839-2024-ai": ({"any": ["animation"], "all": ["union-crew"]}, {}),
    "actra-ipa-2025-ai": ({"all": ["union-performers", "ca"]}, {}),
    "ubcp-actra-bcmpa-2025-ai": ({"all": ["union-performers", "ca"]}, {}),
    # Copyright and litigation. Cases about a model family are risk signals
    # raised by the models in the record, not verdicts on the shot.
    "allen-v-perlmutter": ({"any": ["us", "copyright-claim"]}, {0: "verifiable_process"}),
    "usco-part3-generative-ai-training": ({"any": ["training"], "all": ["us"]}, {}),
    "getty-v-stability-uk": ({"any": ["model-stable-diffusion"], "all": ["gb"]}, {0: "models_recorded"}),
    "getty-v-stability-us": ({"any": ["model-stable-diffusion"], "all": ["us"]}, {0: "models_recorded"}),
    "studios-v-midjourney": ({"any": ["model-midjourney"]}, {2: "models_recorded"}),
    "studios-v-minimax-hailuo": ({"any": ["model-minimax"]}, {0: "models_recorded"}),
    "andersen-v-stability": ({"any": ["model-stable-diffusion", "model-midjourney"]}, {0: "models_recorded"}),
    "bartz-v-anthropic": ({"all": ["training", "llm"]}, {}),
    "kadrey-v-meta": ({"all": ["training", "llm"]}, {0: "models_recorded"}),
    "thomson-reuters-v-ross-3d-cir": ({"all": ["training", "us"]}, {}),
    "cox-v-sony-contributory-liability": ({"all": ["us", "third-party-characters"]}, {}),
    "cn-zhangjiagang-butterfly-chairs": ({"any": ["cn"]}, {0: "verifiable_process"}),
    "cn-changshu-half-heart": ({"any": ["cn"]}, {0: "verifiable_process"}),
    "cn-spc-ai-dispute-opinions-2026": ({"any": ["cn"]}, {}),
    "ca-cippic-v-sahni": ({"any": ["ca"]}, {0: "prompt_authorship"}),
    "jp-chiba-ai-image-referral": ({"any": ["jp"]}, {}),
    # EU and member states.
    "eu-commission-art50-guidelines-2026": ({"any": ["eu"]}, {}),
    "eu-ai-act-art53-gpai-provider-obligations": ({"any": ["eu"]}, {}),
    "eu-gpai-code-of-practice": ({"any": ["eu"]}, {}),
    "eu-ai-act-art5-ncii-prohibition": ({"all": ["eu", "intimate-imagery"]}, {}),
    "it-law-132-2025-copyright-ai-assisted-works": ({"any": ["it"]}, {0: "human_breakpoints"}),
    "it-penal-612-quater-ai-deepfake": ({"any": LIKENESS, "all": ["it"]}, {}),
    "fr-penal-226-8-ai-montage-consent": ({"any": LIKENESS, "all": ["fr"]}, {}),
    "dk-l49-likeness-imitation-bill": ({"any": LIKENESS, "all": ["dk"]}, {}),
    "de-olg-duesseldorf-ai-comic-2026": ({"any": ["de"]}, {}),
    "de-gema-v-suno-2026": ({"any": ["ai-music"]}, {0: "models_recorded"}),
    "fr-ppl-presumption-ai-use-cultural-content": ({"all": ["fr", "training"]}, {}),
    # Studio, platform and insurance. The audit-log item is what a signed,
    # fingerprinted history is; the approvals and inventories are attested.
    # Item 3 (GenAI pixels in the final deliverable) is what a delivered shot
    # with fingerprinted outputs is; the data terms in item 2 are not recorded.
    "netflix-genai-production-partner-guidance": ({"any": ["client-netflix"]}, {3: "final_deliverable"}),
    "mpa-tpn-content-security-ai-controls": ({"any": ["tpn-vendor"]}, {2: "verifiable_process",
                                                                       6: "content_credentials"}),
    "uk-asa-cap-ai-in-ads": ({"all": ["advertising", "gb"]}, {}),
    "eo-insurance-ai-underwriting": ({"any": ["eo-insurance"]}, {0: "models_recorded"}),
    # US states and Asia-Pacific.
    "us-ca-sb1050-synthetic-performer-ad-disclosure": ({"any": ["us", "us-ca"], "all": ["advertising"]}, {}),
    "us-ca-civ3344-digital-replicas": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-il-right-of-publicity-digital-replicas": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-il-digital-voice-likeness-contracts": ({"any": LIKENESS, "all": ["us"]}, {}),
    "us-wa-forged-digital-likeness": ({"any": LIKENESS, "all": ["us"]}, {}),
    "ca-copyright-human-authorship": ({"any": ["ca"]}, {0: "human_breakpoints", 1: "prompt_authorship"}),
    "ca-criminal-code-sexual-deepfakes": ({"all": ["ca", "intimate-imagery"]}, {}),
    "jp-copyright-art30-4-training": ({"all": ["jp", "training"]}, {}),
    "jp-ai-output-infringement-copyrightability": ({"any": ["jp"]}, {1: "human_breakpoints"}),
    "jp-ai-promotion-act": ({"any": ["jp"]}, {}),
    "jp-tsuda-voice-publicity-ruling": ({"any": ["voice"], "all": ["jp"]}, {}),
    "kr-ai-basic-act-art31-transparency": ({"any": ["kr"]}, {}),
    "kr-copyright-registration-genai-guide": ({"any": ["kr"]}, {1: "verifiable_process"}),
}


#: rule id -> {evidence index: "show" | "studio"} for manual items answered once
#: rather than per shot. A show answer (a client's written approval of the AI
#: tools for the project, a union notice) is recorded once per production and
#: every shot with that project inherits it; a studio answer (the company AI
#: policy, provider-or-deployer status) once for every production. Unlisted
#: manual items are per shot: consents, per-deliverable flags, similarity
#: checks and edits made outside ComfyUI belong to the shot itself.
SCOPE: dict[str, dict[int, str]] = {
    "sag-aftra-tvt-2023-synthetic-performers": {0: "show"},
    "sag-aftra-tvt-2026": {6: "show", 7: "show", 8: "show"},
    "sag-aftra-commercials-2025": {3: "show"},
    "wga-mba-ai": {0: "show", 1: "studio"},
    "dga-ai": {2: "show"},
    "eu-ai-act-art50-2-provider-marking": {0: "studio", 4: "studio"},
    "eu-ai-act-art50-4-deployer-deepfake": {1: "show", 2: "show", 3: "show"},
    "eu-code-of-practice-ai-content-transparency": {0: "studio", 1: "show", 3: "studio"},
    "cn-ai-content-labeling-measures": {1: "show", 2: "studio"},
    "us-ca-sb942-ai-transparency-act": {0: "studio", 1: "studio"},
    "us-ny-synthetic-performer-ad-disclosure": {1: "show"},
    "us-take-it-down-act": {0: "studio"},
    "usco-part3-generative-ai-training": {1: "studio"},
    "getty-v-stability-us": {2: "studio"},
    "studios-v-minimax-hailuo": {2: "studio"},
    "bartz-v-anthropic": {0: "studio"},
    "kadrey-v-meta": {1: "studio"},
    "eu-commission-art50-guidelines-2026": {2: "show", 3: "show"},
    "eu-ai-act-art53-gpai-provider-obligations": {1: "studio", 2: "studio", 3: "studio"},
    "eu-gpai-code-of-practice": {0: "studio"},
    "eu-ai-act-art5-ncii-prohibition": {2: "studio"},
    "de-gema-v-suno-2026": {2: "studio"},
    "fr-ppl-presumption-ai-use-cultural-content": {0: "studio"},
    "netflix-genai-production-partner-guidance": {0: "show"},
    "mpa-tpn-content-security-ai-controls": {0: "show", 3: "studio", 4: "studio", 5: "show"},
    "eo-insurance-ai-underwriting": {4: "studio"},
    "ca-criminal-code-sexual-deepfakes": {0: "studio"},
    "jp-copyright-art30-4-training": {1: "studio"},
    "jp-ai-output-infringement-copyrightability": {2: "studio"},
    "kr-ai-basic-act-art31-transparency": {0: "studio", 1: "show"},
    "iatse-basic-agreement-2024-ai": {0: "show"},
    "tag-839-2024-ai": {0: "show", 3: "show"},
    "actra-ipa-2025-ai": {3: "show"},
    "ubcp-actra-bcmpa-2025-ai": {3: "show"},
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
                                          "source_date", "confidence", "notes",
                                          "verified_on", "basis", "check_result",
                                          "check_summary")},
            "sources": r.get("sources", []),
            "summary": f"{lead} {when if when.lower().startswith('when ') else 'when ' + when}.",
            "requires": r["requires"],
            "human_in_loop": r.get("human_in_loop", ""),
            "applies": applies,
            "evidence": [{"text": text, "check": checks.get(i, "manual"),
                          **({"scope": SCOPE.get(r["id"], {}).get(i, "shot")}
                             if i not in checks else {})}
                         for i, text in enumerate(r["evidence"])],
        })
        for i in SCOPE.get(r["id"], {}):
            if i in checks or i >= len(r["evidence"]):
                raise SystemExit(f"{r['id']}: scope given for item {i}, which is not a manual item")
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
        "research": src.get("research", {}),
        "facts": src.get("facts", {}),
        "interop": src.get("interop", {}),
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
