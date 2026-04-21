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
from cardioauth.taxonomy.validation import (
    build_audit_trail,
    extract_reasoner_codes,
    trail_to_dict,
    validate_criteria_for_cpt,
)
from cardioauth.vector_store.client import VectorStoreClient

logger = logging.getLogger(__name__)


def _is_demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "true").lower() == "true"


def _use_unified_reasoner() -> bool:
    """When true, Orchestrator._process_demo uses UnifiedReasoner instead of legacy ReasoningAgent.

    The /api/pa/custom-request endpoint already uses UnifiedReasoner directly;
    this flag aligns the orchestrator path for parity. Default OFF so we can
    validate the change on one endpoint before flipping the other.
    """
    return os.environ.get("USE_UNIFIED_REASONER", "false").lower() in ("1", "true", "yes")


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
    retrieved_chunks: list[dict] = field(default_factory=list)  # Stage 1 RAG chunks
    criterion_citations: list[dict] = field(default_factory=list)  # criterion → chunk_ids
    # Per-criterion journey through the pipeline: stages passed, final status,
    # drop reasons, flags. Populated by the validation layer.
    criterion_audit_trail: list[dict] = field(default_factory=list)
    # Validation reports from each agent boundary (policy_agent_output, reasoner_output, ...).
    # Each entry shows expected vs received codes and any missing/unknown flags.
    validation_reports: list[dict] = field(default_factory=list)
    # Calibration layer (Tier 4 quality): historical stats, cross-CPT rules,
    # policy freshness. Populated from POLICY_AGENT side-band metadata.
    payer_stats: dict | None = None
    payer_global_rules: list[dict] = field(default_factory=list)
    policy_freshness: dict | None = None


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
                     patient_id[:4] + "***" if len(patient_id) > 4 else "***", procedure_code, payer_name)

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
        from cardioauth.demo import get_demo_chart

        logger.info("ORCHESTRATOR: DEMO MODE")

        logger.info("ORCHESTRATOR: Step 1 — CHART_AGENT (demo data)")
        chart_data = get_demo_chart(patient_id, procedure_code)
        return self._run_pipeline(
            chart_data=chart_data,
            procedure_code=procedure_code,
            payer_name=payer_name,
            case_id=f"{patient_id}-{procedure_code}",
            run_taxonomy_matcher=True,
        )

    def _process_live(
        self,
        patient_id: str,
        procedure_code: str,
        payer_id: str,
        payer_name: str,
    ) -> ReviewPackage:
        """Run the full live pipeline with real FHIR + vector store."""
        logger.info("ORCHESTRATOR: Step 1 — CHART_AGENT")
        chart_data = self.chart_agent.run(patient_id, procedure_code, payer_id)
        return self._run_pipeline(
            chart_data=chart_data,
            procedure_code=procedure_code,
            payer_name=payer_name,
            case_id=f"{patient_id}-{procedure_code}",
            run_taxonomy_matcher=False,
        )

    # ──────────────────────────────────────────────────────────────────
    # Shared pipeline — the single path demo + live both flow through.
    # This is what replaces the previously divergent _process_demo / _process_live
    # implementations so bugs can't hide in one path while the other looks fine.
    # ──────────────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        chart_data: ChartData,
        procedure_code: str,
        payer_name: str,
        case_id: str,
        run_taxonomy_matcher: bool,
    ) -> ReviewPackage:
        system_warnings: list[dict] = []
        validation_reports: list[dict] = []

        # Step 2: policy retrieval
        policy_data, retrieved_chunks, criterion_citations, policy_warnings = self._run_policy_agent(
            procedure_code, payer_name,
        )
        system_warnings.extend(policy_warnings)

        # Validate POLICY_AGENT output against taxonomy expectations.
        # This is the first defensive boundary: warn loudly if POLICY_AGENT
        # returned codes the taxonomy doesn't know about, or missed codes
        # it should have produced.
        policy_codes = self._extract_policy_codes(policy_data)
        if policy_codes:
            report = validate_criteria_for_cpt(
                policy_codes, procedure_code, payer_name, stage="policy_agent_output",
            )
            validation_reports.append(report.to_dict())
            system_warnings.extend(report.warnings)

        # Step 3 + 3b: reasoning (+ optional taxonomy matcher in parallel)
        reasoning, tax_result, reasoning_warnings = self._run_reasoning_and_taxonomy(
            chart_data, policy_data, procedure_code, payer_name, case_id,
            run_taxonomy_matcher=run_taxonomy_matcher,
        )
        system_warnings.extend(reasoning_warnings)

        taxonomy_match = None
        if tax_result is not None:
            from cardioauth.taxonomy import record_emerging_criterion
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

        # Validate reasoner output against taxonomy expectations.
        reasoner_matches = self._extract_reasoner_matches(reasoning, taxonomy_match)
        reasoner_codes = [m.get("code", "") for m in reasoner_matches if m.get("code")]
        if reasoner_codes:
            report = validate_criteria_for_cpt(
                reasoner_codes, procedure_code, payer_name, stage="reasoner_output",
            )
            validation_reports.append(report.to_dict())
            system_warnings.extend(report.warnings)

        # Build the per-criterion audit trail — the single artifact that
        # shows whether every applicable criterion made it through every
        # stage. Peter can point at this list to answer "did EX-001 get
        # evaluated?" without tracing logs.
        audit_trail = build_audit_trail(
            cpt_code=procedure_code,
            payer=payer_name,
            policy_codes=policy_codes,
            reasoner_matches=reasoner_matches,
        )
        audit_trail_dict = trail_to_dict(audit_trail)

        silently_dropped = [e.code for e in audit_trail if "reasoner_skipped" in e.flags]
        if silently_dropped:
            logger.warning(
                "ORCHESTRATOR: %d criteria silently skipped by reasoner for CPT %s: %s",
                len(silently_dropped), procedure_code, silently_dropped,
            )

        # Assemble human-review flags
        requires_human_action = self._collect_review_flags(
            chart_data, policy_data, reasoning, audit_trail,
        )

        logger.info(
            "ORCHESTRATOR: review package ready — approval=%s (%s), flags=%d, audit=%d codes, warnings=%d",
            reasoning.approval_likelihood_score,
            reasoning.approval_likelihood_label,
            len(requires_human_action),
            len(audit_trail_dict),
            len(system_warnings),
        )

        # Pull calibration metadata out of policy_data side-band attrs.
        # Fall back to direct lookup when POLICY_AGENT didn't run (demo path
        # or agent failure) — stats/rules are hand-curated so always available.
        payer_stats = policy_data.__dict__.get("_payer_stats") if policy_data else None
        payer_global_rules = policy_data.__dict__.get("_payer_global_rules", []) if policy_data else []
        policy_freshness = policy_data.__dict__.get("_freshness") if policy_data else None

        if payer_stats is None or not payer_global_rules or policy_freshness is None:
            try:
                from cardioauth.stats import (
                    check_policy_freshness,
                    get_global_rules,
                    get_payer_stats,
                )
                if payer_stats is None:
                    s = get_payer_stats(payer_name, procedure_code)
                    payer_stats = s.to_dict() if s else None
                if not payer_global_rules:
                    payer_global_rules = [r.to_dict() for r in get_global_rules(payer_name)]
                if policy_freshness is None:
                    f = check_policy_freshness(
                        getattr(policy_data, "policy_last_updated", "") if policy_data else "",
                    )
                    policy_freshness = {"level": f.level, "age_days": f.age_days, "message": f.message}
            except Exception as e:
                logger.warning("Calibration metadata fallback failed: %s", e)

        # Surface stale-policy + missing-global-rule signals as human flags
        if policy_freshness and policy_freshness.get("level") == "stale_critical":
            requires_human_action.append(
                f"Policy is {policy_freshness.get('age_days')} days old (stale). "
                f"Re-verify current payer policy before submission."
            )

        return ReviewPackage(
            chart_data=chart_data,
            policy_data=policy_data,
            reasoning=reasoning,
            requires_human_action=requires_human_action,
            taxonomy_match=taxonomy_match,
            system_warnings=system_warnings,
            retrieved_chunks=retrieved_chunks,
            criterion_citations=criterion_citations,
            criterion_audit_trail=audit_trail_dict,
            validation_reports=validation_reports,
            payer_stats=payer_stats,
            payer_global_rules=payer_global_rules,
            policy_freshness=policy_freshness,
        )

    def _run_policy_agent(
        self,
        procedure_code: str,
        payer_name: str,
    ) -> tuple[PolicyData, list[dict], list[dict], list[dict]]:
        """Run POLICY_AGENT (or demo fallback) and return policy + RAG metadata + warnings."""
        from cardioauth.demo import get_demo_policy

        warnings: list[dict] = []
        retrieved_chunks: list[dict] = []
        criterion_citations: list[dict] = []

        logger.info("ORCHESTRATOR: Step 2 — POLICY_AGENT")

        if self.config.anthropic_api_key:
            try:
                from cardioauth.agents.policy_agent import PolicyAgent
                from cardioauth.integrations.cms_coverage import get_cms_coverage_context
                cms_context = get_cms_coverage_context(procedure_code)
                policy_data = PolicyAgent(self.config).run(
                    procedure_code, payer_name, cms_context=cms_context,
                )
                retrieved_chunks = policy_data.__dict__.get("_retrieved_chunks", [])
                criterion_citations = policy_data.__dict__.get("_criterion_citations", [])
                logger.info(
                    "ORCHESTRATOR: POLICY_AGENT succeeded — %d chunks, %d citations",
                    len(retrieved_chunks),
                    sum(len(c.get("citations", [])) for c in criterion_citations),
                )
                return policy_data, retrieved_chunks, criterion_citations, warnings
            except Exception as e:
                w = _classify_anthropic_error(e)
                w["agent"] = "POLICY_AGENT"
                warnings.append(w)
                logger.warning("POLICY_AGENT failed (%s), falling back to demo policy", e)

        try:
            policy_data = get_demo_policy(procedure_code, payer_name)
        except Exception as e:
            logger.warning("Demo policy fallback failed: %s", e)
            policy_data = PolicyData(
                payer=payer_name,
                procedure="",
                cpt_code=procedure_code,
                auth_required=None,
                clinical_criteria=[],
            )
        return policy_data, retrieved_chunks, criterion_citations, warnings

    def _run_reasoning_and_taxonomy(
        self,
        chart_data: ChartData,
        policy_data: PolicyData,
        procedure_code: str,
        payer_name: str,
        case_id: str,
        run_taxonomy_matcher: bool,
    ) -> tuple[ReasoningResult, Any, list[dict]]:
        """Run REASONING + optional TAXONOMY_MATCHER in parallel. Returns (reasoning, tax_result, warnings)."""
        from concurrent.futures import ThreadPoolExecutor

        warnings: list[dict] = []

        def _run_reasoning() -> tuple[ReasoningResult, dict | None]:
            if not self.config.anthropic_api_key:
                from cardioauth.demo import get_demo_reasoning
                return get_demo_reasoning(chart_data, policy_data), None

            if _use_unified_reasoner():
                try:
                    return self._run_unified_reasoner(
                        chart_data, policy_data, procedure_code, payer_name, case_id,
                    ), None
                except Exception as e:
                    logger.warning("UnifiedReasoner failed (%s), falling back to legacy ReasoningAgent", e)
                    w = _classify_anthropic_error(e)
                    w["agent"] = "UNIFIED_REASONER"
                    # fall through to legacy path with warning recorded below
                    legacy_warn = w
                    try:
                        from cardioauth.agents.reasoning_agent import ReasoningAgent
                        return ReasoningAgent(self.config).run(chart_data, policy_data), legacy_warn
                    except Exception as e2:
                        logger.warning("Legacy ReasoningAgent also failed: %s", e2)
                        from cardioauth.demo import get_demo_reasoning
                        return get_demo_reasoning(chart_data, policy_data), legacy_warn

            try:
                from cardioauth.agents.reasoning_agent import ReasoningAgent
                return ReasoningAgent(self.config).run(chart_data, policy_data), None
            except Exception as e:
                w = _classify_anthropic_error(e)
                w["agent"] = "REASONING_AGENT"
                logger.warning("REASONING_AGENT failed (%s), using demo reasoning", e)
                from cardioauth.demo import get_demo_reasoning
                return get_demo_reasoning(chart_data, policy_data), w

        def _run_taxonomy() -> tuple[Any, dict | None]:
            if not run_taxonomy_matcher or not self.config.anthropic_api_key:
                return None, None
            try:
                from cardioauth.taxonomy import match_case_to_taxonomy
                return match_case_to_taxonomy(
                    chart_data.model_dump(),
                    procedure_code, payer_name, self.config,
                    case_id=case_id,
                ), None
            except Exception as e:
                w = _classify_anthropic_error(e)
                w["agent"] = "TAXONOMY_MATCHER"
                logger.warning("TAXONOMY_MATCHER failed: %s", e)
                return None, w

        logger.info("ORCHESTRATOR: Step 3 — reasoning (taxonomy_matcher=%s)", run_taxonomy_matcher)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_reason = pool.submit(_run_reasoning)
            fut_tax = pool.submit(_run_taxonomy)
            reasoning, reason_warn = fut_reason.result()
            tax_result, tax_warn = fut_tax.result()

        if reason_warn:
            warnings.append(reason_warn)
        if tax_warn:
            warnings.append(tax_warn)

        return reasoning, tax_result, warnings

    def _run_unified_reasoner(
        self,
        chart_data: ChartData,
        policy_data: PolicyData,
        procedure_code: str,
        payer_name: str,
        case_id: str,
    ) -> ReasoningResult:
        """Run UnifiedReasoner and translate CaseContext output into ReasoningResult shape."""
        from cardioauth.agents.relationship_extractor import extract_relationships
        from cardioauth.agents.unified_reasoner import reason_with_unified_agent
        from cardioauth.case_context import CaseContext
        from cardioauth.models.reasoning import CriterionEvaluation, CriterionGap

        ctx = CaseContext(
            case_id=case_id,
            procedure_code=procedure_code,
            procedure_name=chart_data.procedure_requested or procedure_code,
            payer_name=payer_name,
            chart_data=chart_data.model_dump(),
            policy_data=policy_data.model_dump() if policy_data else {},
        )
        ctx.build_clinical_narrative()
        try:
            extract_relationships(ctx, self.config)
        except Exception as e:
            logger.warning("Relationship extraction failed: %s", e)

        reason_with_unified_agent(ctx, self.config)

        criteria_met = []
        criteria_not_met = []
        for m in ctx.criterion_matches:
            code = m.get("code", "")
            if m.get("status") == "met":
                criteria_met.append(CriterionEvaluation(
                    criterion=f"{code}: {m.get('reasoning', '')[:100]}",
                    met=True,
                    evidence=m.get("evidence_quote", "") or m.get("reasoning", "")[:200],
                    confidence=float(m.get("confidence", 0.8)),
                ))
            elif m.get("status") == "not_met":
                criteria_not_met.append(CriterionGap(
                    criterion=f"{code}: {m.get('reasoning', '')[:100]}",
                    gap=m.get("gap", "") or m.get("reasoning", "")[:200],
                    recommendation=m.get("recommendation", ""),
                ))

        label = ctx.approval_label if ctx.approval_label in ("HIGH", "MEDIUM", "LOW", "DO NOT SUBMIT") else "LOW"
        if ctx.approval_label == "INSUFFICIENT":
            label = "DO NOT SUBMIT"

        result = ReasoningResult(
            criteria_met=criteria_met,
            criteria_not_met=criteria_not_met,
            approval_likelihood_score=ctx.approval_score,
            approval_likelihood_label=label,
            pa_narrative_draft=ctx.narrative_draft or "Narrative not generated.",
            cardiologist_review_flags=[],
        )
        # Stash the raw matches and ctx warnings so the audit builder can see them
        result.__dict__["_unified_matches"] = ctx.criterion_matches
        if ctx.system_warnings:
            result.__dict__["_unified_warnings"] = list(ctx.system_warnings)
        return result

    # ── extraction helpers ────────────────────────────────────────────

    def _extract_policy_codes(self, policy_data: PolicyData | None) -> list[str]:
        """Pull criterion codes out of policy_data.clinical_criteria.

        POLICY_AGENT returns natural-language criterion strings, not taxonomy
        codes, so we reverse-map via short_name. Returns empty if nothing maps.
        """
        if policy_data is None:
            return []
        from cardioauth.taxonomy.taxonomy import CRITERION_TAXONOMY
        by_short = {c.short_name.lower(): c.code for c in CRITERION_TAXONOMY.values()}
        codes: list[str] = []
        for c in policy_data.clinical_criteria or []:
            label = getattr(c, "criterion", "") or (c.get("criterion") if isinstance(c, dict) else "")
            if not label:
                continue
            lower = label.lower().strip()
            if lower in CRITERION_TAXONOMY:
                codes.append(lower)
                continue
            for k, v in by_short.items():
                if k and (k in lower or lower in k):
                    codes.append(v)
                    break
        return codes

    def _extract_reasoner_matches(
        self,
        reasoning: ReasoningResult,
        taxonomy_match: dict | None,
    ) -> list[dict]:
        """Normalize reasoner output to [{code, status, _enforced?, ...}]."""
        unified = reasoning.__dict__.get("_unified_matches") if reasoning else None
        if unified:
            return list(unified)

        if taxonomy_match and taxonomy_match.get("matches"):
            return list(taxonomy_match["matches"])

        matches: list[dict] = []
        if reasoning is None:
            return matches
        codes_from_result = extract_reasoner_codes(reasoning)
        seen: set[str] = set()
        for c in reasoning.criteria_met or []:
            label = getattr(c, "criterion", "")
            code = next((cc for cc in codes_from_result if cc and cc in label), None)
            if code and code not in seen:
                matches.append({"code": code, "status": "met", "evidence_quote": getattr(c, "evidence", "")})
                seen.add(code)
        for c in reasoning.criteria_not_met or []:
            label = getattr(c, "criterion", "")
            code = next((cc for cc in codes_from_result if cc and cc in label), None)
            if code and code not in seen:
                matches.append({"code": code, "status": "not_met", "gap": getattr(c, "gap", "")})
                seen.add(code)
        return matches

    def _collect_review_flags(
        self,
        chart_data: ChartData,
        policy_data: PolicyData,
        reasoning: ReasoningResult,
        audit_trail: list,
    ) -> list[str]:
        flags: list[str] = []

        if chart_data.confidence_score < self.config.chart_confidence_threshold:
            flags.append(
                f"Chart data confidence is {chart_data.confidence_score:.0%} "
                f"(threshold: {self.config.chart_confidence_threshold:.0%}). "
                f"Missing: {', '.join(chart_data.missing_fields)}"
            )

        if policy_data and policy_data.auth_required is None:
            flags.append(
                "Could not determine if prior auth is required for this payer/procedure. "
                "Manual verification needed."
            )

        if reasoning.approval_likelihood_score < self.config.approval_likelihood_threshold:
            flags.append(
                f"Approval likelihood is {reasoning.approval_likelihood_label} "
                f"({reasoning.approval_likelihood_score:.0%}). "
                "Consider strengthening the chart before submission."
            )

        if reasoning.cardiologist_review_flags:
            flags.extend(reasoning.cardiologist_review_flags)

        skipped = [e.code for e in audit_trail if "reasoner_skipped" in e.flags]
        if skipped:
            flags.append(
                f"{len(skipped)} applicable criteria were skipped by the reasoner "
                f"and filled in as not_met: {', '.join(skipped)}. Review the audit trail."
            )

        unexpected = [e.code for e in audit_trail if "unexpected_code" in e.flags]
        if unexpected:
            flags.append(
                f"Reasoner returned codes not applicable to this CPT: {', '.join(unexpected)}. "
                "Possible hallucination — verify against taxonomy."
            )

        return flags

    def submit_after_approval(
        self,
        review: ReviewPackage,
        approved_by: str,
    ) -> SubmissionResult:
        """Submit the PA after the cardiologist has approved.

        Generates the submission-packet PDF and routes it through the
        pluggable channel layer (mock/fax/portal-stub). The channel actually
        transmits (or queues) the package — this isn't just building it.
        """
        if not approved_by:
            raise ValueError("Cannot submit without an identified approver")

        logger.info("ORCHESTRATOR: Step 4 — SUBMISSION_AGENT (approved by %s)", approved_by)

        # Build the payer-ready PDF so the channel has something to transmit.
        pdf_bytes = b""
        cover_summary = ""
        try:
            from cardioauth.pdf_generator import generate_submission_packet
            cover_summary = (review.reasoning.pa_narrative_draft or "")[:600]
            # Trim cover_summary to ~80 words like the API endpoint does
            words = cover_summary.split()
            cover_summary = " ".join(words[:80]) + ("…" if len(words) > 80 else "")
            pdf_bytes = generate_submission_packet(
                chart_data=review.chart_data.model_dump(),
                policy_data=review.policy_data.model_dump(),
                reasoning=review.reasoning.model_dump(),
                cover_summary=cover_summary,
                criterion_audit_trail=review.criterion_audit_trail,
                raw_note="",
            )
        except Exception as e:
            logger.warning("ORCHESTRATOR: PDF generation failed, submitting without attachment: %s", e)

        return self.submission_agent.submit(
            patient_id=review.chart_data.patient_id,
            payer=review.policy_data.payer,
            procedure=review.chart_data.procedure_requested,
            reasoning=review.reasoning,
            submission_channel=review.policy_data.submission_format,
            approved_by=approved_by,
            cpt_code=review.chart_data.procedure_code or review.policy_data.cpt_code or "",
            pdf_bytes=pdf_bytes,
            cover_summary=cover_summary,
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
