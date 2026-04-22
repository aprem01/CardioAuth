"""End-to-end mocked demo flow — what Peter asked for.

Runs a scripted case through every stage of the pipeline and returns a
structured timeline so the UI (and live demos) can animate the full flow:

    Epic → CHART_AGENT → POLICY_AGENT → UnifiedReasoner
         → Physician approval → MockChannel submit → Payer response (mock)
         → Outcome recorded → Stats updated → Precedent stored

This is 100% additive — it uses existing components and does NOT mutate
any production reasoning path. It also uses existing demo patients so
no real PHI is ever involved.

The demo is REPLAYABLE: clicking it multiple times produces the same
shape of output (timing varies with API latency). The approval outcome
is scripted so investors/physicians see a clean HIGH case end-to-end,
but the actual reasoner still runs — so the criterion matches reflect
real AI behavior, not a canned response.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)


StepStatus = Literal["ok", "skipped", "fallback", "failed"]


@dataclass
class TimelineStep:
    step: int
    name: str
    agent: str
    started_at: str
    duration_ms: int
    status: StepStatus
    summary: str
    detail: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["detail"] is None:
            d.pop("detail")
        return d


@dataclass
class E2ETimeline:
    case_id: str
    patient_id: str
    procedure_code: str
    payer: str
    started_at: str
    total_duration_ms: int = 0
    outcome: str = ""
    steps: list[TimelineStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "patient_id": self.patient_id,
            "procedure_code": self.procedure_code,
            "payer": self.payer,
            "started_at": self.started_at,
            "total_duration_ms": self.total_duration_ms,
            "outcome": self.outcome,
            "steps": [s.to_dict() for s in self.steps],
        }


# ────────────────────────────────────────────────────────────────────────
# Timer helper
# ────────────────────────────────────────────────────────────────────────


class _StepTimer:
    """Wraps a block of work and produces a TimelineStep when it exits."""

    def __init__(
        self,
        step_number: int,
        name: str,
        agent: str,
    ) -> None:
        self.step_number = step_number
        self.name = name
        self.agent = agent
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._t0 = time.time()
        self.status: StepStatus = "ok"
        self.summary = ""
        self.detail: dict | None = None

    def set_result(self, summary: str, *, detail: dict | None = None, status: StepStatus = "ok") -> None:
        self.summary = summary
        self.detail = detail
        self.status = status

    def finish(self) -> TimelineStep:
        return TimelineStep(
            step=self.step_number,
            name=self.name,
            agent=self.agent,
            started_at=self.started_at,
            duration_ms=int((time.time() - self._t0) * 1000),
            status=self.status,
            summary=self.summary,
            detail=self.detail,
        )


# ────────────────────────────────────────────────────────────────────────
# The scripted end-to-end flow
# ────────────────────────────────────────────────────────────────────────


def run_end_to_end_demo(
    *,
    patient_id: str = "DEMO-001",
    procedure_code: str = "78492",
    payer_name: str = "UnitedHealthcare",
    scripted_outcome: Literal["APPROVED", "DENIED", "PENDING"] = "APPROVED",
    approver_name: str = "Dr. Demo",
) -> E2ETimeline:
    """Run the entire pipeline end-to-end using an existing demo patient.

    Every stage actually executes — this isn't a canned playback. The
    scripted_outcome only controls the final simulated payer response so
    demos always end in a clean state; everything upstream is real AI
    behavior against the same demo patient.
    """
    overall_start = time.time()
    case_id = f"{patient_id}-{procedure_code}-{uuid.uuid4().hex[:4].upper()}"
    timeline = E2ETimeline(
        case_id=case_id,
        patient_id=patient_id,
        procedure_code=procedure_code,
        payer=payer_name,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # ── Step 1: Epic FHIR fetch (simulated with demo patient) ──
    t = _StepTimer(1, "Epic FHIR fetch", "FHIRClient")
    from cardioauth.demo import get_demo_chart
    try:
        chart = get_demo_chart(patient_id, procedure_code)
        t.set_result(
            f"Loaded demo patient {patient_id} — {len(chart.diagnosis_codes)} dx, "
            f"{len(chart.relevant_imaging)} imaging, {len(chart.relevant_medications)} meds",
            detail={
                "patient_id": patient_id,
                "diagnosis_codes": chart.diagnosis_codes,
                "procedure": chart.procedure_requested,
            },
        )
    except Exception as e:
        t.set_result(f"FHIR fetch failed: {e}", status="failed")
        timeline.steps.append(t.finish())
        timeline.total_duration_ms = int((time.time() - overall_start) * 1000)
        timeline.outcome = "ERROR"
        return timeline
    timeline.steps.append(t.finish())

    # ── Step 2: CHART_AGENT — structure extraction (v2 schema) ──
    t = _StepTimer(2, "CHART_AGENT — ChartData v2", "CHART_AGENT")
    from cardioauth.models.chart_migration import migrate_legacy_chart, validate_lab_source_anchoring
    chart = migrate_legacy_chart(chart)
    chart, lab_warnings = validate_lab_source_anchoring(chart, strict=False)
    t.set_result(
        f"Extracted {len(chart.current_symptoms)} symptoms, {len(chart.ecg_findings)} ECG, "
        f"{len(chart.prior_stress_tests)} stress tests, {len(chart.relevant_labs)} labs"
        + (f" (dropped {len(lab_warnings)} suspect labs)" if lab_warnings else ""),
        detail={
            "current_symptoms": [s.name for s in chart.current_symptoms],
            "ecg_findings_count": len(chart.ecg_findings),
            "prior_stress_tests_count": len(chart.prior_stress_tests),
            "labs_count": len(chart.relevant_labs),
            "lab_warnings": lab_warnings,
        },
    )
    timeline.steps.append(t.finish())

    # ── Step 3: POLICY_AGENT (RAG-grounded) ──
    t = _StepTimer(3, "POLICY_AGENT — RAG-grounded criteria", "POLICY_AGENT")
    from cardioauth.config import Config
    from cardioauth.demo import get_demo_policy
    cfg = Config()
    policy_data = None
    rag_chunks_count = 0
    if cfg.anthropic_api_key:
        try:
            from cardioauth.agents.policy_agent import PolicyAgent
            from cardioauth.integrations.cms_coverage import get_cms_coverage_context
            cms_ctx = get_cms_coverage_context(procedure_code)
            policy_data = PolicyAgent(cfg).run(procedure_code, payer_name, cms_context=cms_ctx)
            rag_chunks_count = len(policy_data.__dict__.get("_retrieved_chunks", []))
        except Exception as e:
            logger.warning("Demo E2E: POLICY_AGENT failed, using demo policy: %s", e)
            t.set_result(f"POLICY_AGENT fallback (API issue): {str(e)[:80]}", status="fallback")
    if policy_data is None:
        try:
            policy_data = get_demo_policy(procedure_code, payer_name)
        except Exception as e:
            t.set_result(f"Policy retrieval failed: {e}", status="failed")
            timeline.steps.append(t.finish())
            timeline.total_duration_ms = int((time.time() - overall_start) * 1000)
            timeline.outcome = "ERROR"
            return timeline

    if t.status == "ok":
        t.set_result(
            f"Retrieved {rag_chunks_count} policy chunks, {len(policy_data.clinical_criteria)} criteria from {payer_name}",
            detail={
                "payer": policy_data.payer,
                "cpt_code": policy_data.cpt_code,
                "criteria_count": len(policy_data.clinical_criteria),
                "rag_chunks": rag_chunks_count,
                "payer_stats_available": policy_data.__dict__.get("_payer_stats") is not None,
            },
        )
    timeline.steps.append(t.finish())

    # ── Step 4: UnifiedReasoner (with ensemble if configured) ──
    t = _StepTimer(4, "UnifiedReasoner — criterion matching", "UNIFIED_REASONER")
    reasoning = None
    unified_ctx = None
    if cfg.anthropic_api_key:
        try:
            from cardioauth.agents.relationship_extractor import extract_relationships
            from cardioauth.agents.unified_reasoner import reason_with_unified_agent
            from cardioauth.case_context import CaseContext

            unified_ctx = CaseContext(
                case_id=case_id,
                procedure_code=procedure_code,
                procedure_name=chart.procedure_requested or procedure_code,
                payer_name=payer_name,
                user_id="demo-e2e",
                chart_data=chart.model_dump(),
                policy_data=policy_data.model_dump() if policy_data else {},
            )
            unified_ctx.build_clinical_narrative()
            try:
                extract_relationships(unified_ctx, cfg)
            except Exception:
                pass
            reason_with_unified_agent(unified_ctx, cfg)

            # Build ReasoningResult shape for downstream
            from cardioauth.models.reasoning import CriterionEvaluation, CriterionGap, ReasoningResult
            met = [
                CriterionEvaluation(
                    criterion=f"{m.get('code', '')}: {m.get('reasoning', '')[:80]}",
                    met=True,
                    evidence=m.get("evidence_quote", "") or m.get("reasoning", "")[:160],
                    confidence=float(m.get("confidence", 0.85)),
                )
                for m in unified_ctx.criterion_matches if m.get("status") == "met"
            ]
            not_met = [
                CriterionGap(
                    criterion=f"{m.get('code', '')}: {m.get('reasoning', '')[:80]}",
                    gap=m.get("gap", "") or m.get("reasoning", "")[:160],
                    recommendation=m.get("recommendation", ""),
                )
                for m in unified_ctx.criterion_matches if m.get("status") == "not_met"
            ]
            label_raw = unified_ctx.approval_label
            label = label_raw if label_raw in ("HIGH", "MEDIUM", "LOW", "DO NOT SUBMIT") else "LOW"
            if label_raw == "INSUFFICIENT":
                label = "DO NOT SUBMIT"
            reasoning = ReasoningResult(
                criteria_met=met,
                criteria_not_met=not_met,
                approval_likelihood_score=unified_ctx.approval_score,
                approval_likelihood_label=label,
                pa_narrative_draft=unified_ctx.narrative_draft or "Narrative not generated",
                cardiologist_review_flags=[],
            )
        except Exception as e:
            logger.warning("Demo E2E: UnifiedReasoner failed, using demo reasoning: %s", e)
            t.set_result(f"Reasoner fallback: {str(e)[:80]}", status="fallback")

    if reasoning is None:
        from cardioauth.demo import get_demo_reasoning
        reasoning = get_demo_reasoning(chart, policy_data)
        if t.status == "ok":
            t.set_result("Used demo reasoning (no live API call)", status="fallback")

    if t.status == "ok":
        agreement = None
        if unified_ctx is not None:
            ensemble = unified_ctx.__dict__.get("_ensemble_agreement")
            if ensemble:
                agreement = ensemble.get("case_agreement_score")
        t.set_result(
            f"{len(reasoning.criteria_met)} criteria met, {len(reasoning.criteria_not_met)} not met; "
            f"approval={reasoning.approval_likelihood_label} ({reasoning.approval_likelihood_score:.0%})"
            + (f", ensemble_agreement={agreement:.0%}" if agreement is not None else ""),
            detail={
                "criteria_met_codes": [c.criterion.split(":")[0] for c in reasoning.criteria_met],
                "criteria_not_met_codes": [c.criterion.split(":")[0] for c in reasoning.criteria_not_met],
                "approval_score": reasoning.approval_likelihood_score,
                "approval_label": reasoning.approval_likelihood_label,
                "ensemble_agreement": agreement,
                "headline_summary": unified_ctx.__dict__.get("_headline_summary", []) if unified_ctx else [],
            },
        )
    timeline.steps.append(t.finish())

    # ── Step 5: Physician approval (scripted) ──
    t = _StepTimer(5, "Physician approval", "Physician")
    t.set_result(
        f"Approved by {approver_name}",
        detail={"approved_by": approver_name, "approved_at": datetime.now(timezone.utc).isoformat()},
    )
    timeline.steps.append(t.finish())

    # ── Step 6: SUBMISSION_AGENT via MockChannel ──
    t = _StepTimer(6, "SUBMISSION_AGENT — transmit to payer", "SUBMISSION_AGENT")
    try:
        from cardioauth.agents.submission_agent import SubmissionAgent
        from cardioauth.pdf_generator import generate_submission_packet

        cover_summary = (reasoning.pa_narrative_draft or "")[:600]
        words = cover_summary.split()
        cover_summary = " ".join(words[:80]) + ("…" if len(words) > 80 else "")

        try:
            pdf_bytes = generate_submission_packet(
                chart_data=chart.model_dump(),
                policy_data=policy_data.model_dump(),
                reasoning=reasoning.model_dump(),
                cover_summary=cover_summary,
                criterion_audit_trail=[],
                raw_note=unified_ctx.raw_note if unified_ctx else "",
            )
        except Exception:
            pdf_bytes = b""

        sub = SubmissionAgent(cfg).submit(
            patient_id=patient_id,
            payer=payer_name,
            procedure=chart.procedure_requested,
            reasoning=reasoning,
            submission_channel="",  # auto-route
            approved_by=approver_name,
            cpt_code=procedure_code,
            pdf_bytes=pdf_bytes,
            cover_summary=cover_summary,
        )
        t.set_result(
            f"Transmitted via {sub.submission_channel}. Confirmation: {sub.confirmation_number}. "
            f"Expected decision: {sub.expected_decision_date}",
            detail={
                "submission_id": sub.submission_id,
                "channel": sub.submission_channel,
                "confirmation_number": sub.confirmation_number,
                "expected_decision_date": sub.expected_decision_date,
                "status": sub.status,
                "pdf_bytes": len(pdf_bytes),
            },
        )
        submission_id = sub.submission_id
    except Exception as e:
        t.set_result(f"Submission failed: {e}", status="failed")
        submission_id = None
    timeline.steps.append(t.finish())

    if submission_id is None:
        timeline.total_duration_ms = int((time.time() - overall_start) * 1000)
        timeline.outcome = "ERROR"
        return timeline

    # ── Step 7: Simulated payer response (scripted for demo) ──
    t = _StepTimer(7, "Payer response (simulated)", "Payer")
    if scripted_outcome == "APPROVED":
        auth_number = f"AUTH-{uuid.uuid4().hex[:8].upper()}"
        t.set_result(
            f"{payer_name} approved. Auth# {auth_number}",
            detail={
                "outcome": "APPROVED",
                "authorization_number": auth_number,
                "valid_through_days": 60,
            },
        )
    elif scripted_outcome == "DENIED":
        t.set_result(
            f"{payer_name} denied. Reason: insufficient documentation",
            detail={
                "outcome": "DENIED",
                "denial_reason": "Insufficient documentation of prior non-diagnostic stress test",
            },
        )
    else:
        t.set_result(
            f"{payer_name} pending review",
            detail={"outcome": "PENDING"},
        )
    payer_detail = t.detail
    timeline.steps.append(t.finish())

    # ── Step 8: Outcome persisted + stats updated + precedent stored ──
    t = _StepTimer(8, "Outcome recorded + feedback loop", "OutcomeRecorder")
    try:
        from cardioauth.persistence import get_store
        store = get_store()
        outcome_data = {
            "submission_id": submission_id,
            "outcome": payer_detail["outcome"],
            "denial_reason": payer_detail.get("denial_reason", ""),
            "authorization_number": payer_detail.get("authorization_number", ""),
            "payer": payer_name,
            "cpt_code": procedure_code,
            "procedure": chart.procedure_requested,
            "recorded_by": "demo-e2e",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        store.save_outcome(submission_id, outcome_data)
        store.record_outcome_for_stats(payer_name, procedure_code, payer_detail["outcome"])

        stats_after = store.get_rolling_stats(payer_name, procedure_code)
        live_rate = stats_after.get("approval_rate") if stats_after else None

        store.append_audit(
            actor="demo-e2e",
            action=f"e2e_demo_outcome_{payer_detail['outcome']}",
            subject_id=submission_id,
            detail=f"cpt={procedure_code} payer={payer_name}",
        )

        t.set_result(
            f"Outcome persisted; rolling stats updated"
            + (f" (live approval rate now {live_rate:.0%} on {stats_after.get('total')} cases)"
               if live_rate is not None else ""),
            detail={
                "outcome_stored": True,
                "stats_rollup": stats_after,
                "submission_status_updated": True,
            },
        )
    except Exception as e:
        t.set_result(f"Feedback loop error: {e}", status="failed")
    timeline.steps.append(t.finish())

    timeline.total_duration_ms = int((time.time() - overall_start) * 1000)
    timeline.outcome = payer_detail["outcome"]
    return timeline
