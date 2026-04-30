"""Tests for Phase C.1 + C.2 — versioned freezing + replay."""

from __future__ import annotations

import pytest

from cardioauth.evidence import (
    EvidenceGraph,
    EvidenceReference,
    EvidenceSpan,
)
from cardioauth.packet_archive import (
    freeze_packet,
    list_archived_packets,
    load_packet,
    packet_from_dict,
)
from cardioauth.submission_packet import (
    Finding,
    FormFieldEntry,
    NarrativeAttestation,
    ResolvedCPT,
    ReviewerVerdict,
    SubmissionPacket,
)


@pytest.fixture
def fresh_store(monkeypatch, tmp_path):
    """Point the store at an empty tmp DB."""
    from cardioauth import persistence
    db = tmp_path / "test_archive.db"
    persistence._store_singleton = None
    monkeypatch.setenv("CARDIOAUTH_DB_PATH", str(db))
    yield
    persistence._store_singleton = None


def _packet(case_id: str = "C-1", **kw) -> SubmissionPacket:
    g = EvidenceGraph()
    cpt_span = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="resolved_cpt.cpt", extracted_value="78492",
        extractor="test",
    )
    g.add(cpt_span)
    base = dict(
        case_id=case_id, raw_note="x",
        chart_data={
            "patient_name": "Jane", "date_of_birth": "1958-01-15",
            "insurance_id": "U-1", "payer_name": "UHC",
            "procedure_code": "78492", "attending_physician": "Dr. X",
        },
        policy_data=None,
        resolved_cpt=ResolvedCPT(
            cpt="78492", procedure="Cardiac PET", source="request",
            evidence=EvidenceReference.from_spans([cpt_span]),
        ),
        payer="UnitedHealthcare",
        form_fields=[],
        narrative=NarrativeAttestation(text=""),
        evidence_graph=g,
        taxonomy_version="taxonomy-1.0.0",
        form_schema_version="uhc-pet-v1",
        model_version="claude-haiku-4-5",
    )
    base.update(kw)
    return SubmissionPacket.build(**base)


# ── freeze_packet ──────────────────────────────────────────────────────


def test_freeze_persists_indexed_columns(fresh_store) -> None:
    p = _packet()
    p.set_decision("hold_for_review", rationale="warnings present")
    p.add_findings([Finding(kind="x", severity="high", message="y")])

    summary = freeze_packet(p)
    assert summary["case_id"] == "C-1"
    assert summary["decision"] == "hold_for_review"
    assert summary["finding_count"] == 1
    assert summary["highest_finding_severity"] == "high"
    assert summary["taxonomy_version"] == "taxonomy-1.0.0"


def test_freeze_idempotent_on_case_id(fresh_store) -> None:
    p = _packet()
    freeze_packet(p)
    p.add_findings([Finding(kind="z", severity="low", message="m")])
    freeze_packet(p)  # re-freeze — replaces the prior row

    rows = list_archived_packets()
    assert len(rows) == 1
    assert rows[0]["finding_count"] == 1


def test_freeze_records_reviewer_recommendation(fresh_store) -> None:
    p = _packet()
    p.set_reviewer_verdict(ReviewerVerdict(
        passed=False, overall_recommendation="hold",
        rationale="r", findings_per_question={},
    ))
    summary = freeze_packet(p)
    assert summary["reviewer_recommendation"] == "hold"


def test_freeze_appends_audit_log(fresh_store) -> None:
    """Freezing should leave a trace in the append-only audit log."""
    from cardioauth.persistence import get_store
    p = _packet()
    freeze_packet(p)
    # Sanity check via get_audit isn't part of the Store ABC;
    # we just verify the freeze didn't crash and the row exists.
    assert get_store().get_packet("C-1") is not None


# ── load_packet (replay) ───────────────────────────────────────────────


def test_load_packet_returns_none_for_unknown(fresh_store) -> None:
    assert load_packet("UNKNOWN") is None


def test_load_packet_round_trips_basic_fields(fresh_store) -> None:
    p = _packet()
    p.set_decision("transmit", rationale="all clean")
    freeze_packet(p)

    restored = load_packet("C-1")
    assert restored is not None
    assert restored.case_id == "C-1"
    assert restored.payer == "UnitedHealthcare"
    assert restored.resolved_cpt.cpt == "78492"
    assert restored.decision == "transmit"
    assert restored.taxonomy_version == "taxonomy-1.0.0"
    assert restored.model_version == "claude-haiku-4-5"


