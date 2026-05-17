"""Tests for synthesize_chart_context — the FHIR-derived patient context
header that bridges the gap between Epic's structured resources and
the lean pipeline's form_field_values extraction.

Without this header, the Epic pathway pulls demographics into a bundle
but never feeds them to the State 2 LLM — Peter flagged this as the
top gap on 2026-05-17.
"""

from __future__ import annotations

import pytest

from cardioauth.fhir.context_synth import synthesize_chart_context
from cardioauth.patient_corpus import CorpusDocument


def _bundle(**resources) -> dict:
    """Build a minimal bundle with the given resource map. Each value
    should be a list of FHIR resource dicts."""
    return {
        "patient_id": resources.pop("patient_id", "test-1"),
        "resources": {
            rtype: {
                "resourceType": "Bundle", "type": "searchset",
                "total": len(rs), "entry": [{"resource": r} for r in rs],
            }
            for rtype, rs in resources.items()
        },
    }


# ── Demographics ────────────────────────────────────────────────────────


def test_extracts_patient_name_dob_sex() -> None:
    bundle = _bundle(Patient=[{
        "resourceType": "Patient",
        "name": [{"given": ["Eleanor", "R."], "family": "Whitford"}],
        "birthDate": "1957-04-12",
        "gender": "female",
    }])
    out = synthesize_chart_context(bundle, raw_note="body")
    assert "Patient: Eleanor R. Whitford" in out
    assert "DOB: 1957-04-12" in out
    assert "Sex: female" in out


def test_falls_back_to_name_text_when_no_given_family() -> None:
    bundle = _bundle(Patient=[{"resourceType": "Patient", "name": [{"text": "Smith, John"}]}])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "Patient: Smith, John" in out


def test_extracts_mrn_from_patient_identifiers() -> None:
    bundle = _bundle(Patient=[{
        "resourceType": "Patient",
        "name": [{"family": "Test"}],
        "identifier": [{
            "type": {"coding": [{"code": "MR", "display": "MRN"}]},
            "value": "MRN-12345",
        }],
    }])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "MRN: MRN-12345" in out


# ── Coverage ────────────────────────────────────────────────────────────


def test_extracts_payer_and_member_id() -> None:
    bundle = _bundle(Coverage=[{
        "resourceType": "Coverage",
        "subscriberId": "UHC-99887766",
        "payor": [{"display": "UnitedHealthcare"}],
    }])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "Insurance Payer: UnitedHealthcare" in out
    assert "Insurance Member ID: UHC-99887766" in out


def test_skips_coverage_when_missing() -> None:
    """No Coverage resource = no Insurance lines. Don't emit empty headers."""
    bundle = _bundle(Patient=[{"resourceType": "Patient", "name": [{"family": "X"}]}])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "Insurance Payer:" not in out
    assert "Insurance Member ID:" not in out


# ── Practitioner (ordering physician + NPI) ─────────────────────────────


def test_extracts_practitioner_name_and_npi() -> None:
    bundle = _bundle(Practitioner=[{
        "resourceType": "Practitioner",
        "name": [{"given": ["Yuki", "R."], "family": "Tanaka"}],
        "identifier": [{
            "system": "http://hl7.org/fhir/sid/us-npi",
            "value": "1029384765",
        }],
    }])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "Ordering Physician: Yuki R. Tanaka" in out
    assert "NPI: 1029384765" in out


def test_picks_npi_among_multiple_identifier_systems() -> None:
    """Practitioner often has multiple identifier systems (DEA, NPI, internal).
    Pick the one tagged with the NPI system URI."""
    bundle = _bundle(Practitioner=[{
        "resourceType": "Practitioner",
        "name": [{"family": "Tester"}],
        "identifier": [
            {"system": "https://internal.epic.com/provider-id", "value": "INT-001"},
            {"system": "http://hl7.org/fhir/sid/us-npi", "value": "9999999999"},
            {"system": "https://dea.gov", "value": "DEA-X"},
        ],
    }])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "NPI: 9999999999" in out
    assert "INT-001" not in out
    assert "DEA-X" not in out


# ── Encounter ───────────────────────────────────────────────────────────


def test_extracts_encounter_date_from_period_start() -> None:
    bundle = _bundle(Encounter=[{
        "resourceType": "Encounter",
        "period": {"start": "2026-05-06T14:30:00Z"},
    }])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "Encounter Date: 2026-05-06" in out


# ── CPT codes (Procedure + ServiceRequest) ──────────────────────────────


def test_extracts_cpt_code_with_display() -> None:
    bundle = _bundle(Procedure=[{
        "resourceType": "Procedure",
        "code": {
            "coding": [{
                "system": "http://www.ama-assn.org/go/cpt",
                "code": "78452",
                "display": "SPECT MPI",
            }],
        },
    }])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "78452" in out
    assert "SPECT MPI" in out


