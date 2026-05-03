"""Tests for the Lean Hybrid State 2 output schema.

The schema is the load-bearing contract for the lean pipeline. These
tests pin the cross-field invariants (ambiguous needs rationale, non-
request CPT resolution needs rationale, scaling-related field shape)
so a future schema edit can't silently break them.

Schema design audit: every test below maps to one of Peter's five
scaling concerns (more procedures, edge cases, nuanced indications,
documentation variability, payer-specific differences). The tests
prove the schema covers all five.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cardioauth.lean_schema import (
    SCHEMA_VERSION,
    ApprovalVerdict,
    ClinicalFact,
    CptResolution,
    CriterionEvaluation,
    DocumentationQuality,
    EvidenceQuote,
    FormFieldValue,
    LeanState2Output,
    NarrativeDraft,
    state2_json_schema,
)


# ── Minimal-valid fixture ──────────────────────────────────────────────


def _minimal_output(**overrides) -> LeanState2Output:
    base = dict(
        case_id="TEST-1",
        request_cpt="78452",
        payer="UnitedHealthcare",
        cpt_resolution=CptResolution(
            cpt="78452", procedure_name="Lexiscan SPECT",
            source="request", request_cpt="78452",
        ),
        approval_verdict=ApprovalVerdict(
            score=0.85, label="HIGH", headline_summary=["SPECT indicated"],
        ),
        narrative=NarrativeDraft(
            text="Patient with LBBB requires SPECT.",
            cpt_referenced="78452",
            procedure_referenced="Lexiscan SPECT",
        ),
        documentation_quality=DocumentationQuality(note_format_quality="structured"),
    )
    base.update(overrides)
    return LeanState2Output(**base)


# ── Schema versioning ────────────────────────────────────────────────


def test_schema_version_is_set() -> None:
    out = _minimal_output()
    assert out.schema_version == SCHEMA_VERSION
    assert SCHEMA_VERSION.startswith("lean-")


def test_state2_json_schema_emits_valid_json_schema_doc() -> None:
    schema = state2_json_schema()
    assert schema["type"] == "object"
    # All top-level fields are described
    props = schema.get("properties", {})
    assert "criteria_evaluated" in props
    assert "cpt_resolution" in props
    assert "approval_verdict" in props


# ── Cross-field invariants ───────────────────────────────────────────


def test_ambiguous_criterion_requires_rationale() -> None:
    """Edge case: when LLM marks a criterion ambiguous, it MUST explain
    why. Otherwise the downstream gate has no signal to act on."""
    with pytest.raises(ValidationError, match="rationale required when status=ambiguous"):
        CriterionEvaluation(code="ECG-001", status="ambiguous", confidence=0.5)


def test_not_met_criterion_requires_rationale() -> None:
    with pytest.raises(ValidationError, match="rationale required when status=not_met"):
        CriterionEvaluation(code="BMI-001", status="not_met", confidence=0.9)


def test_met_criterion_does_not_require_rationale() -> None:
    """When the status is met, the evidence quotes ARE the rationale."""
    c = CriterionEvaluation(
        code="ECG-001", status="met",
        evidence=[EvidenceQuote(quote="chronic LBBB")],
    )
    assert c.status == "met"


def test_not_evaluated_criterion_does_not_require_rationale() -> None:
    """Forward-compat: a criterion not in scope this pass produces
    nothing, no rationale needed."""
    c = CriterionEvaluation(code="UNKNOWN-999", status="not_evaluated")
    assert c.status == "not_evaluated"


def test_cpt_resolution_non_request_requires_rationale() -> None:
    """If we're saying the note ordered a different CPT than the
    request, we owe the physician an explanation."""
    with pytest.raises(ValidationError, match="rationale required"):
        CptResolution(
            cpt="78452", procedure_name="SPECT",
            source="note_extracted", request_cpt="78492",
        )


def test_cpt_resolution_request_does_not_require_rationale() -> None:
    """The default case (request matches note) needs no extra prose."""
    cr = CptResolution(
        cpt="78452", procedure_name="SPECT", source="request", request_cpt="78452",
    )
    assert cr.rationale == ""


# ── Scaling: more procedures, no schema change ────────────────────────


def test_schema_does_not_hardcode_cpt_codes() -> None:
    """Adding TAVR (33361) or PCI (92928) must not require schema
    changes. The schema accepts ANY string CPT."""
    for cpt in ("33361", "92928", "75574", "93312", "93619"):
        out = _minimal_output(
            request_cpt=cpt,
            cpt_resolution=CptResolution(
                cpt=cpt, procedure_name="<any>", source="request", request_cpt=cpt,
            ),
        )
        assert out.cpt_resolution.cpt == cpt


def test_schema_does_not_hardcode_criterion_codes() -> None:
    """Criterion codes come from the runtime taxonomy. Schema must
    accept ANY string code."""
    out = _minimal_output(
        criteria_evaluated=[
            CriterionEvaluation(code="TAVR-NYHA-001", status="met"),
            CriterionEvaluation(code="PCI-LESION-001", status="met"),
            CriterionEvaluation(
                code="EP-PROC-INDICATION-A", status="not_met",
                rationale="Symptoms do not meet threshold.",
            ),
        ],
    )
    codes = {c.code for c in out.criteria_evaluated}
    assert "TAVR-NYHA-001" in codes
    assert "EP-PROC-INDICATION-A" in codes


# ── Scaling: nuanced indications via list-valued evidence ────────────


def test_criterion_supports_multiple_evidence_pieces() -> None:
    """ECG-001 can be satisfied by LBBB OR pacemaker OR digitalis OR
    Wolff-Parkinson-White. Schema allows multiple quotes per criterion."""
    c = CriterionEvaluation(
        code="ECG-001", status="met",
        evidence=[
            EvidenceQuote(quote="chronic LBBB"),
            EvidenceQuote(quote="paced ventricular rhythm"),
        ],
        confidence=0.95,
    )
    assert len(c.evidence) == 2


# ── Scaling: edge cases via ambiguous + human-review flag ────────────


def test_ambiguous_status_with_explicit_human_review_flag() -> None:
    """Real cases are not always binary. Schema exposes ambiguity AND
    a separate human-review flag so the downstream gate can route."""
    c = CriterionEvaluation(
        code="EX-001", status="ambiguous",
        rationale="Note says 'limited exercise' but doesn't quantify.",
        confidence=0.4,
        requires_human_review=True,
    )
    assert c.status == "ambiguous"
    assert c.requires_human_review is True
    assert c.confidence == 0.4


# ── Scaling: documentation variability via quality signals ───────────


def test_documentation_quality_captures_format_variability() -> None:
    for quality in ("structured", "semi_structured", "unstructured", "fragmented"):
        out = _minimal_output(
            documentation_quality=DocumentationQuality(
                note_format_quality=quality,
                missing_essential_fields=["insurance_id", "attending_npi"]
                if quality == "fragmented" else [],
                extraction_warnings=["BMI mentioned with conflicting values: 33.8 vs 35.2"]
                if quality != "structured" else [],
                overall_extraction_confidence=0.5 if quality == "fragmented" else 0.95,
            ),
        )
        assert out.documentation_quality.note_format_quality == quality


def test_clinical_fact_carries_extraction_confidence() -> None:
    """Per-fact confidence so downstream layers can weight evidence."""
    f = ClinicalFact(
        category="ecg_finding", value="LBBB",
        evidence=EvidenceQuote(quote="chronic LBBB"),
        extraction_confidence=0.7,
    )
    assert f.extraction_confidence == 0.7


# ── Scaling: payer-specific routing ──────────────────────────────────


def test_payer_specific_criteria_are_separate_list() -> None:
    """Same fact may apply differently across payers. UHC's MCG-
    specific bundle goes in payer_specific, generic CMS-shaped
    criteria go in the generic list."""
    out = _minimal_output(
        criteria_evaluated=[
            CriterionEvaluation(code="ECG-001", status="met"),
        ],
        payer_specific_criteria_evaluated=[
            CriterionEvaluation(code="UHC-MCG-78452-002", status="met"),
        ],
    )
    assert len(out.criteria_evaluated) == 1
    assert len(out.payer_specific_criteria_evaluated) == 1
    assert out.criteria_evaluated[0].code != out.payer_specific_criteria_evaluated[0].code


# ── Form field generic shape ─────────────────────────────────────────


def test_form_field_value_supports_all_status_states() -> None:
    """Form schema is payer-agnostic; the LLM emits any of these
    statuses based on what it could populate."""
    for status in ("populated", "missing", "needs_verify", "incomplete"):
        f = FormFieldValue(key="some_field", value="X", status=status)
        assert f.status == status


# ── Approval verdict bounds ──────────────────────────────────────────


def test_approval_score_must_be_in_unit_interval() -> None:
    with pytest.raises(ValidationError):
        ApprovalVerdict(score=1.5, label="HIGH")
    with pytest.raises(ValidationError):
        ApprovalVerdict(score=-0.1, label="LOW")


def test_approval_label_constrained_to_known_values() -> None:
    ApprovalVerdict(score=0.5, label="MEDIUM")  # OK
    with pytest.raises(ValidationError):
        ApprovalVerdict(score=0.5, label="MAYBE")  # type: ignore


# ── Patient identity passthrough ─────────────────────────────────────


def test_patient_identity_fields_default_empty_for_skeletal_runs() -> None:
    """When the LLM call fails (skeletal run) and the regex pre-pass
    fills these in via overlay, the schema accepts empty defaults."""
    out = _minimal_output()
    assert out.patient_name == ""
    assert out.attending_npi == ""


def test_patient_identity_fields_populated_when_present() -> None:
    out = _minimal_output(
        patient_name="Margaret Synthetic",
        date_of_birth="1958-01-15",
        insurance_id="UHC987654321",
        payer_name="UnitedHealthcare",
        attending_physician="Dr. James Carter",
        attending_npi="1306939693",
    )
    assert out.patient_name == "Margaret Synthetic"
    assert out.attending_npi == "1306939693"


# ── Schema flattening for Anthropic tool-use ────────────────────────


def test_state2_json_schema_flat_resolves_all_refs() -> None:
    """Anthropic tool-use validator can choke on $refs. The flat
    variant inlines every $defs entry so the schema is self-
    contained."""
    from cardioauth.lean_schema import state2_json_schema_flat

    flat = state2_json_schema_flat()
    refs = _walk_for_refs(flat)
    assert refs == [], f"Found unresolved refs in flat schema: {refs}"


def test_state2_json_schema_flat_drops_defs_table() -> None:
    """After inlining, the $defs / definitions tables should be
    removed (cleaner schema, smaller wire size)."""
    from cardioauth.lean_schema import state2_json_schema_flat

    flat = state2_json_schema_flat()
    assert "$defs" not in flat
    assert "definitions" not in flat


def test_state2_json_schema_flat_preserves_required_top_level_fields() -> None:
    """Flattening must not drop required fields."""
    from cardioauth.lean_schema import state2_json_schema_flat

    flat = state2_json_schema_flat()
    required = set(flat.get("required", []))
    # The same required fields the original Pydantic schema produces
    for field in (
        "case_id", "request_cpt", "payer",
        "cpt_resolution", "approval_verdict",
        "narrative", "documentation_quality",
    ):
        assert field in required


def test_state2_json_schema_flat_preserves_nested_field_constraints() -> None:
    """Inlined nested types must keep their own required fields and
    constraints. Spot-check CriterionEvaluation: inside criteria_evaluated."""
    from cardioauth.lean_schema import state2_json_schema_flat

    flat = state2_json_schema_flat()
    crit_array = flat["properties"]["criteria_evaluated"]
    # Nested inlined: 'items' should be a full object schema, not a $ref
    item = crit_array.get("items", {})
    assert "$ref" not in item
    assert "properties" in item
    assert "code" in item["properties"]
    assert "status" in item["properties"]
    # status enum values preserved
    assert "enum" in item["properties"]["status"]
    enum_vals = set(item["properties"]["status"]["enum"])
    assert {"met", "not_met", "not_evaluated", "ambiguous"}.issubset(enum_vals)


def test_state2_json_schema_flat_is_pure_function() -> None:
    """Calling twice produces equal output and doesn't mutate
    LeanState2Output's schema cache."""
    from cardioauth.lean_schema import state2_json_schema, state2_json_schema_flat

    a = state2_json_schema_flat()
    b = state2_json_schema_flat()
    assert a == b
    # Original (un-flattened) still has $defs
    orig = state2_json_schema()
    assert "$defs" in orig


def _walk_for_refs(obj, path: str = "") -> list[str]:
    """Recursively find any node that contains $ref."""
    found = []
    if isinstance(obj, dict):
        if "$ref" in obj:
            found.append(path)
        if "$defs" in obj:
            found.append(path + "/$defs")
        if "definitions" in obj:
            found.append(path + "/definitions")
        for k, v in obj.items():
            found.extend(_walk_for_refs(v, f"{path}/{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_walk_for_refs(item, f"{path}[{i}]"))
    return found
