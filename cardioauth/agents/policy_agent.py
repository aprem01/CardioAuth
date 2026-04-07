"""POLICY_AGENT - Payer coverage policy retrieval with Claude enhancement."""

from __future__ import annotations

import json
import logging

import anthropic

from cardioauth.config import Config
from cardioauth.models.policy import PolicyData
from cardioauth.vector_store.client import VectorStoreClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are POLICY_AGENT, a payer coverage policy specialist for CardioAuth —
a cardiology prior authorization system.

You receive baseline policy data (from an internal database) for a specific
CPT code and payer. Your job is to enhance, validate, and refine these
criteria using your knowledge of real-world payer policies, CMS guidelines,
and ACC/AHA clinical practice guidelines.

Your tasks:
1. Review the baseline criteria and confirm or correct each one
2. Add any missing criteria that this payer is known to require
3. Ensure common denial reasons reflect actual real-world denial patterns
4. Add specific, actionable appeal success factors
5. Cross-reference CMS National Coverage Determinations and Local Coverage
   Determinations where applicable
6. Cite specific policy document names/numbers where possible

For cardiology imaging (PET, SPECT, echo, MRI, CTA):
- Include ACC Appropriate Use Criteria (AUC) requirements
- Note frequency limitations specific to this payer
- Document when peer-to-peer review is triggered
- Specify BMI/body habitus thresholds for advanced imaging

For interventional cardiology (PCI, cath, TAVR, ablation):
- Include Heart Team requirements where applicable
- Note surgical risk score requirements (STS-PROM)
- Specify prior treatment failure documentation needs

Return ONLY valid JSON matching this schema:
{
  "payer": "",
  "procedure": "",
  "cpt_code": "",
  "auth_required": true/false/null,
  "clinical_criteria": [
    {"criterion": "", "required": true/false, "evidence_type": "", "acceptable_values": ""}
  ],
  "documentation_required": [],
  "submission_format": "portal|fax|clearinghouse|api|unknown",
  "typical_turnaround_days": 0,
  "common_denial_reasons": [],
  "appeal_success_factors": [],
  "policy_source": "",
  "policy_last_updated": ""
}
"""


class PolicyAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def run(self, procedure_code: str, payer_name: str, baseline_policy: dict | None = None) -> PolicyData:
        logger.info("POLICY_AGENT: retrieving criteria for procedure=%s payer=%s", procedure_code, payer_name)

        # Build the prompt with baseline policy if available
        if baseline_policy:
            policy_context = (
                f"BASELINE POLICY DATA (from internal database):\n"
                f"{json.dumps(baseline_policy, indent=2, default=str)}\n\n"
                f"Review, validate, and enhance the above baseline criteria. "
                f"Correct any inaccuracies, add missing criteria this payer "
                f"is known to require, and ensure denial reasons reflect "
                f"real-world patterns for this specific payer and procedure."
            )
        else:
            policy_context = (
                f"No baseline policy data available. Generate the complete "
                f"prior authorization criteria based on your knowledge of "
                f"this payer's policies and CMS/ACC guidelines."
            )

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate prior authorization criteria.\n"
                    f"Procedure code (CPT): {procedure_code}\n"
                    f"Payer: {payer_name}\n\n"
                    f"{policy_context}"
                ),
            }],
        )

        raw = response.content[0].text

        # Handle markdown code blocks
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]

        data = json.loads(raw.strip())
        policy = PolicyData(**data)

        if policy.auth_required is None:
            logger.warning("POLICY_AGENT: auth_required unknown for %s / %s — escalate to human", procedure_code, payer_name)

        return policy
