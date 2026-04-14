"""Tests for the calibration layer + validation harness."""

from __future__ import annotations

from datetime import date

from cardioauth.stats import (
    check_policy_freshness,
    get_global_rules,
    get_payer_stats,
    list_payer_stats,
)
from cardioauth.validation_harness import (
    CalibrationReport,
    LabeledCase,
    _calibration_curve,
    _confusion,
)


# ── Payer stats lookup ──────────────────────────────────────────────────

def test_payer_stats_exact_match() -> None:
    s = get_payer_stats("UnitedHealthcare", "78492")
    assert s is not None
    assert 0.0 <= s.approval_rate <= 1.0
    assert s.top_denial_reasons


def test_payer_stats_alias_resolution() -> None:
    # UHC, United, UnitedHealthcare should all resolve
    assert get_payer_stats("UHC", "78492") is not None
    assert get_payer_stats("United", "78492") is not None
    assert get_payer_stats("united healthcare", "78492") is not None


def test_payer_stats_missing_returns_none() -> None:
    assert get_payer_stats("NopePayer", "99999") is None


def test_list_payer_stats_nonempty() -> None:
    stats = list_payer_stats()
    assert len(stats) > 0


# ── Global rules ────────────────────────────────────────────────────────

def test_global_rules_present_for_uhc() -> None:
    rules = get_global_rules("UnitedHealthcare")
    assert len(rules) >= 2
    assert all(r.payer == "UnitedHealthcare" for r in rules)


def test_global_rules_alias_resolution() -> None:
    # BCBS should resolve to Anthem
    rules = get_global_rules("BCBS")
    assert len(rules) >= 1


def test_global_rules_unknown_payer_empty() -> None:
    assert get_global_rules("UnknownPayer") == []


# ── Policy freshness ────────────────────────────────────────────────────

def test_freshness_fresh() -> None:
    r = check_policy_freshness("2026-03-01", as_of=date(2026, 4, 13))
    assert r.level == "fresh"
    assert r.age_days == 43


def test_freshness_stale_warning() -> None:
    r = check_policy_freshness("2026-01-01", as_of=date(2026, 6, 1))
    assert r.level == "stale_warning"


def test_freshness_stale_critical() -> None:
    r = check_policy_freshness("2024-01-01", as_of=date(2026, 4, 13))
    assert r.level == "stale_critical"


def test_freshness_unknown_on_empty() -> None:
    r = check_policy_freshness("", as_of=date(2026, 4, 13))
    assert r.level == "unknown"


def test_freshness_unknown_on_unparseable() -> None:
    r = check_policy_freshness("not-a-date", as_of=date(2026, 4, 13))
    assert r.level == "unknown"


# ── Validation harness metrics ──────────────────────────────────────────

def _mock_result(**kwargs):
    from cardioauth.validation_harness import CaseResult
    return CaseResult(
        case_id=kwargs.get("case_id", "X"),
        predicted_approval=kwargs["predicted_approval"],
        predicted_score=kwargs["predicted_score"],
        gold_outcome="approved" if kwargs["gold_approval"] else "denied",
        gold_approval=kwargs["gold_approval"],
    )


def test_confusion_all_correct() -> None:
    results = [
        _mock_result(predicted_approval=True, predicted_score=0.9, gold_approval=True),
        _mock_result(predicted_approval=False, predicted_score=0.3, gold_approval=False),
    ]
    c = _confusion(results)
    assert c["sensitivity_recall"] == 1.0
    assert c["specificity"] == 1.0
    assert c["accuracy"] == 1.0


def test_confusion_all_wrong() -> None:
    results = [
        _mock_result(predicted_approval=True, predicted_score=0.9, gold_approval=False),
        _mock_result(predicted_approval=False, predicted_score=0.3, gold_approval=True),
    ]
    c = _confusion(results)
    assert c["sensitivity_recall"] == 0.0
    assert c["specificity"] == 0.0


def test_calibration_curve_buckets() -> None:
    results = [
        _mock_result(predicted_approval=True, predicted_score=0.95, gold_approval=True),
        _mock_result(predicted_approval=True, predicted_score=0.85, gold_approval=True),
        _mock_result(predicted_approval=False, predicted_score=0.15, gold_approval=False),
    ]
    curve = _calibration_curve(results, buckets=5)
    assert len(curve) == 5
    # Top bucket (0.80-1.00) should contain both high-score approvals
    top = curve[-1]
    assert top["n"] == 2
    assert top["actual_approval_rate"] == 1.0


# ── End-to-end LabeledCase shape ────────────────────────────────────────

def test_labeled_case_roundtrip() -> None:
    c = LabeledCase(
        case_id="T1",
        procedure_code="78492",
        procedure_name="Cardiac PET",
        payer_name="UnitedHealthcare",
        raw_note="67M...",
        gold_outcome="approved",
        gold_criterion_labels={"EX-001": "met", "BMI-001": "met"},
    )
    assert c.case_id == "T1"
    assert c.gold_criterion_labels["EX-001"] == "met"
