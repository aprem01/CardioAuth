"""Tests for the needs_verify status on payer form fields.

'in_network_attestation' is a flagged_requires_verify field — we can't
auto-populate it, the user has to attest manually. Pre-hardening it
rendered as 'missing' which falsely blocked the form. It should render
as 'needs_verify' and NOT block ready_to_submit.
"""

from __future__ import annotations

from cardioauth.models.chart import ChartData
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.payer_forms import get_payer_form, populate_payer_form


def _fully_populated_chart() -> ChartData:
    from cardioauth.models.chart import ECGFinding, StressTestResult, Symptom
    return ChartData(
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
        active_comorbidities=["HTN", "DM", "BMI 38"],
        current_symptoms=[Symptom(name="dyspnea", onset="3 weeks ago",
                                  change_vs_baseline="new")],
        ecg_findings=[ECGFinding(conduction="LBBB")],
        prior_stress_tests=[StressTestResult(modality="SPECT",
                                             interpretation="non-diagnostic")],
        confidence_score=0.9,
    )


def test_flagged_verify_field_renders_as_needs_verify() -> None:
    chart = _fully_populated_chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="Strong clinical case for PET.",
    )
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)

    in_network = next(f for f in result["fields"] if f["key"] == "in_network_attestation")
    assert in_network["status"] == "needs_verify"
    # Value rendered empty — UI fills in the attestation copy
    assert in_network["value"] == ""
    # It's NOT counted as a missing_required — otherwise ready_to_submit would be False
    assert result["counts"]["needs_verify"] >= 1


def test_in_network_not_counted_as_missing_required() -> None:
    """Regression: pre-hardening, in_network_attestation returned None,
    which classified as 'missing' and inflated missing_required.
    """
    chart = _fully_populated_chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="Strong clinical case for PET across all criteria.",
    )
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)

    missing_keys = [f["key"] for f in result["fields"] if f["status"] == "missing" and f["required"]]
    assert "in_network_attestation" not in missing_keys
