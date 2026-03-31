"""SUBMISSION_AGENT - Final packaging, submission, and outcome tracking."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import anthropic

from cardioauth.config import Config
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.models.submission import AuditEntry, LearningPayload, OutcomeResult, SubmissionResult

logger = logging.getLogger(__name__)

OUTCOME_SYSTEM_PROMPT = """\
You are SUBMISSION_AGENT processing a payer decision on a prior authorization.

Given the payer response and original submission details, determine:
- Whether to recommend an appeal
- If appealing, draft the appeal narrative using the same clinical rigor as the original
- Extract a learning payload to feed back into the policy knowledge base

Return ONLY valid JSON matching this schema:
{
  "submission_id": "",
  "outcome": "APPROVED|DENIED|PENDING|INFO_REQUESTED",
  "denial_reason": "",
  "appeal_recommended": true/false,
  "appeal_draft": "",
  "appeal_deadline": "",
  "outcome_timestamp": "",
  "learning_payload": {
    "payer": "",
    "procedure": "",
    "criteria_met_at_submission": [],
    "outcome": "",
    "denial_reason": "",
    "appeal_outcome": ""
  }
}
"""


class SubmissionAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def submit(
        self,
        patient_id: str,
        payer: str,
        procedure: str,
        reasoning: ReasoningResult,
        submission_channel: str,
        approved_by: str,
    ) -> SubmissionResult:
        now = datetime.now(timezone.utc)
        submission_id = f"CA-{uuid.uuid4().hex[:8].upper()}"

        logger.info("SUBMISSION_AGENT: submitting %s via %s", submission_id, submission_channel)

        audit = [
            AuditEntry(
                action="human_approval",
                timestamp=now.isoformat(),
                actor=approved_by,
            ),
            AuditEntry(
                action="submission_created",
                timestamp=now.isoformat(),
                actor="SUBMISSION_AGENT",
            ),
        ]

        # In production, this would call the payer portal/clearinghouse/fax API.
        # For now we build the package and return it ready for dispatch.

        turnaround_days = 14  # default; would come from PolicyData in production
        expected_decision = now + timedelta(days=turnaround_days)
        follow_up = expected_decision - timedelta(days=2)

        audit.append(AuditEntry(
            action=f"submitted_via_{submission_channel}",
            timestamp=now.isoformat(),
            actor="SUBMISSION_AGENT",
        ))

        return SubmissionResult(
            submission_id=submission_id,
            payer=payer,
            procedure=procedure,
            patient_id=patient_id,
            submission_channel=submission_channel,
            submission_timestamp=now.isoformat(),
            expected_decision_date=expected_decision.date().isoformat(),
            follow_up_scheduled=follow_up.date().isoformat(),
            status="submitted",
            audit_trail=audit,
        )

    def process_outcome(
        self,
        submission: SubmissionResult,
        payer_response: dict,
    ) -> OutcomeResult:
        logger.info("SUBMISSION_AGENT: processing outcome for %s", submission.submission_id)

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=3000,
            system=OUTCOME_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Process this payer decision.\n\n"
                    f"Original submission:\n{submission.model_dump_json(indent=2)}\n\n"
                    f"Payer response:\n{json.dumps(payer_response, indent=2, default=str)}"
                ),
            }],
        )

        raw = response.content[0].text
        data = json.loads(raw)
        return OutcomeResult(**data)
