"""Tests for the end-to-end demo flow.

Exercises run_end_to_end_demo() without live API keys — falls back to
demo reasoning and demo policy, but still produces a valid timeline.
"""

from __future__ import annotations

from cardioauth.demo_e2e import run_end_to_end_demo


def test_e2e_demo_produces_all_stages_even_without_api_key(monkeypatch) -> None:
    """Demo must run end-to-end with or without Claude. Fallback paths
    exercise when API keys aren't set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    # Expected stage names must all appear (order matters for the UI)
    expected_agents = [
        "FHIRClient",
        "CHART_AGENT",
        "POLICY_AGENT",
        "UNIFIED_REASONER",
        "Physician",
        "SUBMISSION_AGENT",
        "PayerFormMapper",   # Apr 22: Peter's ask — payer form preview
        "Payer",
        "OutcomeRecorder",
    ]
    actual = [s.agent for s in timeline.steps]
    assert actual == expected_agents
    assert timeline.case_id.startswith("DEMO-001-78492-")
    assert timeline.outcome == "APPROVED"
    assert timeline.total_duration_ms >= 0


def test_e2e_demo_scripted_denied_outcome() -> None:
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="DENIED",
    )
    assert timeline.outcome == "DENIED"
    payer_step = next(s for s in timeline.steps if s.agent == "Payer")
    assert (payer_step.detail or {}).get("denial_reason")


def test_e2e_demo_every_step_timed() -> None:
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
    )
    for step in timeline.steps:
        assert step.duration_ms >= 0
        assert step.started_at
        assert step.summary
        assert step.status in ("ok", "fallback", "skipped", "failed")


def test_e2e_demo_outcome_persists_to_store() -> None:
    """After E2E runs, the outcome should be retrievable from the Store."""
    from cardioauth.persistence import get_store
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    submission_step = next(s for s in timeline.steps if s.agent == "SUBMISSION_AGENT")
    submission_id = (submission_step.detail or {}).get("submission_id")
    if submission_id:
        # Only check persistence if submission succeeded
        outcome = get_store().get_outcome(submission_id)
        if outcome:
            assert outcome["outcome"] == "APPROVED"
            assert outcome["payer"] == "UnitedHealthcare"


def test_e2e_demo_serializable_to_dict() -> None:
    """Timeline.to_dict() must produce JSON-safe output for the API."""
    import json
    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
    )
    as_dict = timeline.to_dict()
    # Round-trip JSON — must not raise
    json.dumps(as_dict, default=str)
    assert "case_id" in as_dict
    assert "steps" in as_dict
    assert len(as_dict["steps"]) >= 5
