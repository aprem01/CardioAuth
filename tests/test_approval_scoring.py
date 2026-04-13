"""Tests for _compute_approval_score — the heuristic fallback scoring.

Peter's v2 validation revealed score miscalibration (30% avg for all-approved
cases). The scoring rules are:
  - Required criteria drive base score
  - If all required met, floor at 0.75
  - Supporting criteria only boost (up to +20%), never penalize
"""

from dataclasses import dataclass

from cardioauth.agents.unified_reasoner import _compute_approval_score, _label_from_score


@dataclass
class MockCriterion:
    code: str
    severity: str = "required"


def test_all_required_met_floor_at_0_75():
    """If every required criterion is met, score must be ≥ 0.75."""
    applicable = [
        MockCriterion("R-1", "required"),
        MockCriterion("R-2", "required"),
        MockCriterion("R-3", "required"),
    ]
    matches = [
        {"code": "R-1", "status": "met"},
        {"code": "R-2", "status": "met"},
        {"code": "R-3", "status": "met"},
    ]
    score = _compute_approval_score(matches, applicable)
    assert score >= 0.75


def test_all_required_met_with_supporting_boost():
    """All required met + supporting met should push score higher."""
    applicable = [
        MockCriterion("R-1", "required"),
        MockCriterion("S-1", "supporting"),
        MockCriterion("S-2", "supporting"),
    ]
    matches = [
        {"code": "R-1", "status": "met"},
        {"code": "S-1", "status": "met"},
        {"code": "S-2", "status": "met"},
    ]
    score = _compute_approval_score(matches, applicable)
    # base 1.0 + full supporting boost 0.20 capped at 1.0
    assert score == 1.0


def test_some_required_not_met_drops_score():
    """Not meeting required criteria drops the score."""
    applicable = [
        MockCriterion("R-1", "required"),
        MockCriterion("R-2", "required"),
        MockCriterion("R-3", "required"),
        MockCriterion("R-4", "required"),
    ]
    matches = [
        {"code": "R-1", "status": "met"},
        {"code": "R-2", "status": "met"},
        {"code": "R-3", "status": "not_met"},
        {"code": "R-4", "status": "not_met"},
    ]
    score = _compute_approval_score(matches, applicable)
    # 2/4 required met → base 0.5 (no floor since not all required met)
    assert score == 0.5


def test_supporting_not_met_does_NOT_penalize():
    """Peter's rule: supporting criteria should only boost, never drag down.

    Core check: even when all supporting criteria are not_met, as long as
    required criteria are met, the score must still be ≥ 0.75 (floor).
    """
    applicable = [
        MockCriterion("R-1", "required"),
        MockCriterion("S-1", "supporting"),
        MockCriterion("S-2", "supporting"),
    ]
    matches_no_sup = [
        {"code": "R-1", "status": "met"},
        {"code": "S-1", "status": "not_met"},
        {"code": "S-2", "status": "not_met"},
    ]
    # Even with no supporting met, the all-required-met floor kicks in.
    score_no_sup = _compute_approval_score(matches_no_sup, applicable)
    assert score_no_sup >= 0.75, f"Floor violated: {score_no_sup}"


def test_supporting_boost_shows_when_base_below_cap():
    """With required partially met, supporting boost should differentiate scores."""
    applicable = [
        MockCriterion("R-1", "required"),
        MockCriterion("R-2", "required"),
        MockCriterion("S-1", "supporting"),
        MockCriterion("S-2", "supporting"),
    ]
    matches_with_sup = [
        {"code": "R-1", "status": "met"},
        {"code": "R-2", "status": "not_met"},
        {"code": "S-1", "status": "met"},
        {"code": "S-2", "status": "met"},
    ]
    matches_no_sup = [
        {"code": "R-1", "status": "met"},
        {"code": "R-2", "status": "not_met"},
        {"code": "S-1", "status": "not_met"},
        {"code": "S-2", "status": "not_met"},
    ]
    score_with = _compute_approval_score(matches_with_sup, applicable)
    score_without = _compute_approval_score(matches_no_sup, applicable)
    assert score_with > score_without


def test_label_mapping():
    assert _label_from_score(0.95) == "HIGH"
    assert _label_from_score(0.80) == "HIGH"
    assert _label_from_score(0.75) == "MEDIUM"
    assert _label_from_score(0.60) == "MEDIUM"
    assert _label_from_score(0.45) == "LOW"
    assert _label_from_score(0.40) == "LOW"
    assert _label_from_score(0.30) == "INSUFFICIENT"
    assert _label_from_score(0.0) == "INSUFFICIENT"
