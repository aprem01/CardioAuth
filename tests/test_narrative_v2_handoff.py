"""Tests for the v2 ChartData → reasoner narrative handoff (Peter Cases 3/4).

The bug: build_clinical_narrative read only legacy v1 fields
(`comorbidities`, `prior_treatments`) and treated `ecg_findings` as a
string. With v2 extraction populating only `active_comorbidities`,
`current_symptoms`, `prior_stress_tests`, `past_medical_history`,
structured `ecg_findings` etc., the reasoner saw an empty narrative —
0 criteria met despite strong extraction.
"""

from __future__ import annotations

from cardioauth.case_context import CaseContext


def _ctx(chart: dict, raw_note: str = "") -> CaseContext:
    return CaseContext(
        case_id="TEST-1",
        procedure_code="78492",
        procedure_name="Cardiac PET",
        payer_name="UnitedHealthcare",
        raw_note=raw_note,
        chart_data=chart,
    )


def test_active_comorbidities_v2_surfaced() -> None:
    """v2 active_comorbidities must appear in the narrative."""
    ctx = _ctx({"active_comorbidities": ["HTN", "DM", "BMI 38"]})
    narrative = ctx.build_clinical_narrative()
    assert "HTN" in narrative
    assert "DM" in narrative
    assert "BMI 38" in narrative


def test_current_symptoms_v2_surfaced_with_modifiers() -> None:
    ctx = _ctx({
        "current_symptoms": [
            {
                "name": "dyspnea on exertion",
                "character": "exertional",
                "change_vs_baseline": "new or worsening",
                "onset": "3 weeks ago",
            }
        ],
    })
    narrative = ctx.build_clinical_narrative()
    assert "dyspnea on exertion" in narrative
    assert "new or worsening" in narrative
    assert "3 weeks" in narrative


def test_prior_stress_tests_surfaced() -> None:
    ctx = _ctx({
        "prior_stress_tests": [
            {"modality": "SPECT", "date": "2026-01-15",
             "interpretation": "non-diagnostic — attenuation artifact"}
        ],
    })
    narrative = ctx.build_clinical_narrative()
    assert "SPECT" in narrative
    assert "non-diagnostic" in narrative
    assert "attenuation" in narrative


def test_structured_ecg_findings_decoded() -> None:
    """ECG list-of-dicts (v2) must produce labelled lines, not Python repr."""
    ctx = _ctx({
        "ecg_findings": [
            {"rhythm": "sinus rhythm", "conduction": "LBBB",
             "ischemic_changes": "non-specific ST changes"}
        ],
    })
    narrative = ctx.build_clinical_narrative()
    assert "LBBB" in narrative
    assert "sinus rhythm" in narrative
    # Should NOT show the raw dict repr
    assert "{'rhythm'" not in narrative


def test_past_medical_history_v2_surfaced() -> None:
    ctx = _ctx({
        "past_medical_history": [
            {"condition": "MI", "date": "2021", "details": "STEMI, inferior wall"}
        ],
    })
    narrative = ctx.build_clinical_narrative()
    assert "MI" in narrative
    assert "2021" in narrative
    assert "STEMI" in narrative


def test_family_history_v2_surfaced() -> None:
    ctx = _ctx({
        "family_history": [
            {"relation": "father", "condition": "CAD", "age_at_event": "55"}
        ],
    })
    narrative = ctx.build_clinical_narrative()
    assert "father" in narrative
    assert "CAD" in narrative
    assert "55" in narrative


def test_exam_findings_v2_surfaced() -> None:
    ctx = _ctx({
        "exam_findings": [
            {"finding": "JVD to angle of jaw", "system": "cardiovascular"}
        ],
    })
    narrative = ctx.build_clinical_narrative()
    assert "JVD" in narrative


def test_prior_procedures_v2_surfaced() -> None:
    ctx = _ctx({
        "prior_procedures": [
            {"name": "PCI to LAD", "date": "2020", "outcome": "uncomplicated"}
        ],
    })
    narrative = ctx.build_clinical_narrative()
    assert "PCI to LAD" in narrative
    assert "uncomplicated" in narrative


def test_legacy_comorbidities_still_used_when_v2_empty() -> None:
    """Back-compat: if only legacy field present, use it."""
    ctx = _ctx({"comorbidities": ["HTN", "DM"]})
    narrative = ctx.build_clinical_narrative()
    assert "HTN" in narrative
    assert "DM" in narrative


def test_v2_takes_precedence_over_legacy() -> None:
    """When both are present, v2 wins (legacy is empty in real v2 charts)."""
    ctx = _ctx({
        "active_comorbidities": ["v2_HTN"],
        "comorbidities": ["legacy_HTN"],
    })
    narrative = ctx.build_clinical_narrative()
    assert "v2_HTN" in narrative
    # legacy should NOT also appear since v2 took precedence
    assert "legacy_HTN" not in narrative


def test_raw_note_preserved_alongside_structured() -> None:
    """When raw_note is supplied, narrative must contain BOTH the parsed
    buckets AND the original prose — Claude needs both signals.
    """
    ctx = _ctx(
        chart={
            "active_comorbidities": ["HTN"],
            "current_symptoms": [{"name": "dyspnea"}],
        },
        raw_note="67 yo F with PMH of MI 2019. ROS notable for new dyspnea.",
    )
    narrative = ctx.build_clinical_narrative()
    # Parsed bucket
    assert "HTN" in narrative
    assert "dyspnea" in narrative
    # Original prose preserved verbatim
    assert "MI 2019" in narrative
    assert "ROS notable" in narrative


def test_empty_chart_still_includes_procedure_request() -> None:
    ctx = _ctx({})
    narrative = ctx.build_clinical_narrative()
    assert "Cardiac PET" in narrative
    assert "78492" in narrative
    assert "UnitedHealthcare" in narrative
