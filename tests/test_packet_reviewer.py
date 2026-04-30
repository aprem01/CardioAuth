"""Tests for Phase B.3 — LLM packet reviewer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from cardioauth.evidence import (
    EvidenceGraph,
    EvidenceReference,
    EvidenceSpan,
)
from cardioauth.reviewer import (
    CHECKLIST_QUESTIONS,
    PacketReviewer,
    _parse_reviewer_response,
    review_and_attach,
    serialize_packet_for_review,
    should_review_packet,
)
from cardioauth.submission_packet import (
    Finding,
    FormFieldEntry,
    NarrativeAttestation,
    ResolvedCPT,
    ReviewerVerdict,
    SubmissionPacket,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _packet(**kw) -> SubmissionPacket:
    g = EvidenceGraph()
    cpt_span = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="resolved_cpt.cpt", extracted_value="78492",
        extractor="test",
    )
    g.add(cpt_span)
    base = dict(
        case_id="C-1", raw_note="x",
        chart_data={
            "patient_name": "Jane Synthetic", "date_of_birth": "1958-01-15",
            "insurance_id": "U-1", "payer_name": "UHC",
            "procedure_code": "78492", "attending_physician": "Dr. X",
        },
        policy_data=None,
        resolved_cpt=ResolvedCPT(
            cpt="78492", procedure="Cardiac PET", source="request",
            evidence=EvidenceReference.from_spans([cpt_span]),
        ),
        payer="UHC",
        form_fields=[],
        narrative=NarrativeAttestation(text=""),
        evidence_graph=g,
    )
    base.update(kw)
    return SubmissionPacket.build(**base)


# ── Checklist constants ────────────────────────────────────────────────


def test_checklist_has_nine_questions() -> None:
    assert len(CHECKLIST_QUESTIONS) == 9
    assert {q[0] for q in CHECKLIST_QUESTIONS} == {f"q{i}" for i in range(1, 10)}


# ── should_review_packet (only-on-held policy) ────────────────────────


def test_skips_review_on_clean_packet() -> None:
    p = _packet(reasoner_summary={"approval_score": 0.85, "approval_label": "HIGH"})
    assert should_review_packet(p) is False


def test_reviews_when_deterministic_high_finding() -> None:
    p = _packet(reasoner_summary={"approval_score": 0.85})
    p.add_findings([Finding(kind="x", severity="high", message="y")])
    assert should_review_packet(p) is True


def test_reviews_when_deterministic_medium_finding() -> None:
    p = _packet(reasoner_summary={"approval_score": 0.85})
    p.add_findings([Finding(kind="x", severity="medium", message="y")])
    assert should_review_packet(p) is True


def test_skips_review_on_low_findings_only() -> None:
    """Low-severity findings shouldn't trigger an LLM call (cost discipline)."""
    p = _packet(reasoner_summary={"approval_score": 0.85})
    p.add_findings([Finding(kind="x", severity="low", message="y")])
    assert should_review_packet(p) is False


def test_reviews_when_reasoner_score_below_065() -> None:
    p = _packet(reasoner_summary={"approval_score": 0.55, "approval_label": "MEDIUM"})
    assert should_review_packet(p) is True


def test_reviews_when_reasoner_label_says_do_not_submit() -> None:
    p = _packet(reasoner_summary={"approval_score": 0.85, "approval_label": "DO NOT SUBMIT"})
    assert should_review_packet(p) is True


# ── serialize_packet_for_review ────────────────────────────────────────


def test_serialization_includes_required_sections() -> None:
    p = _packet(reasoner_summary={"approval_score": 0.85, "approval_label": "HIGH"})
    payload = serialize_packet_for_review(p)
    for key in (
        "case_id", "payer", "resolved_cpt", "reasoner_summary",
        "form_fields", "narrative", "deterministic_findings",
        "evidence_graph_summary",
    ):
        assert key in payload


def test_serialization_summarizes_evidence_not_dumps_spans() -> None:
    """The full span list could blow context budget; only counts
    surface in the reviewer payload."""
    p = _packet()
    p.evidence_graph.add(EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="chart.x", extracted_value="x", extractor="claude",
    ))
    payload = serialize_packet_for_review(p)
    eg = payload["evidence_graph_summary"]
    assert eg["total_spans"] >= 1
    assert "by_extractor" in eg
    # No raw spans field
    assert "spans" not in eg


def test_serialization_truncates_long_narrative() -> None:
    long_text = "x" * 3000
    p = _packet(narrative=NarrativeAttestation(text=long_text))
    payload = serialize_packet_for_review(p)
    assert len(payload["narrative"]["text"]) <= 1000


def test_serialization_form_fields_carry_evidence_count() -> None:
    p = _packet()
    s = EvidenceSpan.new(source_id="x", source_type="raw_note",
                         field_path="chart.x", extracted_value="x", extractor="t")
    p.evidence_graph.add(s)
    p.form_fields.append(FormFieldEntry(
        key="exercise_capacity", label="Exercise capacity",
        category="clinical", required=True, format="select",
        value="Yes", evidence=EvidenceReference.from_spans([s]),
        status="populated",
    ))
    payload = serialize_packet_for_review(p)
    f = payload["form_fields"][0]
    assert f["evidence_span_count"] == 1


# ── _parse_reviewer_response ───────────────────────────────────────────