def test_ignores_non_cpt_coding_systems() -> None:
    """Procedure resources can carry both SNOMED + CPT codes. Pick CPT only."""
    bundle = _bundle(Procedure=[{
        "resourceType": "Procedure",
        "code": {"coding": [
            {"system": "http://snomed.info/sct", "code": "12345", "display": "snomed"},
            {"system": "http://www.ama-assn.org/go/cpt", "code": "78452", "display": "SPECT"},
        ]},
    }])
    out = synthesize_chart_context(bundle, raw_note="")
    # CPT code present, SNOMED code not
    assert "78452" in out
    assert "snomed" not in out


# ── ICD-10 diagnoses ────────────────────────────────────────────────────


def test_extracts_icd10_codes_from_conditions() -> None:
    bundle = _bundle(Condition=[
        {
            "resourceType": "Condition",
            "code": {
                "coding": [{
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": "I25.10",
                }],
                "text": "Atherosclerotic heart disease",
            },
        },
        {
            "resourceType": "Condition",
            "code": {
                "coding": [{
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": "I44.7",
                    "display": "Left bundle branch block",
                }],
            },
        },
    ])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "I25.10: Atherosclerotic heart disease" in out
    assert "I44.7: Left bundle branch block" in out


# ── Historical documents index ──────────────────────────────────────────


def test_lists_corpus_documents_in_header() -> None:
    """The LLM should see what historical documents are available so it
    knows what corpus retrieval can pull from."""
    docs = [
        CorpusDocument(doc_id="d1", doc_type="stress_test", date="2023-06-18",
                       title="Treadmill stress", text="..."),
        CorpusDocument(doc_id="d2", doc_type="ecg_report", date="2021-11-03",
                       title="12-lead ECG", text="..."),
    ]
    out = synthesize_chart_context({"resources": {}}, raw_note="", corpus_documents=docs)
    assert "HISTORICAL DOCUMENTS AVAILABLE" in out
    assert "stress_test 2023-06-18" in out
    assert "Treadmill stress" in out
    assert "ecg_report 2021-11-03" in out


def test_skips_current_note_from_historical_index() -> None:
    """The current-note doc shouldn't appear in the 'historical documents
    available' section — it's already inlined below."""
    docs = [
        CorpusDocument(doc_id="cur", doc_type="current_note", date="2026-05-06",
                       title="Current note", text="..."),
        CorpusDocument(doc_id="d1", doc_type="stress_test", date="2023-06-18",
                       title="Stress", text="..."),
    ]
    out = synthesize_chart_context({"resources": {}}, raw_note="", corpus_documents=docs)
    # Stress test (historical) should be listed; current note should not
    assert "Stress" in out
    # The historical-docs index section says "1 indexed" not "2"
    assert "(1 indexed)" in out


# ── Raw note inclusion ──────────────────────────────────────────────────


def test_raw_note_body_appears_after_header() -> None:
    bundle = _bundle(Patient=[{"resourceType": "Patient", "name": [{"family": "X"}]}])
    out = synthesize_chart_context(bundle, raw_note="The patient has chest pain.")
    # Body section appears after the header marker
    header_end = out.find("=== CURRENT ENCOUNTER NOTE ===")
    body_pos = out.find("The patient has chest pain.")
    assert header_end < body_pos


def test_placeholder_when_raw_note_empty() -> None:
    """If no note text is provided, the body section shows a placeholder
    pointing the LLM at historical docs instead of an empty section."""
    bundle = _bundle(Patient=[{"resourceType": "Patient", "name": [{"family": "X"}]}])
    out = synthesize_chart_context(bundle, raw_note="")
    assert "=== CURRENT ENCOUNTER NOTE ===" in out
    assert "No current encounter note text supplied" in out


# ── Resilience ──────────────────────────────────────────────────────────


def test_empty_bundle_returns_minimal_header() -> None:
    """An empty bundle (no resources) should still produce a valid
    enriched note — just with a near-empty header and the raw note body."""
    out = synthesize_chart_context({"resources": {}}, raw_note="body")
    assert "=== PATIENT CONTEXT" in out
    assert "=== CURRENT ENCOUNTER NOTE ===" in out
    assert "body" in out


def test_handles_resource_with_error_field() -> None:
    """If a resource came back with {error: ...} (FHIRClient error path),
    the synthesizer must skip it without exploding."""
    bundle = {
        "resources": {
            "Patient": {"error": "403 Forbidden"},
            "Coverage": {"error": "timeout"},
            "Condition": {
                "resourceType": "Bundle",
                "entry": [{
                    "resource": {
                        "resourceType": "Condition",
                        "code": {
                            "coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I10"}],
                            "text": "Hypertension",
                        },
                    },
                }],
            },
        },
    }
    out = synthesize_chart_context(bundle, raw_note="x")
    # Patient/Coverage failed → no lines for those, but Condition still works
    assert "Patient:" not in out
    assert "Insurance Payer:" not in out
    assert "I10: Hypertension" in out
