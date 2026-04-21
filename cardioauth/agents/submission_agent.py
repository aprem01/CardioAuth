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
        cpt_code: str = "",
        pdf_bytes: bytes = b"",
        cover_summary: str = "",
    ) -> SubmissionResult:
        """Actually submit the PA via the appropriate channel (or mock/fax),
        persist the submission, and return the tracked SubmissionResult.
        """
        from cardioauth.persistence import get_store
        from cardioauth.submission_channels import (
            SubmissionPackage,
            get_channel_for,
        )

        now = datetime.now(timezone.utc)
        submission_id = f"CA-{uuid.uuid4().hex[:8].upper()}"

        logger.info("SUBMISSION_AGENT: preparing submission %s (payer=%s, requested_channel=%s)",
                    submission_id, payer, submission_channel)

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

        # Route through the pluggable channel layer. This actually calls
        # something — mock returns a realistic confirmation, fax queues,
        # portal stubs return pending_credentials until wired.
        channel = get_channel_for(payer=payer, submission_format=submission_channel)
        package = SubmissionPackage(
            submission_id=submission_id,
            patient_id=patient_id,
            payer=payer,
            procedure_name=procedure,
            cpt_code=cpt_code,
            cover_summary=cover_summary,
            pdf_bytes=pdf_bytes,
            metadata={"channel_name": channel.name, "approved_by": approved_by},
        )
        try:
            channel_result = channel.submit(package)
        except Exception as e:
            logger.exception("SUBMISSION_AGENT: channel %s failed: %s", channel.name, e)
            channel_result = None

        if channel_result is None:
            # Channel error — surface but do not silently succeed.
            audit.append(AuditEntry(
                action=f"channel_error_{channel.name}",
                timestamp=now.isoformat(),
                actor="SUBMISSION_AGENT",
            ))
            result = SubmissionResult(
                submission_id=submission_id,
                payer=payer,
                procedure=procedure,
                patient_id=patient_id,
                submission_channel=channel.name,
                submission_timestamp=now.isoformat(),
                status="error",
                audit_trail=audit,
            )
            self._persist(result, reasoning)
            return result

        # Successful channel call (mock / fax / portal-stub).
        expected_decision = (
            channel_result.expected_decision_date
            or (now + timedelta(days=5)).date().isoformat()
        )
        # Follow-up 2 business days before expected decision
        from datetime import date
        try:
            fu_dt = datetime.fromisoformat(expected_decision) - timedelta(days=2)
            follow_up = fu_dt.date().isoformat() if isinstance(fu_dt, datetime) else str(fu_dt)
        except Exception:
            follow_up = ""

        # Map channel status → SubmissionResult.status
        status_map = {
            "submitted": "submitted",
            "fax_queued": "submitted",          # counts as submitted-in-flight
            "pending_credentials": "pending_approval",  # human must push through
            "failed": "error",
        }
        result_status = status_map.get(channel_result.status, "submitted")

        audit.append(AuditEntry(
            action=f"channel_{channel.name}_{channel_result.status}",
            timestamp=channel_result.submitted_at or now.isoformat(),
            actor="SUBMISSION_AGENT",
        ))

        result = SubmissionResult(
            submission_id=submission_id,
            payer=payer,
            procedure=procedure,
            patient_id=patient_id,
            submission_channel=channel.name,
            submission_timestamp=channel_result.submitted_at or now.isoformat(),
            confirmation_number=channel_result.confirmation_number,
            expected_decision_date=expected_decision,
            follow_up_scheduled=follow_up,
            status=result_status,
            audit_trail=audit,
        )
        result.__dict__["_channel_notes"] = channel_result.channel_notes
        result.__dict__["_channel_requires_human_action"] = channel_result.requires_human_action

        self._persist(result, reasoning, cpt_code=cpt_code)
        return result

    def _persist(self, result: SubmissionResult, reasoning: ReasoningResult, cpt_code: str = "") -> None:
        """Write the submission to durable storage so it survives restarts."""
        try:
            from cardioauth.persistence import get_store
            store = get_store()
            data = result.model_dump(mode="json")
            data["cpt_code"] = cpt_code or data.get("procedure_code", "")
            store.save_submission(result.submission_id, data)
            store.append_audit(
                actor=result.audit_trail[0].actor if result.audit_trail else "system",
                action="submission_persisted",
                subject_id=result.submission_id,
                detail=f"status={result.status} channel={result.submission_channel}",
            )
        except Exception as e:
            logger.warning("SUBMISSION_AGENT: failed to persist %s: %s", result.submission_id, e)

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
