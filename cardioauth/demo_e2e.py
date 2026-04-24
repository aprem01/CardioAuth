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
    procedure_name: str = "",
    payer_name: str = "UnitedHealthcare",
    scripted_outcome: Literal["APPROVED", "DENIED", "PENDING"] = "APPROVED",
    approver_name: str = "Dr. Demo",
    raw_note: str = "",
) -> E2ETimeline:
    """Run the entire pipeline end-to-end.

    Two input modes:
      1. Demo patient: patient_id refers to an existing DEMO-00N demo chart.
         FHIR step uses the demo data.
      2. Custom deidentified note: when raw_note is provided, FHIR step is
         skipped and Claude extracts ChartData directly from the note text.
         This is Peter's "run my own case" path.

    Every stage actually executes — this isn't a canned playback. The
    scripted_outcome only controls the final simulated payer response so
    demos always end in a clean state; everything upstream is real AI
    behavior against the input supplied.
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

    # ── Step 1: Input source ──
    # Two paths: demo patient via FHIR, OR raw deidentified note (Peter's ask)
    if raw_note.strip():
        t = _StepTimer(1, "Ingest deidentified clinical note", "NoteIngest")
        t.set_result(
            f"Received {len(raw_note)} chars of clinical note text",
            detail={
                "source": "custom_note",
                "length_chars": len(raw_note),
                "length_words": len(raw_note.split()),
                "preview": raw_note[:400] + ("…" if len(raw_note) > 400 else ""),
            },
        )
        timeline.steps.append(t.finish())

        # Step 2: Extract structured ChartData from the note via Claude
        t = _StepTimer(2, "CHART_AGENT — extract from note", "CHART_AGENT")
        chart = _extract_chart_from_note(
            raw_note=raw_note,
            procedure_code=procedure_code,
            procedure_name=procedure_name or "",
            payer_name=payer_name,
            patient_id=patient_id,
        )
        from cardioauth.models.chart_migration import migrate_legacy_chart, validate_lab_source_anchoring
        chart = migrate_legacy_chart(chart)
        chart, lab_warnings = validate_lab_source_anchoring(chart, strict=False)
        t.set_result(
            f"Extracted {len(chart.current_symptoms)} symptoms, "
            f"{len(chart.ecg_findings)} ECG findings, "
            f"{len(chart.prior_stress_tests)} stress tests, "
            f"{len(chart.relevant_labs)} labs, "
            f"{len(chart.past_medical_history)} PMH"
            + (f" (dropped {len(lab_warnings)} suspect labs)" if lab_warnings else ""),
            detail={
                "chart_data": chart.model_dump(mode="json"),
                "lab_warnings": lab_warnings,
                "extraction_source": "raw_note",
            },
        )
        timeline.steps.append(t.finish())
    else:
        # Demo patient path
        t = _StepTimer(1, "Epic FHIR fetch (demo patient)", "FHIRClient")
        from cardioauth.demo import get_demo_chart
        try:
            chart = get_demo_chart(patient_id, procedure_code)
            t.set_result(
                f"Loaded demo patient {patient_id} — {len(chart.diagnosis_codes)} dx, "
                f"{len(chart.relevant_imaging)} imaging, {len(chart.relevant_medications)} meds",
                detail={
                    "patient_id": patient_id,
                    "source": "demo_fhir",
                    "diagnosis_codes": chart.diagnosis_codes,
                    "procedure": chart.procedure_requested,
                    "chart_data_pre_migration": chart.model_dump(mode="json"),
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
        t = _StepTimer(2, "CHART_AGENT — ChartData v2 migration + lab safety", "CHART_AGENT")
        from cardioauth.models.chart_migration import migrate_legacy_chart, validate_lab_source_anchoring
        chart = migrate_legacy_chart(chart)
        chart, lab_warnings = validate_lab_source_anchoring(chart, strict=False)
        t.set_result(
            f"Categorized {len(chart.current_symptoms)} symptoms, "
            f"{len(chart.ecg_findings)} ECG, "
            f"{len(chart.prior_stress_tests)} stress tests, "
            f"{len(chart.relevant_labs)} labs, "
            f"{len(chart.past_medical_history)} PMH"
            + (f" (dropped {len(lab_warnings)} suspect labs)" if lab_warnings else ""),
            detail={
                "chart_data": chart.model_dump(mode="json"),
                "lab_warnings": lab_warnings,
            },
        )
        timeline.steps.append(t.finish())

    # ── Step 3: POLICY_AGENT (RAG-grounded) ──
    t = _StepTimer(3, "POLICY_AGENT — RAG-grounded criteria", "POLICY_AGENT")
    import os
    from cardioauth.config import Config
    from cardioauth.demo import get_demo_policy
    cfg = Config()
    # Escape hatch: if DEMO_E2E_FORCE_OFFLINE=1, skip live Claude calls entirely.
    # Useful when Anthropic spend limit is hit or for reliably-fast demos.
    _force_offline = os.environ.get("DEMO_E2E_FORCE_OFFLINE", "").lower() in ("1", "true", "yes")
    policy_data = None
    rag_chunks_count = 0
    if cfg.anthropic_api_key and not _force_offline:
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
                "auth_required": policy_data.auth_required,
                "criteria_count": len(policy_data.clinical_criteria),
                "clinical_criteria": [
                    c.model_dump() if hasattr(c, "model_dump") else c
                    for c in (policy_data.clinical_criteria or [])[:30]
                ],
                "retrieved_chunks": policy_data.__dict__.get("_retrieved_chunks", [])[:8],
                "criterion_citations": policy_data.__dict__.get("_criterion_citations", []),
                "payer_stats": policy_data.__dict__.get("_payer_stats"),
                "payer_global_rules": policy_data.__dict__.get("_payer_global_rules", []),
                "policy_freshness": policy_data.__dict__.get("_freshness"),
                "cache_hit": policy_data.__dict__.get("_cache_hit", False),
            },
        )
    timeline.steps.append(t.finish())

    # ── Step 4: UnifiedReasoner (with ensemble if configured) ──
    t = _StepTimer(4, "UnifiedReasoner — criterion matching", "UNIFIED_REASONER")
    reasoning = None
    unified_ctx = None
    if cfg.anthropic_api_key and not _force_offline:
        try:
            from copy import replace  # type: ignore
        except ImportError:
            replace = None  # type: ignore

        # Force single-run reasoning for the demo path so we fit under
        # Railway's 60s gateway timeout. Production cases still use the
        # configured ensemble N — this only affects the demo endpoint.
        from dataclasses import replace as _dc_replace
        try:
            demo_cfg = _dc_replace(cfg, reasoning_ensemble_n=1)
        except Exception:
            demo_cfg = cfg

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
                extract_relationships(unified_ctx, demo_cfg)
            except Exception:
                pass
            reason_with_unified_agent(unified_ctx, demo_cfg)

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
        gap_classification = None
        raw_matches = []
        narrative_preview = ""
        supplemental = ""
        if unified_ctx is not None:
            ensemble = unified_ctx.__dict__.get("_ensemble_agreement")
            if ensemble:
                agreement = ensemble.get("case_agreement_score")
            gap_classification = unified_ctx.__dict__.get("_gap_classification")
            raw_matches = list(unified_ctx.criterion_matches)
            narrative_preview = (unified_ctx.narrative_draft or "")[:1500]
            supplemental = unified_ctx.__dict__.get("_supplemental_clinical_argument", "")

        t.set_result(
            f"{len(reasoning.criteria_met)} criteria met, {len(reasoning.criteria_not_met)} not met; "
            f"approval={reasoning.approval_likelihood_label} ({reasoning.approval_likelihood_score:.0%})"
            + (f", ensemble_agreement={agreement:.0%}" if agreement is not None else ""),
            detail={
                "approval_score": reasoning.approval_likelihood_score,
                "approval_label": reasoning.approval_likelihood_label,
                "ensemble_agreement": agreement,
                "headline_summary": unified_ctx.__dict__.get("_headline_summary", []) if unified_ctx else [],
                # Full per-criterion matches — this is what Peter wants to audit
                "criterion_matches": raw_matches,
                # Split into blocking / alternative / supporting for clarity
                "gap_classification": gap_classification,
                # Narrative preview so physician can read the drafted argument
                "narrative_preview": narrative_preview,
                "supplemental_clinical_argument": supplemental,
            },
        )
    timeline.steps.append(t.finish())

    # ── Step 5: Physician approval (scripted) with submission gate ──
    # Peter Apr 22-23: "the system correctly identified that PET was not
    # appropriate (low approval, fatal flags), but still proceeded with
    # submission rather than blocking or suggesting the correct modality."
    # Gate: if reasoner says DO NOT SUBMIT or score < 0.5, block here.
    t = _StepTimer(5, "Physician approval + submission gate", "Physician")
    score = reasoning.approval_likelihood_score
    label = reasoning.approval_likelihood_label
    should_block = (
        label in ("DO NOT SUBMIT", "INSUFFICIENT")
        or (score is not None and score < 0.5)
    )
    alternative_modality = _suggest_alternative_modality(chart, reasoning, procedure_code)

    if should_block:
        block_reason = (
            f"Reasoner recommends against submission: {label} ({score:.0%}). "
            "The chart lacks qualifying evidence for this procedure."
        )
        detail = {
            "decision": "blocked",
            "reason": block_reason,
            "approval_score": score,
            "approval_label": label,
            "alternative_modality": alternative_modality,
            "blocking_gaps": [
                (g.criterion.split(":")[0].strip() if hasattr(g, "criterion") else (g.get("code") or ""))
                for g in (reasoning.criteria_not_met or [])
            ][:5],
        }
        t.set_result(
            f"BLOCKED — {label}, score {score:.0%}. Submission halted."
            + (f" Suggest: {alternative_modality['name']} (CPT {alternative_modality['cpt']})"
               if alternative_modality else ""),
            detail=detail,
            status="fallback",
        )
        timeline.steps.append(t.finish())
        # Skip remaining steps and return with blocked status
        timeline.total_duration_ms = int((time.time() - overall_start) * 1000)
        timeline.outcome = "BLOCKED_BY_REASONER"
        return timeline

    t.set_result(
        f"Approved by {approver_name} — proceeding to submission (reasoner: {label}, {score:.0%})",
        detail={
            "decision": "approved",
            "approved_by": approver_name,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approval_score": score,
            "approval_label": label,
        },
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
                "follow_up_scheduled": sub.follow_up_scheduled,
                "status": sub.status,
                "channel_notes": sub.__dict__.get("_channel_notes", ""),
                "requires_human_action": sub.__dict__.get("_channel_requires_human_action", ""),
                # The full payload that goes to the payer (minus the PDF bytes)
                "submission_payload": {
                    "submission_id": sub.submission_id,
                    "patient_id": sub.patient_id,
                    "payer": sub.payer,
                    "procedure": sub.procedure,
                    "cpt_code": procedure_code,
                    "cover_summary": cover_summary,
                    "audit_trail": [a.model_dump() if hasattr(a, "model_dump") else a
                                    for a in sub.audit_trail],
                },
                "pdf_bytes_size": len(pdf_bytes),
                "pdf_available": bool(pdf_bytes),
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

    # ── Step 7 (was 7, now inserted before payer response): Payer form preview ──
    # Peter's Apr 22 ask: map extracted data into the actual payer form
    # structure so we can see which fields are populated vs missing.
    t = _StepTimer(7, "Payer form preview", "PayerFormMapper")
    try:
        from cardioauth.payer_forms import get_payer_form, populate_payer_form
        form = get_payer_form(payer_name, procedure_code)
        if form is None:
            t.set_result(
                f"No form template for {payer_name} — using generic UHC shape as fallback",
                status="fallback",
                detail={"form_available": False, "payer": payer_name},
            )
        else:
            populated = populate_payer_form(
                form,
                chart_data=chart,
                policy_data=policy_data,
                reasoning=reasoning,
            )
            counts = populated["counts"]
            summary_str = (
                f"{form.vendor} / {form.name} — "
                f"{counts['populated']}/{counts['total']} populated"
            )
            if counts["missing_required"]:
                summary_str += f", {counts['missing_required']} required MISSING"
            if counts["incomplete"]:
                summary_str += f", {counts['incomplete']} incomplete"
            if populated["ready_to_submit"]:
                summary_str += " — ready to submit"

            t.set_result(
                summary_str,
                detail=populated,
                status=("ok" if populated["ready_to_submit"] else "fallback"),
            )
    except Exception as e:
        logger.warning("Payer form preview failed: %s", e)
        t.set_result(f"Form preview error: {e}", status="failed")
    timeline.steps.append(t.finish())

    # Renumber remaining steps (original step 7 → now step 8)
    # ── Step 8: Simulated payer response (scripted for demo) ──
    t = _StepTimer(8, "Payer response (simulated)", "Payer")
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
    t = _StepTimer(9, "Outcome recorded + feedback loop", "OutcomeRecorder")
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


# ────────────────────────────────────────────────────────────────────────
# Alternative modality suggestion (Peter Apr 22-23)
# ────────────────────────────────────────────────────────────────────────


def _suggest_alternative_modality(
    chart: Any,
    reasoning: Any,
    current_cpt: str,
) -> dict | None:
    """Suggest a less expensive / less demanding modality when the current
    request is likely to fail.

    Peter's example: exercise SPECT case where PET was not appropriate —
    system should have suggested the correct modality, not proceeded.

    Simple rule-based today. Could become ML-driven when we have outcome
    data correlating (rejected CPT, patient profile) → (successful CPT).
    """
    current = (current_cpt or "").strip()

    # PET (78492) without strong justification → suggest SPECT (78452)
    # Strong justification = BMI ≥ 35 or documented attenuation artifact
    if current == "78492":
        bmi = None
        try:
            haystack = " ".join(chart.active_comorbidities or []) + " " + (chart.additional_notes or "")
            import re
            m = re.search(r"bmi\s*(?:of)?[\s:]*(\d{1,2}(?:\.\d)?)", haystack.lower())
            if m:
                bmi = float(m.group(1))
        except Exception:
            pass
        has_attenuation = any(
            "attenuation" in (s.interpretation or "").lower()
            or "attenuation" in (s.result_summary or "").lower()
            for s in (chart.prior_stress_tests or [])
        )
        if (bmi is None or bmi < 35) and not has_attenuation:
            return {
                "name": "Lexiscan/Rest SPECT",
                "cpt": "78452",
                "rationale": (
                    "PET is typically reserved for body habitus (BMI ≥ 35) or "
                    "documented attenuation artifact on prior SPECT. Neither is "
                    "clearly supported by this chart. SPECT is usually approved "
                    "first and is less costly — reserve PET for cases where SPECT "
                    "would be non-diagnostic."
                ),
            }

    # SPECT (78452) without pharmacologic-stress justification → consider ETT-only
    if current == "78452":
        # Has LBBB / paced / exercise incapacity?
        has_pharm_justification = False
        for e in (chart.ecg_findings or []):
            txt = ((e.conduction or "") + " " + (e.pacing or "") + " " + (e.summary or "")).lower()
            if any(k in txt for k in ("lbbb", "paced", "wpw")):
                has_pharm_justification = True
                break
        notes = (chart.additional_notes or "").lower()
        if "unable to exercise" in notes or "cannot exercise" in notes:
            has_pharm_justification = True
        if not has_pharm_justification:
            return {
                "name": "Exercise stress ECG (ETT)",
                "cpt": "93015",
                "rationale": (
                    "No qualifying indication for pharmacologic nuclear stress "
                    "(no LBBB, paced rhythm, WPW, or documented exercise "
                    "incapacity). Exercise ETT should be the first-line option "
                    "unless the patient genuinely cannot exercise."
                ),
            }

    return None


# ────────────────────────────────────────────────────────────────────────
# Raw-note extraction (Peter's "run my own case" path)
# ────────────────────────────────────────────────────────────────────────


_NOTE_EXTRACTION_PROMPT = """\
You are CHART_AGENT, extracting structured clinical data from a free-text
clinical note for prior authorization.

