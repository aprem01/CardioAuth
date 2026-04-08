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

Your job is to generate the EXACT prior authorization criteria for a given
CPT code and commercial payer (UnitedHealthcare, Aetna, BCBS, Cigna, Humana,
Medicare). Use your knowledge of real-world payer medical policies, CMS
National Coverage Determinations (NCDs), Local Coverage Determinations (LCDs),
and ACC/AHA clinical practice guidelines.

CRITICAL — DO NOT FABRICATE:
- Only return criteria you are confident are accurate for this specific payer
- Cite real policy document numbers/names where possible
- If you don't know the exact criteria for a payer, return what is publicly
  documented and note any uncertainty in cardiologist_review_flags

For cardiology imaging (PET 78492/78491, SPECT 78451/78452, echo 93306,
MRI 75557, CTA 75574):
- Include ACC Appropriate Use Criteria (AUC) requirements
- Note frequency limitations (e.g., not within 12 months unless new symptoms)
- ALWAYS include "documentation of new or worsening symptoms" as a criterion
  for any imaging request — this is the #1 denial reason
- Note when peer-to-peer review is triggered
- Specify BMI/body habitus thresholds for PET over SPECT (BMI >= 35)
- Include ECG findings (LBBB, paced rhythm) as alternate justification

For interventional cardiology (PCI, cath, TAVR, ablation):
- Include Heart Team requirements where applicable (TAVR)
- Note surgical risk score requirements (STS-PROM)
- Specify prior procedure failure / failed medical therapy documentation

For all procedures:
- Cross-reference CMS NCDs/LCDs when CMS coverage is provided in context
- Document common denial reasons based on real-world denial patterns
- List specific appeal success factors

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

    def run(self, procedure_code: str, payer_name: str, cms_context: dict | None = None) -> PolicyData:
        logger.info("POLICY_AGENT: generating criteria for procedure=%s payer=%s", procedure_code, payer_name)

        # Build context from real CMS coverage data (if available)
        cms_section = ""
        if cms_context and cms_context.get("cms_ncd_number"):
            cms_section = (
                f"\n\nREAL CMS COVERAGE CONTEXT:\n"
                f"CPT {cms_context['cpt_code']} is covered under "
                f"NCD {cms_context['cms_ncd_number']}: {cms_context['cms_ncd_title']}\n"
                f"NCD URL: {cms_context.get('cms_ncd_url', 'N/A')}\n"
                f"Use this NCD as the foundation for the criteria. Commercial "
                f"payers typically follow CMS coverage requirements with their "
                f"own additions."
            )

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate the real prior authorization criteria for:\n"
                    f"Procedure code (CPT): {procedure_code}\n"
                    f"Payer: {payer_name}\n"
                    f"{cms_section}\n\n"
                    f"Return the exact criteria this specific payer is known "
                    f"to require for this procedure. Cite the real policy "
                    f"document name/number if you know it."
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
