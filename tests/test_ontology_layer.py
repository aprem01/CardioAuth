"""Tests for the SubmissionPacketOntology — Phase A.2 foundation."""

from __future__ import annotations

from cardioauth.ontology import (
    CriterionFormBinding,
    CriterionPolicyBinding,
    EvidenceTypeBinding,
    SubmissionPacketOntology,
    default_ontology,
    get_default_ontology,
)


# ── Binding records ────────────────────────────────────────────────────

def test_evidence_type_binding_serializes() -> None:
    b = EvidenceTypeBinding(
        evidence_type="ecg",
        chart_paths=("chart.ecg_findings",),
        description="ECG only",
    )
    d = b.to_dict()
    assert d["evidence_type"] == "ecg"
    assert d["chart_paths"] == ["chart.ecg_findings"]


def test_criterion_form_binding_normalizes_list_to_tuple() -> None:
    b = CriterionFormBinding(
        criterion_code="EX-001",
        form_field_keys=["exercise_capacity", "exercise_limitation"],  # type: ignore[arg-type]
    )
    assert b.form_field_keys == ("exercise_capacity", "exercise_limitation")


def test_criterion_policy_binding_default_empty() -> None:
    b = CriterionPolicyBinding(criterion_code="X-001")
    assert b.chunk_types == tuple()


# ── Default ontology integrity ─────────────────────────────────────────

def test_default_ontology_validates_clean() -> None:
    ont = default_ontology()
    problems = ont.validate()
    assert problems == [], f"Default ontology has integrity issues: {problems}"


def test_default_ontology_carries_taxonomy_version() -> None:
    ont = default_ontology()
    assert ont.taxonomy_version  # populated from TAXONOMY_VERSION


def test_default_ontology_singleton_returns_same_object() -> None:
    a = get_default_ontology()
    b = get_default_ontology()
    assert a is b


# ── Forward queries: criterion -> form, criterion -> evidence ─────────

def test_form_fields_for_known_criterion() -> None:
    ont = default_ontology()
    # EX-001 binds to both exercise capacity fields
    out = ont.form_fields_for_criterion("EX-001")
    assert "exercise_capacity" in out
    assert "exercise_limitation" in out


def test_form_fields_for_unknown_criterion_returns_empty() -> None:
    ont = default_ontology()
    assert ont.form_fields_for_criterion("NOT-A-REAL-CODE") == tuple()


def test_chart_paths_for_evidence_type() -> None:
    ont = default_ontology()
    ecg_paths = ont.chart_paths_for_evidence_type("ecg")
    assert ecg_paths == ("chart.ecg_findings",)

    img_paths = ont.chart_paths_for_evidence_type("imaging")
    assert "chart.relevant_imaging" in img_paths
    assert "chart.prior_stress_tests" in img_paths


def test_chart_paths_for_unknown_evidence_type() -> None:
    ont = default_ontology()
    assert ont.chart_paths_for_evidence_type("unknown_type") == tuple()


# ── Reverse queries: form -> criteria, cpt -> criteria ─────────────────

def test_criteria_for_form_field_includes_all_bound() -> None:
    ont = default_ontology()
    # primary_symptoms is bound by SX-001/002/003
    out = ont.criteria_for_form_field("primary_symptoms")
    assert "SX-001" in out
    assert "SX-002" in out
    assert "SX-003" in out


def test_criteria_for_unknown_form_field() -> None:
    ont = default_ontology()
    assert ont.criteria_for_form_field("not_a_field") == tuple()


def test_criteria_for_cpt_pulls_from_taxonomy() -> None:
    ont = default_ontology()
    # 78492 (PET) should pull NDX-001, BMI-001, EX-001, ECG-001/2/3/4, etc.
    out = ont.criteria_for_cpt("78492")
    assert "NDX-001" in out
    assert "BMI-001" in out
    assert "EX-001" in out
    assert "ECG-001" in out


def test_criteria_for_empty_cpt() -> None:
    ont = default_ontology()
    assert ont.criteria_for_cpt("") == tuple()


def test_criteria_for_unknown_cpt() -> None:
    ont = default_ontology()
    assert ont.criteria_for_cpt("99999") == tuple()


# ── Indirect query: form field -> chart paths ──────────────────────────

def test_chart_paths_for_form_field_traverses_criterion_bindings() -> None:
    """ecg_findings field is bound to ECG-001/2/3/4, all of which are
    evidence_type='ecg'. So chart_paths_for_form_field should return
    ('chart.ecg_findings',)."""
    ont = default_ontology()
    out = ont.chart_paths_for_form_field("ecg_findings")
    assert "chart.ecg_findings" in out


