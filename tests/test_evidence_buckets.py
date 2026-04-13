"""Tests for evidence_buckets.py — LVEF, BMI, HR, functional class extraction."""

from cardioauth.taxonomy.evidence_buckets import (
    _extract_lvef, _extract_bmi, _extract_hr_percent,
    _extract_ecg_findings, _extract_symptoms, _extract_functional_class,
    bucket_chart_evidence, validate_threshold,
)


# ── LVEF extraction ──

def test_lvef_single_value():
    chart = {"relevant_imaging": [{"type": "TTE", "result_summary": "LVEF 45%, normal"}]}
    lvef = _extract_lvef(chart)
    assert lvef is not None
    assert lvef.get("value") == 45


def test_lvef_range():
    chart = {"relevant_imaging": [{"type": "TTE", "result_summary": "LVEF 50-55%"}]}
    lvef = _extract_lvef(chart)
    assert lvef is not None
    assert lvef.get("value_low") == 50
    assert lvef.get("value_high") == 55


def test_lvef_ejection_fraction_phrasing():
    chart = {"relevant_imaging": [{"type": "TTE", "result_summary": "ejection fraction of 35"}]}
    lvef = _extract_lvef(chart)
    assert lvef is not None
    assert lvef.get("value") == 35


def test_lvef_missing_returns_none():
    chart = {"relevant_imaging": [{"type": "ECG", "result_summary": "Normal sinus"}]}
    assert _extract_lvef(chart) is None


# ── BMI extraction ──

def test_bmi_from_comorbidities():
    chart = {"comorbidities": ["Obesity (BMI 38)", "HTN"]}
    bmi = _extract_bmi(chart)
    assert bmi is not None
    assert bmi.get("value") == 38


def test_bmi_from_imaging():
    chart = {
        "comorbidities": [],
        "relevant_imaging": [{"result_summary": "Technically limited due to BMI 42"}],
    }
    bmi = _extract_bmi(chart)
    assert bmi is not None
    assert bmi.get("value") == 42


def test_bmi_decimal():
    chart = {"comorbidities": ["BMI 36.5"]}
    bmi = _extract_bmi(chart)
    assert bmi is not None
    assert bmi.get("value") == 36.5


# ── HR% from stress test ──

def test_hr_percent_from_stress_ett():
    chart = {"relevant_imaging": [{"type": "Exercise Treadmill Test",
                                   "result_summary": "Achieved 68% of maximum predicted HR"}]}
    hr = _extract_hr_percent(chart)
    assert hr is not None
    assert hr.get("value") == 68


def test_hr_percent_mphr_format():
    chart = {"relevant_imaging": [{"type": "Stress ETT",
                                   "result_summary": "72% MPHR achieved"}]}
    hr = _extract_hr_percent(chart)
    assert hr is not None
    assert hr.get("value") == 72


def test_hr_percent_ignores_non_stress_imaging():
    """Don't pull HR% from echocardiogram reports."""
    chart = {"relevant_imaging": [{"type": "TTE",
                                   "result_summary": "LVEF 55%"}]}
    assert _extract_hr_percent(chart) is None


# ── ECG findings ──

def test_ecg_findings_from_ecg_study():
    chart = {"relevant_imaging": [
        {"type": "12-lead ECG", "date": "2026-01-01", "result_summary": "LBBB"},
    ]}
    ecg = _extract_ecg_findings(chart)
    assert "LBBB" in ecg


def test_ecg_findings_ignores_echo():
    """Don't pull ECG findings from echocardiogram studies."""
    chart = {"relevant_imaging": [
        {"type": "TTE", "result_summary": "Normal wall motion"},
    ]}
    ecg = _extract_ecg_findings(chart)
    assert ecg == ""


# ── Functional class (NYHA / CCS / EHRA) ──

def test_nyha_class_detected():
    chart = {"comorbidities": ["NYHA Class III heart failure"]}
    fc = _extract_functional_class(chart)
    assert "nyha" in fc.lower()


def test_ccs_class_detected():
    chart = {"additional_notes": "CCS Class II angina documented"}
    fc = _extract_functional_class(chart)
    assert "ccs" in fc.lower()


