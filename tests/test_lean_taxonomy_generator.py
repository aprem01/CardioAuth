"""Tests for Agentic Generator 1: taxonomy from policy PDFs.

Covers the same scaling concerns Peter raised, but applied to the
*taxonomy authoring loop* rather than inference: more procedures,
edge cases, payer-specific differences, documentation variability
in the source policy.
"""

from __future__ import annotations

import json

import pytest

from cardioauth.lean_taxonomy_generator import (
    CandidateCriterion,
    CandidateRequiredElement,
    TaxonomyGeneratorOutput,
    generate_taxonomy_candidates,
)


def _good_llm_output(**overrides) -> dict:
    """The shape an LLM would emit via tool-use."""
    base = {
        "schema_version": "taxonomy-gen-1.0",
        "generated_at": "2026-05-03T20:00:00+00:00",
        "payer": "UnitedHealthcare",
        "target_cpts": ["78492"],
        "candidates": [
            {
                "code": "BMI-002-NEW",
                "category": "BMI",
                "short_name": "BMI ≥ 35 favors PET over SPECT",
                "definition": "PET is preferred over SPECT when BMI is at or above 35 due to attenuation artifacts.",
                "evidence_type": "demographic",
                "applies_to": ["78492"],
                "payers": ["UnitedHealthcare"],
                "guideline_source": "UHC Cardiac Imaging 2026 § 3.2",
                "severity": "supporting",
                "pathway_group": "",
                "required_elements": [
                    {"key": "bmi_ge_35", "description": "BMI ≥ 35 documented", "evidence_hint": "BMI in vitals or HPI"},
                ],
                "source_chunk_ids": ["UHC-78492-002"],
                "source_quote": "BMI ≥ 35 — PET preferred",
                "confidence": 0.92,
                "notes_for_reviewer": "",
            },
        ],
        "duplicates_of_existing": [],
        "requires_human_judgment": [],
    }
    base.update(overrides)
    return base


def _fake_llm(payload: dict, tokens: int = 1000, cost: float = 0.05):
    def caller(system, user):
        return payload, {
            "input_tokens": tokens // 2, "output_tokens": tokens // 2,
            "model": "fake", "cost_usd": cost,
        }
    return caller


# ── Phase ordering ────────────────────────────────────────────────────


def test_runs_all_five_phases() -> None:
    result = generate_taxonomy_candidates(
        payer="UnitedHealthcare",
        target_cpts=["78492"],
        policy_text="BMI ≥ 35 favors PET over SPECT.",
        existing_codes=[],
        llm_caller=_fake_llm(_good_llm_output()),
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
    """If the LLM call fails, phases 2-5 don't run."""
    def boom(system, user):
        raise Exception("API down")

    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492"],
        policy_text="...", existing_codes=[],
        llm_caller=boom,
    )
    assert result.stages[0].status == "failed"
    assert len(result.stages) == 1


# ── Phase 2: deterministic dedupe + validation ───────────────────────


def test_phase2_drops_duplicates_of_existing_codes() -> None:
    """If the LLM drafts a code that already exists in the taxonomy,
    Phase 2 silently drops it and adds it to duplicates_of_existing."""
    payload = _good_llm_output(candidates=[
        {
            "code": "ECG-001",  # already in taxonomy
            "category": "ECG",
            "short_name": "LBBB",
            "definition": "Left bundle branch block",
            "evidence_type": "ecg",
            "applies_to": ["78492", "78452"],
            "source_quote": "LBBB present",
        },
        {
            "code": "BMI-NEW",
            "category": "BMI",
            "short_name": "BMI threshold",
            "definition": "BMI ≥ 35",
            "evidence_type": "demographic",
            "applies_to": ["78492"],
            "source_quote": "BMI ≥ 35",
        },
    ])
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492"],
        policy_text="...",
        existing_codes=["ECG-001"],  # Pretend ECG-001 already exists
        llm_caller=_fake_llm(payload),
    )
    codes = [c.code for c in result.candidates]
    assert "ECG-001" not in codes
    assert "BMI-NEW" in codes
    assert "ECG-001" in result.duplicates_of_existing


def test_phase2_prunes_invalid_cpts() -> None:
    """applies_to entries that aren't in the canonical CPT registry
    get dropped. Prevents the LLM from hallucinating CPT codes."""
    payload = _good_llm_output(candidates=[
        {
            "code": "X-NEW", "category": "X",
            "short_name": "X", "definition": "X",
            "evidence_type": "clinical_note",
            "applies_to": ["78492", "FAKE-CPT-999"],
            "source_quote": "X",
        },
    ])
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492"],
        policy_text="...",
        existing_codes=[],
        canonical_cpts=["78492", "78452"],
        llm_caller=_fake_llm(payload),
    )
    assert result.candidates[0].applies_to == ["78492"]
    optimise_stage = next(s for s in result.stages if s.name == "Phase 2: optimise")
    assert "X-NEW:FAKE-CPT-999" in (optimise_stage.detail or {}).get("pruned_cpts", [])


