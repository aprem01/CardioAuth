"""Tests for payer form schemas + population (Peter Apr 22 feedback)."""

from __future__ import annotations

from cardioauth.models import (
    ChartData, ECGFinding, Medication, StressTestResult, Symptom,
)
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.payer_forms import (
    get_payer_form,
    populate_payer_form,
)


def _chart(**kwargs) -> ChartData:
    defaults = dict(
        patient_id="TEST",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        diagnosis_codes=["I25.10"],
        confidence_score=0.9,
    )
    defaults.update(kwargs)
    return ChartData(**defaults)


# ── Form routing ────────────────────────────────────────────────────────

def test_uhc_routes_to_availity_form() -> None:
    form = get_payer_form("UnitedHealthcare", "78492")
    assert form is not None
    assert form.vendor == "Availity"
    assert "78492" in form.applies_to_cpts


def test_uhc_alias_uhc_resolves() -> None:
    form = get_payer_form("UHC", "78492")
    assert form is not None
    assert form.payer == "UnitedHealthcare"


def test_aetna_routes_to_evicore_form() -> None:
    form = get_payer_form("Aetna", "78492")
    assert form is not None
    assert form.vendor == "eviCore"
    assert form.submission_channel == "evicore"


def test_medicare_gets_coverage_checklist_not_pa() -> None:
    """Peter's point: Medicare is NCD/LCD documentation, not PA."""
    form = get_payer_form("Medicare", "78492")
    assert form is not None
    assert form.vendor == "CMS"
    assert form.submission_channel == "documentation_only"
    assert "PA typically NOT required" in form.notes


def test_unknown_payer_returns_none() -> None:
    assert get_payer_form("NebulaHealth", "78492") is None


# ── Population: field-level status ──────────────────────────────────────

def test_empty_chart_produces_many_missing_required() -> None:
    """Bare-minimum chart → most required clinical fields missing."""
    chart = _chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(approval_likelihood_score=0.3, approval_likelihood_label="LOW", pa_narrative_draft="")
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)
    assert result["counts"]["missing_required"] >= 3
    assert result["ready_to_submit"] is False


def test_well_populated_chart_fills_clinical_fields() -> None:
    """Rich chart → symptoms, ECG, stress test, meds all populated."""
    chart = _chart(
        patient_id="TEST-PT",
        insurance_id="UHC123456",
        attending_physician="Dr. Doe",
        current_symptoms=[
            Symptom(name="angina", character="exertional", onset="3 weeks ago",
                    frequency="with stair climbing", change_vs_baseline="worsening"),
        ],
        ecg_findings=[ECGFinding(rhythm="sinus", conduction="LBBB")],
        prior_stress_tests=[StressTestResult(
            modality="ETT", date="2026-01-15", max_hr_percent="68%",
            interpretation="submaximal, non-diagnostic",
            result_summary="Submaximal ETT at 68% MPHR",
        )],
        relevant_medications=[Medication(name="metoprolol", dose="50mg BID", start_date="2025-01-01", indication="CAD")],
    )
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.85, approval_likelihood_label="HIGH",
        pa_narrative_draft="67M with CAD and worsening exertional angina, prior ETT non-diagnostic at 68% MPHR, LBBB on baseline ECG. Requesting Cardiac PET for ischemia evaluation. "*3,
    )
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)

    def _field(key: str):
        return next((f for f in result["fields"] if f["key"] == key), None)

    # Clinical fields should be populated from the chart
    assert _field("chief_complaint_symptoms")["status"] == "populated"
    assert "angina" in _field("chief_complaint_symptoms")["value"]
    assert _field("ecg_findings")["status"] == "populated"
    assert "LBBB" in _field("ecg_findings")["value"]
    assert _field("prior_stress_test")["status"] == "populated"
    assert "ETT" in _field("prior_stress_test")["value"]
    assert _field("medical_therapy")["status"] == "populated"


def test_narrative_populates_necessity_attestation() -> None:
    chart = _chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    narrative = "x" * 200
    reasoning = ReasoningResult(
        approval_likelihood_score=0.7, approval_likelihood_label="HIGH",
        pa_narrative_draft=narrative,
    )
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)
    nec = next(f for f in result["fields"] if f["key"] == "medical_necessity_statement")
    assert nec["status"] == "populated"


def test_min_length_marks_field_incomplete() -> None:
    chart = _chart(attending_physician="Dr")  # 2 chars, below min_length=2 — actually equal, let's test <2
    chart = _chart(attending_physician="D")  # 1 char, below minimum of 2
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(approval_likelihood_score=0.0, approval_likelihood_label="LOW", pa_narrative_draft="")
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)
    op = next(f for f in result["fields"] if f["key"] == "ordering_physician")
    assert op["status"] == "incomplete"


def test_medicare_checklist_uses_different_fields() -> None:
    """Medicare form has NCD/LCD field, not UHC-style worksheet fields."""
    chart = _chart()
    form = get_payer_form("Medicare", "78492")
    field_keys = {f.key for f in form.fields}
    assert "ncd_lcd_applicable" in field_keys
    assert "mac_jurisdiction" in field_keys
    assert "abn_signed" in field_keys
    # Should NOT have UHC-specific fields
    assert "no_duplicate_imaging_12mo" not in field_keys
    assert "in_network_attestation" not in field_keys


def test_counts_add_up_to_total() -> None:
    chart = _chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(approval_likelihood_score=0.0, approval_likelihood_label="LOW", pa_narrative_draft="")
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)
    c = result["counts"]
    assert (
        c["populated"] + c["missing_required"] + c["incomplete"]
        + c["optional_empty"] + c.get("needs_verify", 0)
    ) == c["total"]


def test_ready_to_submit_requires_all_required_populated() -> None:
    """If even one required field is missing/incomplete, not ready."""
    chart = _chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(approval_likelihood_score=0.0, approval_likelihood_label="LOW", pa_narrative_draft="")
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)
    if result["counts"]["missing_required"] > 0 or result["counts"]["incomplete"] > 0:
        assert result["ready_to_submit"] is False