def test_parse_well_formed_response() -> None:
    raw = json.dumps({
        "passed": False,
        "overall_recommendation": "hold",
        "rationale": "CPT mismatch",
        "questions": {
            "q2": {
                "answer": "no",
                "reasoning": "Form CPT 78492 differs from narrative 78452",
                "findings": [
                    {
                        "kind": "cpt_mismatch", "severity": "high",
                        "message": "CPT mismatch", "related_field_keys": ["procedure_code"],
                    }
                ],
            }
        }
    })
    v = _parse_reviewer_response(raw, model="claude-haiku-4-5", cost_input_tokens=100, cost_output_tokens=50, duration_ms=1500)
    assert v.passed is False
    assert v.overall_recommendation == "hold"
    assert "q2" in v.findings_per_question
    assert v.findings_per_question["q2"][0].kind == "cpt_mismatch"
    assert v.findings_per_question["q2"][0].severity == "high"


def test_parse_handles_markdown_fenced_response() -> None:
    raw = "```json\n" + json.dumps({
        "passed": True, "overall_recommendation": "transmit",
        "rationale": "ok", "questions": {},
    }) + "\n```"
    v = _parse_reviewer_response(raw, model="x", cost_input_tokens=0, cost_output_tokens=0, duration_ms=0)
    assert v.passed is True


def test_parse_returns_safe_default_on_garbage() -> None:
    v = _parse_reviewer_response("complete garbage", model="x", cost_input_tokens=0, cost_output_tokens=0, duration_ms=0)
    assert v.passed is False
    assert v.overall_recommendation == "hold"
    assert "could not be parsed" in v.rationale


def test_parse_normalizes_unknown_severity_to_medium() -> None:
    raw = json.dumps({
        "passed": False, "overall_recommendation": "hold",
        "rationale": "x",
        "questions": {
            "q1": {"answer": "no", "reasoning": "y",
                   "findings": [{"kind": "x", "severity": "BOGUS", "message": "y"}]},
        },
    })
    v = _parse_reviewer_response(raw, model="x", cost_input_tokens=0, cost_output_tokens=0, duration_ms=0)
    assert v.findings_per_question["q1"][0].severity == "medium"


def test_parse_normalizes_unknown_recommendation_to_hold() -> None:
    raw = json.dumps({
        "passed": False, "overall_recommendation": "submit_now",
        "rationale": "", "questions": {},
    })
    v = _parse_reviewer_response(raw, model="x", cost_input_tokens=0, cost_output_tokens=0, duration_ms=0)
    assert v.overall_recommendation == "hold"


def test_parse_stamps_checker_on_findings() -> None:
    raw = json.dumps({
        "passed": False, "overall_recommendation": "hold",
        "rationale": "x",
        "questions": {
            "q1": {"answer": "no", "reasoning": "y",
                   "findings": [{"kind": "x", "severity": "high", "message": "z"}]},
        },
    })
    v = _parse_reviewer_response(raw, model="x", cost_input_tokens=0, cost_output_tokens=0, duration_ms=0)
    assert v.findings_per_question["q1"][0].checker.startswith("packet_reviewer@")


# ── PacketReviewer ─────────────────────────────────────────────────────


def test_reviewer_returns_empty_when_no_api_key() -> None:
    reviewer = PacketReviewer(api_key="", model="claude-haiku-4-5")
    p = _packet()
    v = reviewer.review(p)
    assert v.passed is True
    assert "no anthropic api key" in v.rationale.lower()


def test_reviewer_returns_hold_on_api_failure() -> None:
    reviewer = PacketReviewer(api_key="test-key", model="claude-haiku-4-5")
    p = _packet()
    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = RuntimeError("network down")
        v = reviewer.review(p)
    assert v.overall_recommendation == "hold"
    assert v.passed is False


def test_reviewer_parses_response_on_success() -> None:
    reviewer = PacketReviewer(api_key="test-key", model="claude-haiku-4-5")
    p = _packet()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=json.dumps({
        "passed": True, "overall_recommendation": "transmit",
        "rationale": "Looks clean",
        "questions": {
            f"q{i}": {"answer": "yes", "reasoning": f"q{i} ok", "findings": []}
            for i in range(1, 10)
        },
    }))]
    fake_response.usage = MagicMock(input_tokens=1000, output_tokens=200)

    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = fake_response
        v = reviewer.review(p)
    assert v.passed is True
    assert v.overall_recommendation == "transmit"
    assert v.cost_input_tokens == 1000
    assert v.cost_output_tokens == 200


# ── review_and_attach ──────────────────────────────────────────────────


def test_review_and_attach_skips_clean_packet() -> None:
    """Only-on-held policy: clean packet doesn't make an LLM call."""
    p = _packet(reasoner_summary={"approval_score": 0.85, "approval_label": "HIGH"})
    reviewer = PacketReviewer(api_key="test-key", model="claude-haiku-4-5")
    with patch("anthropic.Anthropic") as MockClient:
        v = review_and_attach(p, reviewer=reviewer)
        # No API call made
        MockClient.return_value.messages.create.assert_not_called()
    assert "Skipped" in v.rationale
    assert p.reviewer_verdict is v


def test_review_and_attach_runs_when_high_finding_present() -> None:
    p = _packet(reasoner_summary={"approval_score": 0.85})
    p.add_findings([Finding(kind="x", severity="high", message="y")])

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=json.dumps({
        "passed": False, "overall_recommendation": "hold",
        "rationale": "Reviewer flagged it.", "questions": {},
    }))]
    fake_response.usage = MagicMock(input_tokens=500, output_tokens=100)

    reviewer = PacketReviewer(api_key="test-key", model="claude-haiku-4-5")
    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = fake_response
        v = review_and_attach(p, reviewer=reviewer)
        MockClient.return_value.messages.create.assert_called_once()
    assert v.overall_recommendation == "hold"
    assert p.reviewer_verdict is v
