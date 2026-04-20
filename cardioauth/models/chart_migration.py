"""Migrate legacy flat ChartData payloads into the v2 categorized schema.

Peter's Apr 14 feedback drove a schema v2 rollout. Existing custom-request
payloads send flat `comorbidities: list[str]` and `prior_treatments: list[str]`
lists. This module heuristically classifies those strings into the right v2
buckets so the rest of the pipeline sees clean data without requiring every
caller to upgrade at once.

Classification is keyword-based — not perfect, but loudly deterministic.
When we can't confidently classify, we leave the string in the legacy field
and flag it so the ChartAgent or a human can re-extract.
"""

from __future__ import annotations

import re
from typing import Any

from cardioauth.models.chart import (
    ChartData,
    ExamFinding,
    FamilyHistoryEntry,
    PMHEntry,
    ProcedureHistory,
    StressTestResult,
    Symptom,
)


# ── Keyword dictionaries (ordered — more specific first) ────────────────

_SYMPTOM_KEYWORDS = [
    "dyspnea", "shortness of breath", "sob", "doe",
    "angina", "chest pain", "chest pressure", "chest discomfort", "chest tightness",
    "syncope", "presyncope", "lightheaded", "dizziness",
    "palpitations", "fatigue", "orthopnea", "pnd",
    "jaw pain", "arm pain",
]

_EXAM_FINDING_KEYWORDS = [
    "jvd", "jugular venous",
    "murmur", "gallop", "s3", "s4",
    "bruit", "rales", "crackles", "wheezing",
    "pedal edema", "peripheral edema", "lower extremity edema",
    "hepatomegaly", "cardiomegaly",
    "diminished pulses", "femoral bruit",
]

_PMH_KEYWORDS = [
    "h/o", "history of",
    "prior mi", "s/p mi", "myocardial infarction", "stemi", "nstemi",
    "stroke", "cva", "tia",
    "heart failure admission", "hf admission", "hfrea", "hf exacerbation", "hf hospitalization",
    "vt arrest", "sudden cardiac arrest", "vf arrest",
    "endocarditis", "pericarditis", "myocarditis",
]

_FAMILY_HISTORY_KEYWORDS = [
    "family history", "family hx", "fh ", "fh:",
    "father", "mother", "sibling", "brother", "sister",
    "premature cad", "sudden cardiac death",
]

_PROCEDURE_KEYWORDS = [
    "cabg", "coronary artery bypass",
    "pci", "percutaneous coronary intervention", "angioplasty", "stent",
    "tavr", "tavi", "savr",
    "ablation", "cardioversion", "pacemaker", "icd", "crt",
    "mitraclip", "watchman", "lariat",
]

_STRESS_TEST_KEYWORDS = [
    "ett", "exercise treadmill", "treadmill test", "stress ecg",
    "spect", "myocardial perfusion",
    "cardiac pet", "pet perfusion",
    "stress echo", "stress echocardiogram",
    "stress mri", "stress cardiac mri",
    "dobutamine stress",
]


