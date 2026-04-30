"""Tests for the typed SubmissionPacket layer."""

from __future__ import annotations

import pytest

from cardioauth.evidence import (
    EMPTY_REFERENCE,
    EvidenceGraph,
    EvidenceReference,
    EvidenceSpan,
)
from cardioauth.submission_packet import (
    Finding,
    FormFieldEntry,
    NarrativeAttestation,
    ResolvedCPT,
    ReviewerVerdict,
    SubmissionPacket,
    SubmissionPacketIntegrityError,
    severity_rank,
)


# ── Helper: minimal valid packet ───────────────────────────────────────

def _make_packet(*, with_unresolved_field_ref: bool = False) -> SubmissionPacket:
    """Build a minimal valid packet for tests."""
    g = EvidenceGraph()
    cpt_span = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        char_start=0, char_end=10, quote="CPT 78492",
        extracted_value="78492",
        field_path="resolved_cpt.cpt", extractor="regex_cpt",
        confidence=0.95,
    )
    g.add(cpt_span)

    field_span = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        quote="patient name: Jane Synthetic", extracted_value="Jane Synthetic",
        field_path="form.patient_name", extractor="regex_demographic",
        confidence=1.0,
    )
    g.add(field_span)

    resolved = ResolvedCPT(
        cpt="78492", procedure="Cardiac PET", source="request",
        evidence=EvidenceReference.from_spans([cpt_span]),
    )
    fields = [
        FormFieldEntry(
            key="patient_name", label="Patient name", category="patient",
            required=True, format="text", value="Jane Synthetic",
            evidence=EvidenceReference.from_spans([field_span]),
            status="populated",
        ),
    ]
    if with_unresolved_field_ref:
        fields[0].evidence = EvidenceReference(span_ids=("span_does_not_exist",))

    narrative = NarrativeAttestation(
        text="Cardiac PET medically necessary.",
        cpt_referenced="78492", procedure_referenced="Cardiac PET",
    )

    return SubmissionPacket.build(
        case_id="C-001",
        raw_note="CPT 78492. Patient name: Jane Synthetic.",
        chart_data={"procedure_code": "78492"},
        policy_data=None,
        resolved_cpt=resolved,
        payer="UnitedHealthcare",
        form_fields=fields,
        narrative=narrative,
        evidence_graph=g,
    )


# ── ResolvedCPT ────────────────────────────────────────────────────────

def test_resolved_cpt_carries_evidence() -> None:
    g = EvidenceGraph()
    s = EvidenceSpan.new(source_id="raw_note", source_type="raw_note")
    g.add(s)
    r = ResolvedCPT(
        cpt="78492", procedure="Cardiac PET", source="note_extracted",
        evidence=EvidenceReference.from_spans([s]),
        rationale="extracted from order line in note",
    )
    assert r.evidence.span_ids == (s.span_id,)
    assert r.source == "note_extracted"


def test_resolved_cpt_serializes() -> None:
    r = ResolvedCPT(
        cpt="78452", procedure="Exercise SPECT", source="reasoner_recommended",
    )
    d = r.to_dict()
    assert d["cpt"] == "78452"
    assert d["source"] == "reasoner_recommended"
    assert "evidence" in d


# ── FormFieldEntry ──────────────────────────────────────────────────────

def test_form_field_default_status_is_missing() -> None:
    f = FormFieldEntry(
        key="x", label="X", category="patient",
        required=True, format="text",
    )
    assert f.status == "missing"
    assert f.evidence.is_empty()


def test_form_field_serializes_with_evidence() -> None:
    s = EvidenceSpan.new(source_id="raw_note", source_type="raw_note")
    f = FormFieldEntry(
        key="x", label="X", category="patient", required=True, format="text",
        value="value", evidence_text="rich evidence text",
        evidence=EvidenceReference.from_spans([s]),
        status="populated",
    )
    d = f.to_dict()
    assert d["value"] == "value"
    assert d["evidence_text"] == "rich evidence text"
    assert d["evidence"]["span_ids"] == [s.span_id]


# ── Finding ────────────────────────────────────────────────────────────

def test_finding_severity_ranks() -> None:
    assert severity_rank("info") < severity_rank("low") < severity_rank("medium")
    assert severity_rank("high") < severity_rank("blocking")


def test_finding_normalizes_related_keys_list() -> None:
    f = Finding(
        kind="x", severity="medium", message="m",
        related_field_keys=["a", "b"],  # type: ignore[arg-type]
    )
    assert f.related_field_keys == ("a", "b")


def test_finding_serializes() -> None:
    f = Finding(
        kind="cpt_attestation_mismatch",
        severity="high", message="CPT mismatch",
        auto_fixable=True, fix_suggestion="rewrite attestation",
        checker="coherence_v1",
    )
    d = f.to_dict()
    assert d["kind"] == "cpt_attestation_mismatch"
    assert d["auto_fixable"] is True
    assert d["checker"] == "coherence_v1"


# ── ReviewerVerdict ────────────────────────────────────────────────────

def test_reviewer_verdict_empty_default_passes() -> None:
    v = ReviewerVerdict.empty()
    assert v.passed is True
    assert v.overall_recommendation == "transmit"
    assert v.findings_per_question == {}


