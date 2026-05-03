"""Tests for the lean vs current A/B harness.

Uses fake LLMs for the lean side and the existing no-API-key fallback
for the current side (since demo_e2e is wired to skip Claude when
ANTHROPIC_API_KEY is empty). Keeps the suite deterministic and fast.
"""

from __future__ import annotations

import json

import pytest

from cardioauth.lean_ab_harness import (
    AbReport,
    CaseComparison,
    compare_one_case,
    run_ab,
)


_NOTE_SPECT = """\
Re: Margaret Synthetic
DOB: 01/15/1958
Member ID: UHC987654321
Insurance: UnitedHealthcare PPO

Patient with chronic LBBB on baseline ECG. Severe knee OA limiting
exercise. Prior treadmill 06/2023 was nondiagnostic.

Plan: Lexiscan SPECT (CPT 78452).

Ordering MD: Dr. James Carter
NPI: 1306939693
"""


def _good_lean_payload() -> dict:
    return {
        "case_id": "TEST-1",
        "request_cpt": "78452",
        "payer": "UnitedHealthcare",
        "cpt_resolution": {
            "cpt": "78452", "procedure_name": "Lexiscan SPECT",
            "source": "request", "request_cpt": "78452",
        },
        "patient_name": "Margaret Synthetic",
        "date_of_birth": "01/15/1958",
        "insurance_id": "UHC987654321",
        "payer_name": "UnitedHealthcare",
        "attending_physician": "Dr. James Carter",
        "attending_npi": "1306939693",
        "criteria_evaluated": [
            {"code": "ECG-001", "status": "met", "evidence": [{"quote": "chronic LBBB"}], "confidence": 0.95},
            {"code": "EX-001", "status": "met", "evidence": [{"quote": "Severe knee OA limiting exercise"}], "confidence": 0.9},
        ],
        "approval_verdict": {"score": 0.88, "label": "HIGH"},
        "narrative": {
            "text": "Patient meets SPECT criteria.",
            "cpt_referenced": "78452",
            "procedure_referenced": "Lexiscan SPECT",
        },
        "documentation_quality": {"note_format_quality": "structured"},
    }


def _fake_lean_caller(payload):
    def caller(system, user):
        return json.dumps(payload), {
            "input_tokens": 1200, "output_tokens": 600,
            "model": "fake", "cost_usd": 0.05,
        }
    return caller


# ── Single-case comparison ────────────────────────────────────────────


def test_compare_one_case_runs_both_pipelines(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    case = {
        "case_id": "AB-1",
        "patient_id": "CUSTOM",
        "request_cpt": "78452",
        "payer": "UnitedHealthcare",
        "raw_note": _NOTE_SPECT,
    }
    comp = compare_one_case(case, lean_llm_caller=_fake_lean_caller(_good_lean_payload()))
    assert comp.case_id == "AB-1"
    # Lean side ran (decision populated)
    assert comp.lean_decision in ("transmit", "hold_for_review", "block")
    # Current side ran (timeline produced something — even if degraded
    # without ANTHROPIC_API_KEY, the demo pipeline returns a timeline)
    assert comp.current_latency_ms > 0


def test_compare_one_case_finalize_computes_metrics(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    case = {
        "case_id": "AB-2",
        "request_cpt": "78452",
        "payer": "UnitedHealthcare",
        "raw_note": _NOTE_SPECT,
    }
    comp = compare_one_case(case, lean_llm_caller=_fake_lean_caller(_good_lean_payload()))
    # Derived metrics populated
    assert comp.score_abs_diff >= 0.0
    assert 0.0 <= comp.criteria_jaccard <= 1.0


def test_comparison_serializes_to_dict() -> None:
    comp = CaseComparison(case_id="X", request_cpt="78452", payer="UHC")
    comp.lean_decision = "transmit"
    comp.current_decision = "transmit"
    comp.lean_score = 0.85
    comp.current_score = 0.80
    comp.lean_latency_ms = 6000
    comp.current_latency_ms = 25000
    comp.lean_cost_usd = 0.30
    comp.current_cost_usd = 1.20
    comp.lean_criteria_met = ["ECG-001", "EX-001"]
    comp.current_criteria_met = ["ECG-001", "EX-001"]
    comp.finalize()
    d = comp.to_dict()
    assert d["case_id"] == "X"
    assert "lean" in d and "current" in d and "comparison" in d
    assert d["comparison"]["decisions_agree"] is True
    assert d["comparison"]["cpts_agree"] is False  # neither populated
    assert abs(d["comparison"]["score_delta"] - 0.05) < 1e-9
    assert d["comparison"]["criteria_jaccard"] == 1.0
    assert d["comparison"]["latency_speedup"] == pytest.approx(25000 / 6000, rel=1e-3)
    assert d["comparison"]["cost_savings_pct"] == pytest.approx(75.0, rel=1e-3)


# ── Aggregate report ──────────────────────────────────────────────────


def test_aggregate_report_summarizes_n_cases(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    cases = [
        {"case_id": f"AB-{i}", "request_cpt": "78452", "payer": "UnitedHealthcare", "raw_note": _NOTE_SPECT}
        for i in range(3)
    ]
    report = run_ab(cases, lean_llm_caller=_fake_lean_caller(_good_lean_payload()))
    assert report.n_cases == 3
    assert len(report.case_comparisons) == 3
    assert 0.0 <= report.decision_agreement_rate <= 1.0
    assert report.lean_total_latency_ms >= 0
    assert report.current_total_latency_ms >= 0


def test_report_to_markdown_contains_aggregate_table(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    report = run_ab(
        [{"case_id": "X", "request_cpt": "78452", "payer": "UHC", "raw_note": _NOTE_SPECT}],
        lean_llm_caller=_fake_lean_caller(_good_lean_payload()),
    )
    md = report.to_markdown()
    assert "A/B Report" in md
    assert "Decision agreement" in md
    assert "Per-case detail" in md


def test_report_round_trips_to_json(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    report = run_ab(
        [{"case_id": "X", "request_cpt": "78452", "payer": "UHC", "raw_note": _NOTE_SPECT}],
        lean_llm_caller=_fake_lean_caller(_good_lean_payload()),
    )
    d = report.to_dict()
    json.dumps(d, default=str)
    assert d["n_cases"] == 1


# ── Empty inputs ──────────────────────────────────────────────────────


def test_empty_case_list_produces_empty_report() -> None:
    report = run_ab([])
    assert report.n_cases == 0
    assert report.case_comparisons == []
    assert report.decision_agreement_rate == 0.0
