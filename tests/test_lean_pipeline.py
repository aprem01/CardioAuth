"""Tests for the Lean Hybrid State Machine.

Locks in the state-machine contract: stages run in order, each can
read what previous stages produced, the gate makes the right
decision under each condition, and pipeline_errors propagate when
the LLM call fails.

Crucially, every test uses a FAKE LLM so the suite stays
deterministic and runs in <1s. The live A/B comparison vs the
existing pipeline lives in a separate harness (test_lean_ab_*).
"""

from __future__ import annotations

import json

import pytest

from cardioauth.lean_pipeline import (
    LeanRunResult,
    _classify_llm_error,
    run_lean_pipeline,
)


# ── Fake LLM helpers ───────────────────────────────────────────────────


def _good_payload(**overrides) -> dict:
    base = {
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
        "clinical_facts": [
            {
                "category": "ecg_finding", "value": "LBBB",
                "evidence": {"quote": "chronic LBBB"},
            },
        ],
        "criteria_evaluated": [
            {
                "code": "ECG-001", "status": "met",
                "evidence": [{"quote": "chronic LBBB"}],
                "confidence": 0.95,
            },
        ],
        "approval_verdict": {
            "score": 0.88, "label": "HIGH",
            "headline_summary": ["SPECT indicated."],
        },
        "narrative": {
            "text": "Patient with LBBB requires SPECT.",
            "cpt_referenced": "78452",
            "procedure_referenced": "Lexiscan SPECT",
        },
        "documentation_quality": {"note_format_quality": "structured"},
    }
    base.update(overrides)
    return base


def _fake_llm(payload: dict, tokens: int = 1500, cost: float = 0.05):
    def caller(system, user):
        return json.dumps(payload), {
            "input_tokens": tokens // 2, "output_tokens": tokens // 2,
            "model": "fake", "cost_usd": cost,
        }
    return caller


_NOTE = """\
Re: Margaret Synthetic
DOB: 01/15/1958
Member ID: UHC987654321
Insurance: UnitedHealthcare PPO

Patient with chronic LBBB on baseline ECG.
Ordering: Lexiscan SPECT (CPT 78452).

Ordering MD: Dr. James Carter
NPI: 1306939693
"""


# ── Stage ordering ────────────────────────────────────────────────────


def test_pipeline_runs_all_four_stages_in_order() -> None:
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(_good_payload()),
    )
    stage_names = [s["name"] for s in result.stages]
    assert stage_names == [
        "State 1: pre-pass",
        "State 2: unified call",
        "State 3: safety verify",
        "State 4: gate",
    ]


def test_state1_extracts_essentials_via_regex() -> None:
    """State 1 runs the deterministic essentials backstop."""
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(_good_payload()),
    )
    pre_pass = result.stages[0]
    assert pre_pass["status"] == "ok"
    detail = pre_pass["detail"]
    essentials = detail["essentials"]
    assert essentials.get("patient_name") == "Margaret Synthetic"
    assert essentials.get("date_of_birth") == "01/15/1958"
    assert essentials.get("attending_npi") == "1306939693"


def test_state1_filters_taxonomy_by_request_cpt() -> None:
    """Only criteria with applies_to matching request_cpt are
    forwarded to State 2 — keeps prompt size bounded as taxonomy
    grows."""
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(_good_payload()),
    )
    pre_pass = result.stages[0]
    assert pre_pass["detail"]["applicable_criteria_count"] > 0


# ── State 2 schema validation + retry ─────────────────────────────────


def test_pipeline_succeeds_with_valid_llm_output() -> None:
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(_good_payload()),
    )
    state2 = next(s for s in result.stages if "State 2" in s["name"])
    assert state2["status"] == "ok"
    assert result.approval_score == 0.88
    assert result.approval_label == "HIGH"


