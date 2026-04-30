"""Apr 30 — allowed-value normalization + packet coherence reviewer.

Two tightly related fixes from Peter's email:
  1. Form mappers were emitting clinically rich values that didn't match
     the payer's `options` list (e.g., "No — functional limitation
     documented" vs the allowed "No — see functional limitation"). Fields
     classified as incomplete despite being semantically correct.
  2. Packet artifacts could disagree — narrative attestation referencing
     CPT 78492 while the form was built for CPT 78452. New deterministic
     reviewer flags these mismatches.
"""

from __future__ import annotations

from cardioauth.models.chart import ChartData
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.packet_coherence import (
    _extract_cpt_codes,
    _is_cardiology_cpt,
    check_packet_coherence,
)
from cardioauth.payer_forms import (
    _normalize_to_options,
    get_payer_form,
    populate_payer_form,
)


# ── _normalize_to_options ───────────────────────────────────────────────

EX_CAP_OPTIONS = ["Yes", "No — see functional limitation", "Unknown"]


def test_yes_with_evidence_snaps_to_yes() -> None:
    snapped, evidence = _normalize_to_options(
        "Yes — adequate exercise tolerance documented", EX_CAP_OPTIONS,
    )
    assert snapped == "Yes"
    assert "adequate exercise tolerance" in evidence


def test_no_with_evidence_snaps_to_canonical() -> None:
    snapped, evidence = _normalize_to_options(
        "No — functional limitation documented", EX_CAP_OPTIONS,
    )
    assert snapped == "No — see functional limitation"
    assert "functional limitation documented" in evidence


def test_no_pharmacologic_snaps_to_no() -> None:
    snapped, _ = _normalize_to_options(
        "No — pharmacologic stress agent indicated", EX_CAP_OPTIONS,
    )
    assert snapped == "No — see functional limitation"


def test_exact_match_no_evidence_emitted() -> None:
    snapped, evidence = _normalize_to_options("Yes", EX_CAP_OPTIONS)
    assert snapped == "Yes"
    assert evidence == ""


def test_unknown_passes_through() -> None:
    snapped, _ = _normalize_to_options("Unknown", EX_CAP_OPTIONS)
    assert snapped == "Unknown"


def test_unmappable_value_returns_original() -> None:
    snapped, evidence = _normalize_to_options(
        "Patient is interesting", EX_CAP_OPTIONS,
    )
    assert snapped == "Patient is interesting"
    assert evidence == ""


def test_empty_inputs_safe() -> None:
    assert _normalize_to_options("", EX_CAP_OPTIONS) == ("", "")
    assert _normalize_to_options("Yes", []) == ("Yes", "")


# ── populate_payer_form integration: case 2 + 5 fixed ──────────────────

def _chart_case2() -> ChartData:
    """Mimic Peter's Case 2: pacemaker + treadmill cannot be performed."""
    return ChartData(
        patient_id="P-2", procedure_requested="Cardiac PET",
        procedure_code="78492",
        patient_name="Test Two", date_of_birth="1960-05-12",
        insurance_id="UHC-2", payer_name="UnitedHealthcare",
        attending_physician="Dr. Demo", attending_npi="1234567890",
        diagnosis_codes=["I25.10"],
        additional_notes=(
            "67 yo M with worsening dyspnea. Treadmill testing cannot be "
            "performed due to fatigue and pacemaker rhythm."
        ),
    )


def _chart_case5() -> ChartData:
    """Mimic Peter's Case 5: patient can walk on treadmill."""
    return ChartData(
        patient_id="P-5", procedure_requested="Exercise SPECT",
        procedure_code="78452",
        patient_name="Test Five", date_of_birth="1962-03-04",
        insurance_id="UHC-5", payer_name="UnitedHealthcare",
        attending_physician="Dr. Demo", attending_npi="1234567890",
        diagnosis_codes=["I25.10"],
        additional_notes=(
            "Patient can walk on a treadmill and achieve target heart rate. "
            "Plan: stress-imaging study to evaluate ischemia."
        ),
    )


def test_case2_populates_canonical_no() -> None:
    chart = _chart_case2()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.8, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)
    field = next(f for f in result["fields"] if f["key"] == "exercise_capacity")
    assert field["value"] == "No — see functional limitation"
    assert field["status"] == "populated"
    # Evidence preserved separately
    assert "functional limitation documented" in field["evidence"]


