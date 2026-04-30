"""Tests for Phase A.5 — typed SubmissionPacket builder."""

from __future__ import annotations

from cardioauth.evidence import EvidenceGraph
from cardioauth.evidence_extraction import emit_spans_for_chart_dict
from cardioauth.models.chart import ChartData
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.packet_builder import (
    _extract_cpt_from_text,
    _extract_procedure_modality,
    build_narrative_attestation,
    build_resolved_cpt,
    build_submission_packet,
)
from cardioauth.payer_forms import get_payer_form
from cardioauth.submission_packet import (
    NarrativeAttestation,
    ResolvedCPT,
    SubmissionPacket,
)


# ── _extract_cpt_from_text ─────────────────────────────────────────────

def test_extract_cpt_explicit_tag() -> None:
    assert _extract_cpt_from_text("Ordering: CPT 78492") == "78492"
    assert _extract_cpt_from_text("CPT: 78452 cardiac SPECT") == "78452"


def test_extract_cpt_bareword_in_cardiology_range() -> None:
    assert _extract_cpt_from_text("Order 78492 today") == "78492"
    assert _extract_cpt_from_text("Procedure 33361 planned") == "33361"


def test_extract_cpt_skips_non_cardiology_bareword() -> None:
    """Without explicit CPT tag, only cardiology-range codes match."""
    assert _extract_cpt_from_text("Phone 12345 ext 6789") == ""


def test_extract_cpt_returns_empty_for_no_match() -> None:
    assert _extract_cpt_from_text("nothing useful here") == ""
    assert _extract_cpt_from_text("") == ""


# ── _extract_procedure_modality ────────────────────────────────────────

def test_modality_extracted_from_narrative() -> None:
    text = "Cardiac PET indicated for ischemia evaluation."
    assert _extract_procedure_modality(text) == "Cardiac PET"


def test_modality_handles_treadmill() -> None:
    text = "Patient completed treadmill protocol."
    assert "treadmill" in _extract_procedure_modality(text).lower()


def test_modality_empty_when_unspecified() -> None:
    assert _extract_procedure_modality("Generic narrative.") == ""


# ── build_resolved_cpt ─────────────────────────────────────────────────

def test_build_resolved_cpt_uses_request_canonical() -> None:
    r, findings = build_resolved_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="",
    )
    assert r.cpt == "78492"
    assert r.source == "request"
    assert r.procedure == "Cardiac PET"
    assert findings == []


def test_build_resolved_cpt_flags_note_divergence_in_rationale() -> None:
    """B.1: note-vs-request divergence emits a typed Finding (was just
    recorded in rationale before)."""
    r, findings = build_resolved_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Ordering: CPT 78452 (Exercise SPECT)",
    )
    assert r.cpt == "78492"
    assert "78452" in r.rationale
    kinds = {f.kind for f in findings}
    assert "cpt_family_mismatch" in kinds


def test_build_resolved_cpt_with_evidence_graph() -> None:
    """When the graph has a span at chart.procedure_code, the CPT
    resolution carries it as evidence."""
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict={"procedure_code": "78492"},
        raw_note="CPT 78492 ordered.",
        graph=g,
    )
    r, _ = build_resolved_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="CPT 78492 ordered.",
        evidence_graph=g,
    )
    assert not r.evidence.is_empty()


# ── build_narrative_attestation ────────────────────────────────────────

def test_narrative_extracts_cpt_and_modality() -> None:
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="Cardiac PET (CPT 78492) is medically necessary.",
    )
    n = build_narrative_attestation(reasoning=reasoning)
    assert n.cpt_referenced == "78492"
    assert "PET" in n.procedure_referenced


def test_narrative_handles_empty_reasoning() -> None:
    n = build_narrative_attestation(reasoning=None)
    assert n.text == ""
    assert n.cpt_referenced == ""


def test_narrative_attestation_returns_typed() -> None:
    reasoning = ReasoningResult(
        approval_likelihood_score=0.5, approval_likelihood_label="MEDIUM",
        pa_narrative_draft="x",
    )
    n = build_narrative_attestation(reasoning=reasoning)
    assert isinstance(n, NarrativeAttestation)


# ── build_submission_packet end-to-end ────────────────────────────────