Peter's Apr 22-23 feedback: "Even with very explicit, well-formatted notes,
key elements (symptoms, prior stress test, ECG, PMH) are not being populated
into structured output, and even straightforward header fields like patient
name, DOB, sex, member ID, ordering physician, NPI are not populated."

Your job is to be THOROUGH. Extract every field you can find. If the note
mentions it, put it in the right v2 bucket.

═══════════════════════════════════════════════════════════════════════════
ALWAYS EXTRACT DEMOGRAPHICS FIRST (before clinical content)
═══════════════════════════════════════════════════════════════════════════

Top-level fields that belong at the root of the JSON, not nested:

  patient_name        — full name as it appears (synthetic in test notes)
  date_of_birth       — ISO 8601 (YYYY-MM-DD) preferred, else MM/DD/YYYY
  age                 — integer years
  sex                 — "M" | "F" | "Other"
  insurance_id        — member / subscriber ID
  payer_name          — e.g. "UnitedHealthcare", "Aetna", "Medicare"
  attending_physician — ordering physician full name
  attending_npi       — 10-digit National Provider Identifier
  procedure_code      — CPT code being requested
  procedure_requested — full procedure name
  diagnosis_codes     — list of ICD-10 codes (primary first)

These are NOT optional when the note contains them. Patterns to look for:

  Patient name        — "Patient: Jane Doe", "Name:", "Pt:", "Mrs./Mr./Ms. X"
  DOB                 — "DOB: 01/15/1958", "date of birth", "born"
  Sex                 — "Sex: F", "67 yo M", "Gender:"
  Member ID           — "Member ID:", "Policy #", "ID #", "Subscriber"
  NPI                 — "NPI: 1234567890" (10 digits)
  Ordering MD         — "Ordering:", "Attending:", "Physician:", "Dr."
  CPT                 — "CPT 78492", "procedure code"
  ICD-10              — "I25.10", codes starting with letter then digits

