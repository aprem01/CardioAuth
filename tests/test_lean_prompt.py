"""Tests for the Lean Hybrid State 2 unified prompt."""

from __future__ import annotations

import json

import pytest

from cardioauth.lean_prompt import (
    SYSTEM_PROMPT,
    State2ValidationError,
    build_retry_user_prompt,
    build_user_prompt,
    validate_state2_output,
)


# ── System prompt ──────────────────────────────────────────────────────


def test_system_prompt_includes_schema() -> None:
    """The full JSON Schema MUST be embedded in the system prompt so
    the LLM has the contract."""
    assert "LeanState2Output" in SYSTEM_PROMPT or "criteria_evaluated" in SYSTEM_PROMPT
    assert "criterion" in SYSTEM_PROMPT.lower()
    assert "json" in SYSTEM_PROMPT.lower()


def test_system_prompt_warns_against_silent_cpt_fixes() -> None:
    """Peter's Q3: did the system flag mismatches instead of silently
    fixing them. Must be in the prompt's operating principles."""
    assert "silently" in SYSTEM_PROMPT.lower()
    assert "cpt" in SYSTEM_PROMPT.lower()


def test_system_prompt_demands_verbatim_quotes() -> None:
    """Anti-hallucination: every claim cites verbatim text."""
    assert "verbatim" in SYSTEM_PROMPT.lower()


def test_system_prompt_supports_ambiguous_status() -> None:
    """Edge cases: schema admits 'ambiguous'; prompt must explain it."""
    assert "ambiguous" in SYSTEM_PROMPT.lower()


# ── User prompt builder ────────────────────────────────────────────────


def test_user_prompt_includes_required_sections() -> None:
    p = build_user_prompt(
        case_id="C-1", raw_note="note text",
        request_cpt="78452", payer="UnitedHealthcare",
        applicable_criteria=[{"code": "ECG-001"}],
        payer_policy_chunks=[],
        payer_form_fields=[{"key": "lbbb_present"}],
        pre_pass_essentials={"patient_name": "X"},
    )
    assert "case_id: C-1" in p
    assert "request_cpt: 78452" in p
    assert "ECG-001" in p
    assert "patient_name" in p
    assert "BEGIN NOTE" in p
    assert "END NOTE" in p


def test_user_prompt_passes_taxonomy_subset_only() -> None:
    """The taxonomy injected into the prompt is only the criteria
    applicable to the request CPT — that's how prompt size stays
    bounded as taxonomy grows."""
    p = build_user_prompt(
        case_id="C-1", raw_note="note",
        request_cpt="78492", payer="UHC",
        applicable_criteria=[
            {"code": "ECG-001", "applies_to": ["78452", "78492"]},
            {"code": "BMI-001", "applies_to": ["78492"]},
        ],
        payer_policy_chunks=[],
        payer_form_fields=[],
        pre_pass_essentials={},
    )
    assert "ECG-001" in p
    assert "BMI-001" in p
    # Confirm no junk unrelated criteria (caller filters; prompt
    # builder doesn't second-guess)


def test_user_prompt_includes_payer_policy_chunks() -> None:
    p = build_user_prompt(
        case_id="C-1", raw_note="note",
        request_cpt="78452", payer="UHC",
        applicable_criteria=[],
        payer_policy_chunks=[
            {"id": "UHC-78452-001", "text": "SPECT MPI is medically necessary when..."}
        ],
        payer_form_fields=[],
        pre_pass_essentials={},
    )
    assert "UHC-78452-001" in p
    assert "SPECT MPI is medically necessary" in p


def test_user_prompt_handles_payer_specific_criteria() -> None:
    """When payer_specific_criteria is provided, it gets its own
    section and the LLM is told to emit them in the separate output
    list."""
    p = build_user_prompt(
        case_id="C-1", raw_note="note",
        request_cpt="78452", payer="UHC",
        applicable_criteria=[],
        payer_policy_chunks=[],
        payer_form_fields=[],
        pre_pass_essentials={},
        payer_specific_criteria=[{"code": "UHC-MCG-78452-002"}],
    )
    assert "UHC-MCG-78452-002" in p
    assert "payer_specific_criteria_evaluated" in p


def test_user_prompt_omits_optional_sections_cleanly() -> None:
    """When payer policy / precedents / payer-specific are absent, no
    empty sections should appear (clean prompt = better attention)."""
    p = build_user_prompt(
        case_id="C-1", raw_note="note",
        request_cpt="78452", payer="UHC",
        applicable_criteria=[],
        payer_policy_chunks=[],
        payer_form_fields=[],
        pre_pass_essentials={},
    )
    # Optional sections should not appear
    assert "Payer policy excerpts" not in p
    assert "Similar prior cases" not in p
    assert "Payer-specific criteria" not in p
    # Required sections always appear
    assert "BEGIN NOTE" in p


