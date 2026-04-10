"""REASONING_AGENT - Clinical reasoning and PA narrative drafting."""

from __future__ import annotations

import json
import logging

import anthropic

from cardioauth.config import Config
from cardioauth.models.chart import ChartData
from cardioauth.models.policy import PolicyData
from cardioauth.models.reasoning import ReasoningResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are REASONING_AGENT, a clinical reasoning specialist for CardioAuth.

You receive PRE-BUCKETED chart data and payer criteria. For each criterion
you must determine whether the chart actually satisfies it, and draft a PA
narrative.

═══════════════════════════════════════════════════════════════════════════
STRICT EVIDENCE-TYPE ENFORCEMENT (READ THIS CAREFULLY)
═══════════════════════════════════════════════════════════════════════════

The chart is split into evidence buckets. Each criterion has a clinical
data type it requires. You may ONLY cite evidence from the matching bucket:

  Lab values         → chart_buckets.lab
  Imaging findings   → chart_buckets.imaging
  ECG findings       → chart_buckets.ecg
  BMI / demographics → chart_buckets.demographic
  Medications        → chart_buckets.medication
  Symptoms / notes   → chart_buckets.clinical_note
  Quantitative score → chart_buckets.score (LVEF, BMI, %MPHR pre-extracted)

NEGATIVE EXAMPLES — these are bugs you MUST NOT produce:

  ✗ "Reduced LVEF" criterion marked MET when actual LVEF is 50-55%.
    50% is preserved, not reduced. Read the actual value before judging.

  ✗ ECG criterion (LBBB, paced rhythm) cited with an echocardiogram
    or stress test report. ECG findings come from chart_buckets.ecg only.

  ✗ BMI criterion cited with an echocardiogram report. BMI is a
    demographic value, not an imaging finding.

  ✗ Symptom criterion cited with diagnosis codes (I25.10) instead of
    documented symptoms. Diagnosis codes are billing codes, NOT
    documentation of patient-reported or clinician-observed symptoms.

  ✗ "Failed maximally tolerated medical therapy" marked MET when the
    patient is taking medications but the chart shows no documented
    trial duration, dose escalation, or failure reason.

═══════════════════════════════════════════════════════════════════════════
QUANTITATIVE PRECISION
═══════════════════════════════════════════════════════════════════════════

If a criterion has a numeric threshold (LVEF ≤ 40%, BMI ≥ 35, HR < 85%
of MPHR, mean gradient ≥ 40 mmHg, AVA ≤ 1.0 cm², etc.), extract the
actual value from chart_buckets.score or chart_buckets.lab and compare
it to the threshold. If the value does not satisfy the threshold, the
criterion is NOT met. There is no rounding, no semantic match, no
"close enough".

Pre-extracted quantitative values are available in chart_buckets.score
when present. Use them as the source of truth.

═══════════════════════════════════════════════════════════════════════════
CRITICAL DETECTION RULES (always check these regardless of payer criteria)
═══════════════════════════════════════════════════════════════════════════

1. NEW OR WORSENING SYMPTOMS — For any cardiac imaging procedure or
   repeat procedure, explicitly verify whether the chart documents new
   or worsening symptoms since baseline. If not, add to criteria_not_met:
     criterion: "Documentation of new or worsening symptoms since prior imaging"

2. EXERCISE CAPACITY — For pharmacologic stress tests, verify a SPECIFIC
   reason the patient cannot exercise (not just "unable to exercise").

3. ECG ALTERNATIVE JUSTIFICATION — For PET/SPECT requests, check
   chart_buckets.ecg for LBBB, paced rhythm, or WPW that independently
   justify nuclear imaging.

═══════════════════════════════════════════════════════════════════════════
NARRATIVE RULES
═══════════════════════════════════════════════════════════════════════════

- Clinical language for payer medical reviewer
- Reference specific values and dates
- Cite ACC/AHA guidelines
- Address common denial reasons preemptively
- Maximum 400 words
- Never fabricate
- Always address the new/worsening symptoms question

If approval_likelihood_score is below 0.6, recommend the cardiologist
strengthen the chart rather than submitting.

Return ONLY valid JSON matching this schema:
{
  "criteria_met": [{"criterion": "", "met": true/false, "evidence": "", "confidence": 0.0}],
  "criteria_not_met": [{"criterion": "", "gap": "", "recommendation": ""}],
  "approval_likelihood_score": 0.0,
  "approval_likelihood_label": "HIGH|MEDIUM|LOW|DO NOT SUBMIT",
  "missing_documentation": [],
  "pa_narrative_draft": "",
  "narrative_tone": "clinical",
  "guideline_citations": [],
  "cardiologist_review_flags": []
}
"""


class ReasoningAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def run(self, chart_data: ChartData, policy_data: PolicyData) -> ReasoningResult:
        logger.info(
            "REASONING_AGENT: mapping chart (confidence=%.2f) against %d criteria",
            chart_data.confidence_score,
            len(policy_data.clinical_criteria),
        )

        # Pre-bucket chart data so the LLM cannot cross evidence types
        from cardioauth.taxonomy.evidence_buckets import bucket_chart_evidence
        chart_buckets = bucket_chart_evidence(chart_data.model_dump())

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Map clinical data against payer criteria and draft the PA narrative.\n\n"
                    f"PAYER CRITERIA:\n{policy_data.model_dump_json(indent=2)}\n\n"
                    f"chart_buckets — pre-extracted by evidence type. Use ONLY the\n"
                    f"bucket matching each criterion's evidence type. Quantitative\n"
                    f"values (LVEF, BMI, HR%) are in chart_buckets.score.\n\n"
                    f"{json.dumps(chart_buckets, indent=2, default=str)}"
                ),
            }],
        )

        raw = response.content[0].text
        from cardioauth.agents.json_recovery import parse_llm_json
        data = parse_llm_json(raw)
        if not data:
            raise ValueError("REASONING_AGENT: could not parse LLM response")
        # Fill defaults for required fields if truncation dropped them
        data.setdefault("criteria_met", [])
        data.setdefault("criteria_not_met", [])
        data.setdefault("approval_likelihood_score", 0.0)
        data.setdefault("approval_likelihood_label", "LOW")
        data.setdefault("pa_narrative_draft", "")
        data.setdefault("guideline_citations", [])
        data.setdefault("cardiologist_review_flags", [])
        data.setdefault("missing_documentation", [])
        result = ReasoningResult(**data)

        if result.approval_likelihood_score < self.config.approval_likelihood_threshold:
            logger.warning(
                "REASONING_AGENT: low approval likelihood %.2f (%s) — recommend strengthening chart",
                result.approval_likelihood_score,
                result.approval_likelihood_label,
            )

        return result
