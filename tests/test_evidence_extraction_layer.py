"""Tests for Phase A.3 — extractors that emit EvidenceSpans into a graph."""

from __future__ import annotations

from cardioauth.evidence import EvidenceGraph
from cardioauth.evidence_extraction import (
    ChartExtractionResult,
    emit_spans_for_chart_dict,
    locate_in_source,
    make_span,
)
from cardioauth.symptom_fallback import (
    backfill_symptoms_if_missing,
    extract_symptoms_with_spans,
)


# ── locate_in_source ────────────────────────────────────────────────────

def test_locate_exact_match() -> None:
    src = "67 yo M with BMI 38 and worsening dyspnea on exertion."
    s, e, snippet = locate_in_source(src, "BMI 38")
    assert src[s:e] == "BMI 38"
    assert "BMI 38" in snippet


def test_locate_case_insensitive() -> None:
    src = "Patient: Jane SYNTHETIC, age 67"
    s, e, _ = locate_in_source(src, "Jane Synthetic")
    assert src[s:e].lower() == "jane synthetic"


def test_locate_falls_back_to_token_match() -> None:
    """Multi-word value not found verbatim — token fallback finds the
    longest >=4-char token from the needle."""
    src = "patient with progressive dyspnea on exertion x 3 weeks"
    s, e, snippet = locate_in_source(src, "dyspnea exertion")
    assert s > 0
    # Locator picks the longest token (>=4 chars). Either "dyspnea" or
    # "exertion" is acceptable; the span must lie within the source.
    matched = src[s:e].lower()
    assert any(tok in matched for tok in ("dyspnea", "exertion"))


def test_locate_returns_zero_when_empty() -> None:
    assert locate_in_source("", "foo") == (0, 0, "")
    assert locate_in_source("foo", "") == (0, 0, "")


def test_locate_returns_zero_when_no_match() -> None:
    src = "completely unrelated content"
    s, e, snippet = locate_in_source(src, "BMI 38")
    assert (s, e) == (0, 0)
    assert snippet == ""


# ── make_span ──────────────────────────────────────────────────────────

def test_make_span_with_explicit_offsets() -> None:
    src = "Patient with dyspnea on exertion x 3 weeks"
    span = make_span(
        source_text=src,
        extracted_value="dyspnea on exertion",
        field_path="chart.current_symptoms[0]",
        extractor="regex_symptom",
        explicit_offsets=(13, 32),
    )
    assert span.char_start == 13
    assert span.char_end == 32
    assert span.field_path == "chart.current_symptoms[0]"
    assert span.extractor == "regex_symptom"


def test_make_span_locates_when_no_explicit_offsets() -> None:
    src = "Patient: Jane Synthetic. DOB 1958-01-15."
    span = make_span(
        source_text=src,
        extracted_value="Jane Synthetic",
        field_path="chart.patient_name",
        extractor="claude_chart_extraction",
    )
    assert span.char_start > 0
    assert "Jane" in span.quote


def test_make_span_falls_back_to_value_when_not_in_source() -> None:
    """Value not present in raw note (e.g., normalized form). Span is
    still produced with offsets (0, 0) and quote = the value itself."""
    span = make_span(
        source_text="some unrelated text",
        extracted_value="78492",
        field_path="chart.procedure_code",
        extractor="request_pass_through",
    )
    assert span.char_start == 0
    assert span.char_end == 0
    assert span.quote == "78492" or span.quote == ""


# ── emit_spans_for_chart_dict ──────────────────────────────────────────

def _sample_chart_dict() -> dict:
    return {
        "patient_id": "P-1",
        "patient_name": "Jane Synthetic",
        "date_of_birth": "1958-01-15",
        "age": 67,
        "sex": "F",
        "procedure_code": "78492",
        "procedure_requested": "Cardiac PET",
        "payer_name": "UnitedHealthcare",
        "diagnosis_codes": ["I25.10", "E11.9"],
        "active_comorbidities": ["HTN", "DM", "BMI 38"],
        "current_symptoms": [
            {"name": "dyspnea on exertion", "change_vs_baseline": "new"},
            {"name": "chest pain", "character": "exertional"},
        ],
        "ecg_findings": [{"conduction": "LBBB", "summary": "Sinus rhythm with LBBB"}],
        "prior_stress_tests": [
            {"modality": "SPECT", "interpretation": "non-diagnostic"},
        ],
        "past_medical_history": [
            {"condition": "MI", "date": "2021"},
        ],
        "relevant_labs": [],
        "relevant_medications": [],
    }