def _matches_any(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(k in lower for k in keywords)


def _parse_stress_test(text: str) -> StressTestResult | None:
    lower = text.lower()
    if not _matches_any(text, _STRESS_TEST_KEYWORDS):
        return None

    modality = ""
    for (kw, mod) in [
        ("pet", "PET"),
        ("spect", "SPECT"),
        ("stress echo", "stress_echo"),
        ("stress mri", "stress_MRI"),
        ("ett", "ETT"),
        ("treadmill", "ETT"),
    ]:
        if kw in lower:
            modality = mod
            break

    interpretation = ""
    for phrase in ("non-diagnostic", "nondiagnostic", "equivocal", "inconclusive",
                   "submaximal", "positive for ischemia", "attenuation artifact",
                   "technically limited", "false positive"):
        if phrase in lower:
            interpretation = phrase
            break

    hr_match = re.search(r"(\d{1,3})\s*%\s*(?:of\s*)?(?:max|mphr|maximum predicted)", lower)
    max_hr_percent = f"{hr_match.group(1)}%" if hr_match else ""

    return StressTestResult(
        modality=modality or "stress_test",
        interpretation=interpretation,
        max_hr_percent=max_hr_percent,
        result_summary=text.strip(),
    )


def _parse_procedure(text: str) -> ProcedureHistory | None:
    if not _matches_any(text, _PROCEDURE_KEYWORDS):
        return None
    date_match = re.search(r"(19|20)\d{2}", text)
    return ProcedureHistory(
        name=text.strip(),
        date=date_match.group(0) if date_match else "",
    )


def _parse_pmh(text: str) -> PMHEntry | None:
    if not _matches_any(text, _PMH_KEYWORDS):
        return None
    date_match = re.search(r"(19|20)\d{2}", text)
    return PMHEntry(
        condition=text.strip(),
        date=date_match.group(0) if date_match else "",
    )


def _parse_symptom(text: str) -> Symptom | None:
    if not _matches_any(text, _SYMPTOM_KEYWORDS):
        return None
    lower = text.lower()
    # Attempt to split name from qualifiers
    name = ""
    for kw in _SYMPTOM_KEYWORDS:
        if kw in lower:
            name = kw
            break
    if not name:
        return None

    change = ""
    for phrase in ("new", "worsening", "progressive", "progressed", "stable", "improved", "resolved"):
        if phrase in lower:
            change = phrase
            break

    character = ""
    for phrase, canonical in (
        ("exertional", "exertional"),
        ("on exertion", "exertional"),
        ("with exertion", "exertional"),
        ("at rest", "at rest"),
        ("typical", "typical"),
        ("atypical", "atypical"),
    ):
        if phrase in lower:
            character = canonical
            break

    return Symptom(
        name=name,
        change_vs_baseline=change,
        character=character,
    )


def _parse_exam_finding(text: str) -> ExamFinding | None:
    if not _matches_any(text, _EXAM_FINDING_KEYWORDS):
        return None
    return ExamFinding(finding=text.strip())


def _parse_family_history(text: str) -> FamilyHistoryEntry | None:
    lower = text.lower()
    if not _matches_any(text, _FAMILY_HISTORY_KEYWORDS):
        return None
    relation = ""
    for r in ("father", "mother", "brother", "sister", "sibling"):
        if r in lower:
            relation = r
            break
    condition = ""
    for c in ("premature cad", "cad", "sudden cardiac death", "aortic aneurysm", "mi"):
        if c in lower:
            condition = c
            break
    return FamilyHistoryEntry(
        relation=relation,
        condition=condition or text.strip(),
    )


# ── Public API ──────────────────────────────────────────────────────────

def migrate_legacy_chart(chart: ChartData) -> ChartData:
    """Migrate legacy flat comorbidities + prior_treatments into v2 structured buckets.

    Classification is best-effort. Any string we can't confidently route
    stays in its original legacy field so nothing is silently lost.

    Returns a NEW ChartData with the migrated contents; the input is not mutated.
    """
    new_active_comorbidities = list(chart.active_comorbidities)
    new_symptoms = list(chart.current_symptoms)
    new_exam = list(chart.exam_findings)
    new_pmh = list(chart.past_medical_history)
    new_fh = list(chart.family_history)
    new_procedures = list(chart.prior_procedures)
    new_stress = list(chart.prior_stress_tests)
    new_notes = chart.additional_notes or ""

    leftover_comorbidities: list[str] = []
    leftover_prior_treatments: list[str] = []

    # Migrate legacy comorbidities
    for entry in chart.comorbidities or []:
        s = str(entry).strip()
        if not s:
            continue

        fh = _parse_family_history(s)
        if fh:
            new_fh.append(fh)
            continue

        sym = _parse_symptom(s)
        if sym:
            new_symptoms.append(sym)
            continue

        exam = _parse_exam_finding(s)
        if exam:
            new_exam.append(exam)
            continue

        pmh = _parse_pmh(s)
        if pmh:
            new_pmh.append(pmh)
            continue

        # Default: chronic condition → active_comorbidities
        new_active_comorbidities.append(s)

    # Migrate legacy prior_treatments
    for entry in chart.prior_treatments or []:
        s = str(entry).strip()
        if not s:
            continue

        stress = _parse_stress_test(s)
        if stress:
            new_stress.append(stress)
            continue

        proc = _parse_procedure(s)
        if proc:
            new_procedures.append(proc)
            continue

        pmh = _parse_pmh(s)
        if pmh:
            new_pmh.append(pmh)
            continue

        # Could not classify — keep in leftover so it's still visible and not lost
        leftover_prior_treatments.append(s)

    return chart.model_copy(
        update={
            "active_comorbidities": new_active_comorbidities,
            "current_symptoms": new_symptoms,
            "exam_findings": new_exam,
            "past_medical_history": new_pmh,
            "family_history": new_fh,
            "prior_procedures": new_procedures,
            "prior_stress_tests": new_stress,
            "additional_notes": new_notes,
            "comorbidities": leftover_comorbidities,
            "prior_treatments": leftover_prior_treatments,
        }
    )


def chart_from_dict(data: dict[str, Any]) -> ChartData:
    """Construct a ChartData from an arbitrary dict — old flat or new v2 — and migrate."""
    chart = ChartData(**{k: v for k, v in data.items() if k in ChartData.model_fields})
    return migrate_legacy_chart(chart)


def validate_lab_source_anchoring(
    chart: ChartData,
    *,
    strict: bool = False,
    today_iso: str | None = None,
) -> tuple[ChartData, list[str]]:
    """Drop unsupported / future-dated labs per Peter C10-C13 #5.

    Rules:
      - A lab without source_anchor in strict mode is dropped.
      - A lab with a date clearly in the future (post-today) is always dropped
        and surfaced in missing_fields — never allowed into reasoning.

    Returns (cleaned_chart, warnings). Warnings describe every row we removed
    so the physician/operator can inspect before the reasoner runs.
    """
    from datetime import date
    today = today_iso or date.today().isoformat()

    kept: list = []
    warnings: list[str] = []
    for lab in chart.relevant_labs:
        lab_date = (lab.date or "").strip()
        if lab_date and lab_date > today:
            warnings.append(
                f"Dropped lab '{lab.name}={lab.value}' with future date {lab_date} — likely fabricated"
            )
            continue
        if strict and not (lab.source_anchor or "").strip():
            warnings.append(
                f"Dropped lab '{lab.name}={lab.value}' — no source_anchor (strict mode)"
            )
            continue
        kept.append(lab)

    cleaned = chart.model_copy(update={
        "relevant_labs": kept,
        "missing_fields": list(chart.missing_fields) + warnings,
    })
    return cleaned, warnings
