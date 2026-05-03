"""Tests for Agentic Generator 2: payer form from PDF.

Same scaling story as Generator 1 — applied to PayerForm authoring.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cardioauth.lean_form_generator import (
    CandidateFormField,
    CandidatePayerForm,
    FormGenResult,
    FormGeneratorOutput,
    generate_payer_form,
)


def _good_form_payload(**overrides) -> dict:
    base = {
        "schema_version": "form-gen-1.0",
        "generated_at": "2026-05-03T20:00:00+00:00",
        "candidate_form": {
            "payer": "UnitedHealthcare",
            "vendor": "Availity",
            "name": "Cardiac Imaging PA Worksheet",
            "applies_to_cpts": ["78492", "78452"],
            "submission_channel": "portal",
            "fields": [
                {
                    "key": "patient_name", "label": "Patient Name",
                    "category": "patient", "required": True,
                    "format": "text",
                    "populated_from": "chart_data.patient_name",
                    "source_quote": "Patient Name *",
                    "confidence": 0.98,
                },
                {
                    "key": "lbbb_documented", "label": "LBBB on baseline ECG",
                    "category": "clinical", "required": True,
                    "format": "checkbox",
                    "populated_from": "chart_data.ecg_findings",
                    "source_quote": "LBBB documented?",
                    "confidence": 0.92,
                },
                {
                    "key": "physician_signature", "label": "Physician signature",
                    "category": "attestation", "required": True,
                    "format": "text",
                    "populated_from": "manual_attestation",
                    "source_quote": "Physician signature *",
                    "confidence": 0.95,
                },
            ],
            "notes": "Standard UHC cardiac imaging worksheet, rev 2026.",
        },
        "requires_human_judgment": [],
        "notes": "",
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
    result = generate_payer_form(
        payer="UHC",
        form_pdf_text="Patient Name *\nDOB *\nLBBB documented?\nPhysician signature *",
        llm_caller=_fake_llm(_good_form_payload()),
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
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...", llm_caller=boom,
    )
    assert result.stages[0].status == "failed"
    assert len(result.stages) == 1
    assert result.candidate_form is None


# ── Phase 2 deterministic dedupe + path validation ──────────────────


def test_phase2_normalizes_invalid_populated_from_to_manual() -> None:
    """If the LLM hallucinates a path (e.g., 'chart_data.fictional'),
    Phase 2 normalizes it to manual_attestation rather than letting
    bad mapping reach production."""
    payload = _good_form_payload()
    payload["candidate_form"]["fields"][0]["populated_from"] = "chart_data.totally_fake_field"
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(payload),
    )
    f = result.candidate_form.fields[0]
    assert f.populated_from == "manual_attestation"
    p2 = next(s for s in result.stages if s.name == "Phase 2: optimise")
    assert (p2.detail or {}).get("invalid_paths"), "should record the normalization"


def test_phase2_drops_duplicate_keys() -> None:
    payload = _good_form_payload()
    payload["candidate_form"]["fields"].append({
        "key": "patient_name", "label": "Patient Name (dup)",
        "category": "patient", "required": True, "format": "text",
        "populated_from": "chart_data.patient_name",
        "source_quote": "Patient Name *",
    })
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(payload),
    )
    keys = [f.key for f in result.candidate_form.fields]
    # patient_name appears once — dedupe kept the first one
    assert keys.count("patient_name") == 1


def test_phase2_normalizes_options_whitespace() -> None:
    payload = _good_form_payload()
    payload["candidate_form"]["fields"][1]["options"] = ["  Yes  ", "", "  No"]
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(payload),
    )
    f = result.candidate_form.fields[1]
    assert f.options == ["Yes", "No"]


# ── Phase 3 design checks ─────────────────────────────────────────────


def test_phase3_flags_required_field_missing_path() -> None:
    payload = _good_form_payload()
    # Add a required field with no populated_from
    payload["candidate_form"]["fields"].append({
        "key": "novel_field", "label": "Novel field",
        "category": "clinical", "required": True, "format": "text",
        "populated_from": "",
        "source_quote": "Novel *",
    })
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(payload),
    )
    p3 = next(s for s in result.stages if s.name == "Phase 3: design")
    assert p3.status == "fallback"
    assert "novel_field" in (p3.detail or {}).get("required_without_path", [])


def test_phase3_flags_near_duplicate_labels() -> None:
    payload = _good_form_payload()
    payload["candidate_form"]["fields"].append({
        "key": "patient_legal_name", "label": "patient name",  # case-insensitive dup
        "category": "patient", "required": True, "format": "text",
        "populated_from": "chart_data.patient_name",
        "source_quote": "patient name",
    })
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(payload),
    )
    p3 = next(s for s in result.stages if s.name == "Phase 3: design")
    near_dups = (p3.detail or {}).get("near_duplicate_labels", {})
    assert near_dups, "should detect near-duplicate labels"


# ── Phase 5 reports field-mapping coverage ─────────────────────────


def test_phase5_reports_path_kind_distribution() -> None:
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(_good_form_payload()),
    )
    p5 = next(s for s in result.stages if s.name == "Phase 5: report")
    by_path = (p5.detail or {}).get("by_path_kind", {})
    assert by_path.get("chart", 0) >= 2  # patient_name + lbbb_documented
    assert by_path.get("manual", 0) >= 1  # physician_signature


def test_phase5_reports_category_distribution() -> None:
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(_good_form_payload()),
    )
    p5 = next(s for s in result.stages if s.name == "Phase 5: report")
    by_cat = (p5.detail or {}).get("by_category", {})
    assert by_cat.get("patient", 0) >= 1
    assert by_cat.get("clinical", 0) >= 1
    assert by_cat.get("attestation", 0) >= 1


# ── Source generation ─────────────────────────────────────────────────


def test_to_python_source_includes_each_field() -> None:
    result = generate_payer_form(
        payer="UnitedHealthcare", form_pdf_text="...",
        llm_caller=_fake_llm(_good_form_payload()),
    )
    src = result.to_python_source()
    assert "patient_name" in src
    assert "lbbb_documented" in src
    assert "physician_signature" in src
    assert "PayerForm(" in src
    assert "AUTO-GENERATED" in src


def test_to_python_source_omits_populated_from_for_manual() -> None:
    """A manual-attestation field should NOT carry populated_from in
    the emitted source (matches existing payer_forms.py convention)."""
    result = generate_payer_form(
        payer="UnitedHealthcare", form_pdf_text="...",
        llm_caller=_fake_llm(_good_form_payload()),
    )
    src = result.to_python_source()
    # Find the physician_signature line
    sig_line = next(
        l for l in src.splitlines() if "physician_signature" in l
    )
    assert "populated_from" not in sig_line


# ── Result serialization ─────────────────────────────────────────────


def test_result_round_trips_json() -> None:
    result = generate_payer_form(
        payer="UHC", form_pdf_text="...",
        llm_caller=_fake_llm(_good_form_payload()),
    )
    d = result.to_dict()
    json.dumps(d, default=str)
    assert d["payer"] == "UHC"
    assert d["candidate_form"]["payer"] == "UnitedHealthcare"


# ── Schema invariants ────────────────────────────────────────────────


def test_field_format_constrained() -> None:
    with pytest.raises(ValidationError):
        CandidateFormField(
            key="x", label="X", category="patient",
            format="not-a-real-format",  # type: ignore
        )


def test_field_category_constrained() -> None:
    with pytest.raises(ValidationError):
        CandidateFormField(
            key="x", label="X",
            category="random",  # type: ignore
            format="text",
        )


def test_confidence_bounded() -> None:
    with pytest.raises(ValidationError):
        CandidateFormField(
            key="x", label="X", category="patient", format="text",
            confidence=1.5,
        )