def test_phase2_normalizes_whitespace_and_sorts() -> None:
    """Definitions get .strip()'d and applies_to/payers get sorted
    so reproducibility is deterministic."""
    payload = _good_llm_output(candidates=[
        {
            "code": "A-NEW", "category": "X",
            "short_name": "  X  ", "definition": "  spaces  ",
            "evidence_type": "clinical_note",
            "applies_to": ["78492", "78452", "78492"],  # duplicate
            "payers": ["Aetna", "UnitedHealthcare", "Aetna"],
            "source_quote": "X",
        },
    ])
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78452", "78492"],
        policy_text="...", existing_codes=[],
        llm_caller=_fake_llm(payload),
    )
    c = result.candidates[0]
    assert c.short_name == "X"
    assert c.definition == "spaces"
    assert c.applies_to == ["78452", "78492"]  # sorted, deduped
    assert c.payers == ["Aetna", "UnitedHealthcare"]  # sorted, deduped


# ── Phase 3: design pass detects near-duplicate names ────────────────


def test_phase3_flags_near_duplicate_short_names() -> None:
    payload = _good_llm_output(candidates=[
        {"code": "A-1", "category": "X", "short_name": "BMI threshold",
         "definition": "X", "evidence_type": "demographic",
         "applies_to": ["78492"], "source_quote": "X"},
        {"code": "A-2", "category": "X", "short_name": "BMI Threshold",  # same lower
         "definition": "Y", "evidence_type": "demographic",
         "applies_to": ["78492"], "source_quote": "Y"},
    ])
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492"],
        policy_text="...", existing_codes=[],
        llm_caller=_fake_llm(payload),
    )
    design = next(s for s in result.stages if s.name == "Phase 3: design")
    assert design.status == "fallback"
    assert "near_duplicates" in (design.detail or {})


# ── Phase 5: coverage report ─────────────────────────────────────────


def test_phase5_reports_cpt_coverage_per_target() -> None:
    payload = _good_llm_output(candidates=[
        {"code": "A-1", "category": "X", "short_name": "X",
         "definition": "X", "evidence_type": "clinical_note",
         "applies_to": ["78492"], "source_quote": "X"},
        {"code": "A-2", "category": "X", "short_name": "Y",
         "definition": "Y", "evidence_type": "clinical_note",
         "applies_to": ["78492", "78452"], "source_quote": "Y"},
        {"code": "A-3", "category": "X", "short_name": "Z",
         "definition": "Z", "evidence_type": "clinical_note",
         "applies_to": ["78452"], "source_quote": "Z"},
    ])
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492", "78452"],
        policy_text="...", existing_codes=[],
        llm_caller=_fake_llm(payload),
    )
    report = next(s for s in result.stages if s.name == "Phase 5: report")
    coverage = (report.detail or {}).get("cpt_coverage")
    assert coverage == {"78492": 2, "78452": 2}


# ── Output: Python source generation ─────────────────────────────────


def test_to_python_source_includes_each_candidate() -> None:
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492"],
        policy_text="...", existing_codes=[],
        llm_caller=_fake_llm(_good_llm_output()),
    )
    src = result.to_python_source()
    assert "BMI-002-NEW" in src
    assert "Criterion(" in src
    assert "RequiredElement(" in src
    assert "AUTO-GENERATED" in src
    assert "review before merging" in src


def test_to_python_source_handles_no_required_elements() -> None:
    payload = _good_llm_output(candidates=[
        {"code": "X-NEW", "category": "X", "short_name": "X",
         "definition": "X", "evidence_type": "clinical_note",
         "applies_to": ["78492"], "source_quote": "X",
         "required_elements": []},
    ])
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492"],
        policy_text="...", existing_codes=[],
        llm_caller=_fake_llm(payload),
    )
    src = result.to_python_source()
    assert "X-NEW" in src
    # No empty required_elements=[] block
    assert "required_elements=[]" not in src


# ── Result serialization ─────────────────────────────────────────────


def test_result_round_trips_json() -> None:
    result = generate_taxonomy_candidates(
        payer="UHC", target_cpts=["78492"],
        policy_text="...", existing_codes=[],
        llm_caller=_fake_llm(_good_llm_output()),
    )
    d = result.to_dict()
    json.dumps(d, default=str)
    assert d["payer"] == "UHC"
    assert d["target_cpts"] == ["78492"]
    assert len(d["candidates"]) == 1


# ── Schema invariants ────────────────────────────────────────────────


def test_candidate_confidence_bounded() -> None:
    """LLM confidence must be in [0, 1] — schema enforces it."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CandidateCriterion(
            code="X", category="X", short_name="X",
            definition="X", evidence_type="clinical_note",
            confidence=1.5,
        )


def test_evidence_type_constrained_to_known_values() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CandidateCriterion(
            code="X", category="X", short_name="X",
            definition="X", evidence_type="not-a-real-type",  # type: ignore
        )