def test_ehra_class_detected():
    chart = {"comorbidities": ["EHRA Class IIb symptoms"]}
    fc = _extract_functional_class(chart)
    assert "ehra" in fc.lower()


def test_functional_class_empty_when_none():
    chart = {"comorbidities": ["HTN"], "additional_notes": "No symptoms"}
    fc = _extract_functional_class(chart)
    assert fc == ""


# ── Symptom extraction ──

def test_symptoms_dyspnea():
    chart = {"comorbidities": ["Exertional dyspnea"]}
    syms = _extract_symptoms(chart)
    assert "dyspnea" in syms.lower()


def test_symptoms_chest_pain():
    chart = {"additional_notes": "Patient reports chest pain on exertion"}
    syms = _extract_symptoms(chart)
    assert "chest pain" in syms.lower()


def test_symptoms_not_triggered_by_diagnoses_only():
    chart = {"comorbidities": ["CAD", "HTN"], "additional_notes": ""}
    syms = _extract_symptoms(chart)
    assert syms == ""


# ── Full bucketing ──

def test_bucket_chart_evidence_returns_all_sections():
    chart = {
        "age": 67, "sex": "M",
        "diagnosis_codes": ["I25.10"],
        "relevant_imaging": [{"type": "TTE", "date": "2026-01-01",
                              "result_summary": "LVEF 45%"}],
        "relevant_labs": [{"name": "BNP", "value": "342", "unit": "pg/mL", "date": "2026-01-01"}],
        "relevant_medications": [{"name": "Metoprolol", "dose": "50mg BID"}],
        "comorbidities": ["CAD", "Obesity BMI 38"],
        "prior_treatments": ["PCI (2020)"],
        "additional_notes": "CCS Class III angina",
    }
    buckets = bucket_chart_evidence(chart)

    # All expected keys present
    for key in ("imaging", "lab", "ecg", "demographic", "medication",
                "clinical_note", "score", "diagnosis_codes"):
        assert key in buckets

    # LVEF extracted to score bucket
    assert buckets["score"]["lvef"] is not None

    # BMI extracted
    assert buckets["score"]["bmi"] is not None
    assert buckets["score"]["bmi"]["value"] == 38

    # Clinical note has functional_class + office_notes fields
    assert "functional_class" in buckets["clinical_note"]
    assert "office_notes" in buckets["clinical_note"]


# ── Deterministic threshold validation ──

def test_validate_lvef002_reduced():
    """LVEF-002 requires LVEF ≤ 40."""
    chart = {"relevant_imaging": [{"type": "TTE", "result_summary": "LVEF 30%"}]}
    buckets = bucket_chart_evidence(chart)
    result = validate_threshold("LVEF-002", buckets)
    assert result is not None
    assert result["met"] is True


def test_validate_lvef002_preserved():
    """LVEF 55% does NOT satisfy LVEF-002 (≤40 threshold)."""
    chart = {"relevant_imaging": [{"type": "TTE", "result_summary": "LVEF 55%"}]}
    buckets = bucket_chart_evidence(chart)
    result = validate_threshold("LVEF-002", buckets)
    assert result is not None
    assert result["met"] is False


def test_validate_bmi001_meets_threshold():
    chart = {"comorbidities": ["BMI 38"]}
    buckets = bucket_chart_evidence(chart)
    result = validate_threshold("BMI-001", buckets)
    assert result is not None
    assert result["met"] is True


def test_validate_bmi001_below_threshold():
    chart = {"comorbidities": ["BMI 28"]}
    buckets = bucket_chart_evidence(chart)
    result = validate_threshold("BMI-001", buckets)
    assert result is not None
    assert result["met"] is False


def test_validate_ecg001_lbbb_found():
    chart = {"relevant_imaging": [{"type": "12-lead ECG", "result_summary": "LBBB present"}]}
    buckets = bucket_chart_evidence(chart)
    result = validate_threshold("ECG-001", buckets)
    assert result is not None
    assert result["met"] is True


def test_validate_ecg001_no_lbbb():
    chart = {"relevant_imaging": [{"type": "12-lead ECG", "result_summary": "Normal sinus rhythm"}]}
    buckets = bucket_chart_evidence(chart)
    result = validate_threshold("ECG-001", buckets)
    assert result is not None
    assert result["met"] is False