def test_chart_paths_for_form_field_unions_across_evidence_types() -> None:
    """exercise_capacity is bound to EX-001 only (clinical_note evidence
    type), so paths should include the clinical_note chart bucket set."""
    ont = default_ontology()
    out = ont.chart_paths_for_form_field("exercise_capacity")
    # Should include at least chart.current_symptoms and chart.additional_notes
    assert "chart.current_symptoms" in out
    assert "chart.additional_notes" in out


def test_chart_paths_for_unmapped_field_returns_empty() -> None:
    ont = default_ontology()
    # patient_name is identification, not in the criterion bindings
    assert ont.chart_paths_for_form_field("patient_name") == tuple()


# ── Policy bindings ────────────────────────────────────────────────────

def test_expected_policy_chunk_types_for_ndx_001() -> None:
    ont = default_ontology()
    out = ont.expected_policy_chunk_types("NDX-001")
    assert "policy" in out
    assert "ncd" in out


def test_expected_policy_chunk_types_for_unmapped_criterion() -> None:
    ont = default_ontology()
    # Most criteria don't have explicit policy bindings — empty is fine
    assert ont.expected_policy_chunk_types("HT-001") == tuple()


# ── Helpers: evidence_type / applies_to from taxonomy ─────────────────

def test_evidence_type_for_criterion() -> None:
    ont = default_ontology()
    assert ont.evidence_type_for_criterion("ECG-001") == "ecg"
    assert ont.evidence_type_for_criterion("BMI-001") == "demographic"
    assert ont.evidence_type_for_criterion("NDX-001") == "imaging"


def test_evidence_type_for_unknown_criterion() -> None:
    ont = default_ontology()
    assert ont.evidence_type_for_criterion("FAKE-999") == ""


def test_applies_to_for_criterion() -> None:
    ont = default_ontology()
    out = ont.applies_to_for_criterion("BMI-002")
    # BMI-002 applies only to PET (78492 per the taxonomy)
    assert "78492" in out


# ── Listings ──────────────────────────────────────────────────────────

def test_all_form_fields_listed() -> None:
    ont = default_ontology()
    fields = ont.all_form_fields_in_ontology()
    assert "exercise_capacity" in fields
    assert "ecg_findings" in fields
    assert "primary_symptoms" in fields
    assert len(fields) > 5


def test_all_criteria_listed() -> None:
    ont = default_ontology()
    crits = ont.all_criteria_in_ontology()
    assert "EX-001" in crits
    assert "BMI-001" in crits
    assert len(crits) >= 15


# ── Integrity validation surfaces problems ─────────────────────────────

def test_validate_catches_unknown_criterion_code() -> None:
    bad = SubmissionPacketOntology(
        evidence_type_bindings=tuple(),
        criterion_form_bindings=(
            CriterionFormBinding(criterion_code="NOT-REAL", form_field_keys=("x",)),
        ),
        criterion_policy_bindings=tuple(),
    )
    problems = bad.validate()
    assert any("NOT-REAL" in p for p in problems)


def test_validate_catches_duplicate_criterion_form_bindings() -> None:
    bad = SubmissionPacketOntology(
        evidence_type_bindings=tuple(),
        criterion_form_bindings=(
            CriterionFormBinding(criterion_code="EX-001", form_field_keys=("a",)),
            CriterionFormBinding(criterion_code="EX-001", form_field_keys=("b",)),
        ),
        criterion_policy_bindings=tuple(),
    )
    problems = bad.validate()
    assert any("duplicate" in p.lower() and "EX-001" in p for p in problems)


def test_validate_flags_unmapped_evidence_types() -> None:
    """If the taxonomy has an evidence_type but no binding, validate
    should flag the gap so we don't silently lose evidence."""
    sparse = SubmissionPacketOntology(
        evidence_type_bindings=tuple(),  # nothing bound!
        criterion_form_bindings=tuple(),
        criterion_policy_bindings=tuple(),
    )
    problems = sparse.validate()
    # There are 7 evidence types in the taxonomy; all should be flagged.
    assert len([p for p in problems if "evidence_type" in p]) >= 5


# ── Round-trip serialization ───────────────────────────────────────────

def test_ontology_round_trip_serialization() -> None:
    ont = default_ontology()
    d = ont.to_dict()
    assert "evidence_type_bindings" in d
    assert "criterion_form_bindings" in d
    assert "criterion_policy_bindings" in d
    assert "taxonomy_version" in d
    assert len(d["criterion_form_bindings"]) >= 15
