"""Regression tests for the criterion validation + audit trail layer.

These tests are the safety net for the EX-001 class of bug: a criterion
silently dropped somewhere in the pipeline. Any future change that lets
one slip through without a warning should fail here.
"""

from __future__ import annotations

from cardioauth.taxonomy.validation import (
    build_audit_trail,
    extract_reasoner_codes,
    trail_to_dict,
    validate_criteria_for_cpt,
)


def test_validator_flags_missing_criterion() -> None:
    """Missing criterion → report.is_clean is False and warning is emitted."""
    r = validate_criteria_for_cpt(["EX-001"], "78492", "UHC", stage="test")
    assert not r.is_clean
    assert "EX-001" in r.valid_codes
    # Plenty of expected codes should be missing (taxonomy has many for 78492)
    assert len(r.missing_codes) >= 5
    assert any(w["kind"] == "missing_criteria" for w in r.warnings)


def test_validator_flags_unknown_code() -> None:
    """Code not in taxonomy → flagged as unknown (likely hallucination)."""
    r = validate_criteria_for_cpt(["BOGUS-999", "EX-001"], "78492", "UHC")
    assert "BOGUS-999" in r.unknown_codes
    assert "EX-001" in r.valid_codes
    assert any(w["kind"] == "unknown_criteria" for w in r.warnings)


def test_validator_clean_when_all_expected_returned() -> None:
    """When every expected code is returned and nothing extra → is_clean=True."""
    from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure
    expected = [c.code for c in get_criteria_for_procedure("78492", "UHC")]
    r = validate_criteria_for_cpt(expected, "78492", "UHC")
    assert r.is_clean
    assert not r.missing_codes
    assert not r.unknown_codes


def test_audit_trail_records_every_expected_criterion() -> None:
    """The audit trail covers every applicable criterion, even skipped ones."""
    from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure
    expected_codes = {c.code for c in get_criteria_for_procedure("78492", "UHC")}

    reasoner_matches = [{"code": "EX-001", "status": "met"}]
    trail = build_audit_trail("78492", "UHC",
                              policy_codes=["EX-001"],
                              reasoner_matches=reasoner_matches)
    trail_codes = {e.code for e in trail}
    assert expected_codes.issubset(trail_codes)


def test_audit_trail_flags_reasoner_skips() -> None:
    """Criteria the reasoner didn't return are marked reasoner_skipped + dropped."""
    trail = build_audit_trail("78492", "UHC",
                              policy_codes=[],
                              reasoner_matches=[{"code": "EX-001", "status": "met"}])
    skipped = [e for e in trail if "reasoner_skipped" in e.flags]
    assert len(skipped) >= 1
    for e in skipped:
        assert e.final_status == "dropped"
        assert e.drop_reason  # non-empty reason


def test_audit_trail_flags_unexpected_codes() -> None:
    """Reasoner-returned codes not in the applicable set get unexpected_code flag."""
    trail = build_audit_trail("78492", "UHC",
                              policy_codes=[],
                              reasoner_matches=[{"code": "BOGUS-1", "status": "met"}])
    unexpected = [e for e in trail if "unexpected_code" in e.flags]
    assert len(unexpected) == 1
    assert unexpected[0].code == "BOGUS-1"
    assert unexpected[0].applicable_to_cpt is False


def test_audit_trail_records_cpt_gating_fill() -> None:
    """Enforced (filled-in by CPT gating) criteria are tracked as such."""
    matches = [
        {"code": "EX-001", "status": "met"},
        {"code": "BMI-001", "status": "not_met", "_enforced": "missing_filled"},
    ]
    trail = build_audit_trail("78492", "UHC",
                              policy_codes=["EX-001", "BMI-001"],
                              reasoner_matches=matches)
    bmi = next(e for e in trail if e.code == "BMI-001")
    assert "cpt_gating_fill" in bmi.stages_passed
    assert any(f.startswith("enforced:") for f in bmi.flags)


def test_trail_to_dict_serializable() -> None:
    """Audit trail must be JSON-serializable for API responses."""
    import json
    trail = build_audit_trail("78492", "UHC",
                              policy_codes=["EX-001"],
                              reasoner_matches=[{"code": "EX-001", "status": "met"}])
    as_dict = trail_to_dict(trail)
    assert isinstance(as_dict, list)
    # Round-trip JSON — must not raise
    json.dumps(as_dict)


def test_ex001_audit_trail_catches_regression() -> None:
    """The Apr 13 EX-001 bug: criterion was silently dropped from CPT 78492.

    If EX-001 ever gets removed from the applicable set for 78492 again,
    OR if a reasoner fails to return it, the audit trail will surface it.
    This is the canonical regression test.
    """
    # Reasoner omits EX-001 entirely
    trail = build_audit_trail(
        "78492", "UHC",
        policy_codes=["BMI-001", "NDX-001"],
        reasoner_matches=[
            {"code": "BMI-001", "status": "met"},
            {"code": "NDX-001", "status": "met"},
        ],
    )
    ex001 = next((e for e in trail if e.code == "EX-001"), None)
    assert ex001 is not None, "EX-001 must appear in audit trail for CPT 78492"
    assert "reasoner_skipped" in ex001.flags
    assert ex001.applicable_to_cpt is True
