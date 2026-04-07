"""CardioAuth Orchestrator - Coordinates all four specialist agents."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from cardioauth.agents.submission_agent import SubmissionAgent
from cardioauth.config import Config
from cardioauth.models.chart import ChartData
from cardioauth.models.policy import PolicyData
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.models.submission import OutcomeResult, SubmissionResult
from cardioauth.vector_store.client import VectorStoreClient

logger = logging.getLogger(__name__)


def _is_demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "true").lower() == "true"


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
        self.submission_agent = SubmissionAgent(self.config)

        if not _is_demo_mode():
            from cardioauth.agents.chart_agent import ChartAgent
            from cardioauth.agents.policy_agent import PolicyAgent
            from cardioauth.agents.reasoning_agent import ReasoningAgent

            missing = self.config.validate()
            if missing:
                logger.warning("Missing config: %s", missing)

            self.chart_agent = ChartAgent(self.config)
            self.policy_agent = PolicyAgent(self.config)
            self.reasoning_agent = ReasoningAgent(self.config)

    def process_request(
        self,
        patient_id: str,
        procedure_code: str,
        payer_id: str,
        payer_name: str,
    ) -> ReviewPackage:
        """Run the full pipeline up to human review."""
        logger.info("=" * 60)
        logger.info("ORCHESTRATOR: starting PA request — patient=%s procedure=%s payer=%s",
                     patient_id, procedure_code, payer_name)

        if _is_demo_mode():
            return self._process_demo(patient_id, procedure_code, payer_name)

        return self._process_live(patient_id, procedure_code, payer_id, payer_name)

    def _process_demo(
        self,
        patient_id: str,
        procedure_code: str,
        payer_name: str,
    ) -> ReviewPackage:
        """Run pipeline with demo/mock data + real Claude reasoning."""
        from cardioauth.demo import get_demo_chart, get_demo_policy

        logger.info("ORCHESTRATOR: DEMO MODE")

        # Step 1: Get demo chart data
        logger.info("ORCHESTRATOR: Step 1 — CHART_AGENT (demo data)")
        chart_data = get_demo_chart(patient_id, procedure_code)

        # Step 2: Get policy — use Claude to enhance baseline demo policy
        logger.info("ORCHESTRATOR: Step 2 — POLICY_AGENT")
        baseline_policy = get_demo_policy(procedure_code, payer_name)
        if self.config.anthropic_api_key:
            from cardioauth.agents.policy_agent import PolicyAgent
            policy_agent = PolicyAgent(self.config)
            try:
                policy_data = policy_agent.run(
                    procedure_code, payer_name,
                    baseline_policy=baseline_policy.model_dump(),
                )
                logger.info("ORCHESTRATOR: Claude policy enhancement succeeded")
            except Exception as e:
                logger.warning("POLICY_AGENT Claude enhancement failed (%s), using baseline", e)
                policy_data = baseline_policy
        else:
            policy_data = baseline_policy

        # Step 3: Use real Claude reasoning if API key available, else fallback
        logger.info("ORCHESTRATOR: Step 3 — REASONING_AGENT")
        if self.config.anthropic_api_key:
            from cardioauth.agents.reasoning_agent import ReasoningAgent
            reasoning_agent = ReasoningAgent(self.config)
            try:
                reasoning = reasoning_agent.run(chart_data, policy_data)
                logger.info("ORCHESTRATOR: Claude reasoning succeeded")
            except Exception as e:
                logger.warning("REASONING_AGENT failed (%s), using demo reasoning", e)
                from cardioauth.demo import get_demo_reasoning
                reasoning = get_demo_reasoning(chart_data, policy_data)
        else:
            from cardioauth.demo import get_demo_reasoning
            reasoning = get_demo_reasoning(chart_data, policy_data)

        requires_human_action: list[str] = []

        if chart_data.confidence_score < self.config.chart_confidence_threshold:
            requires_human_action.append(
                f"Chart data confidence is {chart_data.confidence_score:.0%} "
                f"(threshold: {self.config.chart_confidence_threshold:.0%}). "
                f"Missing: {', '.join(chart_data.missing_fields)}"
            )

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

    def _process_live(
        self,
        patient_id: str,
        procedure_code: str,
        payer_id: str,
        payer_name: str,
    ) -> ReviewPackage:
        """Run the full live pipeline with real FHIR + vector store."""
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
        """Submit the PA after the cardiologist has approved."""
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
