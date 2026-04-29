"""Tests for the raw-note path (Peter's 'run my own case' feature)."""

from __future__ import annotations

from cardioauth.demo_e2e import _extract_chart_from_note, run_end_to_end_demo


def test_extract_from_note_fallback_when_no_api_key(monkeypatch) -> None:
    """Without an API key, extraction still produces a valid ChartData."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    note = (
        "67 yo F with CAD, HTN, DM. Recent SPECT non-diagnostic due to "
        "attenuation artifact. BMI 38. Dyspnea on exertion, worsening x 2 mo. "
        "Ordering: Cardiac Stress PET."
    )
    chart = _extract_chart_from_note(
        raw_note=note,
        procedure_code="78492",
        procedure_name="Cardiac PET",
        payer_name="UnitedHealthcare",
        patient_id="TEST-1",
    )
    assert chart.procedure_code == "78492"
    assert chart.procedure_requested == "Cardiac PET"
    # Note is preserved in additional_notes so reasoner can still read it
    assert note in chart.additional_notes
    # Confidence is low when we fall back
    assert chart.confidence_score <= 0.5


def test_e2e_with_raw_note_uses_note_ingest_step(monkeypatch) -> None:
    """Custom-note path: step 1 is NoteIngest, not FHIRClient."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    note = "67 yo M with CAD. BMI 38. Ordered Cardiac PET."
    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=note,
    )
    assert timeline.steps[0].agent == "NoteIngest"
    assert timeline.steps[1].agent == "CHART_AGENT"
    # NoteIngest preview should reflect the input
    assert "BMI 38" in (timeline.steps[0].detail or {}).get("preview", "")


def test_e2e_with_raw_note_runs_full_pipeline(monkeypatch) -> None:
    """Custom-note path runs through POLICY + REASONER stages.

    Apr 28: weak cases now route to HELD_FOR_REVIEW (was BLOCKED_BY_REASONER).
    Cases with missing essentials (no patient name etc.) still hard-block as
    BLOCKED_MISSING_ESSENTIALS.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    note = "72 yo F with AS, NYHA III, pre-TAVR evaluation. BMI 32."
    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="33361",
        payer_name="Medicare",
        raw_note=note,
        scripted_outcome="APPROVED",
    )
    agents = [s.agent for s in timeline.steps]
    assert agents[0] == "NoteIngest"
    assert "POLICY_AGENT" in agents
    assert "UNIFIED_REASONER" in agents
    assert "Physician" in agents
    # Custom note with no patient identifiers → BLOCKED_MISSING_ESSENTIALS;
    # if essentials happen to be present, weak case → HELD_FOR_REVIEW.
    assert timeline.outcome in (
        "APPROVED", "HELD_FOR_REVIEW", "BLOCKED_MISSING_ESSENTIALS",
    )


def test_detail_payloads_include_full_stage_output(monkeypatch) -> None:
    """Peter: 'view more detail at each step.' Verify key fields are present."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
    )

    # CHART_AGENT detail exposes the full ChartData v2
    chart_step = next(s for s in timeline.steps if s.agent == "CHART_AGENT")
    assert "chart_data" in (chart_step.detail or {})

    # POLICY_AGENT detail exposes criteria + chunks + stats
    policy_step = next(s for s in timeline.steps if s.agent == "POLICY_AGENT")
    assert "clinical_criteria" in (policy_step.detail or {})

    # SUBMISSION detail exposes the full submission payload (when the case
    # actually transmits — held-for-review cases short-circuit with
    # status="skipped" and only carry warnings).
    sub_step = next(s for s in timeline.steps if s.agent == "SUBMISSION_AGENT")
    if sub_step.status == "ok":
        assert "submission_payload" in (sub_step.detail or {})
    elif sub_step.status == "skipped":
        assert (sub_step.detail or {}).get("held_for_review") is True
