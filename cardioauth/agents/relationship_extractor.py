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
            r"technically\s+limited\s+(?:echo|tte|study)",
            r"poor\s+acoustic\s+windows",
            r"suboptimal\s+echo",
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


class RelationshipExtractor:
    """Populates CaseContext.relationships from the raw clinical note."""

    def __init__(self, config=None) -> None:
        self.config = config

    def extract(self, ctx: CaseContext) -> None:
        """Extract clinical relationships and write to ctx.relationships.

        Currently uses rule-based extraction only — cheap, fast, and
        catches the canonical chains. Can be extended with Claude-powered
        extraction for novel patterns if needed.
        """
        start = time.time()
        note = ctx.build_clinical_narrative()
        rels = extract_relationships_rule_based(note)
        ctx.relationships = rels

        elapsed = int((time.time() - start) * 1000)
        ctx.trace(
            agent_name="RelationshipExtractor",
            action=f"extracted {len(rels)} clinical relationships",
            summary="; ".join(r.conclusion[:60] for r in rels[:3]),
            ms=elapsed,
        )


def extract_relationships(ctx: CaseContext, config=None) -> None:
    """Public entry point — run relationship extraction on the context."""
    RelationshipExtractor(config).extract(ctx)
