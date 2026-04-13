"""Regression tests for Peter's Apr 13 feedback:

presence of a feature was being treated as satisfaction of the definition.
Each test here represents one of his four flagged cases (MED-002, SX-001,
SX-002, EX-001) plus generic element-completeness behavior.
"""

from __future__ import annotations

from cardioauth.agents.unified_reasoner import (
    _enforce_cpt_gating,
    _enforce_element_completeness,
)
from cardioauth.taxonomy.taxonomy import get_criterion


def _crit(code: str):
    c = get_criterion(code)
    assert c is not None, f"Missing criterion {code}"
    return c


# ── MED-002 ─────────────────────────────────────────────────────────────
def test_med002_meds_listed_without_duration_is_not_met() -> None:
    """Peter C1: medications present but no start date or duration."""
    crit = _crit("MED-002")
    entry = {
        "code": "MED-002",
        "status": "met",  # LLM says met
        "evidence_quote": "metoprolol, atorvastatin",
        "elements_satisfied": [
            {"key": "medication_name", "found": True, "evidence_quote": "metoprolol"},
            {"key": "dose", "found": True, "evidence_quote": "50 mg BID"},
            {"key": "start_date_or_duration", "found": False, "evidence_quote": ""},
        ],
    }
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "not_met"
    assert "start_date_or_duration" in result["_missing_elements"]
    assert "element_incomplete" in result["_enforced"]


def test_med002_fully_documented_stays_met() -> None:
    """Full documentation of med + dose + duration → met is preserved."""
    crit = _crit("MED-002")
    entry = {
        "code": "MED-002",
        "status": "met",
        "evidence_quote": "metoprolol 50mg BID x 8 weeks",
        "elements_satisfied": [
            {"key": "medication_name", "found": True, "evidence_quote": "metoprolol"},
            {"key": "dose", "found": True, "evidence_quote": "50 mg BID"},
            {"key": "start_date_or_duration", "found": True, "evidence_quote": "x 8 weeks"},
        ],
    }
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "met"
    assert result["_missing_elements"] == []


# ── SX-001 ──────────────────────────────────────────────────────────────
def test_sx001_symptom_without_baseline_comparison_is_not_met() -> None:
    """Peter C2/C4/C5: symptom noted but no change vs baseline."""
    crit = _crit("SX-001")
    entry = {
        "code": "SX-001",
        "status": "met",
        "evidence_quote": "dyspnea on exertion",
        "elements_satisfied": [
            {"key": "symptom_documented", "found": True, "evidence_quote": "dyspnea"},
            {"key": "change_vs_baseline", "found": False, "evidence_quote": ""},
        ],
    }
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "not_met"
    assert "change_vs_baseline" in result["_missing_elements"]


def test_sx001_explicit_worsening_stays_met() -> None:
    """'Worsening chest pain since last visit' satisfies both elements."""
    crit = _crit("SX-001")
    entry = {
        "code": "SX-001",
        "status": "met",
        "elements_satisfied": [
            {"key": "symptom_documented", "found": True, "evidence_quote": "chest pain"},
            {"key": "change_vs_baseline", "found": True, "evidence_quote": "worsening since last visit"},
        ],
    }
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "met"


# ── SX-002 ──────────────────────────────────────────────────────────────
def test_sx002_missing_onset_or_frequency_is_not_met() -> None:
    """Peter C5: symptoms present but no timeline elements."""
    crit = _crit("SX-002")
    entry = {
        "code": "SX-002",
        "status": "met",
        "elements_satisfied": [
            {"key": "onset", "found": False, "evidence_quote": ""},
            {"key": "frequency", "found": False, "evidence_quote": ""},
            {"key": "progression_or_character", "found": True, "evidence_quote": "exertional"},
        ],
    }
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "not_met"
    assert set(result["_missing_elements"]) == {"onset", "frequency"}


# ── EX-001 ──────────────────────────────────────────────────────────────
def test_ex001_dyspnea_without_causal_link_is_not_met() -> None:
    """Peter C4: dyspnea on exertion noted, no explicit link to inability to exercise."""
    crit = _crit("EX-001")
    entry = {
        "code": "EX-001",
        "status": "met",
        "evidence_quote": "dyspnea on exertion",
        "elements_satisfied": [
            {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea"},
            {"key": "explicit_causal_link_to_exercise", "found": False, "evidence_quote": ""},
        ],
    }
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "not_met"
    assert "explicit_causal_link_to_exercise" in result["_missing_elements"]


def test_ex001_explicit_causal_statement_stays_met() -> None:
    """'Unable to do TST due to dyspnea and obesity' satisfies both elements."""
    crit = _crit("EX-001")
    entry = {
        "code": "EX-001",
        "status": "met",
        "elements_satisfied": [
            {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea and obesity"},
            {"key": "explicit_causal_link_to_exercise", "found": True, "evidence_quote": "Unable to do TST due to"},
        ],
    }
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "met"


# ── Integration with _enforce_cpt_gating ────────────────────────────────
def test_cpt_gating_enforces_completeness_at_full_pipeline() -> None:
    """The public enforce_cpt_gating applies element completeness across all criteria."""
    from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure
    applicable = get_criteria_for_procedure("78492", "UnitedHealthcare")
    ex001 = next(c for c in applicable if c.code == "EX-001")

    raw_matches = [
        {
            "code": "EX-001",
            "status": "met",
            "evidence_quote": "dyspnea noted",
            "elements_satisfied": [
                {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea"},
                {"key": "explicit_causal_link_to_exercise", "found": False, "evidence_quote": ""},
            ],
        },
    ]
    enforced = _enforce_cpt_gating(raw_matches, applicable)
    ex = next(e for e in enforced if e["code"] == "EX-001")
    assert ex["status"] == "not_met"
    assert "element_incomplete" in (ex.get("_enforced") or "")


# ── Backward compatibility ──────────────────────────────────────────────
def test_legacy_criterion_without_elements_unchanged() -> None:
    """A criterion with no required_elements is unaffected by element enforcement."""
    # BMI-001 doesn't have required_elements populated yet
    crit = _crit("BMI-001")
    assert crit.required_elements == []

    entry = {"code": "BMI-001", "status": "met", "evidence_quote": "BMI 38"}
    result = _enforce_element_completeness(entry, crit)
    assert result["status"] == "met"


# ── Audit trail surfaces element gaps ───────────────────────────────────
def test_audit_trail_includes_element_level_detail() -> None:
    """Missing elements should appear in the trail entry for the cardiologist."""
    from cardioauth.taxonomy.validation import build_audit_trail
    matches = [
        {
            "code": "EX-001",
            "status": "not_met",
            "_missing_elements": ["explicit_causal_link_to_exercise"],
            "elements_satisfied": [
                {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea"},
                {"key": "explicit_causal_link_to_exercise", "found": False, "evidence_quote": ""},
            ],
        },
    ]
    trail = build_audit_trail("78492", "UHC",
                              policy_codes=["EX-001"],
                              reasoner_matches=matches)
    ex = next(e for e in trail if e.code == "EX-001")
    assert ex.missing_elements == ["explicit_causal_link_to_exercise"]
    assert len(ex.elements_satisfied) == 2
    assert "element_incomplete" in ex.flags
