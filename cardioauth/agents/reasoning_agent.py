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

You receive structured chart data and payer criteria. Map one against the other,
identify gaps, score approval likelihood, and draft the PA narrative.

Rules for the narrative draft:
- Write in clinical language appropriate for a payer medical reviewer.
- Lead with the primary diagnosis and clinical urgency.
- Reference specific lab values, imaging findings, and prior treatments from
  the chart by date and value. Never generalize.
- Cite relevant ACC/AHA clinical guidelines to justify the procedure.
- Address the payer's most common denial reasons preemptively.
- Maximum 400 words. Payer reviewers do not read long narratives.
- End with a clear statement of medical necessity.
- Never fabricate clinical data. If data is missing, flag it — do not fill it in.

If approval_likelihood_score is below 0.6, recommend the cardiologist strengthen
the chart before submission rather than submitting and risking a denial.

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

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Map clinical data against payer criteria and draft the PA narrative.\n\n"
                    f"CHART DATA:\n{chart_data.model_dump_json(indent=2)}\n\n"
                    f"PAYER CRITERIA:\n{policy_data.model_dump_json(indent=2)}"
                ),
            }],
        )

        raw = response.content[0].text
        data = json.loads(raw)
        result = ReasoningResult(**data)

        if result.approval_likelihood_score < self.config.approval_likelihood_threshold:
            logger.warning(
                "REASONING_AGENT: low approval likelihood %.2f (%s) — recommend strengthening chart",
                result.approval_likelihood_score,
                result.approval_likelihood_label,
            )

        return result
