"""Form-mapper fixes from Peter's Apr 28 email.

Three mappers that were leaving fields blank despite the chart having
the evidence:
  1. Exercise capacity ('Yes / No / Unknown')
  2. No similar imaging within 12 mo OR new/worsening symptoms
  3. Medication duration (when start_date is empty but the note has it)
"""

from __future__ import annotations

from datetime import date, timedelta

from cardioauth.models.chart import (
    ChartData, ImagingResult, Medication, StressTestResult, Symptom,
)
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.payer_forms import (
    _check_freq_compliance,
    _fmt_exercise_capacity,
    _fmt_meds,
)


def _chart(**kw) -> ChartData:
    defaults = dict(
        patient_id="P-1",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
    )
    defaults.update(kw)
    return ChartData(**defaults)


# ── Exercise capacity ───────────────────────────────────────────────────

def test_case2_treadmill_cannot_be_performed_no() -> None:
    """Peter Case 2: 'treadmill testing cannot be performed due to fatigue
    and pacemaker rhythm' must populate as No."""
    chart = _chart(additional_notes=(
        "67 yo M with worsening dyspnea. Treadmill testing cannot be "
        "performed due to fatigue and pacemaker rhythm."
    ))
    assert _fmt_exercise_capacity(chart).startswith("No")


def test_case5_can_walk_treadmill_yes() -> None:
    """Peter Case 5: 'patient can walk on a treadmill and achieve target
    heart rate' must populate as Yes."""
    chart = _chart(additional_notes=(
        "Patient can walk on a treadmill and achieve target heart rate "
        "without significant limitation."
    ))
    assert _fmt_exercise_capacity(chart).startswith("Yes")


def test_unable_to_exercise_still_caught() -> None:
    chart = _chart(additional_notes="Patient is unable to exercise due to severe arthritis.")
    assert _fmt_exercise_capacity(chart).startswith("No")


def test_pharm_agent_in_note_implies_no() -> None:
    chart = _chart(additional_notes="Plan: regadenoson SPECT given inability to exercise.")
    assert _fmt_exercise_capacity(chart).startswith("No")


def test_pharm_agent_on_prior_test_implies_no() -> None:
    chart = _chart(prior_stress_tests=[
        StressTestResult(modality="SPECT", interpretation="regadenoson stress, non-diagnostic"),
    ])
    assert _fmt_exercise_capacity(chart).startswith("No")


def test_completed_treadmill_yes() -> None:
    chart = _chart(additional_notes="Patient completed treadmill protocol, achieved 9 METs.")
    assert _fmt_exercise_capacity(chart).startswith("Yes")


def test_deconditioned_no() -> None:
    chart = _chart(additional_notes="Severely deconditioned, walks with cane.")
    assert _fmt_exercise_capacity(chart).startswith("No")


def test_wheelchair_bound_no() -> None:
    chart = _chart(additional_notes="Patient is wheelchair-bound after stroke.")
    assert _fmt_exercise_capacity(chart).startswith("No")


def test_silent_when_no_exercise_evidence() -> None:
    chart = _chart(additional_notes="Routine follow-up, BP stable.")
    assert _fmt_exercise_capacity(chart) == ""


def test_unknown_when_mentioned_but_ambiguous() -> None:
    chart = _chart(additional_notes="Plans to begin an exercise program.")
    assert _fmt_exercise_capacity(chart) == "Unknown"


# ── No similar imaging in 12mo OR new/worsening symptoms ───────────────

def test_freq_yes_when_new_symptoms_documented() -> None:
    chart = _chart(current_symptoms=[
        Symptom(name="dyspnea", change_vs_baseline="new"),
    ])
    assert _check_freq_compliance(chart, None) == "yes"


def test_freq_yes_when_worsening_symptoms() -> None:
    chart = _chart(current_symptoms=[
        Symptom(name="chest pain", change_vs_baseline="worsening"),
    ])
    assert _check_freq_compliance(chart, None) == "yes"


def test_freq_yes_from_note_phrasing() -> None:
    """When extraction missed change_vs_baseline but the note has it."""
    chart = _chart(additional_notes="HPI: progressive dyspnea on exertion x 3 weeks.")
    assert _check_freq_compliance(chart, None) == "yes"


def test_freq_yes_when_no_recent_similar_imaging() -> None:
    """Stable symptoms but no PET/SPECT in last 12 mo → still compliant."""
    old = (date.today() - timedelta(days=400)).isoformat()
    chart = _chart(
        current_symptoms=[Symptom(name="angina", change_vs_baseline="stable")],
        relevant_imaging=[
            ImagingResult(type="Echocardiogram", date=old, result_summary="Old echo"),
        ],
    )
    assert _check_freq_compliance(chart, None) == "yes"


def test_freq_no_when_recent_pet_and_stable() -> None:
    """Recent SPECT + stable symptoms = the case repeat imaging blocks against."""
    recent = (date.today() - timedelta(days=60)).isoformat()
    chart = _chart(
        current_symptoms=[Symptom(name="angina", change_vs_baseline="stable")],
        prior_stress_tests=[
            StressTestResult(modality="SPECT", date=recent, interpretation="negative"),
        ],
    )
    assert _check_freq_compliance(chart, None) == "no"


def test_freq_silent_when_no_evidence() -> None:
    """No symptoms recorded, no imaging history — can't attest either way."""
    chart = _chart()
    assert _check_freq_compliance(chart, None) == "yes"  # no recent imaging = compliant


# ── Medication duration capture ────────────────────────────────────────

def test_meds_uses_explicit_start_date() -> None:
    chart = _chart(relevant_medications=[
        Medication(name="metoprolol", dose="50mg", start_date="2024-01"),
    ])
    out = _fmt_meds(chart)
    assert "metoprolol" in out
    assert "since 2024-01" in out


def test_meds_extracts_duration_from_note_when_missing() -> None:
    """Peter: medication duration sometimes blank even when note has it."""
    chart = _chart(
        relevant_medications=[Medication(name="metoprolol", dose="50mg", start_date="")],
        additional_notes="Patient on metoprolol 50mg since 2024 for HTN.",
    )
    out = _fmt_meds(chart)
    assert "since 2024" in out


def test_meds_extracts_relative_duration() -> None:
    chart = _chart(
        relevant_medications=[Medication(name="atorvastatin", dose="40mg", start_date="")],
        additional_notes="Started atorvastatin 40mg for 3 years.",
    )
    out = _fmt_meds(chart)
    assert "3 years" in out


def test_meds_no_duration_when_unfindable() -> None:
    chart = _chart(
        relevant_medications=[Medication(name="aspirin", dose="81mg", start_date="")],
        additional_notes="Routine.",
    )
    out = _fmt_meds(chart)
    assert "since" not in out
    assert "aspirin" in out
