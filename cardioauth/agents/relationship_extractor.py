"""RelationshipExtractor — captures clinical causal chains from raw notes.

Peter's validation showed the pipeline was losing clinical relationships
that a human cardiologist would trivially preserve:

  Chart: "Unable to do TST due to dyspnea and obesity"
  Human: dyspnea + obesity → can't exercise → pharmacologic imaging justified
  Our pipeline: [dyspnea] in symptoms bucket, [obesity] in demographic bucket,
                causal relationship lost before reasoner sees the data.

This agent runs on the RAW clinical note BEFORE any bucketing and extracts
canonical cardiology relationships. It uses two layers:

  Layer 1 — Rule-based matching against a curated seed list of known chains.
             Fast, deterministic, no API call.
  Layer 2 — Claude-powered extraction for novel patterns (optional).

The output populates CaseContext.relationships, which the UnifiedReasoner
includes in its prompt alongside the raw note and structured data.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from cardioauth.case_context import CaseContext, ClinicalRelationship

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Canonical clinical relationships — seed knowledge base
# ────────────────────────────────────────────────────────────────────────

# Each entry encodes: patterns to match (any) + the clinical implication.
# These are cardiology-specific chains that any cardiologist would draw.
SEED_RELATIONSHIPS = [
    {
        "id": "REL-001",
        "patterns": [
            r"unable\s+to\s+(?:do|perform|complete)\s+(?:tst|treadmill|exercise|ett|stress\s+test)",
            r"cannot\s+exercise",
            r"inability\s+to\s+exercise",
            r"exercise\s+(?:intolerance|limitation)",
        ],
        "conclusion": "Patient cannot perform adequate exercise stress testing, which justifies pharmacologic/nuclear imaging.",
        "supports_criterion": "EX-001",
        "confidence": 0.95,
    },
    {
        "id": "REL-002",
        "patterns": [
            r"dyspnea.*(?:obesity|bmi|deconditioning)",
            r"(?:obesity|bmi).*dyspnea",
            r"dyspnea\s+on\s+exertion.*(?:obesity|bmi)",
        ],
        "conclusion": "Combined dyspnea and obesity prevent adequate exercise — pharmacologic stress imaging indicated.",
        "supports_criterion": "EX-001",
        "confidence": 0.9,
    },
    {
        "id": "REL-003",
        "patterns": [
            r"attenuation\s+artifact",
            r"soft[-\s]tissue\s+attenuation",
            r"breast\s+attenuation",
            r"diaphragmatic\s+attenuation",
        ],
        "conclusion": "Prior imaging had attenuation artifact, rendering it non-diagnostic. PET over SPECT justified.",
        "supports_criterion": "NDX-001",
        "confidence": 0.95,
    },
    {
        "id": "REL-004",
        "patterns": [
            r"false[-\s]positive\s+(?:spect|stress|perfusion|imaging)",
            r"likely\s+false\s+positive",
            r"prior\s+(?:spect|stress).*false\s+positive",
        ],
        "conclusion": "Prior study interpreted as false positive — effectively non-diagnostic, supports advanced imaging.",
        "supports_criterion": "NDX-001",
        "confidence": 0.9,
    },
    {
        "id": "REL-005",
        "patterns": [
            r"(?:equivocal|non[-\s]diagnostic|inconclusive)\s+(?:spect|stress|perfusion|ett|treadmill)",
            r"(?:spect|stress|perfusion|ett).*(?:equivocal|non[-\s]diagnostic|inconclusive)",
        ],
        "conclusion": "Prior stress test formally equivocal/non-diagnostic — supports repeat with advanced imaging.",
        "supports_criterion": "NDX-001",
        "confidence": 0.95,
    },
    {
        "id": "REL-006",
        "patterns": [
            r"submaximal\s+(?:hr|heart\s+rate|stress|test)",
            r"(?:\d{1,2})%\s+(?:of\s+)?(?:max|mphr|maximum\s+predicted)",
            r"failed\s+to\s+achieve\s+(?:target|maximum)\s+(?:hr|heart\s+rate)",
        ],
        "conclusion": "Prior stress test was submaximal — insufficient to rule out ischemia, supports pharmacologic imaging.",
        "supports_criterion": "NDX-002",
        "confidence": 0.9,
    },
    {
        "id": "REL-007",
        "patterns": [
            r"(?:lbbb|left\s+bundle\s+branch\s+block)",
        ],
        "conclusion": "LBBB precludes standard stress ECG interpretation — nuclear/pharmacologic imaging indicated.",
        "supports_criterion": "ECG-001",
        "confidence": 0.95,
    },
    {
        "id": "REL-008",
        "patterns": [
            r"paced\s+rhythm",
            r"ventricular\s+pacing",
            r"pacemaker\s+rhythm",
            r"biventricular\s+(?:paced|pacing)",
        ],
        "conclusion": "Ventricular pacing precludes stress ECG interpretation — alternative imaging indicated.",
        "supports_criterion": "ECG-002",
        "confidence": 0.95,
    },
    {
        "id": "REL-009",
        "patterns": [
            r"bmi\s*(?:of\s*)?(3[5-9]|[4-9]\d)",
            r"obesity\s+(?:class|grade)\s+(?:ii|iii|2|3)",
            r"morbid(?:ly)?\s+obese",
        ],
        "conclusion": "BMI ≥35 — soft tissue attenuation risk on SPECT justifies PET.",
        "supports_criterion": "BMI-001",
        "confidence": 0.95,
    },
    {
        "id": "REL-010",
        "patterns": [
            r"technically\s+limited\s+(?:echo|tte|study|echocardiogram)",
            r"(?:echo|tte|echocardiogram)\s+technically\s+limited",
            r"poor\s+acoustic\s+windows",
            r"suboptimal\s+echo",
            r"limited\s+(?:echo|tte|echocardiogram)",
        ],
        "conclusion": "Prior echo technically limited — supports alternative advanced imaging.",
        "supports_criterion": "NDX-004",
        "confidence": 0.9,
    },
    {
        "id": "REL-011",
        "patterns": [
            r"failed\s+(?:maximally\s+tolerated\s+)?medical\s+(?:therapy|management)",
            r"refractory\s+to\s+medical\s+therapy",
            r"persistent\s+symptoms\s+(?:despite|on)\s+(?:optimal|gdmt|maximum)\s+(?:medical|therapy)",
            r"(?:on|despite)\s+(?:optimal\s+|maximal\s+|maximum\s+)?(?:medical\s+therapy|gdmt|guideline[-\s]directed\s+medical\s+therapy)\s+(?:x|for|over)\s+\d+\s+(?:weeks?|months?|days?)",
            r"(?:optimal\s+|maximal\s+|maximum\s+)?(?:medical\s+therapy|gdmt)\s+(?:x|for|over)\s+\d+\s+(?:weeks?|months?)",
            r"(?:despite|on)\s+maximal\s+(?:medical\s+therapy|tolerated)",
            r"\d+\s+(?:weeks?|months?)\s+(?:of\s+)?(?:optimal\s+|maximal\s+)?(?:medical\s+therapy|gdmt)",
            r"persistent\s+symptoms\s+(?:despite|on)\s+(?:optimal\s+)?(?:medical\s+therapy|gdmt)",
            r"symptoms\s+despite\s+(?:optimal|maximal)\s+(?:therapy|gdmt|medical)",
        ],
        "conclusion": "Patient has documented trial of maximally tolerated medical therapy — MED-001 satisfied.",
        "supports_criterion": "MED-001",
        "confidence": 0.9,
    },
    {
        "id": "REL-012",
        "patterns": [
            r"nyha\s+(?:class\s+)?(?:i{1,4}v?|[1-4])",
            r"ccs\s+(?:class\s+)?(?:i{1,4}v?|[1-4])",
            r"ehra\s+(?:class\s+)?(?:i{1,4}v?|[1-4])",
            r"class\s+(?:i{1,4}v?|[1-4])\s+(?:angina|heart\s+failure|hf|symptoms)",
            r"functional\s+class\s+(?:i{1,4}v?|[1-4])",
        ],
        "conclusion": "Validated functional class documented — SX-004 satisfied.",
        "supports_criterion": "SX-004",
        "confidence": 0.95,
    },
    {
        "id": "REL-013",
        "patterns": [
            r"office\s+(?:note|visit|consult)",
            r"consultation\s+note",
            r"progress\s+note",
            r"clinic\s+note",
            r"h&p\s+(?:performed|documented)",
            r"cardiology\s+(?:consult|evaluation)",
        ],
        "conclusion": "Cardiology consultation/office note present — DOC-001 satisfied.",
        "supports_criterion": "DOC-001",
        "confidence": 0.9,
    },
    {
        "id": "REL-014",
        "patterns": [
            r"(?:new|worsening|progressive)\s+(?:symptoms|dyspnea|chest\s+pain|angina)",
            r"symptoms\s+(?:have\s+)?(?:worsened|progressed)",
            r"(?:recent|recurrent)\s+onset\s+(?:of\s+)?(?:chest\s+pain|dyspnea|angina)",
            r"new\s+onset\s+(?:chest\s+pain|dyspnea|palpitations|angina|symptoms)",
            r"onset\s+(?:of\s+)?(?:new\s+)?(?:symptoms|chest\s+pain|dyspnea)",
        ],
        "conclusion": "New or worsening symptoms documented — SX-001 satisfied.",
        "supports_criterion": "SX-001",
        "confidence": 0.9,
    },
    {
        "id": "REL-015",
        "patterns": [
            r"stemi|nstemi|myocardial\s+infarction|acute\s+coronary\s+syndrome|acs",
            r"unstable\s+angina",
        ],
        "conclusion": "Acute/unstable cardiac event — urgency of advanced imaging supported.",
        "supports_criterion": "SX-003",
        "confidence": 0.95,
    },
    {
        "id": "REL-016",
        "patterns": [
            r"chest\s+pain.*(?:exertion|exertional|walking|climbing|activity)",
            r"angina\s+(?:on\s+)?exertion",
            r"exertional\s+(?:chest\s+pain|angina|discomfort)",
            r"typical\s+anginal\s+symptoms",
        ],
        "conclusion": "Exertional anginal symptoms documented — SX-003 satisfied.",
        "supports_criterion": "SX-003",
        "confidence": 0.9,
    },
]


# ────────────────────────────────────────────────────────────────────────
# Extractor
# ────────────────────────────────────────────────────────────────────────


def _extract_verbatim_quote(text: str, match: re.Match, window: int = 80) -> str:
    """Grab a short verbatim quote around the regex match."""
    start = max(0, match.start() - 20)
    end = min(len(text), match.end() + 40)
    quote = text[start:end].strip()
    # Trim to sentence-like boundary
    if len(quote) > window:
        quote = quote[:window] + "..."
    return quote


def extract_relationships_rule_based(raw_note: str) -> list[ClinicalRelationship]:
    """Scan the clinical note for canonical relationships using regex rules.

    Fast and deterministic — no API call, no cost. Always runs.
    """
    if not raw_note:
        return []

    text_lower = raw_note.lower()
    found: list[ClinicalRelationship] = []
    seen_conclusions: set[str] = set()

    for rel in SEED_RELATIONSHIPS:
        for pattern in rel["patterns"]:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match and rel["conclusion"] not in seen_conclusions:
                quote = _extract_verbatim_quote(raw_note, match)
                found.append(ClinicalRelationship(
                    premises=[quote],
                    conclusion=rel["conclusion"],
                    supports_criterion=rel["supports_criterion"],
                    evidence_quote=quote,
                    confidence=rel["confidence"],
                ))
                seen_conclusions.add(rel["conclusion"])
                break  # Only first match per relationship

    logger.info("RelationshipExtractor: found %d rule-based relationships", len(found))
    return found


LLM_EXTRACTION_PROMPT = """\
You are a clinical relationship extractor for cardiology prior authorization.

