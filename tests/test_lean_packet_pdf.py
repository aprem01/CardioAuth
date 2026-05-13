"""Tests for the staff-submittable packet PDF.

Peter's stated #1 gap: "the critical bridge between a good demo and a
useful workflow product." A PA staffer should be able to download this
PDF, attach it to a payer portal submission, and not have to rework
anything we put in it.
"""

from __future__ import annotations

import pytest

from cardioauth.pdf_generator import generate_lean_packet_pdf


def _full_fixture() -> dict:
    return {
        "case_id": "TEST-78452-ABC",
        "request_cpt": "78452",
        "payer": "UnitedHealthcare",
        "approval_score": 0.88,
        "approval_label": "HIGH",
        "corpus_snippets": [
            {"doc_id": "stress-2023", "doc_type": "stress_test", "date": "2023-06-18",
             "title": "Exercise treadmill stress test",
             "text": "Submaximal 4 min 12s, 6.2 METs, 76% MPHR. Baseline LBBB nondiagnostic."},
            {"doc_id": "ecg-2021", "doc_type": "ecg_report", "date": "2021-11-03",
             "title": "12-lead ECG", "text": "Chronic LBBB, QRS 152 ms."},
        ],
        "state2_output": {
            "cpt_resolution": {"cpt": "78452", "procedure_name": "Myocardial Perfusion Imaging, SPECT"},
            "payer_name": "UnitedHealthcare",
            "form_field_values": [
                {"key": "patient_name", "value": "Eleanor R. Whitford", "status": "populated"},
                {"key": "patient_dob", "value": "04/12/1957", "status": "populated"},
                {"key": "member_id", "value": "UHC-66432198", "status": "populated"},
                {"key": "ordering_physician", "value": "Yuki R. Tanaka, MD", "status": "populated"},
                {"key": "primary_icd10", "value": "I25.10", "status": "populated"},
                {"key": "bmi", "value": "", "status": "missing", "missing_reason": "Not in note"},
            ],
            "criteria_evaluated": [
                {"code": "ECG-001", "status": "met", "rationale": "LBBB documented in 2021 ECG."},
                {"code": "NDX-001", "status": "met", "rationale": "Prior submaximal treadmill nondiagnostic."},
                {"code": "EX-001", "status": "not_met", "rationale": "No quantified exercise limitation in note."},
            ],
            "payer_specific_criteria_evaluated": [
                {"code": "UHC-PHARM-001", "status": "met", "rationale": "Inability to exercise documented."},
            ],
            "approval_verdict": {"score": 0.88, "label": "HIGH"},
            "narrative": {"text": "68F with CAD, T2DM, chronic LBBB; recurrent exertional chest pressure × 6w. Prior submaximal treadmill (2023) nondiagnostic. Pharmacologic SPECT indicated."},
            "provenance": {"id": "prov-abc123"},
        },
        "archive_paths": {"signature_digest": "deadbeef1234567890abcd"},
    }


# ── Smoke ───────────────────────────────────────────────────────────────


def test_packet_pdf_generates_bytes() -> None:
    pdf = generate_lean_packet_pdf(_full_fixture(), payer="UnitedHealthcare", cpt_code="78452")
    assert isinstance(pdf, bytes)
    assert len(pdf) > 1500, "PDF suspiciously small — sections likely empty"
    assert pdf.startswith(b"%PDF-"), "Output must be a real PDF"


# ── Content checks ─────────────────────────────────────────────────────


def _text_blob(pdf: bytes) -> str:
    """Extract visible text from a PDF for content assertions."""
    import io
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def test_packet_includes_patient_and_payer_on_cover() -> None:
    pdf = generate_lean_packet_pdf(_full_fixture(), payer="UnitedHealthcare", cpt_code="78452")
    blob = _text_blob(pdf)
    assert "Eleanor R. Whitford" in blob
    assert "UnitedHealthcare" in blob
    assert "78452" in blob
    assert "UHC-66432198" in blob


def test_packet_includes_medical_necessity_narrative() -> None:
    pdf = generate_lean_packet_pdf(_full_fixture(), payer="UnitedHealthcare", cpt_code="78452")
    blob = _text_blob(pdf)
    assert "Pharmacologic SPECT" in blob or "SPECT" in blob


def test_packet_includes_criterion_codes() -> None:
    pdf = generate_lean_packet_pdf(_full_fixture(), payer="UnitedHealthcare", cpt_code="78452")
    blob = _text_blob(pdf)
    assert "ECG-001" in blob
    assert "NDX-001" in blob
    assert "UHC-PHARM-001" in blob


def test_packet_includes_historical_evidence_citations() -> None:
    """The killer-feature payload: prior-document citations Peter described
    as 'reading the whole chart' must appear in the PDF."""
    pdf = generate_lean_packet_pdf(_full_fixture(), payer="UnitedHealthcare", cpt_code="78452")
    blob = _text_blob(pdf)
    assert "stress_test" in blob
    assert "2023-06-18" in blob
    assert "ecg_report" in blob
    assert "2021-11-03" in blob


def test_packet_includes_provenance_in_footer() -> None:
    pdf = generate_lean_packet_pdf(_full_fixture(), payer="UnitedHealthcare", cpt_code="78452")
    blob = _text_blob(pdf)
    assert "prov-abc123" in blob


# ── Robustness ─────────────────────────────────────────────────────────


def test_packet_handles_empty_lean_result() -> None:
    """No state2_output — must still produce a valid PDF (degenerate
    cover sheet only) rather than throw."""
    pdf = generate_lean_packet_pdf({"case_id": "EMPTY-1"}, payer="UHC", cpt_code="78452")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 800


def test_packet_handles_missing_corpus_snippets() -> None:
    fx = _full_fixture()
    fx["corpus_snippets"] = []
    pdf = generate_lean_packet_pdf(fx, payer="UHC", cpt_code="78452")
    assert pdf.startswith(b"%PDF-")
    # No historical-evidence section when there are no snippets, but
    # other sections still render
    blob = _text_blob(pdf)
    assert "Eleanor R. Whitford" in blob


def test_packet_escapes_html_in_values() -> None:
    """Reportlab Paragraph treats &<> as HTML. Verify our escape works
    so a clinical note with '<' (e.g. 'BMI < 25') doesn't crash."""
    fx = _full_fixture()
    fx["state2_output"]["narrative"]["text"] = "BMI < 25 & history of CAD"
    pdf = generate_lean_packet_pdf(fx, payer="UHC", cpt_code="78452")
    assert pdf.startswith(b"%PDF-")