def _populated_chart() -> ChartData:
    return ChartData(
        patient_id="P-1",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        patient_name="Jane Synthetic",
        date_of_birth="1958-01-15",
        age=67, sex="F",
        attending_physician="Dr. John Doe",
        attending_npi="1234567890",
        insurance_id="UHC-1",
        payer_name="UnitedHealthcare",
        diagnosis_codes=["I25.10"],
    )


def test_build_packet_returns_validated_submission_packet() -> None:
    chart = _populated_chart()
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict=chart.model_dump(mode="json"),
        raw_note="Patient: Jane Synthetic. CPT 78492 ordered.",
        graph=g,
    )
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="Cardiac PET (CPT 78492) is medically necessary.",
    )
    packet = build_submission_packet(
        case_id="C-001",
        raw_note="Patient: Jane Synthetic. CPT 78492 ordered.",
        chart=chart, policy_data=None, reasoning=reasoning,
        evidence_graph=g,
        payer_form=get_payer_form("UnitedHealthcare", "78492"),
        payer="UnitedHealthcare",
    )
    assert isinstance(packet, SubmissionPacket)
    assert packet.case_id == "C-001"
    assert packet.resolved_cpt.cpt == "78492"
    assert packet.resolved_cpt.source == "request"
    assert len(packet.form_fields) > 0


def test_build_packet_carries_versioning_metadata() -> None:
    chart = _populated_chart()
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict=chart.model_dump(mode="json"),
        raw_note="x", graph=g,
    )
    packet = build_submission_packet(
        case_id="C-2", raw_note="x", chart=chart,
        policy_data=None, reasoning=None,
        evidence_graph=g,
        payer_form=get_payer_form("UnitedHealthcare", "78492"),
        payer="UnitedHealthcare",
        model_version="claude-sonnet-4-6",
        form_schema_version="uhc-pet-v1",
    )
    assert packet.model_version == "claude-sonnet-4-6"
    assert packet.form_schema_version == "uhc-pet-v1"
    assert packet.taxonomy_version  # auto-populated from TAXONOMY_VERSION


def test_build_packet_handles_no_form_template() -> None:
    chart = _populated_chart()
    g = EvidenceGraph()
    emit_spans_for_chart_dict(chart_dict=chart.model_dump(mode="json"), raw_note="", graph=g)
    packet = build_submission_packet(
        case_id="C-3", raw_note="", chart=chart,
        policy_data=None, reasoning=None,
        evidence_graph=g,
        payer_form=None, payer="UnknownPayer",
    )
    assert packet.form_fields == []
    assert packet.payer == "UnknownPayer"


def test_build_packet_form_fields_have_evidence_refs() -> None:
    """Form fields whose ontology paths have spans in the graph should
    carry non-empty EvidenceReferences."""
    chart = _populated_chart()
    chart.ecg_findings = []  # populate via emit
    g = EvidenceGraph()
    # Manually add an ECG span
    from cardioauth.evidence import EvidenceSpan
    g.add(EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="chart.ecg_findings[0]",
        extracted_value="LBBB",
        extractor="manual_test",
    ))
    # Plus the demographic spans
    emit_spans_for_chart_dict(chart_dict=chart.model_dump(mode="json"), raw_note="", graph=g)

    packet = build_submission_packet(
        case_id="C-4", raw_note="", chart=chart,
        policy_data=None, reasoning=None,
        evidence_graph=g,
        payer_form=get_payer_form("UnitedHealthcare", "78492"),
        payer="UnitedHealthcare",
    )
    # Patient-name field should pick up the demographic span via direct path
    name_field = next(f for f in packet.form_fields if f.key == "patient_name")
    assert not name_field.evidence.is_empty()


def test_build_packet_validates_referential_integrity() -> None:
    """The packet's .build() validation runs — broken refs would raise."""
    # We rely on the fact that build_submission_packet only inserts spans
    # that exist in the supplied graph, so no exception should fire here.
    chart = _populated_chart()
    g = EvidenceGraph()
    emit_spans_for_chart_dict(chart_dict=chart.model_dump(mode="json"), raw_note="", graph=g)
    packet = build_submission_packet(
        case_id="C-5", raw_note="", chart=chart,
        policy_data=None, reasoning=None,
        evidence_graph=g,
        payer_form=get_payer_form("UnitedHealthcare", "78492"),
        payer="UnitedHealthcare",
    )
    # Sanity: validation error would have raised; reaching here means it passed
    assert packet is not None
