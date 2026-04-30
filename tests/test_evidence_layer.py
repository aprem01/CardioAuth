"""Tests for the evidence-span lineage layer."""

from __future__ import annotations

import pytest

from cardioauth.evidence import (
    EMPTY_REFERENCE,
    EvidenceGraph,
    EvidenceReference,
    EvidenceSpan,
)


# ── EvidenceSpan ────────────────────────────────────────────────────────

def test_span_factory_assigns_id_and_timestamp() -> None:
    s = EvidenceSpan.new(
        source_id="raw_note",
        source_type="raw_note",
        char_start=0, char_end=10,
        quote="dyspnea",
        extracted_value="dyspnea",
        field_path="chart.current_symptoms[0].name",
        extractor="regex_symptom",
        confidence=0.9,
    )
    assert s.span_id.startswith("span_")
    assert s.extracted_at  # ISO timestamp populated
    assert s.confidence == 0.9


def test_span_quote_truncated() -> None:
    long_quote = "x" * 1000
    s = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        quote=long_quote, extracted_value="x", field_path="x",
    )
    assert len(s.quote) <= 240


def test_span_confidence_validated() -> None:
    with pytest.raises(ValueError):
        EvidenceSpan.new(
            source_id="x", source_type="raw_note", confidence=1.5,
        )
    with pytest.raises(ValueError):
        EvidenceSpan.new(
            source_id="x", source_type="raw_note", confidence=-0.1,
        )


def test_span_is_immutable() -> None:
    s = EvidenceSpan.new(source_id="x", source_type="raw_note")
    with pytest.raises(Exception):
        s.confidence = 0.5  # type: ignore[misc]


# ── EvidenceReference ──────────────────────────────────────────────────

def test_reference_from_spans_keeps_ids() -> None:
    s1 = EvidenceSpan.new(source_id="x", source_type="raw_note")
    s2 = EvidenceSpan.new(source_id="x", source_type="raw_note")
    ref = EvidenceReference.from_spans([s1, s2], rationale="symptom + duration")
    assert ref.span_ids == (s1.span_id, s2.span_id)
    assert ref.rationale == "symptom + duration"


def test_reference_normalizes_list_to_tuple() -> None:
    ref = EvidenceReference(span_ids=["a", "b"])  # type: ignore[arg-type]
    assert ref.span_ids == ("a", "b")


def test_empty_reference_is_empty() -> None:
    assert EMPTY_REFERENCE.is_empty()
    assert EvidenceReference.from_spans([]).is_empty()
    assert not EvidenceReference.from_spans(
        [EvidenceSpan.new(source_id="x", source_type="raw_note")]
    ).is_empty()


def test_reference_default_derivation() -> None:
    ref = EvidenceReference(span_ids=("a",))
    assert ref.derivation == "direct"


# ── EvidenceGraph ──────────────────────────────────────────────────────

def test_graph_add_and_get() -> None:
    g = EvidenceGraph()
    s = EvidenceSpan.new(source_id="raw_note", source_type="raw_note")
    sid = g.add(s)
    assert sid == s.span_id
    assert g.get(sid) is s


def test_graph_add_idempotent() -> None:
    g = EvidenceGraph()
    s = EvidenceSpan.new(source_id="raw_note", source_type="raw_note")
    g.add(s)
    g.add(s)  # adding the same span twice doesn't dup
    assert len(g) == 1


def test_graph_lookup_by_source() -> None:
    g = EvidenceGraph()
    s1 = EvidenceSpan.new(source_id="raw_note", source_type="raw_note")
    s2 = EvidenceSpan.new(source_id="fhir_obs:abc", source_type="fhir_resource")
    g.add(s1)
    g.add(s2)
    assert len(g.spans_for_source("raw_note")) == 1
    assert len(g.spans_for_source("fhir_obs:abc")) == 1


def test_graph_lookup_by_extractor() -> None:
    g = EvidenceGraph()
    g.add(EvidenceSpan.new(source_id="x", source_type="raw_note", extractor="regex"))
    g.add(EvidenceSpan.new(source_id="x", source_type="raw_note", extractor="claude"))
    g.add(EvidenceSpan.new(source_id="x", source_type="raw_note", extractor="claude"))
    assert len(g.spans_for_extractor("claude")) == 2
    assert len(g.spans_for_extractor("regex")) == 1


def test_graph_lookup_by_field_path() -> None:
    g = EvidenceGraph()
    g.add(EvidenceSpan.new(
        source_id="x", source_type="raw_note",
        field_path="chart.current_symptoms[0].name",
    ))
    g.add(EvidenceSpan.new(
        source_id="x", source_type="raw_note",
        field_path="chart.active_comorbidities[0]",
    ))
    out = g.spans_for_field_path("chart.current_symptoms[0].name")
    assert len(out) == 1


def test_graph_references_resolve() -> None:
    g = EvidenceGraph()
    s = EvidenceSpan.new(source_id="x", source_type="raw_note")
    g.add(s)

    good_ref = EvidenceReference(span_ids=(s.span_id,))
    assert g.references_resolve(good_ref) is True

    bad_ref = EvidenceReference(span_ids=("span_nonexistent",))
    assert g.references_resolve(bad_ref) is False
    assert g.unresolved_span_ids(bad_ref) == ["span_nonexistent"]


def test_graph_round_trip_serialization() -> None:
    g = EvidenceGraph()
    g.add(EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        char_start=0, char_end=7, quote="dyspnea",
        extracted_value="dyspnea", field_path="chart.current_symptoms[0].name",
        extractor="regex_symptom", confidence=0.85,
    ))
    g.add(EvidenceSpan.new(
        source_id="fhir_obs:bmi", source_type="fhir_resource",
        extracted_value="BMI 38", field_path="chart.active_comorbidities[2]",
        extractor="fhir_observation", confidence=1.0,
    ))
    payload = g.to_dict()
    restored = EvidenceGraph.from_dict(payload)
    assert len(restored) == 2
    assert all(s.span_id in restored.spans for s in g.all_spans())


def test_graph_get_many_skips_missing() -> None:
    g = EvidenceGraph()
    s = EvidenceSpan.new(source_id="x", source_type="raw_note")
    g.add(s)
    out = g.get_many([s.span_id, "missing_id"])
    assert len(out) == 1
    assert out[0] is s