def test_pipeline_retries_on_schema_violation_then_succeeds() -> None:
    """First call returns garbage, second call returns valid output —
    retry loop must heal."""
    call_count = {"n": 0}

    def flaky(system, user):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "not valid json at all", {"input_tokens": 100, "output_tokens": 50, "model": "fake", "cost_usd": 0.01}
        return json.dumps(_good_payload()), {"input_tokens": 1000, "output_tokens": 500, "model": "fake", "cost_usd": 0.05}

    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=flaky, max_retries=1,
    )
    state2 = next(s for s in result.stages if "State 2" in s["name"])
    assert state2["status"] == "ok"
    assert state2["detail"]["attempts"] == 2


def test_pipeline_fails_state2_after_retries_exhausted() -> None:
    def always_garbage(system, user):
        return "still not json", {"input_tokens": 50, "output_tokens": 20, "model": "fake", "cost_usd": 0.01}

    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=always_garbage, max_retries=1,
    )
    state2 = next(s for s in result.stages if "State 2" in s["name"])
    assert state2["status"] == "failed"
    # State 4 still runs but blocks because State 2 had no output
    assert result.decision == "block"


# ── Pipeline-level error classification ──────────────────────────────


def test_spend_limit_error_classified_as_blocking() -> None:
    err = _classify_llm_error(Exception("Your credit balance is too low"))
    assert err["kind"] == "anthropic_spend_limit"
    assert err["severity"] == "blocking"


def test_generic_error_classified_high_severity() -> None:
    err = _classify_llm_error(Exception("Connection reset"))
    assert err["kind"] == "anthropic_unavailable"
    assert err["severity"] == "high"


def test_pipeline_surfaces_spend_limit_when_llm_raises_it() -> None:
    """Same UX as the demo_e2e pipeline: spend-limit raises a top-level
    pipeline_error rather than producing garbage downstream."""
    def boom(system, user):
        raise Exception("Your credit balance is too low to access the API")

    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=boom,
    )
    assert len(result.pipeline_errors) == 1
    assert result.pipeline_errors[0]["kind"] == "anthropic_spend_limit"
    assert result.decision == "block"


# ── State 3: safety verifier hookup ──────────────────────────────────


def test_state3_emits_finding_when_reasoner_misses_signal() -> None:
    """If the LLM marked ECG-001 as not_met but the note clearly has
    LBBB, State 3 should emit a safety_reasoner_missed_signal."""
    bad_payload = _good_payload(
        criteria_evaluated=[
            {
                "code": "ECG-001", "status": "not_met",
                "rationale": "I claim no ECG abnormality.",
                "confidence": 0.9,
            },
        ],
    )
    result = run_lean_pipeline(
        raw_note=_NOTE,  # the note literally says "chronic LBBB"
        request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(bad_payload),
    )
    kinds = {f["kind"] for f in result.findings}
    assert "safety_reasoner_missed_signal" in kinds


# ── State 4: gate logic ──────────────────────────────────────────────


def test_gate_blocks_on_missing_essentials() -> None:
    """A note without essential headers should block, even if LLM
    output is otherwise clean."""
    bare_note = "Patient has chest pain.\nLBBB on ECG."  # no Patient:/DOB:/NPI: headers
    result = run_lean_pipeline(
        raw_note=bare_note, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(_good_payload(
            patient_name="", date_of_birth="", insurance_id="",
            payer_name="", attending_physician="", attending_npi="",
        )),
    )
    assert result.decision == "block"
    kinds = {f["kind"] for f in result.findings}
    assert "missing_essential" in kinds


def test_gate_holds_on_cpt_attestation_mismatch() -> None:
    """Narrative cites a different CPT than resolved → high finding,
    hold for review."""
    payload = _good_payload(
        cpt_resolution={
            "cpt": "78452", "procedure_name": "SPECT",
            "source": "request", "request_cpt": "78452",
        },
        narrative={
            "text": "PET indicated...", "cpt_referenced": "78492",
            "procedure_referenced": "Cardiac PET",
        },
    )
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(payload),
    )
    assert result.decision == "hold_for_review"
    kinds = {f["kind"] for f in result.findings}
    assert "cpt_attestation_vs_resolved" in kinds


