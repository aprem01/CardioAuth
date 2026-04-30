"""Tests for Phase C.3 — packet outcome correlator."""

from __future__ import annotations

import pytest

from cardioauth.evidence import (
    EvidenceGraph,
    EvidenceReference,
    EvidenceSpan,
)
from cardioauth.packet_archive import freeze_packet
from cardioauth.packet_correlation import (
    CorrelationBucket,
    CorrelationReport,
    _bucketize,
    _classify_outcome,
    correlate_outcomes,
)
from cardioauth.submission_packet import (
    Finding,
    NarrativeAttestation,
    ResolvedCPT,
    ReviewerVerdict,
    SubmissionPacket,
)


@pytest.fixture
def fresh_store(monkeypatch, tmp_path):
    from cardioauth import persistence
    db = tmp_path / "test_correlation.db"
    persistence._store_singleton = None
    monkeypatch.setenv("CARDIOAUTH_DB_PATH", str(db))
    yield
    persistence._store_singleton = None


def _packet(*, case_id: str, cpt: str = "78492", payer: str = "UHC",
            decision: str = "transmit", reviewer_rec: str = "transmit",
            findings: list[Finding] | None = None,
            taxonomy_version: str = "1.0.0",
            model_version: str = "claude-haiku-4-5") -> SubmissionPacket:
    g = EvidenceGraph()
    cpt_span = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="resolved_cpt.cpt", extracted_value=cpt, extractor="t",
    )
    g.add(cpt_span)
    p = SubmissionPacket.build(
        case_id=case_id, raw_note="x",
        chart_data={
            "patient_name": "Jane", "date_of_birth": "1958-01-15",
            "insurance_id": "X", "payer_name": payer,
            "procedure_code": cpt, "attending_physician": "Dr. X",
        },
        policy_data=None,
        resolved_cpt=ResolvedCPT(cpt=cpt, procedure="P", source="request",
                                  evidence=EvidenceReference.from_spans([cpt_span])),
        payer=payer, form_fields=[],
        narrative=NarrativeAttestation(text=""),
        evidence_graph=g,
        taxonomy_version=taxonomy_version, model_version=model_version,
    )
    p.set_decision(decision)
    if reviewer_rec:
        p.set_reviewer_verdict(ReviewerVerdict(
            passed=(reviewer_rec == "transmit"),
            overall_recommendation=reviewer_rec,
            rationale="r", findings_per_question={},
        ))
    if findings:
        p.add_findings(findings)
    return p


def _record_outcome(case_id: str, outcome: str, payer: str = "UHC", cpt: str = "78492"):
    from cardioauth.persistence import get_store
    get_store().save_outcome(case_id, {"outcome": outcome})
    get_store().record_outcome_for_stats(payer, cpt, outcome)


# ── _classify_outcome ──────────────────────────────────────────────────


def test_classify_outcome_normalizes_strings() -> None:
    assert _classify_outcome("approved") == "approved"
    assert _classify_outcome("APPROVED") == "approved"
    assert _classify_outcome("approve") == "approved"
    assert _classify_outcome("approved-with-conditions") == "approved"
    assert _classify_outcome("denied") == "denied"
    assert _classify_outcome("DENIED") == "denied"
    assert _classify_outcome("pending") == "pending"
    assert _classify_outcome("") == "pending"
    assert _classify_outcome("garbage") == "pending"


# ── _bucketize ─────────────────────────────────────────────────────────


def test_bucketize_groups_and_counts() -> None:
    rows = [
        ("78492", "approved"), ("78492", "approved"),
        ("78492", "denied"), ("78452", "approved"),
    ]
    buckets = _bucketize(rows, dimension_label="resolved_cpt")
    assert len(buckets) == 2
    by_value = {b.value: b for b in buckets}
    assert by_value["78492"].decisive_count == 3
    assert by_value["78492"].approved == 2
    assert by_value["78492"].denied == 1
    assert by_value["78492"].approval_rate == pytest.approx(2/3)
    assert by_value["78452"].decisive_count == 1
    assert by_value["78452"].approval_rate == 1.0


def test_bucketize_treats_pending_separately() -> None:
    rows = [("X", "approved"), ("X", "pending"), ("X", "pending")]
    buckets = _bucketize(rows, dimension_label="d")
    b = buckets[0]
    assert b.decisive_count == 1
    assert b.pending == 2
    assert b.approval_rate == 1.0  # 1/1 decisive approved


def test_bucketize_skips_empty_keys() -> None:
    rows = [("", "approved"), ("X", "approved")]
    buckets = _bucketize(rows, dimension_label="d")
    assert len(buckets) == 1
    assert buckets[0].value == "X"


def test_bucketize_returns_none_rate_for_pending_only() -> None:
    rows = [("X", "pending"), ("X", "pending")]
    buckets = _bucketize(rows, dimension_label="d")
    assert buckets[0].approval_rate is None


