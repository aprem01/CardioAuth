"""CardioAuth Orchestrator - Coordinates all four specialist agents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cardioauth.agents.chart_agent import ChartAgent
from cardioauth.agents.policy_agent import PolicyAgent
from cardioauth.agents.reasoning_agent import ReasoningAgent
from cardioauth.agents.submission_agent import SubmissionAgent
from cardioauth.config import Config
from cardioauth.models.chart import ChartData
from cardioauth.models.policy import PolicyData
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.models.submission import OutcomeResult, SubmissionResult
from cardioauth.vector_store.client import VectorStoreClient

logger = logging.getLogger(__name__)


@dataclass
class ReviewPackage:
    """Everything the cardiologist sees before approving submission."""
    chart_data: ChartData
    policy_data: PolicyData
    reasoning: ReasoningResult
    requires_human_action: list[str]


class Orchestrator:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        missing = self.config.validate()
        if missing:
            logger.warning("Missing config: %s", missing)

        self.chart_agent = ChartAgent(self.config)
        self.policy_agent = PolicyAgent(self.config)
        self.reasoning_agent = ReasoningAgent(self.config)
        self.submission_agent = SubmissionAgent(self.config)

    def process_request(
        self,
        patient_id: str,
        procedure_code: str,
        payer_id: str,
        payer_name: str,
    ) -> ReviewPackage:
        """Run the full pipeline up to human review.

        Returns a ReviewPackage for the cardiologist to approve/edit.
        Does NOT submit — that requires explicit human approval.
        """
        logger.info("=" * 60)
        logger.info("ORCHESTRATOR: starting PA request — patient=%s procedure=%s payer=%s",
                     patient_id, procedure_code, payer_name)

        # Step 1: Extract chart data
        logger.info("ORCHESTRATOR: Step 1 — CHART_AGENT")
        chart_data = self.chart_agent.run(patient_id, procedure_code, payer_id)

        requires_human_action: list[str] = []

        if chart_data.confidence_score < self.config.chart_confidence_threshold:
            requires_human_action.append(
                f"Chart data confidence is {chart_data.confidence_score:.0%} "
                f"(threshold: {self.config.chart_confidence_threshold:.0%}). "
                f"Missing: {', '.join(chart_data.missing_fields)}"
            )

        # Step 2: Retrieve payer criteria
        logger.info("ORCHESTRATOR: Step 2 — POLICY_AGENT")
        policy_data = self.policy_agent.run(procedure_code, payer_name)

        if policy_data.auth_required is None:
            requires_human_action.append(
                "Could not determine if prior auth is required for this payer/procedure. "
                "Manual verification needed."
            )

        # Step 3: Reason over chart vs criteria
        logger.info("ORCHESTRATOR: Step 3 — REASONING_AGENT")
        reasoning = self.reasoning_agent.run(chart_data, policy_data)

        if reasoning.approval_likelihood_score < self.config.approval_likelihood_threshold:
            requires_human_action.append(
                f"Approval likelihood is {reasoning.approval_likelihood_label} "
                f"({reasoning.approval_likelihood_score:.0%}). "
                "Consider strengthening the chart before submission."
            )

        if reasoning.cardiologist_review_flags:
            requires_human_action.extend(reasoning.cardiologist_review_flags)

        logger.info(
            "ORCHESTRATOR: review package ready — approval_likelihood=%s (%s), flags=%d",
            reasoning.approval_likelihood_score,
            reasoning.approval_likelihood_label,
            len(requires_human_action),
        )

        return ReviewPackage(
            chart_data=chart_data,
            policy_data=policy_data,
            reasoning=reasoning,
            requires_human_action=requires_human_action,
        )

    def submit_after_approval(
        self,
        review: ReviewPackage,
        approved_by: str,
    ) -> SubmissionResult:
        """Submit the PA after the cardiologist has approved.

        This MUST only be called after explicit human approval.
        """
        if not approved_by:
            raise ValueError("Cannot submit without an identified approver")

        logger.info("ORCHESTRATOR: Step 4 — SUBMISSION_AGENT (approved by %s)", approved_by)

        return self.submission_agent.submit(
            patient_id=review.chart_data.patient_id,
            payer=review.policy_data.payer,
            procedure=review.chart_data.procedure_requested,
            reasoning=review.reasoning,
            submission_channel=review.policy_data.submission_format,
            approved_by=approved_by,
        )

    def process_payer_response(
        self,
        submission: SubmissionResult,
        payer_response: dict[str, Any],
    ) -> OutcomeResult:
        """Process payer decision and handle denial/appeal flow."""
        logger.info("ORCHESTRATOR: processing payer response for %s", submission.submission_id)

        outcome = self.submission_agent.process_outcome(submission, payer_response)

        # Feed learning back to vector store
        if outcome.learning_payload:
            vector_store = VectorStoreClient(self.config)
            vector_store.ingest_learning(outcome.learning_payload.model_dump())

        if outcome.outcome == "DENIED" and outcome.appeal_recommended:
            logger.warning(
                "ORCHESTRATOR: DENIED — appeal recommended. Deadline: %s. "
                "Awaiting human review of appeal draft before submission.",
                outcome.appeal_deadline,
            )

        return outcome
