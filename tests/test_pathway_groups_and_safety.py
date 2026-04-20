"""Tests for Peter C10-C13 fixes:

  - pathway_group: unmet alternatives not flagged as blocking gaps
  - classify_gaps: blocking vs alternative_not_used vs supporting_unmet
  - lab source anchoring: future-dated labs dropped
  - narrative stay-in-taxonomy: headline summary wiring
"""

from __future__ import annotations

from cardioauth.models import ChartData, LabResult
from cardioauth.models.chart_migration import validate_lab_source_anchoring
from cardioauth.taxonomy.taxonomy import (
    classify_gaps,
    get_criteria_for_procedure,
    get_pathway_groups,
)


# ── Pathway group topology ──────────────────────────────────────────────

def test_pharm_stress_justification_group_has_expected_members() -> None:
    """EX-001, ECG-001..004 all share pharm_stress_justification group for 78492."""
    groups = get_pathway_groups("78492", "UnitedHealthcare")
    pharm = groups.get("pharm_stress_justification", [])
    codes = {c.code for c in pharm}
    assert "EX-001" in codes
    assert "ECG-001" in codes
    assert "ECG-002" in codes
    assert "ECG-003" in codes
    assert "ECG-004" in codes


def test_prior_testing_nondiagnostic_group_has_expected_members() -> None:
    groups = get_pathway_groups("78492", "UnitedHealthcare")
    ndx = groups.get("prior_testing_nondiagnostic", [])
    codes = {c.code for c in ndx}
    assert "NDX-001" in codes
    assert "NDX-002" in codes


# ── classify_gaps — Peter C10-C13 #1 & #2 ───────────────────────────────

def test_met_alternative_reclassifies_others_as_alternative_not_used() -> None:
    """If EX-001 is met, ECG-001/002/003/004 not_met entries must NOT be blocking.

    This is the exact failure mode Peter flagged: case was strong
    (exercise incapacity documented) but every missing ECG pathway got
    listed as a gap, making the case look weaker than it was.
    """
    applicable = get_criteria_for_procedure("78492", "UnitedHealthcare")
    matches = [
        {"code": "EX-001", "status": "met"},
        {"code": "ECG-001", "status": "not_met"},
        {"code": "ECG-002", "status": "not_met"},
        {"code": "ECG-003", "status": "not_met"},
        {"code": "ECG-004", "status": "not_met"},
    ]
    result = classify_gaps(applicable, matches)
    blocking_codes = {m.get("code") for m in result["blocking"]}
    alternative_codes = {m.get("code") for m in result["alternative_not_used"]}
    # ECG-001..004 must land in alternative_not_used, NOT blocking
    assert alternative_codes.issuperset({"ECG-001", "ECG-002", "ECG-003", "ECG-004"})
    assert "ECG-001" not in blocking_codes
    assert "ECG-002" not in blocking_codes


def test_all_pathway_members_unmet_means_blocking() -> None:
    """If no pathway member is met, required members are still blocking."""
    applicable = get_criteria_for_procedure("78492", "UnitedHealthcare")
    matches = [
        {"code": "EX-001", "status": "not_met"},
        {"code": "ECG-001", "status": "not_met"},
        {"code": "ECG-002", "status": "not_met"},
    ]
    result = classify_gaps(applicable, matches)
    blocking_codes = {m.get("code") for m in result["blocking"]}
    # EX-001 and ECG-001 are required severity — both blocking when none met
    assert "EX-001" in blocking_codes
    assert "ECG-001" in blocking_codes


def test_supporting_unmet_is_separate_class() -> None:
    """Supporting (not required) criteria not met go to supporting_unmet, not blocking."""
    applicable = get_criteria_for_procedure("78492", "UnitedHealthcare")
    matches = [
        # EX-001 met — so the alternative pathway is satisfied
        {"code": "EX-001", "status": "met"},
        # GUI-001 is severity=supporting, no pathway group
        {"code": "GUI-001", "status": "not_met"},
    ]
    result = classify_gaps(applicable, matches)
    supporting_codes = {m.get("code") for m in result["supporting_unmet"]}
    assert "GUI-001" in supporting_codes


# ── Lab safety validation — Peter C10-C13 #5 ───────────────────────────

def test_future_dated_lab_is_dropped() -> None:
    """Labs with dates after today are dropped — never allowed into reasoning."""
    chart = ChartData(
        patient_id="T-001",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        diagnosis_codes=["I25.10"],
        relevant_labs=[
            LabResult(name="LDL", value="130", date="2030-01-15", unit="mg/dL"),
            LabResult(name="HbA1c", value="7.2", date="2025-06-01", unit="%"),
        ],
        confidence_score=0.9,
    )
    cleaned, warnings = validate_lab_source_anchoring(chart, today_iso="2026-04-14")
    remaining = {lab.name for lab in cleaned.relevant_labs}
    assert "HbA1c" in remaining
    assert "LDL" not in remaining  # future-dated → dropped
    assert any("future date" in w for w in warnings)


def test_past_dated_lab_is_kept() -> None:
    chart = ChartData(
        patient_id="T-001",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        diagnosis_codes=["I25.10"],
        relevant_labs=[
            LabResult(name="HbA1c", value="7.2", date="2025-06-01", unit="%"),
        ],
        confidence_score=0.9,
    )
    cleaned, warnings = validate_lab_source_anchoring(chart, today_iso="2026-04-14")
    assert len(cleaned.relevant_labs) == 1
    assert not warnings


def test_strict_mode_drops_unanchored_labs() -> None:
    """In strict mode, a lab with no source_anchor is rejected."""
    chart = ChartData(
        patient_id="T-001",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        diagnosis_codes=["I25.10"],
        relevant_labs=[
            LabResult(name="LDL", value="130", date="2025-06-01", unit="mg/dL"),
            LabResult(
                name="HbA1c",
                value="7.2",
                date="2025-06-01",
                unit="%",
                source_anchor="HbA1c 7.2 (drawn 06/01/2025)",
            ),
        ],
        confidence_score=0.9,
    )
    cleaned, warnings = validate_lab_source_anchoring(chart, strict=True, today_iso="2026-04-14")
    remaining = {lab.name for lab in cleaned.relevant_labs}
    assert "HbA1c" in remaining
    assert "LDL" not in remaining
    assert any("source_anchor" in w for w in warnings)


# ── End-to-end: reasoner output surfaces classification correctly ───────

def test_merge_ensemble_then_classify_gaps_produces_expected_buckets() -> None:
    """Integration: UnifiedReasoner output → classify_gaps → correct buckets."""
    from cardioauth.agents.unified_reasoner import UnifiedReasoner
    applicable = get_criteria_for_procedure("78492", "UnitedHealthcare")

    raw_runs = [
        [
            {"code": "EX-001", "status": "met",
             "elements_satisfied": [
                 {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea"},
                 {"key": "explicit_causal_link_to_exercise", "found": True, "evidence_quote": "Unable to do TST due to"},
             ]},
        ],
    ]
    merged, _ = UnifiedReasoner._merge_single(raw_runs[0], applicable)
    result = classify_gaps(applicable, merged)

    # EX-001 met → pharm_stress_justification satisfied → ECG-* unmet go to alternative
    alt_codes = {m.get("code") for m in result["alternative_not_used"]}
    assert any(c.startswith("ECG-") for c in alt_codes)
