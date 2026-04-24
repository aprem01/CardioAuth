"""Data models for CHART_AGENT output.

Canonical ChartData v2 (Apr 14 refactor per Peter's feedback):

Previously the model had three over-broad buckets (comorbidities, relevant_imaging,
prior_treatments) that silently absorbed data that belonged elsewhere — symptoms
ended up in comorbidities, ECG in imaging, past clinical events in procedures,
family history with no home at all.

v2 splits those buckets explicitly. Claude now has named categories with clear
rules for each. Legacy fields are retained for backward compatibility — old
custom-request payloads continue to work — but ChartAgent now writes to the
new fields by default.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Diagnostic data ──────────────────────────────────────────────────────

class LabResult(BaseModel):
    """A single lab value.

    Apr 14 (Peter C10-C13 safety-critical): source_anchor is a verbatim
    snippet from the source document that supports this row. When present,
    it lets the pipeline verify the value isn't fabricated. When absent,
    the validator may drop or flag the row depending on strictness.
    """
    name: str
    value: str
    date: str = Field(description="ISO 8601 date")
    unit: str
    flag: str = ""
    source_anchor: str = Field(
        default="",
        description="Verbatim quote from source document (lab table row or note text) that anchors this lab value. Required for production extraction; empty is flagged as suspect.",
    )


class ImagingResult(BaseModel):
    """Imaging studies ONLY — echocardiogram, CT, MRI, angiography.

    NOT ECG. ECG is a dedicated field. See ECGFinding below.
    NOT stress tests. Stress tests are StressTestResult.
    """
    type: str
    date: str = Field(description="ISO 8601 date")
    result_summary: str
    ordering_provider: str = ""


class ECGFinding(BaseModel):
    """Baseline ECG finding — rhythm, conduction, ischemia, hypertrophy.

    Separate from ImagingResult because ECG criteria (LBBB, paced rhythm, WPW)
    must only reference this bucket — never a stress test or echo report.
    """
    rhythm: str = ""                    # e.g. "sinus rhythm", "atrial fibrillation"
    conduction: str = ""                # e.g. "LBBB", "RBBB", "first-degree AV block"
    hypertrophy_or_strain: str = ""     # e.g. "LVH with strain pattern"
    ischemic_changes: str = ""          # e.g. "inferior Q waves", "non-specific ST changes"
    pacing: str = ""                    # e.g. "ventricular paced", "A-V paced"
    date: str = ""                      # ISO 8601
    summary: str = ""                   # free-text overall impression if not decomposable


class StressTestResult(BaseModel):
    """Prior stress tests — ETT, SPECT, PET, stress echo.

    Separate from both ImagingResult and PMH so criteria like NDX-001
    (non-diagnostic prior stress test) can reference the exact test result.
    """
    modality: str                       # "ETT" / "SPECT" / "PET" / "stress_echo" / "stress_MRI"
    date: str = ""
    max_hr_percent: str = ""            # % MPHR if reported (e.g. "68%")
    interpretation: str = ""            # e.g. "non-diagnostic", "equivocal", "positive for ischemia"
    result_summary: str = ""


# ── Interventions / medications ──────────────────────────────────────────

class Medication(BaseModel):
    name: str
    dose: str
    start_date: str = Field(description="ISO 8601 date; or explicit duration string (e.g. 'since 2024-01')")
    indication: str = ""


class ProcedureHistory(BaseModel):
    """Procedures performed ON the patient — angioplasty, CABG, TAVR, ablation.

    Distinct from StressTestResult (tests) and PMHEntry (events / diagnoses).
    """
    name: str                           # "PCI to LAD", "CABG x 3", "TAVR", "AF ablation"
    date: str = ""
    indication: str = ""
    outcome: str = ""                   # e.g. "uncomplicated", "complicated by AKI"


# ── Clinical context ────────────────────────────────────────────────────

class Symptom(BaseModel):
    """Patient-reported or clinician-observed symptom with timeline.

    NOT a comorbidity. Dyspnea is a symptom; COPD is a comorbidity. A patient
    can have both. Criteria like SX-001 (new or worsening) and SX-002 (timeline)
    read from this bucket.
    """
    name: str                           # "dyspnea", "angina", "syncope", "palpitations"
    onset: str = ""                     # "3 weeks ago", "since 2026-02", "acute"
    frequency: str = ""                 # "daily", "with every flight of stairs", "episodic"
    character: str = ""                 # "typical", "atypical", "exertional", "rest"
    severity: str = ""                  # "mild", "moderate", "severe", or NYHA/CCS class
    change_vs_baseline: str = ""        # "new", "worsening", "stable", "no change"


class ExamFinding(BaseModel):
    """Physical exam finding — JVD, edema, murmur, bruit, rales.

    NOT a symptom (what the patient reports) and NOT a comorbidity.
    """
    finding: str                        # "JVD to the angle of jaw", "2+ pedal edema"
    system: str = ""                    # "cardiovascular", "pulmonary", "vascular"


class PMHEntry(BaseModel):
    """Past medical history — prior clinical events and resolved conditions.

    MI, stroke, heart failure exacerbation, VT arrest, etc. Events that
    happened with a date. Distinct from active_comorbidities (chronic
    ongoing conditions) and ProcedureHistory (interventions).
    """
    condition: str                      # "Myocardial infarction", "Ischemic stroke", "HF hospitalization"
    date: str = ""                      # ISO 8601 or year
    details: str = ""                   # "STEMI, inferior wall"


class FamilyHistoryEntry(BaseModel):
    """Family history — first-degree relative conditions relevant to cardiac risk.

    NOT a comorbidity (those are the patient's own conditions).
    """
    relation: str                       # "father", "mother", "sibling"
    condition: str                      # "CAD", "sudden cardiac death", "aortic aneurysm"
    age_at_event: str = ""              # premature CAD < 55 M / < 65 F matters for risk


# ── Top-level chart data (v2) ───────────────────────────────────────────

class ChartData(BaseModel):
    """Canonical patient clinical context extracted from the chart.

    v2 (Apr 14): explicit category separation. Every bucket has one and only
    one kind of data. See field docstrings for what belongs where.
    """

    # Identity + encounter
    patient_id: str
    procedure_requested: str
    procedure_code: str = Field(description="CPT code")

    # Patient demographics (Apr 22 — Peter feedback). Extraction was thin on
    # these because they weren't model fields. Adding as optional-with-default
    # so back-compat is preserved and the payer form's populated_from paths
    # actually resolve.
    patient_name: str = ""
    date_of_birth: str = Field(default="", description="ISO 8601 or MM/DD/YYYY")
    age: int | None = None
    sex: str = ""                       # "M" | "F" | "Other" | ""

    # Provider
    attending_physician: str = ""
    attending_npi: str = Field(default="", description="10-digit NPI")

    # Insurance
    insurance_id: str = ""
    payer_name: str = ""
    # Medicare-specific fields (relevant when payer_name is "Medicare")
    mac_jurisdiction: str = Field(default="", description="Medicare Administrative Contractor region")
    secondary_payer: str = Field(default="", description="Secondary commercial payer name if any")
    abn_signed: bool = Field(default=False, description="Advance Beneficiary Notice signed")

    # Diagnoses
    diagnosis_codes: list[str] = Field(default_factory=list, description="ICD-10 codes, primary first")

    # ── Clinical context (separated v2) ──
    active_comorbidities: list[str] = Field(
        default_factory=list,
        description="Chronic ongoing conditions ONLY — HTN, DM, CKD, COPD, CAD, HFrEF. NOT symptoms, NOT past events, NOT family history.",
    )
    past_medical_history: list[PMHEntry] = Field(
        default_factory=list,
        description="Prior clinical events with dates — MI 2021, CABG 2019, prior stroke. Not active chronic conditions.",
    )
    family_history: list[FamilyHistoryEntry] = Field(
        default_factory=list,
        description="First-degree relative cardiac conditions. Separate bucket so it doesn't pollute comorbidities.",
    )
    current_symptoms: list[Symptom] = Field(
        default_factory=list,
        description="Patient-reported or clinician-observed symptoms with timeline. NOT comorbidities.",
    )
    exam_findings: list[ExamFinding] = Field(
        default_factory=list,
        description="Physical exam findings. NOT symptoms, NOT comorbidities.",
    )

    # ── Diagnostic data (separated v2) ──
    relevant_labs: list[LabResult] = Field(default_factory=list)
    relevant_imaging: list[ImagingResult] = Field(
        default_factory=list,
        description="Echocardiogram, CT, MRI, angiography. NOT ECG (separate bucket). NOT stress tests (separate bucket).",
    )
    ecg_findings: list[ECGFinding] = Field(
        default_factory=list,
        description="Baseline ECG findings. Dedicated bucket — criteria like ECG-001 read only from here.",
    )
    prior_stress_tests: list[StressTestResult] = Field(
        default_factory=list,
        description="ETT, SPECT, PET, stress echo, stress MRI. Separate from imaging and procedures.",
    )

    # ── Interventions (separated v2) ──
    relevant_medications: list[Medication] = Field(default_factory=list)
    prior_procedures: list[ProcedureHistory] = Field(
        default_factory=list,
        description="Procedures performed ON the patient — PCI, CABG, TAVR, ablation. NOT tests, NOT events.",
    )

    # ── Free-form ──
    additional_notes: str = ""

    # ── Legacy flat fields (retained for back-compat) ──
    # Pre-v2 clients sent flat string lists. These are accepted on input and
    # migrated into the v2 structured fields on ingest — see
    # cardioauth.models.chart_migration.migrate_legacy_chart.
    # Leaving them on the model means old payloads validate; new code should
    # prefer the structured fields above.
    prior_treatments: list[str] = Field(
        default_factory=list,
        description="Legacy: migrated into prior_procedures + past_medical_history + prior_stress_tests.",
    )
    comorbidities: list[str] = Field(
        default_factory=list,
        description="Legacy: migrated into active_comorbidities + current_symptoms + exam_findings + family_history.",
    )

    # Quality
    confidence_score: float = Field(ge=0.0, le=1.0, description="Data completeness 0-1", default=0.0)
    missing_fields: list[str] = Field(default_factory=list)
