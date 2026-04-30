"""Tests for Phase B.1 — CPT reconciler."""

from __future__ import annotations

from cardioauth.cpt_resolver import (
    extract_cpt_from_text,
    resolve_cpt,
    same_family,
)
from cardioauth.evidence import EvidenceGraph
from cardioauth.evidence_extraction import emit_spans_for_chart_dict
from cardioauth.models.reasoning import ReasoningResult


# ── same_family ────────────────────────────────────────────────────────

def test_pet_codes_same_family() -> None:
    assert same_family("78491", "78492") is True
    assert same_family("78492", "78491") is True


def test_spect_codes_same_family() -> None:
    assert same_family("78451", "78452") is True


def test_pet_and_spect_different_families() -> None:
    assert same_family("78492", "78452") is False


def test_tavr_family_membership() -> None:
    assert same_family("33361", "33362") is True
    assert same_family("33361", "33365") is True


def test_unknown_codes_default_false() -> None:
    assert same_family("99999", "12345") is False


def test_identical_codes_same_family() -> None:
    assert same_family("78492", "78492") is True


# ── extract_cpt_from_text ──────────────────────────────────────────────

def test_extract_explicit_cpt_tag() -> None:
    assert extract_cpt_from_text("Ordering: CPT 78492 today") == "78492"


def test_extract_cpt_with_colon() -> None:
    assert extract_cpt_from_text("CPT: 78452 cardiac SPECT") == "78452"


def test_extract_bareword_cardiology_cpt() -> None:
    assert extract_cpt_from_text("78492 indicated") == "78492"


def test_skip_non_cardiology_bareword() -> None:
    assert extract_cpt_from_text("phone 12345 ext 6789") == ""


def test_explicit_tag_with_adjacent_digits_returns_tagged_code() -> None:
    """An explicit 'CPT NNNNN' tag (digits immediately after) is preferred
    over any bareword. The text in this test has only one match overall."""
    text = "Patient discussion. CPT 78452 ordered today."
    assert extract_cpt_from_text(text) == "78452"


def test_explicit_tag_with_filler_falls_back_to_bareword() -> None:
    """When 'CPT' is followed by filler words (not just punctuation/space),
    the explicit-tag regex doesn't match and we fall through to bareword."""
    text = "Order 78492 today; the actual CPT was discussed."
    # No explicit 'CPT 78xxx' adjacency, so the bareword wins.
    assert extract_cpt_from_text(text) == "78492"


# ── resolve_cpt — clean cases ──────────────────────────────────────────

def test_resolve_no_divergence_no_findings() -> None:
    resolved, findings = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Ordering CPT 78492 cardiac PET.",
    )
    assert resolved.cpt == "78492"
    assert resolved.source == "request"
    assert findings == []


def test_resolve_no_note_no_findings() -> None:
    """When the note doesn't mention any CPT, there's nothing to flag."""
    resolved, findings = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Patient with worsening dyspnea, BMI 38.",
    )
    assert findings == []


# ── resolve_cpt — note divergence ─────────────────────────────────────

def test_resolve_minor_variant_low_finding() -> None:
    """78491 vs 78492 are same family — minor variant only."""
    _, findings = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Ordering CPT 78491 (single-study cardiac PET).",
    )
    kinds = {f.kind for f in findings}
    assert "cpt_minor_variant" in kinds
    sev = next(f.severity for f in findings if f.kind == "cpt_minor_variant")
    assert sev == "low"


def test_resolve_family_mismatch_high_finding() -> None:
    """78492 vs 78452 are different families — high-severity flag."""
    _, findings = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Ordering: CPT 78452 (Exercise SPECT).",
    )
    kinds = {f.kind for f in findings}
    assert "cpt_family_mismatch" in kinds
    sev = next(f.severity for f in findings if f.kind == "cpt_family_mismatch")
    assert sev == "high"


def test_resolve_request_canonical_when_note_disagrees() -> None:
    """Even with a high-severity note mismatch, the request CPT remains canonical."""
    resolved, _ = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Ordering: CPT 78452 (Exercise SPECT).",
    )
    assert resolved.cpt == "78492"
    assert resolved.source == "request"


# ── resolve_cpt — reasoner divergence ─────────────────────────────────

def test_resolve_alternative_modality_medium_finding() -> None:
    reasoning = ReasoningResult(
        approval_likelihood_score=0.5, approval_likelihood_label="MEDIUM",
        pa_narrative_draft="",
    )
    reasoning.__dict__["_alternative_modality"] = {"cpt": "78452", "name": "Exercise SPECT"}

    _, findings = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="",
        reasoning=reasoning,
    )
    kinds = {f.kind for f in findings}
    assert "cpt_alternative_modality" in kinds
    sev = next(f.severity for f in findings if f.kind == "cpt_alternative_modality")
    assert sev == "medium"


def test_resolve_reasoner_supports_note_when_they_agree() -> None:
    """When the reasoner's alternative matches the note's CPT, emit a
    distinct finding (not two redundant findings)."""
    reasoning = ReasoningResult(
        approval_likelihood_score=0.5, approval_likelihood_label="MEDIUM",
        pa_narrative_draft="",
    )
    reasoning.__dict__["_alternative_modality"] = {"cpt": "78452"}

    _, findings = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Ordering: CPT 78452 (Exercise SPECT).",
        reasoning=reasoning,
    )
    kinds = {f.kind for f in findings}
    # Note divergence → high; reasoner agrees with note → medium-supports
    assert "cpt_family_mismatch" in kinds
    assert "cpt_reasoner_supports_note" in kinds
    # Should NOT also emit the generic "alternative_modality" — that
    # would be redundant with the supports_note finding
    assert "cpt_alternative_modality" not in kinds


def test_resolve_no_finding_when_reasoner_agrees_with_request() -> None:
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    reasoning.__dict__["_alternative_modality"] = {"cpt": "78492"}

    _, findings = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="",
        reasoning=reasoning,
    )
    # No findings — request, reasoner all agree, note silent
    assert all("alternative" not in f.kind for f in findings)


# ── resolve_cpt — evidence graph ──────────────────────────────────────

def test_resolved_cpt_has_evidence_when_graph_has_span() -> None:
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict={"procedure_code": "78492"},
        raw_note="CPT 78492",
        graph=g,
    )
    resolved, _ = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="CPT 78492",
        evidence_graph=g,
    )
    assert not resolved.evidence.is_empty()


def test_resolved_cpt_evidence_empty_without_graph() -> None:
    resolved, _ = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="",
    )
    assert resolved.evidence.is_empty()


# ── Rationale captures all signals ─────────────────────────────────────

def test_rationale_records_note_and_reasoner_alternatives() -> None:
    reasoning = ReasoningResult(
        approval_likelihood_score=0.5, approval_likelihood_label="MEDIUM",
        pa_narrative_draft="",
    )
    reasoning.__dict__["_alternative_modality"] = {"cpt": "78451"}

    resolved, _ = resolve_cpt(
        chart_procedure_code="78492",
        chart_procedure_requested="Cardiac PET",
        raw_note="Ordering CPT 78452.",
        reasoning=reasoning,
    )
    assert "78452" in resolved.rationale
    assert "78451" in resolved.rationale
