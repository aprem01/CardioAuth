"""Tests for the reliability / calibration analysis."""

from __future__ import annotations

import pytest

from cardioauth.calibration import (
    CalibrationBin,
    compute_calibration,
    report_to_dict,
)


def _row(score: float, outcome: str) -> dict:
    return {"score": score, "outcome": outcome}


def test_no_decisive_outcomes_returns_warning() -> None:
    rep = compute_calibration([])
    assert rep.decisive_count == 0
    assert rep.brier_score is None
    assert rep.ece is None
    assert "No decisive outcomes" in rep.reliability_warning


def test_only_pending_excluded_from_decisive() -> None:
    rows = [_row(0.5, "pending"), _row(0.7, "PENDING")]
    rep = compute_calibration(rows)
    assert rep.decisive_count == 0
    assert rep.pending_count == 2


def test_perfect_calibration_brier_zero() -> None:
    """All approvals with score=1.0, all denials with score=0.0 → Brier=0."""
    rows = [_row(1.0, "approved")] * 5 + [_row(0.0, "denied")] * 5
    rep = compute_calibration(rows, n_bins=10)
    assert rep.decisive_count == 10
    assert rep.brier_score == pytest.approx(0.0, abs=1e-9)
    assert rep.ece == pytest.approx(0.0, abs=1e-9)


def test_systematic_overconfidence_detected() -> None:
    """Reasoner says 0.9 but actual rate is 50%."""
    rows = [_row(0.9, "approved")] * 5 + [_row(0.9, "denied")] * 5
    rep = compute_calibration(rows, n_bins=10)
    assert rep.decisive_count == 10
    # over_confident_score = mean(predicted - actual) = 0.9 - 0.5 = 0.4
    assert rep.over_confident_score == pytest.approx(0.4, abs=1e-6)
    # ECE picks up the gap in this single populated bin
    assert rep.ece is not None and rep.ece > 0.3


def test_underconfidence_negative_score() -> None:
    """Reasoner says 0.3 but everyone gets approved."""
    rows = [_row(0.3, "approved")] * 8
    rep = compute_calibration(rows, n_bins=10)
    # over_confident_score < 0 (under-promising)
    assert rep.over_confident_score < 0


def test_bins_only_emitted_when_populated() -> None:
    rows = [_row(0.85, "approved"), _row(0.85, "denied"), _row(0.85, "approved")]
    rep = compute_calibration(rows, n_bins=10)
    # Only the [0.8, 0.9) bin populated
    assert len(rep.bins) == 1
    b = rep.bins[0]
    assert b.bin_low == pytest.approx(0.8)
    assert b.bin_high == pytest.approx(0.9)
    assert b.count == 3
    assert b.approved == 2
    assert b.actual_rate == pytest.approx(2/3)


def test_score_at_one_lands_in_top_bin() -> None:
    rows = [_row(1.0, "approved"), _row(1.0, "approved")]
    rep = compute_calibration(rows, n_bins=10)
    assert len(rep.bins) == 1
    assert rep.bins[0].bin_high == pytest.approx(1.0)


def test_invalid_score_skipped() -> None:
    rows = [
        _row(0.5, "approved"),
        _row(None, "approved"),  # type: ignore[arg-type]
        _row(1.5, "approved"),   # out of range
        _row("garbage", "denied"),  # type: ignore[arg-type]
    ]
    rep = compute_calibration(rows)
    assert rep.decisive_count == 1


def test_reliability_warning_below_threshold() -> None:
    rows = [_row(0.5, "approved")] * 5
    rep = compute_calibration(rows, min_decisive_for_reliable=20)
    assert "noisy" in rep.reliability_warning


def test_no_warning_when_enough_data() -> None:
    rows = [_row(0.5, "approved")] * 25
    rep = compute_calibration(rows, min_decisive_for_reliable=20)
    assert rep.reliability_warning == ""


def test_outcome_string_variants() -> None:
    rows = [
        _row(0.7, "approved"),
        _row(0.7, "Approved"),
        _row(0.7, "APPROVED-WITH-CONDITIONS"),  # Caught by startswith("approved")
        _row(0.7, "denied"),
        _row(0.7, "DENIED"),
    ]
    rep = compute_calibration(rows)
    assert rep.decisive_count == 5


def test_report_to_dict_round_trip() -> None:
    rows = [_row(0.8, "approved"), _row(0.8, "denied")]
    rep = compute_calibration(rows)
    d = report_to_dict(rep)
    assert d["decisive_count"] == 2
    assert "bins" in d
    assert d["bins"][0]["count"] == 2
    assert "brier_score" in d
    assert "ece" in d
    assert "reliability_warning" in d
