"""Tests for the DEFER band + extraction-confidence ceiling.

From the failure-aware-medical-AI review (Apr 25):
  (B) middle band 0.5-0.65 = uncertain → DEFER, don't auto-submit
  (C) cap reasoner score by extraction confidence — garbage in shouldn't
      license confident out
"""

from __future__ import annotations

import math

import pytest

from cardioauth.demo_e2e import _propagate_extraction_confidence


# ── (C) extraction-confidence ceiling ───────────────────────────────────

def test_no_cap_when_extraction_solid() -> None:
    # chart_confidence=1.0 → cap=1.0 → pass-through
    assert _propagate_extraction_confidence(0.9, 1.0) == 0.9
    assert _propagate_extraction_confidence(0.5, 1.0) == 0.5


def test_cap_when_extraction_thin() -> None:
    # chart_confidence=0.5 → cap=0.75; reasoner saying 0.9 is NOT licensed
    assert _propagate_extraction_confidence(0.9, 0.5) == 0.75


def test_cap_when_extraction_zero() -> None:
    # chart_confidence=0.0 → cap=0.5; will fall into reasoner-block branch
    assert _propagate_extraction_confidence(0.9, 0.0) == 0.5


def test_cap_below_threshold_passthrough() -> None:
    # If reasoner already below cap, no change
    assert _propagate_extraction_confidence(0.4, 0.5) == 0.4


def test_cap_handles_none() -> None:
    assert _propagate_extraction_confidence(None, 0.5) is None
    assert _propagate_extraction_confidence(0.8, None) == 0.8


def test_cap_curve_monotonic() -> None:
    """Cap should rise monotonically with chart confidence."""
    prev = -1.0
    for cc in [0.0, 0.2, 0.5, 0.7, 0.9, 1.0]:
        capped = _propagate_extraction_confidence(0.95, cc)
        assert capped is not None and capped >= prev
        prev = capped


# ── (B) DEFER band — integration via run_end_to_end_demo ────────────────

def _patched_reasoner(monkeypatch, score: float, label: str):
    """Patch the no-API-key fallback reasoner to return controlled output."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from cardioauth.models.reasoning import ReasoningResult
    from cardioauth import demo as demo_mod

    def fake(chart, policy):
        return ReasoningResult(
            approval_likelihood_score=score,
            approval_likelihood_label=label,
            pa_narrative_draft="Test narrative for gate.",
            criteria_not_met=[],
        )
    monkeypatch.setattr(demo_mod, "get_demo_reasoning", fake)


def test_defer_band_outcome(monkeypatch) -> None:
    """0.55 / MEDIUM → DEFERRED_TO_PHYSICIAN, not submitted."""
    _patched_reasoner(monkeypatch, score=0.55, label="MEDIUM")
    from cardioauth.demo_e2e import run_end_to_end_demo

    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    assert timeline.outcome == "DEFERRED_TO_PHYSICIAN", \
        f"Expected DEFERRED_TO_PHYSICIAN, got {timeline.outcome}"


def test_high_score_still_approves(monkeypatch) -> None:
    """0.85 / HIGH → not deferred, not blocked."""
    _patched_reasoner(monkeypatch, score=0.85, label="HIGH")
    from cardioauth.demo_e2e import run_end_to_end_demo

    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    assert timeline.outcome not in (
        "DEFERRED_TO_PHYSICIAN", "BLOCKED_BY_REASONER", "BLOCKED_MISSING_CRITICAL",
    ), f"Expected approval-track outcome, got {timeline.outcome}"


def test_low_score_still_blocks(monkeypatch) -> None:
    """0.30 / LOW → BLOCKED_BY_REASONER (not deferred)."""
    _patched_reasoner(monkeypatch, score=0.30, label="LOW")
    from cardioauth.demo_e2e import run_end_to_end_demo

    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )
    assert timeline.outcome == "BLOCKED_BY_REASONER", \
        f"Expected BLOCKED_BY_REASONER, got {timeline.outcome}"


def test_score_at_band_edges(monkeypatch) -> None:
    """0.65 should NOT defer (band is half-open [0.5, 0.65)). 0.5 SHOULD defer."""
    _patched_reasoner(monkeypatch, score=0.65, label="MEDIUM")
    from cardioauth.demo_e2e import run_end_to_end_demo

    t1 = run_end_to_end_demo(
        patient_id="DEMO-001", procedure_code="78492",
        payer_name="UnitedHealthcare", scripted_outcome="APPROVED",
    )
    assert t1.outcome != "DEFERRED_TO_PHYSICIAN"

    _patched_reasoner(monkeypatch, score=0.50, label="MEDIUM")
    t2 = run_end_to_end_demo(
        patient_id="DEMO-001", procedure_code="78492",
        payer_name="UnitedHealthcare", scripted_outcome="APPROVED",
    )
    assert t2.outcome == "DEFERRED_TO_PHYSICIAN"
