"""Tests for critical-fields submission gate (Peter Apr 24 feedback).

Peter: 'distinguish between truly critical fields (patient identifiers,
primary symptoms, CPT code) and less essential ones. Only the key
clinical and identification fields would need to trigger a submission
block, while others could simply be flagged for completeness.'
"""

from __future__ import annotations

from cardioauth.demo_e2e import _critical_field_gaps
from cardioauth.models.chart import ChartData, Symptom


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
        current_symptoms=[Symptom(name="dyspnea on exertion",
                                  change_vs_baseline="new")],
        confidence_score=0.9,
    )
    defaults.update(overrides)
    return ChartData(**defaults)


def test_complete_chart_no_critical_gaps() -> None:
    chart = _complete_chart()
    assert _critical_field_gaps(chart) == []


def test_missing_patient_name_blocks() -> None:
    chart = _complete_chart(patient_name="")
    assert "Patient name" in _critical_field_gaps(chart)


def test_missing_dob_blocks() -> None:
    chart = _complete_chart(date_of_birth="")
    assert "Date of birth" in _critical_field_gaps(chart)


def test_missing_member_id_blocks() -> None:
    chart = _complete_chart(insurance_id="")
    assert "Member ID" in _critical_field_gaps(chart)


def test_missing_ordering_md_blocks() -> None:
    chart = _complete_chart(attending_physician="")
    assert "Ordering physician" in _critical_field_gaps(chart)


def test_missing_primary_symptom_blocks() -> None:
    chart = _complete_chart(current_symptoms=[])
    assert "Primary symptom" in _critical_field_gaps(chart)


def test_missing_multiple_critical_lists_all() -> None:
    chart = _complete_chart(
        patient_name="",
        date_of_birth="",
        insurance_id="",
        current_symptoms=[],
    )
    gaps = _critical_field_gaps(chart)
    assert set(gaps) == {
        "Patient name", "Date of birth", "Member ID", "Primary symptom",
    }


def test_missing_noncritical_field_is_not_gap() -> None:
    """Missing exam_findings, family_history, etc. don't belong here."""
    chart = _complete_chart()
    # Those fields default to empty — still shouldn't block
    assert _critical_field_gaps(chart) == []
