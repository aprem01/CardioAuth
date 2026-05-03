"""Tests for Agentic Generator 3: safety_verifier extractors."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cardioauth.lean_safety_extractor_generator import (
    CandidateExtractor,
    CandidatePattern,
    SafetyExtractorGeneratorOutput,
    generate_safety_extractor,
)


def _good_payload(**overrides) -> dict:
    base = {
        "schema_version": "safety-ext-gen-1.0",
        "generated_at": "2026-05-03T20:00:00+00:00",
        "candidate_extractor": {
            "fact_id": "lbbb_present",
            "label": "Left bundle branch block (LBBB)",
            "function_name": "check_lbbb",
            "criterion_codes": ["ECG-001"],
            "patterns": [
                {"pattern": r"\bLBBB\b", "rationale": "Common abbreviation",
                 "sample_match": "ECG shows LBBB"},
                {"pattern": r"\bleft\s+bundle\s+branch\s+block\b",
                 "rationale": "Long form", "sample_match": "left bundle branch block"},
            ],
            "denial_patterns": [
                {"pattern": r"\bdenies\s+LBBB\b", "rationale": "ROS denial",
                 "sample_match": "denies LBBB"},
                {"pattern": r"\bno\s+LBBB\b", "rationale": "Negative",
                 "sample_match": "no LBBB"},
            ],
            "confidence": 0.95,
        },
        "requires_human_judgment": [],
    }
    base.update(overrides)
    return base


def _fake(payload: dict, tokens: int = 1000, cost: float = 0.05):
    def caller(system, user):
        return payload, {
            "input_tokens": tokens // 2, "output_tokens": tokens // 2,
            "model": "fake", "cost_usd": cost,
        }
    return caller


# ── Phase ordering ────────────────────────────────────────────────────


def test_runs_all_five_phases() -> None:
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB on baseline ECG.",
        positive_samples=["ECG shows LBBB", "chronic LBBB"],
        negative_samples=["denies LBBB"],
        llm_caller=_fake(_good_payload()),
    )
    names = [s.name for s in result.stages]
    assert names == [
        "Phase 1: analyse",
        "Phase 2: optimise",
        "Phase 3: design",
        "Phase 4: generate",
        "Phase 5: report",
    ]


def test_phase1_failure_short_circuits() -> None:
    def boom(system, user):
        raise Exception("API down")
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB",
        positive_samples=["LBBB"],
        llm_caller=boom,
    )
    assert result.stages[0].status == "failed"
    assert len(result.stages) == 1


# ── Phase 2: pattern compilation + dedupe ────────────────────────────


def test_phase2_rejects_uncompilable_patterns() -> None:
    payload = _good_payload()
    payload["candidate_extractor"]["patterns"].append({
        "pattern": "[unclosed bracket",  # invalid regex
        "rationale": "broken", "sample_match": "x",
    })
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB", positive_samples=["LBBB"],
        llm_caller=_fake(payload),
    )
    p2 = next(s for s in result.stages if s.name == "Phase 2: optimise")
    assert (p2.detail or {}).get("rejected_positive"), "should reject the broken pattern"
    # The good patterns survive
    assert len(result.candidate_extractor.patterns) == 2


def test_phase2_dedupes_identical_patterns() -> None:
    payload = _good_payload()
    payload["candidate_extractor"]["patterns"].append({
        "pattern": r"\bLBBB\b",  # exact duplicate
        "rationale": "dup", "sample_match": "x",
    })
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB", positive_samples=["LBBB"],
        llm_caller=_fake(payload),
    )
    patterns = [p.pattern for p in result.candidate_extractor.patterns]
    assert patterns.count(r"\bLBBB\b") == 1


def test_phase2_warns_on_missing_word_boundaries() -> None:
    payload = _good_payload()
    payload["candidate_extractor"]["patterns"] = [
        {"pattern": "LBBB", "rationale": "no boundaries",
         "sample_match": "x"},  # no \b
    ]
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB", positive_samples=["LBBB"],
        llm_caller=_fake(payload),
    )
    p2 = next(s for s in result.stages if s.name == "Phase 2: optimise")
    warnings = (p2.detail or {}).get("missing_word_boundaries", [])
    assert "LBBB" in warnings


# ── Phase 3: design check ───────────────────────────────────────────


def test_phase3_flags_no_positive_patterns() -> None:
    payload = _good_payload()
    payload["candidate_extractor"]["patterns"] = []
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB", positive_samples=["LBBB"],
        llm_caller=_fake(payload),
    )
    p3 = next(s for s in result.stages if s.name == "Phase 3: design")
    assert p3.status == "fallback"
    issues = (p3.detail or {}).get("issues", [])
    assert any("No positive patterns" in i for i in issues)


def test_phase3_flags_bad_function_name() -> None:
    payload = _good_payload()
    payload["candidate_extractor"]["function_name"] = "BadName"
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB", positive_samples=["LBBB"],
        llm_caller=_fake(payload),
    )
    p3 = next(s for s in result.stages if s.name == "Phase 3: design")
    issues = (p3.detail or {}).get("issues", [])
    assert any("function_name" in i for i in issues)


# ── Phase 5: regression against samples ─────────────────────────────


def test_phase5_reports_recall_and_false_positive_rate() -> None:
    """Exercise the patterns against the samples and report stats."""
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB on baseline ECG.",
        positive_samples=[
            "ECG shows LBBB",
            "chronic left bundle branch block",
        ],
        negative_samples=[
            "denies LBBB",
            "no LBBB",
        ],
        llm_caller=_fake(_good_payload()),
    )
    p5 = next(s for s in result.stages if s.name == "Phase 5: report")
    assert (p5.detail or {})["positive_hits"] == 2
    assert (p5.detail or {})["negative_false_positives"] == 0
    assert p5.status == "ok"


def test_phase5_marks_fallback_when_recall_too_low() -> None:
    """If the LLM's patterns miss 50%+ of positive samples, mark
    fallback so the reviewer knows the draft needs work."""
    payload = _good_payload()
    # Patterns that DON'T cover the positive samples below
    payload["candidate_extractor"]["patterns"] = [
        {"pattern": r"\bSPECT\b", "rationale": "wrong",
         "sample_match": "wrong"},
    ]
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB",
        positive_samples=["ECG shows LBBB", "left bundle branch block"],
        negative_samples=[],
        llm_caller=_fake(payload),
    )
    p5 = next(s for s in result.stages if s.name == "Phase 5: report")
    assert p5.status == "fallback"


# ── Source generation ─────────────────────────────────────────────────


def test_to_python_source_emits_check_function() -> None:
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB",
        positive_samples=["LBBB"], negative_samples=["denies LBBB"],
        llm_caller=_fake(_good_payload()),
    )
    src = result.to_python_source()
    assert "def check_lbbb(note: str) -> FactCheck:" in src
    assert "_check_pattern" in src
    assert "fact_id='lbbb_present'" in src
    assert "AUTO-GENERATED" in src
    # Pattern appears repr-escaped in the source (\\b in the string
    # literal becomes \b at runtime when Python evaluates it).
    assert "LBBB" in src


def test_to_python_source_handles_no_denial_patterns() -> None:
    payload = _good_payload()
    payload["candidate_extractor"]["denial_patterns"] = []
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB",
        positive_samples=["LBBB"], negative_samples=[],
        llm_caller=_fake(payload),
    )
    src = result.to_python_source()
    # Source is well-formed even without denial_patterns
    assert "denial_patterns" not in src
    assert "patterns=[" in src


# ── Result serialization ─────────────────────────────────────────────


def test_result_round_trips_json() -> None:
    result = generate_safety_extractor(
        criterion_codes=["ECG-001"],
        criterion_definition="LBBB",
        positive_samples=["LBBB"], negative_samples=["denies LBBB"],
        llm_caller=_fake(_good_payload()),
    )
    d = result.to_dict()
    json.dumps(d, default=str)
    assert d["candidate_extractor"]["fact_id"] == "lbbb_present"


# ── Schema invariants ────────────────────────────────────────────────


def test_confidence_bounded() -> None:
    with pytest.raises(ValidationError):
        CandidateExtractor(
            fact_id="x", label="X", function_name="check_x",
            confidence=1.5,
        )


def test_pattern_required_field_present() -> None:
    """Pattern's `pattern` field is required."""
    with pytest.raises(ValidationError):
        CandidatePattern(rationale="X")  # type: ignore[call-arg]