def test_user_prompt_size_stays_reasonable_with_full_inputs() -> None:
    """Sanity check on prompt size with realistic input volume."""
    p = build_user_prompt(
        case_id="C-1",
        raw_note="x" * 5000,  # 5KB note
        request_cpt="78452", payer="UHC",
        applicable_criteria=[{"code": f"C-{i}"} for i in range(15)],
        payer_policy_chunks=[{"id": f"P-{i}", "text": "x" * 200} for i in range(8)],
        payer_form_fields=[{"key": f"f_{i}"} for i in range(22)],
        pre_pass_essentials={"patient_name": "X"},
    )
    # 5KB note + 15 criteria + 8 chunks + 22 fields = should fit
    # well under any model's input limit (Claude Sonnet 4: 200K tokens)
    assert len(p) < 50_000  # ~12K tokens — comfortable


# ── Output validation ──────────────────────────────────────────────────


def test_validate_state2_output_raises_on_garbage_input() -> None:
    with pytest.raises(State2ValidationError):
        validate_state2_output("definitely not JSON")


def test_validate_state2_output_raises_on_incomplete_json() -> None:
    """Missing required fields → schema validation failure, not silent
    pass-through."""
    incomplete = json.dumps({"case_id": "X"})  # missing most fields
    with pytest.raises(State2ValidationError) as exc_info:
        validate_state2_output(incomplete)
    err_msg = str(exc_info.value)
    assert "Field required" in err_msg or "required" in err_msg.lower()


def test_validate_state2_output_accepts_valid_minimal_object() -> None:
    """A minimum-required JSON validates to a LeanState2Output."""
    payload = {
        "case_id": "C-1",
        "request_cpt": "78452",
        "payer": "UnitedHealthcare",
        "cpt_resolution": {
            "cpt": "78452", "procedure_name": "SPECT",
            "source": "request", "request_cpt": "78452",
        },
        "approval_verdict": {
            "score": 0.85, "label": "HIGH",
            "headline_summary": ["SPECT indicated."],
        },
        "narrative": {
            "text": "Patient has LBBB...",
            "cpt_referenced": "78452",
            "procedure_referenced": "SPECT",
        },
        "documentation_quality": {
            "note_format_quality": "structured",
        },
    }
    out = validate_state2_output(json.dumps(payload))
    assert out.case_id == "C-1"
    assert out.cpt_resolution.cpt == "78452"
    assert out.approval_verdict.label == "HIGH"


def test_validate_state2_output_extracts_json_from_markdown_fences() -> None:
    """Models sometimes wrap JSON in ```json fences despite the prompt
    saying not to. The recovery path should still parse it."""
    payload = {
        "case_id": "C-1",
        "request_cpt": "78452",
        "payer": "UHC",
        "cpt_resolution": {
            "cpt": "78452", "procedure_name": "SPECT",
            "source": "request", "request_cpt": "78452",
        },
        "approval_verdict": {"score": 0.85, "label": "HIGH"},
        "narrative": {
            "text": "x", "cpt_referenced": "78452", "procedure_referenced": "SPECT",
        },
        "documentation_quality": {"note_format_quality": "structured"},
    }
    wrapped = "```json\n" + json.dumps(payload) + "\n```"
    out = validate_state2_output(wrapped)
    assert out.case_id == "C-1"


# ── Retry prompt ──────────────────────────────────────────────────────


def test_build_retry_prompt_includes_errors_and_original() -> None:
    retry = build_retry_user_prompt(
        original_user_prompt="ORIGINAL_PROMPT_TEXT",
        failed_output='{"case_id": "X"}',
        errors=[{"loc": ["request_cpt"], "msg": "Field required"}],
    )
    assert "request_cpt" in retry
    assert "Field required" in retry
    assert "ORIGINAL_PROMPT_TEXT" in retry
    assert '{"case_id": "X"}' in retry


def test_build_retry_prompt_truncates_oversized_failed_output() -> None:
    """Don't blow up the retry context with a 50KB failed output."""
    retry = build_retry_user_prompt(
        original_user_prompt="orig",
        failed_output="x" * 10_000,
        errors=[{"loc": ["x"], "msg": "y"}],
    )
    # The 10K failed output should be truncated to the size cap
    assert len(retry) < 5000
