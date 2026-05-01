"""Tests for Phase B.2 — composable verification pipeline."""

from __future__ import annotations

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
    SubmissionPacket,
)
from cardioauth.verification import (
    AlternativeModalityChecker,
    Checker,
    CoherenceChecker,
    CriteriaMatchResolvedCPTChecker,
    EssentialsChecker,
    EvidenceCompletenessChecker,
    ExtractionConfidenceChecker,
    ReasonerConfidenceChecker,
    VerificationPipeline,
    default_pipeline,
)


# ── Test fixtures ──────────────────────────────────────────────────────


def _empty_packet(**overrides) -> SubmissionPacket:
    """Build a minimal valid packet for checker testing."""
    g = EvidenceGraph()
    cpt_span = EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="resolved_cpt.cpt", extracted_value="78492",
        extractor="test",
    )
    g.add(cpt_span)
    base = dict(
        case_id="C-1",
        raw_note="",
        chart_data={
            "patient_name": "Jane Synthetic",
            "date_of_birth": "1958-01-15",
            "insurance_id": "UHC-1",
            "payer_name": "UnitedHealthcare",
            "procedure_code": "78492",
            "attending_physician": "Dr. Demo",
            "confidence_score": 0.9,
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
    )
    base.update(overrides)
    return SubmissionPacket.build(**base)


# ── Checker base ───────────────────────────────────────────────────────

def test_checker_is_abstract() -> None:
    """Cannot instantiate base Checker directly."""
    import pytest
    with pytest.raises(TypeError):
        Checker()  # type: ignore[abstract]


def test_findings_record_checker_name_and_version() -> None:
    """Every finding must record which checker emitted it."""
    p = _empty_packet(chart_data={"patient_name": "", "date_of_birth": ""})
    findings = EssentialsChecker().check(p)
    assert all(f.checker.startswith("essentials@") for f in findings)


# ── EssentialsChecker ──────────────────────────────────────────────────

def test_essentials_emits_blocking_findings_for_missing_fields() -> None:
    p = _empty_packet(chart_data={
        "patient_name": "",
        "date_of_birth": "",
        "insurance_id": "UHC-1",
        "payer_name": "UHC",
        "procedure_code": "78492",
        "attending_physician": "Dr. X",
    })
    findings = EssentialsChecker().check(p)
    assert all(f.severity == "blocking" for f in findings)
    kinds = {f.kind for f in findings}
    assert kinds == {"missing_essential"}
    related = {k for f in findings for k in f.related_field_keys}
    assert "patient_name" in related
    assert "date_of_birth" in related


def test_essentials_returns_empty_when_all_present() -> None:
    p = _empty_packet()  # default fixture has all essentials
    assert EssentialsChecker().check(p) == []


def test_essentials_message_names_each_field() -> None:
    p = _empty_packet(chart_data={
        "patient_name": "", "date_of_birth": "1958-01-15",
        "insurance_id": "UHC-1", "payer_name": "UHC",
        "procedure_code": "78492", "attending_physician": "Dr. X",
    })
    findings = EssentialsChecker().check(p)
    assert "Patient name" in findings[0].message


# ── ReasonerConfidenceChecker ──────────────────────────────────────────

def test_reasoner_low_score_emits_high_finding() -> None:
    p = _empty_packet(reasoner_summary={"approval_score": 0.3, "approval_label": "LOW"})
    findings = ReasonerConfidenceChecker().check(p)
    assert any(f.kind == "reasoner_low_score" and f.severity == "high" for f in findings)


def test_reasoner_uncertain_band_emits_medium_finding() -> None:
    p = _empty_packet(reasoner_summary={"approval_score": 0.55, "approval_label": "MEDIUM"})
    findings = ReasonerConfidenceChecker().check(p)
    assert any(f.kind == "reasoner_uncertain" and f.severity == "medium" for f in findings)


def test_reasoner_high_score_emits_no_finding() -> None:
    p = _empty_packet(reasoner_summary={"approval_score": 0.85, "approval_label": "HIGH"})
    findings = ReasonerConfidenceChecker().check(p)
    assert findings == []


def test_reasoner_do_not_submit_label_high_finding() -> None:
    p = _empty_packet(reasoner_summary={"approval_score": 0.4, "approval_label": "DO NOT SUBMIT"})
    findings = ReasonerConfidenceChecker().check(p)
    assert any(f.kind == "reasoner_low_confidence" and f.severity == "high" for f in findings)
    # Should NOT also emit reasoner_low_score (the DO_NOT_SUBMIT label is
    # the more specific signal)
    assert not any(f.kind == "reasoner_low_score" for f in findings)


def test_reasoner_band_edges_inclusive_at_low_exclusive_at_high() -> None:
    """Score == 0.50 is uncertain; score == 0.65 is NOT uncertain (out of band)."""
    p_low = _empty_packet(reasoner_summary={"approval_score": 0.50})
    assert any(f.kind == "reasoner_uncertain" for f in ReasonerConfidenceChecker().check(p_low))
    p_high = _empty_packet(reasoner_summary={"approval_score": 0.65})
    assert not any(f.kind == "reasoner_uncertain" for f in ReasonerConfidenceChecker().check(p_high))


# ── AlternativeModalityChecker ─────────────────────────────────────────

def test_alternative_modality_emits_when_present() -> None:
    p = _empty_packet(reasoner_summary={
        "alternative_modality": {"name": "Exercise SPECT", "cpt": "78452"},
    })
    findings = AlternativeModalityChecker().check(p)
    assert any(f.kind == "alternative_modality" and f.severity == "medium" for f in findings)
    assert "Exercise SPECT" in findings[0].message


def test_alternative_modality_silent_when_missing() -> None:
    p = _empty_packet()
    assert AlternativeModalityChecker().check(p) == []


# ── ExtractionConfidenceChecker ────────────────────────────────────────

def test_extraction_thin_finding_when_capped() -> None:
    p = _empty_packet(reasoner_summary={
        "approval_score": 0.75,
        "approval_score_raw": 0.95,
        "score_capped_by_extraction": True,
        "chart_confidence": 0.5,
    })
    findings = ExtractionConfidenceChecker().check(p)
    assert any(f.kind == "extraction_thin" and f.severity == "low" for f in findings)


def test_extraction_no_finding_when_not_capped() -> None:
    p = _empty_packet(reasoner_summary={
        "approval_score": 0.95,
        "approval_score_raw": 0.95,
        "score_capped_by_extraction": False,
        "chart_confidence": 1.0,
    })
    assert ExtractionConfidenceChecker().check(p) == []


# ── EvidenceCompletenessChecker ────────────────────────────────────────

def test_evidence_completeness_flags_populated_field_without_evidence() -> None:
    p = _empty_packet()
    p.form_fields.append(FormFieldEntry(
        key="exercise_capacity", label="Can patient exercise adequately?",
        category="clinical", required=True, format="select",
        value="Yes", status="populated",
        evidence=EMPTY_REFERENCE,
    ))
    findings = EvidenceCompletenessChecker().check(p)
    assert any(f.kind == "form_field_unsupported" for f in findings)
    assert "exercise_capacity" in findings[0].related_field_keys


def test_evidence_completeness_skips_missing_fields() -> None:
    """Missing or needs_verify fields don't claim support — no flag."""
    p = _empty_packet()
    p.form_fields.append(FormFieldEntry(
        key="x", label="X", category="clinical",
        required=True, format="text",
        value="", status="missing", evidence=EMPTY_REFERENCE,
    ))
    p.form_fields.append(FormFieldEntry(
        key="y", label="Y", category="attestation",
        required=True, format="checkbox",
        value="", status="needs_verify", evidence=EMPTY_REFERENCE,
    ))
    assert EvidenceCompletenessChecker().check(p) == []


def test_evidence_completeness_silent_when_evidence_present() -> None:
    p = _empty_packet()
    s = EvidenceSpan.new(source_id="raw_note", source_type="raw_note",
                         field_path="chart.x", extracted_value="x", extractor="t")
    p.evidence_graph.add(s)
    p.form_fields.append(FormFieldEntry(
        key="z", label="Z", category="clinical",
        required=True, format="text",
        value="something", status="populated",
        evidence=EvidenceReference.from_spans([s]),
    ))
    assert EvidenceCompletenessChecker().check(p) == []


# ── CoherenceChecker ───────────────────────────────────────────────────

def test_coherence_resolved_vs_narrative_cpt_mismatch() -> None:
    p = _empty_packet(narrative=NarrativeAttestation(
        text="Cardiac PET (CPT 78492) is medically necessary.",
        cpt_referenced="78492", procedure_referenced="Cardiac PET",
    ))
    # ResolvedCPT in fixture is 78492 — agrees with narrative — no mismatch
    findings = CoherenceChecker().check(p)
    assert not any(f.kind == "cpt_attestation_vs_resolved" for f in findings)


def test_coherence_emits_when_resolved_diverges_from_narrative() -> None:
    p = _empty_packet(
        resolved_cpt=ResolvedCPT(
            cpt="78452", procedure="Exercise SPECT", source="request",
        ),
        narrative=NarrativeAttestation(
            text="Cardiac PET (CPT 78492) is medically necessary.",
            cpt_referenced="78492", procedure_referenced="Cardiac PET",
        ),
    )
    findings = CoherenceChecker().check(p)
    assert any(
        f.kind == "cpt_attestation_vs_resolved" and f.severity == "high"
        for f in findings
    )


# ── CriteriaMatchResolvedCPTChecker (Case 5 fix) ─────────────────────


def test_criteria_match_silent_when_no_evaluated_list() -> None:
    """When reasoner_summary doesn't carry criteria_evaluated, the
    checker is forward-compatible — no findings."""
    p = _empty_packet(reasoner_summary={"approval_score": 0.8})
    assert CriteriaMatchResolvedCPTChecker().check(p) == []


def test_criteria_match_silent_when_evaluated_apply() -> None:
    """ECG-001 applies to 78492 — reasoner correctly evaluating it."""
    p = _empty_packet(reasoner_summary={
        "approval_score": 0.8,
        "criteria_evaluated": ["ECG-001", "BMI-001"],
    })
    # default_pipeline ResolvedCPT is 78492 in fixture
    out = CriteriaMatchResolvedCPTChecker().check(p)
    assert out == []


def test_criteria_match_flags_misapplied_codes_high() -> None:
    """Peter Case 5: reasoner evaluated PET-only criterion (BMI-002) on
    a SPECT case (78452). Must flag as high severity."""
    p = _empty_packet(
        resolved_cpt=ResolvedCPT(
            cpt="78452", procedure="Cardiac SPECT", source="request",
        ),
        reasoner_summary={
            "approval_score": 0.5,
            "criteria_evaluated": ["BMI-002", "ECG-001"],
        },
    )
    findings = CriteriaMatchResolvedCPTChecker().check(p)
    kinds = {f.kind for f in findings}
    assert "criteria_evaluated_outside_resolved_cpt" in kinds
    msg = next(f.message for f in findings if f.kind == "criteria_evaluated_outside_resolved_cpt")
    assert "BMI-002" in msg
    assert "78452" in msg
    sev = next(f.severity for f in findings if f.kind == "criteria_evaluated_outside_resolved_cpt")
    assert sev == "high"


def test_criteria_match_ignores_unknown_codes() -> None:
    """Codes not in the taxonomy can't be evaluated against applies_to;
    they're skipped, not falsely flagged."""
    p = _empty_packet(reasoner_summary={
        "approval_score": 0.8,
        "criteria_evaluated": ["UNKNOWN-999", "ECG-001"],
    })
    out = CriteriaMatchResolvedCPTChecker().check(p)
    assert out == []


# ── VerificationPipeline ───────────────────────────────────────────────

def test_pipeline_runs_each_checker_and_collects_findings() -> None:
    p = _empty_packet(chart_data={
        "patient_name": "",  # essentials will fire
        "date_of_birth": "1958-01-15", "insurance_id": "UHC",
        "payer_name": "UHC", "procedure_code": "78492",
        "attending_physician": "Dr. X",
    }, reasoner_summary={"approval_score": 0.3, "approval_label": "LOW"})

    pipe = VerificationPipeline([
        EssentialsChecker(),
        ReasonerConfidenceChecker(),
    ])
    findings = pipe.run(p)
    kinds = {f.kind for f in findings}
    assert "missing_essential" in kinds
    assert "reasoner_low_score" in kinds


def test_pipeline_isolates_failing_checker() -> None:
    """A checker raising shouldn't break the pipeline; others run."""

    class BrokenChecker(Checker):
        name = "broken"
        def check(self, packet):
            raise RuntimeError("boom")

    p = _empty_packet(reasoner_summary={"approval_score": 0.3})
    pipe = VerificationPipeline([BrokenChecker(), ReasonerConfidenceChecker()])
    findings = pipe.run(p)
    assert any(f.kind == "reasoner_low_score" for f in findings)


def test_default_pipeline_has_all_checkers() -> None:
    pipe = default_pipeline()
    names = {c.name for c in pipe.checkers}
    assert names == {
        "essentials", "reasoner_confidence", "alternative_modality",
        "extraction_confidence_cap", "coherence", "evidence_completeness",
        "criteria_match_resolved_cpt", "safety_verifier",
    }


def test_default_pipeline_run_on_clean_packet_no_findings() -> None:
    """A fully-populated, non-divergent packet produces zero findings."""
    p = _empty_packet(reasoner_summary={
        "approval_score": 0.85, "approval_label": "HIGH",
        "score_capped_by_extraction": False,
    })
    findings = default_pipeline().run(p)
    # No essentials, no reasoner band, no alt modality, no cap, no coherence,
    # no form fields → no completeness findings.
    assert findings == []


def test_pipeline_findings_added_to_packet() -> None:
    """Demonstrate the canonical usage: run pipeline → packet.add_findings."""
    p = _empty_packet(reasoner_summary={"approval_score": 0.3})
    pipe = VerificationPipeline([ReasonerConfidenceChecker()])
    findings = pipe.run(p)
    p.add_findings(findings)
    assert len(p.deterministic_findings) == 1
    assert p.deterministic_findings[0].kind == "reasoner_low_score"