def test_gate_holds_on_low_approval_label() -> None:
    payload = _good_payload(
        criteria_evaluated=[
            {
                "code": "ECG-001", "status": "not_met",
                "rationale": "No ECG abnormality documented.",
                "confidence": 0.9,
            },
        ],
        approval_verdict={"score": 0.30, "label": "LOW"},
    )
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(payload),
    )
    # Low label → hold_for_review (could also have safety findings,
    # but the score band itself triggers the hold)
    assert result.decision == "hold_for_review"


def test_gate_holds_on_ambiguous_criteria() -> None:
    """Ambiguous criteria → physician review recommended → hold."""
    payload = _good_payload(
        criteria_evaluated=[
            {
                "code": "ECG-001", "status": "ambiguous",
                "rationale": "Note mentions 'limited ECG' but doesn't specify.",
                "confidence": 0.4,
                "requires_human_review": True,
            },
        ],
    )
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(payload),
    )
    kinds = {f["kind"] for f in result.findings}
    assert "criteria_ambiguous" in kinds


def test_gate_transmits_when_everything_clean() -> None:
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(_good_payload()),
    )
    assert result.decision == "transmit"


# ── Result serialization ─────────────────────────────────────────────


def test_result_serializes_to_dict() -> None:
    result = run_lean_pipeline(
        raw_note=_NOTE, request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=_fake_llm(_good_payload()),
    )
    d = result.to_dict()
    assert "case_id" in d
    assert "decision" in d
    assert "stages" in d
    assert "state2_output" in d
    assert "approval_score" in d
    # Must round-trip JSON for the API endpoint
    json.dumps(d, default=str)


# ── Cost estimator (per-model pricing) ───────────────────────────────


def test_cost_estimator_handles_known_models() -> None:
    """Known model prefixes resolve to concrete prices."""
    from cardioauth.lean_pipeline import _estimate_cost_usd

    # Opus is most expensive
    opus = _estimate_cost_usd("claude-opus-4-7", 1000, 500)
    sonnet = _estimate_cost_usd("claude-sonnet-4-6", 1000, 500)
    haiku = _estimate_cost_usd("claude-haiku-4-5-20251001", 1000, 500)
    assert opus > sonnet > haiku
    # Sonnet at 1000 in / 500 out should be ~ $0.0105 (3 + 7.5 = 10.5/1000)
    assert 0.005 < sonnet < 0.020


def test_cost_estimator_strips_window_suffix() -> None:
    """`claude-opus-4-7[1m]` should match the same price as `claude-opus-4-7`."""
    from cardioauth.lean_pipeline import _estimate_cost_usd
    a = _estimate_cost_usd("claude-opus-4-7", 1000, 500)
    b = _estimate_cost_usd("claude-opus-4-7[1m]", 1000, 500)
    assert a == b


def test_cost_estimator_falls_back_for_unknown_model() -> None:
    """Unknown model gets a neutral default — never zero."""
    from cardioauth.lean_pipeline import _estimate_cost_usd
    cost = _estimate_cost_usd("some-future-model", 1000, 500)
    assert cost > 0.0


def test_cost_estimator_zero_tokens_yields_zero() -> None:
    from cardioauth.lean_pipeline import _estimate_cost_usd
    assert _estimate_cost_usd("claude-opus-4-7", 0, 0) == 0.0


# ── Tool-use real caller (smoke; doesn't actually call Anthropic) ──────


def test_real_anthropic_caller_requires_api_key(monkeypatch) -> None:
    """No key → RuntimeError. Caller must not silently fall through."""
    from cardioauth.lean_pipeline import _real_anthropic_caller
    # Force an empty Config().anthropic_api_key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _real_anthropic_caller("system", "user")
