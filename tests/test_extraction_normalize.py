"""Tests for real-world Claude JSON normalization.

Covers the shape variance we see when Claude extracts ChartData from a
clinical note: nested demographics, bare-string list items, "67 y/o" /
"Male" / "01/15/1958" / "NPI: 1234567890" value variance.
"""

from __future__ import annotations

from cardioauth.extraction_normalize import (
    normalize_age,
    normalize_claude_extraction,
    normalize_date,
    normalize_npi,
    normalize_sex,
)
from cardioauth.models.chart import ChartData


# ── Demographic normalizers ─────────────────────────────────────────────

def test_age_int_passes_through() -> None:
    assert normalize_age(67) == 67


def test_age_yo_suffix() -> None:
    assert normalize_age("67 y/o") == 67
    assert normalize_age("67yo") == 67
    assert normalize_age("67 years old") == 67


def test_age_word_form() -> None:
    assert normalize_age("sixty-seven") == 67
    assert normalize_age("seventy") == 70


def test_age_out_of_range_returns_none() -> None:
    assert normalize_age(0) is None
    assert normalize_age(200) is None
    assert normalize_age("garbage") is None


def test_sex_variants() -> None:
    assert normalize_sex("Male") == "M"
    assert normalize_sex("female") == "F"
    assert normalize_sex("F") == "F"
    assert normalize_sex("Other") == "Other"
    assert normalize_sex("") == ""
    assert normalize_sex("unknown") == ""


def test_date_formats() -> None:
    assert normalize_date("1958-01-15") == "1958-01-15"
    assert normalize_date("01/15/1958") == "1958-01-15"
    assert normalize_date("January 15, 1958") == "1958-01-15"
    # Unrecognized — preserved so UI can flag
    assert normalize_date("a while back") == "a while back"


def test_npi_strip_prefix() -> None:
    assert normalize_npi("NPI: 1234567890") == "1234567890"
    assert normalize_npi("1234567890") == "1234567890"
    # Wrong length — preserved as-is so it classifies as incomplete
    assert normalize_npi("123") == "123"


# ── Nested flattening ───────────────────────────────────────────────────

def test_flatten_nested_patient_block() -> None:
    data = {
        "procedure_code": "78492",
        "patient": {
            "name": "Jane Synthetic",
            "dob": "01/15/1958",
            "age": "67 y/o",
            "sex": "Female",
        },
    }
    out = normalize_claude_extraction(data)
    assert out["patient_name"] == "Jane Synthetic"
    assert out["date_of_birth"] == "1958-01-15"
    assert out["age"] == 67
    assert out["sex"] == "F"


def test_flatten_nested_provider_block() -> None:
    data = {
        "procedure_code": "78492",
        "provider": {"name": "Dr. John Doe", "npi": "NPI: 1234567890"},
    }
    out = normalize_claude_extraction(data)
    assert out["attending_physician"] == "Dr. John Doe"
    assert out["attending_npi"] == "1234567890"


def test_top_level_wins_over_nested() -> None:
    data = {
        "procedure_code": "78492",
        "patient_name": "Top Level",
        "patient": {"name": "Nested Level"},
    }
    out = normalize_claude_extraction(data)
    assert out["patient_name"] == "Top Level"


# ── List-item coercion ──────────────────────────────────────────────────

def test_symptoms_as_bare_strings_coerce() -> None:
    data = {
        "procedure_code": "78492",
        "current_symptoms": ["dyspnea", "angina"],
    }
    out = normalize_claude_extraction(data)
    assert out["current_symptoms"] == [
        {"name": "dyspnea"}, {"name": "angina"},
    ]


def test_family_history_as_bare_strings_coerce() -> None:
    data = {
        "procedure_code": "78492",
        "family_history": ["father with CAD at 55"],
    }
    out = normalize_claude_extraction(data)
    assert out["family_history"][0]["condition"] == "father with CAD at 55"


def test_mixed_list_shapes_preserved() -> None:
    data = {
        "procedure_code": "78492",
        "current_symptoms": [
            "dyspnea",
            {"name": "angina", "character": "typical"},
        ],
    }
    out = normalize_claude_extraction(data)
    assert out["current_symptoms"] == [
        {"name": "dyspnea"},
        {"name": "angina", "character": "typical"},
    ]


def test_comorbidities_coerce_from_dicts() -> None:
    data = {
        "procedure_code": "78492",
        "active_comorbidities": [{"name": "HTN"}, {"condition": "DM"}, "CKD"],
    }
    out = normalize_claude_extraction(data)
    assert out["active_comorbidities"] == ["HTN", "DM", "CKD"]


def test_icd_prefix_stripped() -> None:
    data = {
        "procedure_code": "78492",
        "diagnosis_codes": ["ICD-10: I25.10", "icd10:E11.9", "I10"],
    }
    out = normalize_claude_extraction(data)
    assert out["diagnosis_codes"] == ["I25.10", "E11.9", "I10"]


# ── End-to-end: normalized dict validates as ChartData ──────────────────

def test_normalized_output_builds_chartdata() -> None:
    """Normalizer output must be a valid ChartData payload."""
    data = {
        "patient_id": "TEST-1",
        "procedure_code": "78492",
        "procedure_requested": "Cardiac PET",
        "patient": {
            "name": "Jane Synthetic",
            "dob": "01/15/1958",
            "age": "67 y/o",
            "sex": "Female",
        },
        "provider": {"name": "Dr. John Doe", "npi": "NPI: 1234567890"},
        "current_symptoms": ["dyspnea on exertion"],
        "active_comorbidities": ["HTN", {"name": "DM"}],
        "family_history": ["mother with MI at 60"],
        "diagnosis_codes": ["ICD-10: I25.10"],
    }
    out = normalize_claude_extraction(data)
    # This is the critical assertion — would have failed pre-hardening.
    chart = ChartData(**{k: v for k, v in out.items() if k in ChartData.model_fields})
    assert chart.patient_name == "Jane Synthetic"
    assert chart.age == 67
    assert chart.sex == "F"
    assert chart.attending_npi == "1234567890"
    assert chart.current_symptoms[0].name == "dyspnea on exertion"
    assert chart.active_comorbidities == ["HTN", "DM"]
    assert chart.diagnosis_codes == ["I25.10"]


def test_non_dict_input_returns_empty() -> None:
    assert normalize_claude_extraction("garbage") == {}
    assert normalize_claude_extraction(None) == {}
    assert normalize_claude_extraction([]) == {}