def test_case5_populates_canonical_yes() -> None:
    chart = _chart_case5()
    form = get_payer_form("UnitedHealthcare", "78452")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.8, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    result = populate_payer_form(form, chart_data=chart, policy_data=None, reasoning=reasoning)
    field = next(f for f in result["fields"] if f["key"] == "exercise_capacity")
    assert field["value"] == "Yes"
    assert field["status"] == "populated"
    assert "adequate exercise tolerance" in field["evidence"]


# ── CPT extraction helpers ──────────────────────────────────────────────

def test_cardiology_cpt_ranges() -> None:
    assert _is_cardiology_cpt("78492") is True
    assert _is_cardiology_cpt("78452") is True
    assert _is_cardiology_cpt("33361") is True
    assert _is_cardiology_cpt("93458") is True
    assert _is_cardiology_cpt("75574") is True


def test_non_cardiology_cpt_skipped() -> None:
    """A 5-digit number outside the relevant ranges should not be flagged
    unless explicitly tagged 'CPT'."""
    assert _is_cardiology_cpt("12345") is False
    assert _is_cardiology_cpt("99213") is False  # E&M code, not cardiology


def test_extract_cpts_from_explicit_tagging() -> None:
    text = "Ordering: CPT 78452, prior auth needed."
    assert "78452" in _extract_cpt_codes(text)


def test_extract_cpts_from_bareword_in_range() -> None:
    text = "78492 cardiac PET indicated."
    assert "78492" in _extract_cpt_codes(text)


def test_phone_number_not_extracted() -> None:
    """A phone-like 5-digit prefix outside cardiology range shouldn't match."""
    text = "Patient phone: 12345-6789. Dispatched 50001 to triage."
    cpts = _extract_cpt_codes(text)
    assert "12345" not in cpts
    assert "50001" not in cpts


# ── check_packet_coherence ─────────────────────────────────────────────

def _chart_with_cpt(cpt: str, proc: str = "Cardiac PET") -> ChartData:
    return ChartData(
        patient_id="P-1",
        procedure_requested=proc,
        procedure_code=cpt,
    )


def test_no_warnings_when_artifacts_agree() -> None:
    chart = _chart_with_cpt("78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="Cardiac PET (CPT 78492) is medically necessary.",
    )
    note = "Ordering CPT 78492 cardiac PET."
    warnings = check_packet_coherence(chart=chart, reasoning=reasoning, raw_note=note)
    assert warnings == []


def test_cpt_note_mismatch_flagged() -> None:
    """Note orders 78452 but request is 78492 — Peter's Case 5 pattern."""
    chart = _chart_with_cpt("78492", "Cardiac PET")
    note = "Ordering: Exercise SPECT (CPT 78452)."
    warnings = check_packet_coherence(chart=chart, reasoning=None, raw_note=note)
    kinds = {w["kind"] for w in warnings}
    assert "cpt_note_mismatch" in kinds


def test_cpt_attestation_mismatch_flagged() -> None:
    """Form built for 78452 but narrative attestation says 78492."""
    chart = _chart_with_cpt("78452", "Exercise SPECT")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.8, approval_likelihood_label="HIGH",
        pa_narrative_draft="Exercise SPECT (CPT 78492) is appropriate.",
    )
    warnings = check_packet_coherence(chart=chart, reasoning=reasoning, raw_note="")
    kinds = {w["kind"] for w in warnings}
    assert "cpt_attestation_mismatch" in kinds


def test_attestation_mismatch_severity_high() -> None:
    chart = _chart_with_cpt("78452", "Exercise SPECT")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.8, approval_likelihood_label="HIGH",
        pa_narrative_draft="CPT 78492 indicated.",
    )
    warnings = check_packet_coherence(chart=chart, reasoning=reasoning, raw_note="")
    sev = next(w["severity"] for w in warnings if w["kind"] == "cpt_attestation_mismatch")
    assert sev == "high"


def test_note_without_cpt_no_warning() -> None:
    """Notes that don't mention any CPT code shouldn't trigger a mismatch."""
    chart = _chart_with_cpt("78492")
    note = "Patient with worsening dyspnea, attenuation artifact noted."
    warnings = check_packet_coherence(chart=chart, reasoning=None, raw_note=note)
    assert all(w["kind"] != "cpt_note_mismatch" for w in warnings)
