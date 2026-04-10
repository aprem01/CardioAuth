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
import re
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
    validation_warnings: list[str] = field(default_factory=list)

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
            "validation_warnings": self.validation_warnings,
        }


SYSTEM_PROMPT = """\
You are TAXONOMY_MATCHER, a clinical reasoning specialist for CardioAuth.

CORE RULES — STRICT EVIDENCE TYPE ENFORCEMENT (READ CAREFULLY)

You evaluate each criterion against pre-bucketed chart data. Each
criterion has an `evidence_type` (lab, imaging, ecg, demographic,
clinical_note, score, medication). For each criterion you must:

  STEP 1 — Identify the required evidence type from the criterion.
  STEP 2 — Look ONLY in the matching bucket of chart_buckets.
           DO NOT cite data from any other bucket.
  STEP 3 — Verify the data quantitatively or qualitatively satisfies the
           criterion. Just having data of the right TYPE is not enough —
           the VALUE must satisfy the requirement.
  STEP 4 — Assign exactly one status: met / not_met / not_applicable.

NEGATIVE EXAMPLES (never do this):

  ✗ BMI criterion → cite an echocardiogram report
    (BMI is a demographic measurement; echo reports are imaging.)

  ✗ ECG criterion (LBBB, paced rhythm) → cite an echocardiogram or
    stress test report. ECG findings come from the ECG bucket only.

  ✗ Symptom criterion → cite a diagnosis code (I25.10) as evidence.
    Diagnosis codes are NOT documented symptoms — they are payer
    billing codes. Symptoms must come from clinical_note.symptoms.

  ✗ "Reduced LVEF" criterion (LVEF ≤ 40%) → marking it MET when the
    actual LVEF in the chart is 50%, 55%, or "low normal".
    50% is NOT reduced. Read the actual value.

  ✗ Marking "Failed maximally tolerated medical therapy" as MET when
    the chart shows the patient is taking medications but with no
    documentation of trial duration or failure reason.

QUANTITATIVE PRECISION

If a criterion has a numeric threshold (LVEF ≤ 40%, BMI ≥ 35, HR < 85%
of maximum predicted, age ≥ 65, gradient ≥ 40 mmHg, etc.), you MUST
extract the actual value from the chart and compare it against the
threshold. If the value does not satisfy the threshold, the criterion
is NOT met. There is no rounding, no "close enough", no semantic match.

REASONING OUTPUT REQUIRED

For each criterion, before assigning status, fill in `reasoning`:
  - what evidence type the criterion requires
  - which bucket you searched
  - what specific value or finding you found (or did not find)
  - whether the value satisfies the criterion threshold

EMERGING CRITERIA — only flag findings the taxonomy does not yet capture.
Do not invent. Do not add aliases for existing criteria.

Return ONLY valid JSON matching this schema:
{
  "matches": [
    {
      "code": "LVEF-002",
      "reasoning": "Required evidence type: score. Searched chart_buckets.score.lvef. Found LVEF 55% from echocardiogram dated 2025-11-12. Threshold for this criterion is LVEF ≤ 40%. Value 55% does NOT satisfy threshold.",
      "status": "not_met",
      "confidence": 0.98,
      "evidence_type_required": "score",
      "evidence_source_bucket": "score.lvef",
      "evidence": "",
      "gap": "LVEF documented at 55%, which is preserved (not reduced). Criterion requires LVEF ≤ 40%.",
      "recommendation": "This criterion may be not_applicable for this case if patient does not have HFrEF."
    },
    {
      "code": "BMI-001",
      "reasoning": "Required evidence type: demographic. Searched chart_buckets.demographic.bmi. Found BMI 38 from comorbidities. Threshold ≥ 35. Value 38 ≥ 35: SATISFIED.",
      "status": "met",
      "confidence": 0.99,
      "evidence_type_required": "demographic",
      "evidence_source_bucket": "demographic.bmi",
      "evidence": "BMI 38 documented in comorbidities (≥ 35 threshold)",
      "gap": "",
      "recommendation": ""
    }
  ],
  "emerging_criteria": []
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
        """Run the matcher: chart → buckets → criteria → validated matrix."""
        applicable = get_criteria_for_procedure(procedure_code, payer_name)
        if not applicable:
            logger.warning("No taxonomy criteria for CPT %s", procedure_code)
            return CaseMatchResult(
                case_id=case_id,
                procedure_code=procedure_code,
                payer=payer_name,
                taxonomy_version=TAXONOMY_VERSION,
            )

        # ── Step A: Pre-bucket chart data by evidence type ──
        from cardioauth.taxonomy.evidence_buckets import bucket_chart_evidence, validate_threshold
        chart_buckets = bucket_chart_evidence(chart_data)

        # Build the criterion list with explicit evidence type
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
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Evaluate this case against the taxonomy.\n\n"
                    f"PROCEDURE: CPT {procedure_code}\n"
                    f"PAYER: {payer_name}\n"
                    f"TAXONOMY VERSION: {TAXONOMY_VERSION}\n\n"
                    f"FIXED CRITERIA TO EVALUATE (each has a required evidence_type):\n"
                    f"{json.dumps(criteria_for_claude, indent=2)}\n\n"
                    f"CHART DATA — PRE-BUCKETED BY EVIDENCE TYPE.\n"
                    f"For each criterion, look ONLY in the bucket matching its\n"
                    f"`evidence_type`. Crossing buckets is forbidden.\n\n"
                    f"chart_buckets = {json.dumps(chart_buckets, indent=2, default=str)}\n\n"
                    f"For each criterion: fill in `reasoning` first, then the status. "
                    f"Verify quantitative thresholds against actual values. "
                    f"Then list emerging criteria not in the taxonomy."
                ),
            }],
        )

        raw = response.content[0].text
        from cardioauth.agents.json_recovery import parse_llm_json
        data = parse_llm_json(raw, fallback={"matches": [], "emerging_criteria": []})
        data.setdefault("matches", [])
        data.setdefault("emerging_criteria", [])

        # ── Step B: Build CriterionMatch objects ──
        crit_by_code = {c.code: c for c in applicable}
        matches = []
        for m in data.get("matches", []):
            code = m.get("code", "")
            matches.append(CriterionMatch(
                code=code,
                status=m.get("status", "not_applicable"),
                confidence=float(m.get("confidence", 0.0)),
                evidence=m.get("evidence", ""),
                gap=m.get("gap", ""),
                recommendation=m.get("recommendation", ""),
            ))

        # ── Step C: Post-validation pass ──
        # 1) Override with deterministic threshold checks for criteria that have them
        # 2) Detect evidence-type mismatches (cited evidence comes from wrong bucket)
        validation_warnings: list[str] = []
        for match in matches:
            crit = crit_by_code.get(match.code)
            if not crit:
                continue

            # Deterministic threshold validation (LVEF, BMI, HR%, ECG findings)
            det = validate_threshold(match.code, chart_buckets)
            if det is not None:
                expected_status = "met" if det["met"] else "not_met"
                if match.status != expected_status:
                    validation_warnings.append(
                        f"{match.code}: LLM said '{match.status}' but deterministic "
                        f"check says '{expected_status}' ({det['explanation']}). Override applied."
                    )
                    match.status = expected_status
                    match.evidence = det["explanation"] if det["met"] else ""
                    match.gap = "" if det["met"] else det["explanation"]
                    match.confidence = 1.0  # deterministic = certain

            # Evidence-type sanity check on remaining LLM-judged matches
            if match.status == "met" and match.evidence:
                ev_lower = match.evidence.lower()
                req_type = crit.evidence_type
                if req_type == "ecg":
                    # ECG criteria should not cite echo or stress test
                    if any(x in ev_lower for x in ["echocardiogram", "stress test", "treadmill",
                                                     "spect", "pet", "tte ", "tee "]) and \
                       not any(x in ev_lower for x in ["ecg", "ekg", "12-lead", "electrocardiogram"]):
                        validation_warnings.append(
                            f"{match.code} (ECG criterion): cited evidence appears to come from "
                            f"non-ECG source: '{match.evidence[:80]}'. Demoting confidence."
                        )
                        match.confidence = min(match.confidence, 0.3)
                if req_type == "demographic":
                    # BMI/demographic criteria should not cite imaging studies
                    if any(x in ev_lower for x in ["echocardiogram", "ecg", "stress test"]) and \
                       "bmi" not in ev_lower and "obesity" not in ev_lower:
                        validation_warnings.append(
                            f"{match.code} (demographic): cited evidence is from imaging instead "
                            f"of demographic data: '{match.evidence[:80]}'."
                        )
                        match.confidence = min(match.confidence, 0.3)
                if req_type == "clinical_note":
                    # Symptom criteria should not cite ICD-10 codes alone
                    if re.match(r"^[A-Z]\d{2}\.[\d]+", match.evidence.strip()):
                        validation_warnings.append(
                            f"{match.code} (symptom/clinical_note): cited evidence is a diagnosis "
                            f"code, not symptom documentation: '{match.evidence[:80]}'."
                        )
                        match.confidence = min(match.confidence, 0.3)

        if validation_warnings:
            for w in validation_warnings:
                logger.info("VALIDATION: %s", w)

        result = CaseMatchResult(
            case_id=case_id,
            procedure_code=procedure_code,
            payer=payer_name,
            taxonomy_version=TAXONOMY_VERSION,
            matches=matches,
            emerging_criteria=data.get("emerging_criteria", []),
            validation_warnings=validation_warnings,
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