def test_reviewer_verdict_serializes_findings_by_question() -> None:
    v = ReviewerVerdict(
        passed=False, overall_recommendation="hold",
        rationale="CPT inconsistency detected",
        findings_per_question={
            "q3": [Finding(kind="cpt_mismatch", severity="high", message="CPT differs")],
        },
        model="claude-sonnet-4-6",
        cost_input_tokens=1200, cost_output_tokens=300, duration_ms=4200,
    )
    d = v.to_dict()
    assert d["passed"] is False
    assert d["overall_recommendation"] == "hold"
    assert "q3" in d["findings_per_question"]
    assert d["findings_per_question"]["q3"][0]["kind"] == "cpt_mismatch"


# ── SubmissionPacket integrity ─────────────────────────────────────────

def test_packet_builds_with_valid_references() -> None:
    p = _make_packet()
    assert p.case_id == "C-001"
    assert p.resolved_cpt.cpt == "78492"
    assert p.form_fields[0].status == "populated"


def test_packet_rejects_unresolved_field_evidence() -> None:
    with pytest.raises(SubmissionPacketIntegrityError) as exc:
        _make_packet(with_unresolved_field_ref=True)
    assert "patient_name" in str(exc.value)
    assert "span_does_not_exist" in str(exc.value)


def test_packet_rejects_unresolved_cpt_evidence() -> None:
    g = EvidenceGraph()
    resolved = ResolvedCPT(
        cpt="78492", procedure="x", source="request",
        evidence=EvidenceReference(span_ids=("span_phantom",)),
    )
    narrative = NarrativeAttestation(text="x")
    with pytest.raises(SubmissionPacketIntegrityError):
        SubmissionPacket.build(
            case_id="C-001", raw_note="",
            chart_data={}, policy_data=None,
            resolved_cpt=resolved, payer="X",
            form_fields=[], narrative=narrative,
            evidence_graph=g,
        )


def test_packet_accepts_empty_evidence_references() -> None:
    """Form fields without evidence should be allowed (e.g., needs_verify)."""
    g = EvidenceGraph()
    cpt_span = EvidenceSpan.new(source_id="raw_note", source_type="raw_note")
    g.add(cpt_span)
    resolved = ResolvedCPT(
        cpt="78492", procedure="x", source="request",
        evidence=EvidenceReference.from_spans([cpt_span]),
    )
    fields = [
        FormFieldEntry(
            key="attestation", label="Attestation",
            category="attestation", required=True, format="checkbox",
            status="needs_verify",
            # evidence intentionally empty
        ),
    ]
    p = SubmissionPacket.build(
        case_id="C-1", raw_note="x", chart_data={}, policy_data=None,
        resolved_cpt=resolved, payer="X",
        form_fields=fields, narrative=NarrativeAttestation(text="x"),
        evidence_graph=g,
    )
    assert p.form_fields[0].status == "needs_verify"


def test_packet_findings_can_be_appended() -> None:
    p = _make_packet()
    p.add_findings([
        Finding(kind="x", severity="medium", message="m"),
        Finding(kind="y", severity="high", message="m2"),
    ])
    assert len(p.deterministic_findings) == 2


def test_packet_highest_severity_aggregates_across_sources() -> None:
    p = _make_packet()
    p.add_findings([Finding(kind="x", severity="low", message="m")])
    v = ReviewerVerdict(
        passed=False, overall_recommendation="hold", rationale="r",
        findings_per_question={"q1": [Finding(kind="y", severity="high", message="m")]},
    )
    p.set_reviewer_verdict(v)
    assert p.highest_severity() == "high"


def test_packet_blocking_finding_detected() -> None:
    p = _make_packet()
    assert p.has_blocking_findings() is False
    p.add_findings([Finding(kind="x", severity="blocking", message="stop")])
    assert p.has_blocking_findings() is True


def test_packet_round_trip_serialization() -> None:
    p = _make_packet()
    p.add_findings([Finding(kind="x", severity="high", message="m")])
    d = p.to_dict()
    assert d["case_id"] == "C-001"
    assert d["resolved_cpt"]["cpt"] == "78492"
    assert len(d["form_fields"]) == 1
    assert len(d["evidence_graph"]["spans"]) == 2
    assert d["deterministic_findings"][0]["kind"] == "x"


def test_packet_versioning_metadata_carried() -> None:
    g = EvidenceGraph()
    cpt_span = EvidenceSpan.new(source_id="raw_note", source_type="raw_note")
    g.add(cpt_span)
    p = SubmissionPacket.build(
        case_id="C-1", raw_note="", chart_data={}, policy_data=None,
        resolved_cpt=ResolvedCPT(
            cpt="78492", procedure="x", source="request",
            evidence=EvidenceReference.from_spans([cpt_span]),
        ),
        payer="X", form_fields=[], narrative=NarrativeAttestation(text="x"),
        evidence_graph=g,
        taxonomy_version="taxonomy-v3.2",
        form_schema_version="uhc-pet-v1.4",
        model_version="claude-sonnet-4-6",
    )
    assert p.taxonomy_version == "taxonomy-v3.2"
    assert p.form_schema_version == "uhc-pet-v1.4"
    assert p.model_version == "claude-sonnet-4-6"