def test_bucket_serializes() -> None:
    b = CorrelationBucket(
        dimension="d", value="v",
        decisive_count=3, approved=2, denied=1, pending=0,
        approval_rate=2/3,
    )
    d = b.to_dict()
    assert d["dimension"] == "d"
    assert d["approval_rate"] == pytest.approx(2/3)


# ── correlate_outcomes — empty + populated ────────────────────────────


def test_correlate_empty_store_returns_zero_decisive(fresh_store) -> None:
    r = correlate_outcomes()
    assert r.total_decisive == 0
    assert r.total_pending == 0
    assert r.by_resolved_cpt == []


def test_correlate_with_decisive_outcomes(fresh_store) -> None:
    p1 = _packet(case_id="C-1", cpt="78492", reviewer_rec="transmit")
    freeze_packet(p1)
    _record_outcome("C-1", "approved")

    p2 = _packet(case_id="C-2", cpt="78492", reviewer_rec="hold")
    freeze_packet(p2)
    _record_outcome("C-2", "denied")

    p3 = _packet(case_id="C-3", cpt="78452", reviewer_rec="transmit")
    freeze_packet(p3)
    _record_outcome("C-3", "approved")

    r = correlate_outcomes()
    assert r.total_decisive == 3
    assert r.total_pending == 0

    by_cpt = {b.value: b for b in r.by_resolved_cpt}
    assert by_cpt["78492"].decisive_count == 2
    assert by_cpt["78492"].approved == 1
    assert by_cpt["78492"].denied == 1
    assert by_cpt["78452"].approved == 1


def test_correlate_by_reviewer_recommendation(fresh_store) -> None:
    p1 = _packet(case_id="C-1", reviewer_rec="hold")
    freeze_packet(p1)
    _record_outcome("C-1", "approved")
    p2 = _packet(case_id="C-2", reviewer_rec="hold")
    freeze_packet(p2)
    _record_outcome("C-2", "denied")
    p3 = _packet(case_id="C-3", reviewer_rec="transmit")
    freeze_packet(p3)
    _record_outcome("C-3", "approved")

    r = correlate_outcomes()
    by_rec = {b.value: b for b in r.by_reviewer_recommendation}
    assert by_rec["hold"].approval_rate == 0.5
    assert by_rec["transmit"].approval_rate == 1.0


def test_correlate_by_finding_kind(fresh_store) -> None:
    p1 = _packet(case_id="C-1",
                 findings=[Finding(kind="cpt_family_mismatch",
                                   severity="high", message="x")])
    freeze_packet(p1)
    _record_outcome("C-1", "denied")

    p2 = _packet(case_id="C-2", findings=[])  # no findings
    freeze_packet(p2)
    _record_outcome("C-2", "approved")

    r = correlate_outcomes()
    by_kind = {b.value: b for b in r.by_finding_kind}
    assert "cpt_family_mismatch" in by_kind
    assert by_kind["cpt_family_mismatch"].denied == 1


def test_correlate_by_taxonomy_version(fresh_store) -> None:
    p1 = _packet(case_id="C-1", taxonomy_version="1.0.0")
    freeze_packet(p1)
    _record_outcome("C-1", "approved")
    p2 = _packet(case_id="C-2", taxonomy_version="1.1.0")
    freeze_packet(p2)
    _record_outcome("C-2", "denied")

    r = correlate_outcomes()
    by_tx = {b.value: b for b in r.by_taxonomy_version}
    assert "1.0.0" in by_tx
    assert "1.1.0" in by_tx


def test_correlate_skips_packets_without_outcomes(fresh_store) -> None:
    """Frozen packets without recorded outcomes should not appear."""
    p = _packet(case_id="C-1")
    freeze_packet(p)
    # no outcome recorded
    r = correlate_outcomes()
    assert r.total_decisive == 0


def test_correlate_includes_pending_in_total_pending(fresh_store) -> None:
    p = _packet(case_id="C-1")
    freeze_packet(p)
    _record_outcome("C-1", "pending")
    r = correlate_outcomes()
    assert r.total_pending == 1
    assert r.total_decisive == 0


def test_correlate_notes_below_threshold(fresh_store) -> None:
    p = _packet(case_id="C-1")
    freeze_packet(p)
    _record_outcome("C-1", "approved")
    r = correlate_outcomes()
    assert "directional" in r.notes


def test_correlate_no_notes_when_above_threshold(fresh_store) -> None:
    """20+ decisive outcomes → no caveat note."""
    for i in range(22):
        p = _packet(case_id=f"C-{i}")
        freeze_packet(p)
        _record_outcome(f"C-{i}", "approved")
    r = correlate_outcomes()
    assert r.notes == ""
