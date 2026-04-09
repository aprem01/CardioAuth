"""Map case data against the fixed criterion taxonomy.

The matcher uses Claude to evaluate each applicable criterion against
the chart data. Output is a structured matrix of met/not_met/n/a per
criterion code — never free text.

This standardized output is what enables case-to-case comparison and
ML training.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

import anthropic

from cardioauth.config import Config
from cardioauth.taxonomy.taxonomy import (
    Criterion,
    CRITERION_TAXONOMY,
    TAXONOMY_VERSION,
    get_criteria_for_procedure,
)

logger = logging.getLogger(__name__)


CriterionStatus = Literal["met", "not_met", "not_applicable"]


@dataclass
class CriterionMatch:
    """Result of matching one taxonomy criterion against chart data."""
    code: str
    status: CriterionStatus
    confidence: float
    evidence: str = ""
    gap: str = ""
    recommendation: str = ""


@dataclass
class CaseMatchResult:
    """Full structured matching result for a single case."""
    case_id: str
    procedure_code: str
    payer: str
    taxonomy_version: str
    matches: list[CriterionMatch] = field(default_factory=list)
    emerging_criteria: list[dict] = field(default_factory=list)
    score_required: float = 0.0
    score_supporting: float = 0.0
    overall_score: float = 0.0
    label: str = "LOW"

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "procedure_code": self.procedure_code,
            "payer": self.payer,
            "taxonomy_version": self.taxonomy_version,
            "matches": [
                {
                    "code": m.code,
                    "status": m.status,
                    "confidence": m.confidence,
                    "evidence": m.evidence,
                    "gap": m.gap,
                    "recommendation": m.recommendation,
                }
                for m in self.matches
            ],
            "emerging_criteria": self.emerging_criteria,
            "score_required": self.score_required,
            "score_supporting": self.score_supporting,
            "overall_score": self.overall_score,
            "label": self.label,
        }


SYSTEM_PROMPT = """\
You are TAXONOMY_MATCHER, a clinical reasoning specialist for CardioAuth.

You receive:
1. A patient's chart data (labs, imaging, medications, diagnoses, prior procedures)
2. A FIXED list of coded clinical criteria (the taxonomy)
3. Optional payer-specific context

Your job is to evaluate EACH criterion in the taxonomy against the chart
data and return a structured match result. You DO NOT generate new criteria
or paraphrase existing ones — you only assign one of three statuses:

  • met            — chart data clearly satisfies this criterion
  • not_met        — criterion applies but chart data does not satisfy it
  • not_applicable — criterion does not apply to this specific case

For each criterion you must provide:
  • status (met / not_met / not_applicable)
  • confidence (0.0 - 1.0)
  • evidence — exact chart data that satisfies it (when met)
  • gap — what is missing (when not_met)
  • recommendation — what the cardiologist should add (when not_met)

ADDITIONALLY, capture EMERGING CRITERIA: clinical findings or payer
considerations the chart suggests that DO NOT match any criterion in the
provided taxonomy. These will be reviewed for promotion to the formal
taxonomy. Do not invent — only flag genuine clinical observations the
taxonomy does not yet capture.