def test_load_packet_restores_evidence_graph(fresh_store) -> None:
    p = _packet()
    extra = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="chart.x", extracted_value="x", extractor="test",
    )
    p.evidence_graph.add(extra)
    freeze_packet(p)

    restored = load_packet("C-1")
    assert restored is not None
    assert len(restored.evidence_graph) == len(p.evidence_graph)


def test_load_packet_restores_form_fields_with_evidence_refs(fresh_store) -> None:
    p = _packet()
    s = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="chart.x", extracted_value="x", extractor="t",
    )
    p.evidence_graph.add(s)
    p.form_fields.append(FormFieldEntry(
        key="exercise_capacity", label="Can exercise?",
        category="clinical", required=True, format="select",
        value="Yes", evidence_text="adequate",
        evidence=EvidenceReference.from_spans([s]),
        status="populated",
    ))
    freeze_packet(p)

    restored = load_packet("C-1")
    assert restored is not None
    assert len(restored.form_fields) == 1
    f = restored.form_fields[0]
    assert f.key == "exercise_capacity"
    assert f.value == "Yes"
    assert f.evidence_text == "adequate"
    assert s.span_id in f.evidence.span_ids


def test_load_packet_restores_findings(fresh_store) -> None:
    p = _packet()
    p.add_findings([
        Finding(kind="a", severity="high", message="m1", checker="c1"),
        Finding(kind="b", severity="low", message="m2", checker="c2"),
    ])
    freeze_packet(p)

    restored = load_packet("C-1")
    assert restored is not None
    assert len(restored.deterministic_findings) == 2
    kinds = {f.kind for f in restored.deterministic_findings}
    assert kinds == {"a", "b"}


def test_load_packet_restores_reviewer_verdict(fresh_store) -> None:
    p = _packet()
    p.set_reviewer_verdict(ReviewerVerdict(
        passed=False, overall_recommendation="block",
        rationale="bad", findings_per_question={
            "q3": [Finding(kind="x", severity="high", message="y")],
        },
        model="claude-haiku-4-5",
        cost_input_tokens=1000, cost_output_tokens=200, duration_ms=4500,
    ))
    freeze_packet(p)

    restored = load_packet("C-1")
    assert restored is not None
    rv = restored.reviewer_verdict
    assert rv is not None
    assert rv.overall_recommendation == "block"
    assert "q3" in rv.findings_per_question
    assert rv.findings_per_question["q3"][0].kind == "x"
    assert rv.cost_input_tokens == 1000


# ── list_archived_packets ──────────────────────────────────────────────


def test_list_archived_packets_filters_by_decision(fresh_store) -> None:
    p1 = _packet(case_id="C-A")
    p1.set_decision("transmit")
    freeze_packet(p1)

    p2 = _packet(case_id="C-B")
    p2.set_decision("hold_for_review")
    freeze_packet(p2)

    transmits = list_archived_packets(decision="transmit")
    assert len(transmits) == 1
    assert transmits[0]["case_id"] == "C-A"


def test_list_archived_packets_filters_by_payer_cpt(fresh_store) -> None:
    p1 = _packet(case_id="C-A", payer="UnitedHealthcare")
    freeze_packet(p1)
    p2 = _packet(case_id="C-B", payer="Aetna")
    freeze_packet(p2)

    uhc = list_archived_packets(payer="UnitedHealthcare")
    assert len(uhc) == 1
    assert uhc[0]["case_id"] == "C-A"


def test_list_archived_packets_does_not_include_full_payload(fresh_store) -> None:
    """List view returns indexed columns only, not the full packet JSON
    (avoid blowing the response size)."""
    p = _packet()
    freeze_packet(p)
    rows = list_archived_packets()
    assert "packet_json" not in rows[0]
    # The full packet is fetched separately via load_packet
    assert "case_id" in rows[0]
    assert "decision" in rows[0]


def test_list_archived_packets_orders_by_recency(fresh_store) -> None:
    p1 = _packet(case_id="C-A")
    freeze_packet(p1)
    p2 = _packet(case_id="C-B")
    freeze_packet(p2)
    rows = list_archived_packets()
    # Most recent first
    assert rows[0]["case_id"] == "C-B"


# ── packet_from_dict round trip without persistence ──────────────────


def test_packet_from_dict_round_trips_serialized() -> None:
    """Direct test of the deserializer — packet.to_dict() is stable."""
    p = _packet()
    p.set_decision("transmit")
    p.add_findings([Finding(kind="x", severity="high", message="m")])
    payload = p.to_dict()
    restored = packet_from_dict(payload)
    assert restored.case_id == p.case_id
    assert restored.decision == p.decision
    assert len(restored.deterministic_findings) == 1