Given a clinical note and a list of clinical relationships that have already
been detected by regex rules, find ADDITIONAL relationships that the rules
missed. Focus on:

  - Failed/maximally tolerated medical therapy (MED-001) — any phrasing
    like "on GDMT for X weeks", "maximized medications", "optimized therapy",
    "exhausted pharmacological options"
  - Symptom severity changes (SX-001) — new/worsening/progressive
  - Functional class (SX-004) — NYHA, CCS, EHRA variants
  - Anatomic/procedural contraindications
  - Inability to exercise (EX-001) — any medical/physical barrier

Rules:
  - ONLY extract relationships clearly supported by verbatim text in the note
  - Never invent findings not present
  - If the note doesn't contain support, output an empty list
  - Do NOT repeat relationships already found (shown in already_found)

Return JSON:
{
  "new_relationships": [
    {
      "supports_criterion": "MED-001",
      "conclusion": "Patient on GDMT for 8 weeks, satisfying MED-001.",
      "evidence_quote": "On GDMT for 8 weeks with persistent symptoms",
      "confidence": 0.9
    }
  ]
}
"""


def extract_relationships_llm(
    ctx: CaseContext,
    already_found: list[ClinicalRelationship],
    config=None,
) -> list[ClinicalRelationship]:
    """Use Claude to find novel clinical relationships the regex missed.

    Only runs if ANTHROPIC_API_KEY is set. Cheap call (~500 tokens).
    Returns additional ClinicalRelationship objects; never duplicates
    what's in `already_found`.
    """
    if config is None or not getattr(config, "anthropic_api_key", ""):
        return []

    try:
        import anthropic
        import json

        client = anthropic.Anthropic(api_key=config.anthropic_api_key)

        # Get applicable criteria for this procedure (don't bother with n/a ones)
        from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure
        applicable = get_criteria_for_procedure(ctx.procedure_code, ctx.payer_name)
        applicable_codes = [c.code for c in applicable]

        already_codes = [r.supports_criterion for r in already_found]
        already_summary = [
            {"supports": r.supports_criterion, "conclusion": r.conclusion[:60]}
            for r in already_found
        ]

        user_msg = (
            f"Clinical note:\n\n{ctx.raw_note}\n\n"
            f"─────────\n\n"
            f"Procedure: CPT {ctx.procedure_code} ({ctx.procedure_name})\n"
            f"Applicable criteria: {', '.join(applicable_codes)}\n\n"
            f"Already detected by regex:\n{json.dumps(already_summary, indent=2)}\n\n"
            f"Find ADDITIONAL relationships the regex missed. Focus on criteria "
            f"not yet in 'already_found'. Return empty list if nothing more."
        )

        response = client.messages.create(
            model=config.model,
            max_tokens=1500,
            system=LLM_EXTRACTION_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text

        # Parse JSON
        from cardioauth.agents.json_recovery import parse_llm_json
        data = parse_llm_json(raw, fallback={"new_relationships": []})
        new_rels_raw = data.get("new_relationships", [])

        novel = []
        for r in new_rels_raw:
            code = r.get("supports_criterion", "")
            if not code or code in already_codes:
                continue
            # Only accept for applicable criteria
            if code not in applicable_codes:
                continue
            novel.append(ClinicalRelationship(
                premises=[r.get("evidence_quote", "")],
                conclusion=r.get("conclusion", ""),
                supports_criterion=code,
                evidence_quote=r.get("evidence_quote", ""),
                confidence=float(r.get("confidence", 0.75)),
            ))

        logger.info("LLM relationship extraction: found %d novel relationships", len(novel))
        return novel
    except Exception as e:
        logger.warning("LLM relationship extraction failed (non-blocking): %s", e)
        return []


class RelationshipExtractor:
    """Populates CaseContext.relationships from the raw clinical note.

    Two-pass strategy:
      1. Rule-based regex — fast, deterministic, free. Catches canonical chains.
      2. LLM augmentation — catches novel phrasings the regex missed.
         Only runs if config has anthropic_api_key set.
    """

    def __init__(self, config=None) -> None:
        self.config = config

    def extract(self, ctx: CaseContext) -> None:
        """Extract clinical relationships in two passes."""
        start = time.time()
        note = ctx.build_clinical_narrative()

        # Pass 1: rule-based
        rule_rels = extract_relationships_rule_based(note)

        # Pass 2: LLM augmentation (if API key available)
        llm_rels = []
        if self.config is not None and getattr(self.config, "anthropic_api_key", ""):
            ctx.relationships = rule_rels  # temp set so LLM sees the rule-based ones
            llm_rels = extract_relationships_llm(ctx, rule_rels, self.config)

        ctx.relationships = rule_rels + llm_rels

        elapsed = int((time.time() - start) * 1000)
        ctx.trace(
            agent_name="RelationshipExtractor",
            action=f"extracted {len(rule_rels)} rule-based + {len(llm_rels)} LLM-novel relationships",
            summary="; ".join(r.conclusion[:60] for r in ctx.relationships[:3]),
            ms=elapsed,
        )


def extract_relationships(ctx: CaseContext, config=None) -> None:
    """Public entry point — run relationship extraction on the context."""
    RelationshipExtractor(config).extract(ctx)
