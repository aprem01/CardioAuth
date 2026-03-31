"""POLICY_AGENT - Payer coverage policy retrieval."""

from __future__ import annotations

import json
import logging

import anthropic

from cardioauth.config import Config
from cardioauth.models.policy import PolicyData
from cardioauth.vector_store.client import VectorStoreClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are POLICY_AGENT, a payer coverage policy specialist for CardioAuth.

You receive relevant policy documents retrieved from a vector knowledge base.
Your job is to extract the exact prior authorization criteria for the given
procedure and payer, returning a structured requirements checklist.

Rules:
- Always cite the specific policy document your criteria came from.
- If payer criteria are ambiguous, return both interpretations and flag.
- If no relevant policy found, return auth_required as null and note escalation needed.
- Prioritize policies updated within the last 6 months.
- Cross-reference CMS guidelines if payer policy is silent on a criterion.

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
        self.vector_store = VectorStoreClient(config)

    def run(self, procedure_code: str, payer_name: str) -> PolicyData:
        logger.info("POLICY_AGENT: retrieving criteria for procedure=%s payer=%s", procedure_code, payer_name)

        query = f"Prior authorization criteria for CPT {procedure_code} from {payer_name}"
        policy_docs = self.vector_store.search(query, top_k=5)

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Extract prior authorization criteria.\n"
                    f"Procedure code (CPT): {procedure_code}\n"
                    f"Payer: {payer_name}\n\n"
                    f"Retrieved policy documents:\n{json.dumps(policy_docs, indent=2, default=str)}"
                ),
            }],
        )

        raw = response.content[0].text
        data = json.loads(raw)
        policy = PolicyData(**data)

        if policy.auth_required is None:
            logger.warning("POLICY_AGENT: auth_required unknown for %s / %s — escalate to human", procedure_code, payer_name)

        return policy
