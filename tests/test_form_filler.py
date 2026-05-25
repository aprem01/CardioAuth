"""Tests for the payer form filler — actually writes values into the real
UHC MA Cardiac Imaging AcroForm PDF and reads them back to verify.

These tests use the vendored PDF under tests/fixtures/payer_forms/pdfs/
so they're hermetic and don't depend on mass.gov uptime.
"""

from __future__ import annotations

import io

import pypdf
import pytest

from cardioauth.payer_routing.form_filler import (
    fill_form,
    fill_uhc_ma_cardiac_imaging,
    supported_form_ids,
    FillResult,
)


def _lean_result(overrides: dict | None = None) -> dict:
    """Build a fake lean_result shape with sensible defaults for Whitford."""
    base = {
        "request_cpt": "78452",
        "payer": "UnitedHealthcare",
        "state2_output": {
            "cpt_resolution": {"cpt": "78452", "procedure_name": "Cardiac SPECT MPI"},
            "form_field_values": [
                {"key": "patient_name", "value": "Eleanor R. Whitford"},
                {"key": "patient_dob", "value": "1957-04-12"},
                {"key": "member_id", "value": "UHC-66432198"},
                {"key": "payer_name", "value": "UnitedHealthcare"},
                {"key": "ordering_physician", "value": "Yuki R. Tanaka, MD"},
                {"key": "ordering_npi", "value": "1029384765"},
                {"key": "cpt_code", "value": "78452"},
                {"key": "procedure_name", "value": "Cardiac SPECT MPI"},
                {"key": "primary_icd10", "value": "I25.10"},
            ],
        },
    }
    if overrides:
        base.update(overrides)
    return base


def _read_text_fields(pdf_bytes: bytes) -> dict[str, str]:
    """Re-read filled values out of a PDF blob."""
    return pypdf.PdfReader(io.BytesIO(pdf_bytes)).get_form_text_fields() or {}


# ── Registry ───────────────────────────────────────────────────────────


def test_uhc_ma_cardiac_imaging_is_registered() -> None:
    assert "uhc-ma-cardiac-imaging" in supported_form_ids()


def test_unknown_form_id_returns_error_not_raises() -> None:
    """fill_form must NEVER raise — it should always return a FillResult,
    even when the form_id is bogus. The route handler turns it into a
    400/500; we don't want it to crash the worker."""
    r = fill_form("does-not-exist", lean_result={})
    assert r.pdf_bytes == b""
    assert r.errors and "No filler" in r.errors[0]


# ── UHC MA Cardiac Imaging round-trip ──────────────────────────────────


def test_uhc_ma_fills_demographics_into_real_pdf() -> None:
    """Run the filler against the real UHC MA AcroForm PDF and read the
    values back out. Verifies the mapping is using real field names
    (not typos)."""
    r = fill_uhc_ma_cardiac_imaging(
        lean_result=_lean_result(),
        chart_context={"encounter_date": "2026-05-06"},
    )
    assert r.errors == []
    assert r.pdf_bytes.startswith(b"%PDF-")
    assert r.fields_populated == 10  # everything we mapped

    filled = _read_text_fields(r.pdf_bytes)
    assert filled["Patient Name First Last"] == "Eleanor R. Whitford"
    assert filled["DOB"] == "1957-04-12"
    assert filled["Health Plan"] == "UnitedHealthcare"
    assert filled["Member ID"] == "UHC-66432198"
    assert filled["Physician Name First Last"] == "Yuki R. Tanaka, MD"
    assert filled["NPI"] == "1029384765"
    assert filled["CPT Codes"] == "78452"
    assert filled["Description"] == "Cardiac SPECT MPI"
    assert filled["ICD Diagnosis Codes"] == "I25.10"
    assert filled["Date of Service"] == "2026-05-06"


def test_missing_form_field_values_leave_pdf_field_blank() -> None:
    """If a form_field_value is missing or empty, we should NOT write an
    empty string to the PDF — leave the field truly blank so reviewers
    aren't misled into thinking we tried."""
    r = fill_uhc_ma_cardiac_imaging(
        lean_result={
            "state2_output": {
                "form_field_values": [
                    {"key": "patient_name", "value": "Test Patient"},
                    # No DOB, no member_id, no provider
                ],
            },
        },
    )
    assert r.errors == []
    filled = _read_text_fields(r.pdf_bytes)
    assert filled.get("Patient Name First Last") == "Test Patient"
    # Fields with no source data should be absent or empty in the PDF
    assert not filled.get("DOB"), "DOB should be blank when no value provided"
    assert not filled.get("Member ID")


def test_field_names_match_real_pdf_no_typos() -> None:
    """The filler maps to specific UHC field names. If any of them
    don't exist on the actual PDF, that's a typo bug that would
    silently fail in production. Catch it at test time."""
    r = fill_uhc_ma_cardiac_imaging(lean_result=_lean_result())
    assert r.missing_values == [], (
        f"These mapped field names don't exist on the real PDF "
        f"(probably a typo): {r.missing_values}"
    )


def test_cpt_falls_back_to_request_cpt_when_form_value_missing() -> None:
    """If form_field_values doesn't include cpt_code, fall back to
    state2_output.cpt_resolution.cpt or lean_result.request_cpt."""
    r = fill_uhc_ma_cardiac_imaging(
        lean_result={
            "request_cpt": "78492",
            "state2_output": {
                "cpt_resolution": {"cpt": "78492", "procedure_name": "PET MPI"},
                "form_field_values": [
                    {"key": "patient_name", "value": "Test"},
                    # Note: no cpt_code in form_field_values
                ],
            },
        },
    )
    filled = _read_text_fields(r.pdf_bytes)
    assert filled["CPT Codes"] == "78492"
    assert filled["Description"] == "PET MPI"


def test_encounter_date_defaults_to_today_if_no_context() -> None:
    """When no chart_context, fall back to today's date for Date of Service —
    better than leaving it blank since UHC requires it."""
    from datetime import date
    r = fill_uhc_ma_cardiac_imaging(lean_result=_lean_result(), chart_context=None)
    filled = _read_text_fields(r.pdf_bytes)
    assert filled["Date of Service"] == date.today().isoformat()


# ── Output integrity ───────────────────────────────────────────────────


def test_filled_pdf_is_still_a_valid_pdf() -> None:
    """After we write values, the PDF must still be readable by any
    standard reader. Mass.gov / UHC won't take a malformed one."""
    r = fill_uhc_ma_cardiac_imaging(lean_result=_lean_result())
    # Re-parse as a sanity check
    reader = pypdf.PdfReader(io.BytesIO(r.pdf_bytes))
    assert len(reader.pages) == 2
    # The 153 form fields should still all be present (we didn't drop any)
    assert len(reader.get_fields() or {}) == 153