Return ONLY valid JSON matching this schema:
{
  "matches": [
    {
      "code": "NDX-001",
      "status": "met",
      "confidence": 0.95,
      "evidence": "Exercise treadmill 2025-11-10: equivocal/non-diagnostic, 6 METs, submaximal HR",
      "gap": "",
      "recommendation": ""
    }
  ],
  "emerging_criteria": [
    {
      "category": "MISC",
      "description": "Family history of premature CAD documented (father MI age 52)",
      "suggested_code": "RISK-FAMHX",
      "rationale": "Strong family history supports advanced imaging but no taxonomy code exists"
    }
  ]
}
"""


class TaxonomyMatcher:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def match(
        self,
        chart_data: dict,
        procedure_code: str,
        payer_name: str,
        case_id: str = "",
    ) -> CaseMatchResult:
        """Run the matcher: chart → taxonomy → structured matrix."""
        applicable = get_criteria_for_procedure(procedure_code, payer_name)
        if not applicable:
            logger.warning("No taxonomy criteria for CPT %s", procedure_code)
            return CaseMatchResult(
                case_id=case_id,
                procedure_code=procedure_code,
                payer=payer_name,
                taxonomy_version=TAXONOMY_VERSION,
            )

        # Build the criterion list as compact JSON for Claude
        criteria_for_claude = [
            {
                "code": c.code,
                "category": c.category,
                "short_name": c.short_name,
                "definition": c.definition,
                "evidence_type": c.evidence_type,
                "severity": c.severity,
            }
            for c in applicable
        ]

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Evaluate this case against the taxonomy.\n\n"
                    f"PROCEDURE: CPT {procedure_code}\n"
                    f"PAYER: {payer_name}\n"
                    f"TAXONOMY VERSION: {TAXONOMY_VERSION}\n\n"
                    f"FIXED CRITERIA TO EVALUATE:\n"
                    f"{json.dumps(criteria_for_claude, indent=2)}\n\n"
                    f"CHART DATA:\n"
                    f"{json.dumps(chart_data, indent=2, default=str)}\n\n"
                    f"For EACH criterion above, return its match status. "
                    f"Then list any emerging criteria the chart suggests "
                    f"that the taxonomy does not yet capture."
                ),
            }],
        )

        raw = response.content[0].text
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]

        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError as e:
            logger.error("TAXONOMY_MATCHER: failed to parse JSON: %s", e)
            data = {"matches": [], "emerging_criteria": []}

        matches = [
            CriterionMatch(
                code=m.get("code", ""),
                status=m.get("status", "not_applicable"),
                confidence=float(m.get("confidence", 0.0)),
                evidence=m.get("evidence", ""),
                gap=m.get("gap", ""),
                recommendation=m.get("recommendation", ""),
            )
            for m in data.get("matches", [])
        ]

        result = CaseMatchResult(
            case_id=case_id,
            procedure_code=procedure_code,
            payer=payer_name,
            taxonomy_version=TAXONOMY_VERSION,
            matches=matches,
            emerging_criteria=data.get("emerging_criteria", []),
        )

        # Compute scores
        score_from_matches(result, applicable)

        return result


def score_from_matches(result: CaseMatchResult, applicable: list[Criterion]) -> None:
    """Compute weighted approval scores from criterion matches."""
    crit_by_code = {c.code: c for c in applicable}

    req_total = 0
    req_met = 0
    sup_total = 0
    sup_met = 0

    for m in result.matches:
        c = crit_by_code.get(m.code)
        if not c:
            continue
        if m.status == "not_applicable":
            continue
        if c.severity == "required":
            req_total += 1
            if m.status == "met":
                req_met += 1
        else:
            sup_total += 1
            if m.status == "met":
                sup_met += 1

    result.score_required = (req_met / req_total) if req_total > 0 else 1.0
    result.score_supporting = (sup_met / sup_total) if sup_total > 0 else 1.0
    # Weighted combined score: 75% required, 25% supporting
    result.overall_score = round(0.75 * result.score_required + 0.25 * result.score_supporting, 2)

    if result.overall_score >= 0.85:
        result.label = "HIGH"
    elif result.overall_score >= 0.65:
        result.label = "MEDIUM"
    elif result.overall_score >= 0.45:
        result.label = "LOW"
    else:
        result.label = "DO NOT SUBMIT"


def match_case_to_taxonomy(
    chart_data: dict,
    procedure_code: str,
    payer_name: str,
    config: Config,
    case_id: str = "",
) -> CaseMatchResult:
    """Public entry point: run the taxonomy matcher on a case."""
    matcher = TaxonomyMatcher(config)
    return matcher.match(chart_data, procedure_code, payer_name, case_id)