def test_emit_spans_demographics() -> None:
    src = "67 yo F Jane Synthetic DOB 1958-01-15 with PMH ..."
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict=_sample_chart_dict(),
        raw_note=src, graph=g,
    )
    paths = {s.field_path for s in g.all_spans()}
    assert "chart.patient_name" in paths
    assert "chart.date_of_birth" in paths
    assert "chart.sex" in paths
    assert "chart.procedure_code" in paths


def test_emit_spans_for_list_buckets() -> None:
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict=_sample_chart_dict(),
        raw_note="placeholder", graph=g,
    )
    paths = {s.field_path for s in g.all_spans()}
    assert "chart.current_symptoms[0]" in paths
    assert "chart.current_symptoms[1]" in paths
    assert "chart.ecg_findings[0]" in paths
    assert "chart.prior_stress_tests[0]" in paths
    assert "chart.past_medical_history[0]" in paths
    assert "chart.diagnosis_codes[0]" in paths
    assert "chart.active_comorbidities[0]" in paths


def test_emit_spans_skips_empty_lists_and_nones() -> None:
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict={
            "patient_name": "",
            "diagnosis_codes": [],
            "current_symptoms": [],
        },
        raw_note="x", graph=g,
    )
    assert len(g) == 0


def test_emit_spans_handles_non_dict_input() -> None:
    g = EvidenceGraph()
    emit_spans_for_chart_dict(chart_dict="not a dict", raw_note="x", graph=g)  # type: ignore[arg-type]
    assert len(g) == 0


def test_emit_spans_extractor_recorded() -> None:
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict={"patient_name": "Jane"},
        raw_note="Patient: Jane Doe.",
        graph=g,
        extractor="my_extractor",
        extractor_version="v2.1",
        base_confidence=0.9,
    )
    s = g.all_spans()[0]
    assert s.extractor == "my_extractor"
    assert s.extractor_version == "v2.1"
    assert s.confidence == 0.9


# ── Symptom backstop emits spans with precise offsets ─────────────────

def test_extract_symptoms_with_spans_returns_offsets() -> None:
    text = "Patient with new-onset dyspnea on exertion x 3 weeks."
    pairs = extract_symptoms_with_spans(text)
    assert len(pairs) > 0
    sym, (start, end) = pairs[0]
    assert sym["name"] == "dyspnea on exertion"
    # Offsets should bracket the matched phrase
    assert "dyspnea on exertion" in text[start:end]


def test_backfill_emits_spans_when_graph_provided() -> None:
    g = EvidenceGraph()
    chart = {"current_symptoms": [], "additional_notes": ""}
    text = "Patient with new-onset dyspnea on exertion x 3 weeks."
    backfill_symptoms_if_missing(chart, text, evidence_graph=g)

    # A span should exist with field_path chart.current_symptoms[0]
    paths = {s.field_path for s in g.all_spans()}
    assert "chart.current_symptoms[0]" in paths
    # And it should have non-zero offsets (regex match has precise span)
    s = g.spans_for_field_path("chart.current_symptoms[0]")[0]
    assert s.char_start > 0
    assert s.char_end > s.char_start
    assert s.extractor == "symptom_fallback"


def test_backfill_no_spans_when_no_graph() -> None:
    """When evidence_graph is None the backstop still works (back-compat)."""
    chart = {"current_symptoms": [], "additional_notes": ""}
    text = "new-onset dyspnea on exertion."
    out = backfill_symptoms_if_missing(chart, text)  # no graph
    assert len(out["current_symptoms"]) == 1


def test_backfill_no_spans_when_claude_already_populated() -> None:
    """When Claude's extraction filled current_symptoms, the backstop
    skips and doesn't add to the graph."""
    g = EvidenceGraph()
    chart = {"current_symptoms": [{"name": "angina"}]}
    text = "Patient with dyspnea on exertion."
    backfill_symptoms_if_missing(chart, text, evidence_graph=g)
    assert len(g) == 0


# ── ChartExtractionResult ──────────────────────────────────────────────

def test_chart_extraction_result_serializes() -> None:
    from cardioauth.models.chart import ChartData

    g = EvidenceGraph()
    chart = ChartData(
        patient_id="P-1", procedure_requested="x", procedure_code="78492",
    )
    r = ChartExtractionResult(chart=chart, evidence_graph=g)
    d = r.to_dict()
    assert "chart" in d
    assert "evidence_graph" in d
    assert d["chart"]["procedure_code"] == "78492"