If the note gives age but not DOB, leave date_of_birth="" — do NOT fabricate.
If sex is written as "Male"/"Female", normalize to "M"/"F".

═══════════════════════════════════════════════════════════════════════════
THEN EXTRACT CLINICAL CONTENT (each field has one kind of data)
═══════════════════════════════════════════════════════════════════════════

  active_comorbidities     — chronic conditions (HTN, DM, CKD, CAD, COPD)
  past_medical_history     — prior events with dates (MI 2021, stroke, PCI'05)
  family_history           — first-degree relative cardiac conditions
  current_symptoms         — patient-reported or clinician-observed symptoms
                              with onset / frequency / character / severity /
                              change_vs_baseline
  exam_findings            — physical exam findings (JVD, edema, murmur,
                              gallop, bruit, rales)
  ecg_findings             — baseline ECG with structured sub-fields:
                              rhythm, conduction, hypertrophy_or_strain,
                              ischemic_changes, pacing, summary
  prior_stress_tests       — ETT, SPECT, PET, stress echo, stress MRI with
                              modality, date, max_hr_percent, interpretation,
                              result_summary
  prior_procedures         — PCI, CABG, TAVR, ablation, ICD, pacemaker
                              implant — things done TO the patient
  relevant_imaging         — echo, CT, MRI, angiography (NOT ECG,
                              NOT stress tests)
  relevant_labs            — lab values with name, value, unit, date, and
                              source_anchor (verbatim quote from note)
  relevant_medications     — name, dose, start_date or duration, indication

═══════════════════════════════════════════════════════════════════════════
PER-CATEGORY EXTRACTION PATTERNS — EXTRACT AGGRESSIVELY
═══════════════════════════════════════════════════════════════════════════

Typical patterns to extract aggressively:

  SYMPTOMS: Any mention of chest pain, dyspnea, palpitations, syncope,
  presyncope, fatigue, orthopnea, PND, edema that the PATIENT REPORTS or
  the clinician describes in HPI/assessment. Extract each as a Symptom
  object with character (typical/atypical/exertional/rest), onset
  ("3 weeks ago", "since March"), frequency ("daily", "weekly"),
  change_vs_baseline ("new", "worsening", "stable"), severity.

  ECG: Any mention of rhythm (sinus, AF, paced), conduction
  (LBBB, RBBB, LAFB, first-degree block), LVH with strain, ischemic
  changes, pacing. Break into the structured sub-fields. "ECG: NSR, LBBB"
  → rhythm="sinus rhythm (NSR)", conduction="LBBB". Do NOT stuff it all
  into the summary field.

  PRIOR STRESS TESTS: Any mention of ETT, treadmill, SPECT, MPI, stress
  echo, PET, dobutamine. Extract modality, date if given, %MPHR if given,
  interpretation (non-diagnostic / equivocal / positive / negative /
  attenuation artifact / technically limited), and result_summary.

  EXAM: Findings from physical examination — edema, JVD, murmur, S3/S4,
  rales, hepatomegaly, bruit. These are NOT symptoms (patient report) or
  comorbidities (chronic conditions).

  PMH: Prior events with years — "s/p MI 2021", "HF admission 2023",
  "stroke 2019". NOT procedures (those go to prior_procedures).

═══════════════════════════════════════════════════════════════════════════
SAFETY RULES (do not violate)
═══════════════════════════════════════════════════════════════════════════

  - Never invent labs not in the source note. Leave out rather than fabricate.
  - Never infer diagnoses from medications (levothyroxine ≠ hypothyroidism,
    atorvastatin ≠ hyperlipidemia, metformin ≠ diabetes, warfarin ≠ AF).
    A medication list evidences what the patient takes, not the diagnosis.
  - Never emit future-dated labs — set date to "" if source date is future.
  - Resolved symptoms → past_medical_history, not current_symptoms.
  - When ROS explicitly denies a symptom, do NOT include it as a current
    symptom. Current HPI + assessment outrank old problem list carryover.

═══════════════════════════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════════════════════════

Return ONLY valid JSON matching the ChartData v2 schema. Fields you cannot
find from the note should be empty arrays or empty strings — do NOT invent.
If in doubt about a categorization, include it with its best guess; better
to extract and let downstream reasoning assess than leave it out.
"""


def _extract_chart_from_note(
    *,
    raw_note: str,
    procedure_code: str,
    procedure_name: str = "",
    payer_name: str = "",
    patient_id: str = "",
) -> Any:
    """Run Claude over a deidentified note to build a ChartData v2.

    Falls back to a minimal ChartData with raw_note stashed in
    additional_notes when Anthropic is unavailable so the rest of
    the pipeline can still run.
    """
    from cardioauth.config import Config
    from cardioauth.models.chart import ChartData

    cfg = Config()

    if not cfg.anthropic_api_key:
        logger.warning("Note extraction: no API key, skeletal chart only")
        return ChartData(
            patient_id=patient_id or "CUSTOM",
            procedure_requested=procedure_name or procedure_code,
            procedure_code=procedure_code,
            payer_name=payer_name,
            diagnosis_codes=[],
            additional_notes=raw_note,
            confidence_score=0.5,
            missing_fields=["Anthropic API unavailable; only raw note preserved"],
        )

    try:
        import anthropic
        from cardioauth.claude_cost import TimedCall, system_with_cache_control, track_usage

        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        with TimedCall() as _t:
            response = client.messages.create(
                model=cfg.model,
                max_tokens=4000,
                system=system_with_cache_control(_NOTE_EXTRACTION_PROMPT),
                messages=[{
                    "role": "user",
                    "content": (
                        f"CPT: {procedure_code}\n"
                        f"Procedure: {procedure_name or procedure_code}\n"
                        f"Payer: {payer_name}\n\n"
                        f"DEIDENTIFIED CLINICAL NOTE:\n{raw_note}\n\n"
                        f"Extract ChartData v2 as JSON."
                    ),
                }],
            )
        track_usage(response, agent="CHART_AGENT_NOTE",
                    model=cfg.model, duration_ms=_t.ms,
                    case_id=patient_id or "CUSTOM")

        raw = response.content[0].text
        import json as _json
        import re as _re
        # Strip any markdown fences
        cleaned = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=_re.MULTILINE)
        data = _json.loads(cleaned)

        # Ensure required identity fields are set
        data.setdefault("patient_id", patient_id or "CUSTOM")
        data.setdefault("procedure_code", procedure_code)
        data.setdefault("procedure_requested", procedure_name or procedure_code)
        data.setdefault("payer_name", payer_name)
        data.setdefault("confidence_score", 0.85)
        data.setdefault("missing_fields", [])

        # Preserve the raw note so downstream reasoner can read it directly
        existing_notes = data.get("additional_notes", "") or ""
        if raw_note and raw_note not in existing_notes:
            data["additional_notes"] = (existing_notes + "\n\n" + raw_note).strip()

        # Only pass fields ChartData knows about
        valid_keys = set(ChartData.model_fields.keys())
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return ChartData(**filtered)

    except Exception as e:
        logger.warning("Note extraction: Claude call failed: %s — falling back to skeletal chart", e)
        return ChartData(
            patient_id=patient_id or "CUSTOM",
            procedure_requested=procedure_name or procedure_code,
            procedure_code=procedure_code,
            payer_name=payer_name,
            diagnosis_codes=[],
            additional_notes=raw_note,
            confidence_score=0.5,
            missing_fields=[f"Note extraction failed: {str(e)[:120]}"],
        )
