"""Tests for the MVP-workflow gate (Peter Apr 28).

- Essentials missing → BLOCKED_MISSING_ESSENTIALS (hard block).
- Reasoner concerns / weak score / DEFER band → HELD_FOR_REVIEW
  (submission package built but not transmitted, awaiting human decision).
- All clear → submission proceeds normally.
"""

from __future__ import annotations

from cardioauth.demo_e2e import _critical_field_gaps
from cardioauth.models.chart import ChartData


def _complete_chart(**overrides) -> ChartData:
    defaults = dict(
        patient_id="P-001",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        patient_name="Jane Synthetic",
        date_of_birth="1958-01-15",
        age=67,
        sex="F",
        attending_physician="Dr. John Doe",
        attending_npi="1234567890",
        insurance_id="UHC456789",
        payer_name="UnitedHealthcare",
        diagnosis_codes=["I25.10"],
        confidence_score=0.9,
    )
    defaults.update(overrides)
    return ChartData(**defaults)


# ── _critical_field_gaps now includes payer, drops primary symptom ──

def test_complete_chart_no_essentials_missing() -> None:
    assert _critical_field_gaps(_complete_chart()) == []


def test_missing_payer_now_blocks() -> None:
    """Apr 28: payer_name is now in the essentials list."""
    chart = _complete_chart(payer_name="")
    assert "Payer" in _critical_field_gaps(chart)


def test_missing_symptom_does_not_block() -> None:
    """Apr 28: primary symptom moved out of essentials — clinical content
    is the physician's call to fix or accept on the holding queue.
    """
    chart = _complete_chart()
    chart.current_symptoms = []
    assert _critical_field_gaps(chart) == []


def test_missing_patient_name_still_blocks() -> None:
    chart = _complete_chart(patient_name="")
    assert "Patient name" in _critical_field_gaps(chart)


def test_essentials_set_is_six_items() -> None:
    """Sanity: essentials are exactly the six identification fields."""
    blank = ChartData(
        patient_id="P-001",
        procedure_requested="",
        procedure_code="",
    )
    gaps = _critical_field_gaps(blank)
    assert set(gaps) == {
        "Patient name", "Date of birth", "Member ID",
        "Payer", "CPT code", "Ordering physician",
    }


# ── Gate behavior: low score does NOT hard-block when essentials present ──

def test_low_score_holds_for_review_not_blocked(monkeypatch) -> None:
    """Apr 28 MVP-workflow: reasoner LOW score → HELD_FOR_REVIEW (queue),
    not BLOCKED_BY_REASONER. Essentials are present so the case still
    becomes a submission package the office can review.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from cardioauth.models.reasoning import ReasoningResult
    from cardioauth import demo as demo_mod

    def fake(chart, policy):
        return ReasoningResult(
            approval_likelihood_score=0.30,
            approval_likelihood_label="LOW",
            pa_narrative_draft="Weak case.",
            criteria_not_met=[],
        )
    monkeypatch.setattr(demo_mod, "get_demo_reasoning", fake)

    from cardioauth.demo_e2e import run_end_to_end_demo
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    assert timeline.outcome == "HELD_FOR_REVIEW"


def test_uncertain_band_holds_for_review(monkeypatch) -> None:
    """Score in 0.5-0.65 still routes to holding queue (was DEFERRED before)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from cardioauth.models.reasoning import ReasoningResult
    from cardioauth import demo as demo_mod

    def fake(chart, policy):
        return ReasoningResult(
            approval_likelihood_score=0.55,
            approval_likelihood_label="MEDIUM",
            pa_narrative_draft="Borderline.",
            criteria_not_met=[],
        )
    monkeypatch.setattr(demo_mod, "get_demo_reasoning", fake)

    from cardioauth.demo_e2e import run_end_to_end_demo
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    assert timeline.outcome == "HELD_FOR_REVIEW"


def test_high_score_proceeds_to_submission(monkeypatch) -> None:
    """0.85 / HIGH + no alt-modality + no critical gaps → submits normally.

    DEMO-001 is configured for CPT 93458 (cath), which doesn't trigger the
    alt-modality suggester. Using 78492 here would hold-for-review because
    the alt-modality (SPECT suggestion for PET without BMI 35+) is itself a
    medium-severity warning by design.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from cardioauth.models.reasoning import ReasoningResult
    from cardioauth import demo as demo_mod

    def fake(chart, policy):
        return ReasoningResult(
            approval_likelihood_score=0.85,
            approval_likelihood_label="HIGH",
            pa_narrative_draft="Strong.",
            criteria_not_met=[],
        )
    monkeypatch.setattr(demo_mod, "get_demo_reasoning", fake)

    from cardioauth.demo_e2e import run_end_to_end_demo
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="93458",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    # Should proceed past the gate to the scripted outcome
    assert timeline.outcome != "HELD_FOR_REVIEW"
    assert timeline.outcome != "BLOCKED_MISSING_ESSENTIALS"


def test_held_for_review_step_runs_form_preview(monkeypatch) -> None:
    """When held for review, the timeline still includes the payer form
    preview so the physician sees what the submission package looks like.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from cardioauth.models.reasoning import ReasoningResult
    from cardioauth import demo as demo_mod

    def fake(chart, policy):
        return ReasoningResult(
            approval_likelihood_score=0.30,
            approval_likelihood_label="LOW",
            pa_narrative_draft="",
            criteria_not_met=[],
        )
    monkeypatch.setattr(demo_mod, "get_demo_reasoning", fake)

    from cardioauth.demo_e2e import run_end_to_end_demo
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    agents = [s.agent for s in timeline.steps]
    assert "PayerFormMapper" in agents
    # The submission step shows up but as skipped, not a real transmit
    sub = next(s for s in timeline.steps if s.agent == "SUBMISSION_AGENT")
    assert sub.status == "skipped"
