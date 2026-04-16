"""Tests for the self-consistency ensemble merging + agreement scoring.

Exercises UnifiedReasoner._merge_ensemble + _merge_single in isolation
(no LLM calls). The real ensemble integration requires live API access
and is covered by consistency harness in production.
"""

from __future__ import annotations

from cardioauth.agents.unified_reasoner import UnifiedReasoner
from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure


def _applicable(cpt: str = "78492", payer: str = "UnitedHealthcare"):
    return get_criteria_for_procedure(cpt, payer)


# ── Single-run passthrough ──────────────────────────────────────────────

def test_merge_single_sets_unanimous_agreement() -> None:
    applicable = _applicable()
    raw = [
        {"code": "EX-001", "status": "met", "evidence_quote": "unable to TST"},
        {"code": "BMI-001", "status": "not_met"},
    ]
    merged, agreement = UnifiedReasoner._merge_single(raw, applicable)
    # Every enforced entry should have agreement=1.0 (single run)
    assert agreement["EX-001"] == 1.0
    assert agreement["BMI-001"] == 1.0
    # Enforcement still fills missing criteria
    assert len(merged) == len(applicable)


# ── Ensemble majority vote ──────────────────────────────────────────────

def test_ensemble_unanimous_met() -> None:
    applicable = _applicable()
    raw_runs = [
        [{"code": "EX-001", "status": "met", "evidence_quote": "q1",
          "elements_satisfied": [
              {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea and obesity"},
              {"key": "explicit_causal_link_to_exercise", "found": True, "evidence_quote": "Unable to do TST due to"},
          ]}],
        [{"code": "EX-001", "status": "met", "evidence_quote": "q2",
          "elements_satisfied": [
              {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea and obesity"},
              {"key": "explicit_causal_link_to_exercise", "found": True, "evidence_quote": "Unable to do TST due to"},
          ]}],
        [{"code": "EX-001", "status": "met", "evidence_quote": "q3",
          "elements_satisfied": [
              {"key": "specific_limiting_condition", "found": True, "evidence_quote": "dyspnea and obesity"},
              {"key": "explicit_causal_link_to_exercise", "found": True, "evidence_quote": "Unable to do TST due to"},
          ]}],
    ]
    merged, agreement = UnifiedReasoner._merge_ensemble(raw_runs, applicable)
    ex = next(e for e in merged if e["code"] == "EX-001")
    assert ex["status"] == "met"
    assert ex["_ensemble_agreement"] == 1.0
    assert ex["_ensemble_n_runs"] == 3
    assert agreement["EX-001"] == 1.0


def test_ensemble_split_decision_defaults_not_met() -> None:
    """2-of-3 split: majority wins; agreement reflects the vote."""
    applicable = _applicable()
    raw_runs = [
        [{"code": "BMI-001", "status": "met",
          "elements_satisfied": [
              {"key": "bmi_value_documented", "found": True, "evidence_quote": "BMI 38"},
              {"key": "bmi_at_or_above_35", "found": True, "evidence_quote": "BMI 38"},
          ]}],
        [{"code": "BMI-001", "status": "not_met",
          "elements_satisfied": [
              {"key": "bmi_value_documented", "found": False, "evidence_quote": ""},
          ]}],
        [{"code": "BMI-001", "status": "not_met",
          "elements_satisfied": [
              {"key": "bmi_value_documented", "found": False, "evidence_quote": ""},
          ]}],
    ]
    merged, agreement = UnifiedReasoner._merge_ensemble(raw_runs, applicable)
    bmi = next(e for e in merged if e["code"] == "BMI-001")
    assert bmi["status"] == "not_met"
    # 2-of-3 agreed on not_met
    assert bmi["_ensemble_agreement"] == round(2 / 3, 2)
    assert agreement["BMI-001"] == round(2 / 3, 2)


def test_ensemble_tie_goes_to_not_met() -> None:
    """1-met, 1-not-met tie with a third 'other' status → conservative not_met default."""
    applicable = _applicable()
    raw_runs = [
        [{"code": "EX-001", "status": "met",
          "elements_satisfied": [
              {"key": "specific_limiting_condition", "found": True, "evidence_quote": "x"},
              {"key": "explicit_causal_link_to_exercise", "found": True, "evidence_quote": "y"},
          ]}],
        [{"code": "EX-001", "status": "not_met"}],
    ]
    merged, _ = UnifiedReasoner._merge_ensemble(raw_runs, applicable)
    ex = next(e for e in merged if e["code"] == "EX-001")
    # Equal counts (1 met, 1 not_met) → default to not_met per safety rule
    assert ex["status"] == "not_met"


def test_ensemble_carries_dissent_note_when_not_unanimous() -> None:
    applicable = _applicable()
    raw_runs = [
        [{"code": "BMI-001", "status": "met",
          "elements_satisfied": [
              {"key": "bmi_value_documented", "found": True, "evidence_quote": "BMI 36"},
              {"key": "bmi_at_or_above_35", "found": True, "evidence_quote": "BMI 36"},
          ]}],
        [{"code": "BMI-001", "status": "met",
          "elements_satisfied": [
              {"key": "bmi_value_documented", "found": True, "evidence_quote": "BMI 36"},
              {"key": "bmi_at_or_above_35", "found": True, "evidence_quote": "BMI 36"},
          ]}],
        [{"code": "BMI-001", "status": "not_met",
          "elements_satisfied": [
              {"key": "bmi_value_documented", "found": False, "evidence_quote": ""},
          ]}],
    ]
    merged, _ = UnifiedReasoner._merge_ensemble(raw_runs, applicable)
    bmi = next(e for e in merged if e["code"] == "BMI-001")
    assert bmi["status"] == "met"
    assert "_ensemble_dissent" in bmi
    assert "1 dissenting" in bmi["_ensemble_dissent"]
    assert bmi["_ensemble_statuses"] == ["met", "met", "not_met"]


# ── Low-agreement flag in audit trail ────────────────────────────────────

def test_audit_trail_flags_low_agreement() -> None:
    from cardioauth.taxonomy.validation import build_audit_trail
    matches = [
        {"code": "EX-001", "status": "not_met",
         "_ensemble_agreement": 0.5, "_ensemble_n_runs": 4,
         "elements_satisfied": [],
        },
    ]
    trail = build_audit_trail("78492", "UHC", policy_codes=["EX-001"], reasoner_matches=matches)
    ex = next(e for e in trail if e.code == "EX-001")
    assert ex.ensemble_agreement == 0.5
    assert ex.ensemble_n_runs == 4
    assert "low_agreement" in ex.flags


def test_audit_trail_no_low_flag_when_unanimous() -> None:
    from cardioauth.taxonomy.validation import build_audit_trail
    matches = [
        {"code": "EX-001", "status": "met",
         "_ensemble_agreement": 1.0, "_ensemble_n_runs": 3,
         "elements_satisfied": [
             {"key": "specific_limiting_condition", "found": True, "evidence_quote": "x"},
             {"key": "explicit_causal_link_to_exercise", "found": True, "evidence_quote": "y"},
         ],
        },
    ]
    trail = build_audit_trail("78492", "UHC", policy_codes=["EX-001"], reasoner_matches=matches)
    ex = next(e for e in trail if e.code == "EX-001")
    assert ex.ensemble_agreement == 1.0
    assert "low_agreement" not in ex.flags


# ── Config propagation ──────────────────────────────────────────────────

def test_config_ensemble_defaults_to_one() -> None:
    """By default (no env var) the ensemble is a single run — cheap + deterministic."""
    import os
    # Ensure the env var isn't set for this test
    os.environ.pop("REASONING_ENSEMBLE_N", None)
    from cardioauth.config import Config
    c = Config()
    assert c.reasoning_ensemble_n == 1


def test_config_ensemble_reads_env_override() -> None:
    import os
    os.environ["REASONING_ENSEMBLE_N"] = "3"
    try:
        from cardioauth.config import Config
        c = Config()
        assert c.reasoning_ensemble_n == 3
    finally:
        del os.environ["REASONING_ENSEMBLE_N"]
