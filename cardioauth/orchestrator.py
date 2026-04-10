"""CardioAuth Orchestrator - Coordinates all four specialist agents."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
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


def _classify_anthropic_error(exc: Exception) -> dict:
    """Turn an Anthropic SDK exception into a structured warning."""
    msg = str(exc)
    lower = msg.lower()
    if "usage limit" in lower or "spend limit" in lower:
        return {
            "level": "critical",
            "kind": "spend_limit",
            "message": (
                "Anthropic API spend limit reached. Results below are from the "
                "fallback engine, not real-time AI reasoning. Raise the spend "
                "cap at console.anthropic.com → Limits to restore full quality."
            ),
            "raw": msg,
        }
    if "rate limit" in lower or "429" in lower:
        return {
            "level": "warning",
            "kind": "rate_limit",
            "message": "Anthropic API rate limit hit briefly. Results from fallback engine; retry in a moment for full AI reasoning.",
            "raw": msg,
        }
    return {
        "level": "warning",
        "kind": "agent_failure",
        "message": f"AI agent failed: {msg[:200]}. Showing fallback results.",
        "raw": msg,
    }


@dataclass
class ReviewPackage:
    """Everything the cardiologist sees before approving submission."""
    chart_data: ChartData
    policy_data: PolicyData
    reasoning: ReasoningResult
    requires_human_action: list[str]
    taxonomy_match: dict | None = None  # Structured criterion match matrix
    system_warnings: list[dict] = field(default_factory=list)  # Agent failures, fallbacks


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

        system_warnings: list[dict] = []

        # Step 2: Get policy — use Claude to generate criteria from real payer
        # knowledge and CMS NCDs/LCDs. No hardcoded policy baselines.
        logger.info("ORCHESTRATOR: Step 2 — POLICY_AGENT")
        if self.config.anthropic_api_key:
            from cardioauth.agents.policy_agent import PolicyAgent
            from cardioauth.integrations.cms_coverage import get_cms_coverage_context
            policy_agent = PolicyAgent(self.config)
            try:
                cms_context = get_cms_coverage_context(procedure_code)
                policy_data = policy_agent.run(
                    procedure_code, payer_name,
                    cms_context=cms_context,
                )
                logger.info("ORCHESTRATOR: Claude policy generation succeeded")
            except Exception as e:
                w = _classify_anthropic_error(e)
                w["agent"] = "POLICY_AGENT"
                system_warnings.append(w)
                logger.warning("POLICY_AGENT failed (%s), falling back to demo policy", e)
                policy_data = get_demo_policy(procedure_code, payer_name)
        else:
            policy_data = get_demo_policy(procedure_code, payer_name)

        # Steps 3 + 3b: Run REASONING_AGENT and TAXONOMY_MATCHER in parallel.
        # Both need chart_data and policy_data but not each other, so we can
        # roughly halve the end-to-end latency by running them concurrently.
        reasoning = None
        taxonomy_match = None

        def _run_reasoning():
            if not self.config.anthropic_api_key:
                from cardioauth.demo import get_demo_reasoning
                return get_demo_reasoning(chart_data, policy_data), None
            from cardioauth.agents.reasoning_agent import ReasoningAgent
            try:
                return ReasoningAgent(self.config).run(chart_data, policy_data), None
            except Exception as e:
                logger.warning("REASONING_AGENT failed (%s), using demo reasoning", e)
                from cardioauth.demo import get_demo_reasoning
                w = _classify_anthropic_error(e)
                w["agent"] = "REASONING_AGENT"
                return get_demo_reasoning(chart_data, policy_data), w

        def _run_taxonomy():
            if not self.config.anthropic_api_key:
                return None, None
            try:
                from cardioauth.taxonomy import match_case_to_taxonomy
                tax_result = match_case_to_taxonomy(
                    chart_data.model_dump(),
                    procedure_code,
                    payer_name,
                    self.config,
                    case_id=f"{patient_id}-{procedure_code}",
                )
                return tax_result, None
            except Exception as e:
                logger.warning("TAXONOMY_MATCHER failed: %s", e)
                w = _classify_anthropic_error(e)
                w["agent"] = "TAXONOMY_MATCHER"
                return None, w

        logger.info("ORCHESTRATOR: Step 3 + 3b — REASONING_AGENT and TAXONOMY_MATCHER in parallel")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_reason = pool.submit(_run_reasoning)
            fut_tax = pool.submit(_run_taxonomy)
            reasoning, reason_warn = fut_reason.result()
            tax_result, tax_warn = fut_tax.result()

        if reason_warn:
            system_warnings.append(reason_warn)
        if tax_warn:
            system_warnings.append(tax_warn)

        if tax_result is not None:
            from cardioauth.taxonomy import record_emerging_criterion
            case_id = f"{patient_id}-{procedure_code}"
            for ec in tax_result.emerging_criteria:
                try:
                    record_emerging_criterion(
                        suggested_code=ec.get("suggested_code", "MISC"),
                        category=ec.get("category", "MISC"),
                        description=ec.get("description", ""),
                        rationale=ec.get("rationale", ""),
                        case_id=case_id,
                        procedure_code=procedure_code,
                        payer=payer_name,
                    )
                except Exception as e:
                    logger.warning("Failed to record emerging criterion: %s", e)
            taxonomy_match = tax_result.to_dict()
            logger.info(
                "ORCHESTRATOR: taxonomy match — %d criteria, score=%.2f (%s), %d emerging",
                len(tax_result.matches), tax_result.overall_score,
                tax_result.label, len(tax_result.emerging_criteria),
            )

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
            taxonomy_match=taxonomy_match,
            system_warnings=system_warnings,
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
