"""Tests for the patient longitudinal corpus + retrieval."""

from __future__ import annotations

import pytest

from cardioauth.patient_corpus import (
    CorpusDocument,
    PatientCorpus,
    build_query_terms,
    retrieve_corpus,
)


def _doc(doc_id: str, dtype: str, text: str, date: str = "", title: str = "") -> CorpusDocument:
    return CorpusDocument(
        doc_id=doc_id,
        doc_type=dtype,  # type: ignore[arg-type]
        date=date,
        title=title or doc_id,
        text=text,
    )


# ── PatientCorpus basics ───────────────────────────────────────────────


def test_corpus_separates_current_from_historical() -> None:
    c = PatientCorpus(
        patient_id="P1",
        documents=[
            _doc("a", "current_note", "current"),
            _doc("b", "stress_test", "old stress"),
        ],
    )
    assert c.current_note().doc_id == "a"
    assert [d.doc_id for d in c.historical()] == ["b"]


def test_corpus_to_dict_summarizes_shape() -> None:
    c = PatientCorpus(
        patient_id="P1",
        documents=[
            _doc("a", "current_note", "x"),
            _doc("b", "stress_test", "y", date="2023-06-18"),
            _doc("c", "ecg_report", "z", date="2021-11-03"),
        ],
    )
    d = c.to_dict()
    assert d["document_count"] == 3
    assert d["historical_count"] == 2
    assert set(d["doc_types"]) == {"stress_test", "ecg_report"}
    assert d["earliest_date"] == "2021-11-03"
    assert d["latest_date"] == "2023-06-18"


# ── BM25 retrieval ─────────────────────────────────────────────────────


def test_retrieve_finds_lbbb_in_historical_ecg() -> None:
    """Killer feature: current note doesn't mention LBBB, but a prior
    ECG report does. The retriever should surface it for the LLM."""
    c = PatientCorpus(
        patient_id="P1",
        documents=[
            _doc("current", "current_note", "Patient with chest pain. Plan: SPECT."),
            _doc(
                "ecg-2021", "ecg_report",
                "12-lead ECG shows chronic LBBB with QRS duration 152 ms. "
                "Nonspecific ST-T abnormalities secondary to conduction delay.",
                date="2021-11-03", title="Prior ECG",
            ),
            _doc(
                "stress-2023", "stress_test",
                "Patient exercised 4 minutes 12 seconds on Bruce protocol, "
                "achieved 6 METs and 76 percent of target heart rate. "
                "Test stopped due to knee pain. Submaximal nondiagnostic.",
                date="2023-06-18", title="Prior stress",
            ),
        ],
    )
    snippets = retrieve_corpus(
        c,
        query_terms=["LBBB", "left bundle", "exercise", "treadmill", "nondiagnostic"],
        top_k=5,
    )
    assert snippets, "expected at least one snippet"
    # First snippet should reference LBBB or the stress test
    top_docs = [s.doc_id for s in snippets[:2]]
    assert ("ecg-2021" in top_docs) or ("stress-2023" in top_docs)


def test_retrieve_excludes_current_note() -> None:
    """Retrieval should NOT pull from the current encounter; the LLM
    already has that document in the prompt directly."""
    c = PatientCorpus(
        patient_id="P1",
        documents=[
            _doc("current", "current_note", "LBBB documented today on ECG."),
            _doc("hist", "ecg_report", "Old ECG shows LBBB."),
        ],
    )
    snippets = retrieve_corpus(c, query_terms=["LBBB"], top_k=5)
    for s in snippets:
        assert s.doc_id != "current", "current note must not be retrieved"


def test_retrieve_returns_empty_when_no_match() -> None:
    c = PatientCorpus(
        patient_id="P1",
        documents=[
            _doc("hist", "stress_test", "Patient ran 12 minutes, normal."),
        ],
    )
    snippets = retrieve_corpus(c, query_terms=["TAVR", "aortic stenosis"], top_k=5)
    assert snippets == []


def test_retrieve_returns_empty_with_no_query_terms() -> None:
    c = PatientCorpus(
        patient_id="P1",
        documents=[_doc("hist", "stress_test", "some content")],
    )
    assert retrieve_corpus(c, query_terms=[], top_k=5) == []


def test_snippet_citation_includes_doc_title_and_date() -> None:
    c = PatientCorpus(
        patient_id="P1",
        documents=[
            _doc("h", "stress_test",
                 "Exercise treadmill nondiagnostic submaximal.",
                 date="2023-06-18", title="Stress test 2023"),
        ],
    )
    snippets = retrieve_corpus(
        c, query_terms=["treadmill", "exercise", "nondiagnostic"], top_k=1,
    )
    assert snippets
    cite = snippets[0].citation()
    assert "Stress test 2023" in cite
    assert "2023-06-18" in cite


# ── Query-term construction ────────────────────────────────────────────


def test_build_query_terms_from_taxonomy_slice() -> None:
    crit = [
        {"code": "ECG-001"},
        {"code": "EX-001"},
        {"code": "BMI-001"},
    ]
    terms = build_query_terms(crit, "78452")
    assert "LBBB" in terms
    assert "exercise" in terms
    assert "BMI" in terms
    # CPT-specific keywords also injected
    assert "SPECT" in terms


def test_build_query_terms_handles_unknown_criterion_prefix() -> None:
    """Unknown criterion code prefixes are silently skipped — schema
    forward-compat as new procedures land."""
    crit = [{"code": "ZZZ-001"}]
    terms = build_query_terms(crit, "78492")
    # No keywords for ZZZ but CPT-specific keywords still come through
    assert "PET" in terms


def test_build_query_terms_dedupes() -> None:
    crit = [{"code": "ECG-001"}, {"code": "ECG-002"}, {"code": "ECG-003"}]
    terms = build_query_terms(crit, "78452")
    # 'LBBB' is in the ECG category — should appear once, not 3 times
    assert terms.count("LBBB") == 1
