"""Tests for the FHIR → PatientCorpus mapper.

The mapper is the bridge between Epic's R4 Bundle shape and the lean
pipeline's longitudinal-corpus contract. If this is right, the
treadmill-3-years-ago feature works on real Epic data; if it's wrong,
the LLM gets the wrong document types and retrieval misses the snippets
that matter.
"""

from __future__ import annotations

import base64

from cardioauth.fhir.corpus_mapper import (
    bundle_to_patient_corpus,
    document_reference_to_corpus_doc,
    encounter_to_corpus_doc,
)


def _docref(
    doc_id: str = "dr1",
    loinc: str = "18746-1",
    date: str = "2023-06-18T10:00:00Z",
    type_text: str = "",
    inline_text: str = "stress test body",
    content_type: str = "text/plain",
) -> dict:
    coding = []
    if loinc:
        coding.append({
            "system": "http://loinc.org",
            "code": loinc,
            "display": "Cardiac stress study report",
        })
    return {
        "resourceType": "DocumentReference",
        "id": doc_id,
        "date": date,
        "type": {"coding": coding, "text": type_text} if (coding or type_text) else {},
        "content": [{
            "attachment": {
                "contentType": content_type,
                "data": base64.b64encode(inline_text.encode()).decode() if inline_text else "",
            }
        }],
    }


# ── DocumentReference mapping ───────────────────────────────────────────


def test_docref_with_stress_loinc_maps_to_stress_test() -> None:
    cd = document_reference_to_corpus_doc(_docref(loinc="18746-1"))
    assert cd is not None
    assert cd.doc_type == "stress_test"


def test_docref_ekg_loinc_maps_to_ecg_report() -> None:
    cd = document_reference_to_corpus_doc(_docref(loinc="11524-6"))
    assert cd is not None
    assert cd.doc_type == "ecg_report"


def test_docref_falls_back_to_display_text_when_no_loinc() -> None:
    """Epic sometimes emits useful display text but no LOINC code."""
    docref = _docref(loinc="", type_text="Echocardiogram report")
    cd = document_reference_to_corpus_doc(docref)
    assert cd is not None
    assert cd.doc_type == "echo_report"


def test_docref_unknown_loinc_and_display_becomes_outside_records() -> None:
    """Forward-compat: when neither LOINC nor display gives a clue, the
    document still participates in retrieval as outside_records."""
    docref = _docref(loinc="")
    docref["type"] = {
        "coding": [{"system": "http://loinc.org", "code": "99999-9", "display": "Unknown form"}],
        "text": "",
    }
    cd = document_reference_to_corpus_doc(docref)
    assert cd is not None
    assert cd.doc_type == "outside_records"


def test_docref_date_truncated_to_iso_date() -> None:
    cd = document_reference_to_corpus_doc(_docref(date="2023-06-18T10:00:00Z"))
    assert cd is not None
    assert cd.date == "2023-06-18"


def test_docref_text_override_wins_over_inline() -> None:
    """When the caller has already resolved a Binary attachment, that text
    must override the inline `data` field."""
    docref = _docref(inline_text="inline body")
    cd = document_reference_to_corpus_doc(docref, text_override="resolved Binary body")
    assert cd is not None
    assert cd.text == "resolved Binary body"


def test_docref_with_no_body_returns_none() -> None:
    """A DocumentReference whose attachment is URL-only and unresolved
    must be skipped so it doesn't pollute BM25 with an empty document."""
    docref = _docref(inline_text="", content_type="application/pdf")
    docref["content"] = [{"attachment": {"url": "Binary/abc", "contentType": "application/pdf"}}]
    assert document_reference_to_corpus_doc(docref) is None


def test_docref_binary_pdf_inline_is_ignored() -> None:
    """We don't try to decode PDF bytes inline — caller must layer in
    PDF parsing through text_override."""
    docref = _docref(inline_text="not really a pdf", content_type="application/pdf")
    assert document_reference_to_corpus_doc(docref) is None


# ── Encounter mapping ───────────────────────────────────────────────────


def test_encounter_with_reason_and_diagnosis_becomes_prior_encounter() -> None:
    enc = {
        "resourceType": "Encounter",
        "id": "enc1",
        "period": {"start": "2024-09-22T09:00:00Z"},
        "reasonReference": [{"display": "Stable angina, CCS Class II"}],
        "diagnosis": [{"condition": {"display": "Chronic LBBB"}}],
        "type": [{"coding": [{"display": "Office visit"}]}],
    }
    cd = encounter_to_corpus_doc(enc)
    assert cd is not None
    assert cd.doc_type == "prior_encounter"
    assert cd.date == "2024-09-22"
    assert "Stable angina" in cd.text
    assert "Chronic LBBB" in cd.text


def test_encounter_with_no_narrative_is_skipped() -> None:
    """Empty billing-row encounters add noise — skip them."""
    enc = {"resourceType": "Encounter", "id": "enc1", "status": "finished"}
    assert encounter_to_corpus_doc(enc) is None


# ── Full bundle ─────────────────────────────────────────────────────────


def test_bundle_to_corpus_builds_full_patient_view() -> None:
    """End-to-end mapping of a synthetic Epic-shape Bundle.

    Mirrors what we expect to see from fhir.epic.com: one current
    encounter, one prior stress test, one prior ECG, one prior visit.
    """
    bundle = {
        "patient_id": "test-pt-1",
        "resources": {
            "DocumentReference": {
                "entry": [
                    {"resource": _docref(doc_id="stress-2023", loinc="18746-1",
                                          date="2023-06-18T10:00:00Z",
                                          inline_text="Submaximal nondiagnostic treadmill, LBBB.")},
                    {"resource": _docref(doc_id="ecg-2021", loinc="11524-6",
                                          date="2021-11-03T10:00:00Z",
                                          inline_text="Chronic LBBB on 12-lead ECG.")},
                ],
            },
            "Encounter": {
                "entry": [
                    {"resource": {
                        "resourceType": "Encounter",
                        "id": "enc-2024",
                        "period": {"start": "2024-09-22T09:00:00Z"},
                        "diagnosis": [{"condition": {"display": "Stable angina"}}],
                    }},
                ],
            },
        },
    }
    corpus = bundle_to_patient_corpus(
        bundle,
        current_note_text="Patient with chest pain. Plan: SPECT.",
        current_note_date="2026-05-06",
    )
    assert corpus.patient_id == "test-pt-1"
    assert corpus.current_note() is not None
    assert corpus.current_note().date == "2026-05-06"
    historical = corpus.historical()
    types = {d.doc_type for d in historical}
    assert "stress_test" in types
    assert "ecg_report" in types
    assert "prior_encounter" in types


def test_bundle_attachment_text_override_replaces_inline() -> None:
    """When the caller resolved Binary refs externally and passes a
    {doc_id: text} map, that text wins."""
    bundle = {
        "patient_id": "test-pt-2",
        "resources": {
            "DocumentReference": {
                "entry": [
                    {"resource": _docref(doc_id="stress-2023", inline_text="inline placeholder")},
                ],
            },
        },
    }
    corpus = bundle_to_patient_corpus(
        bundle,
        attachment_texts={"stress-2023": "resolved Binary body with rich detail"},
    )
    assert any("rich detail" in d.text for d in corpus.documents)


def test_bundle_with_no_documents_returns_empty_corpus() -> None:
    """Empty patient (e.g. brand-new chart) — mapper must not explode."""
    bundle = {"patient_id": "empty", "resources": {}}
    corpus = bundle_to_patient_corpus(bundle)
    assert corpus.patient_id == "empty"
    assert corpus.documents == []
