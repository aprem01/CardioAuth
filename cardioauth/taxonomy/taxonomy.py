"""Fixed clinical criterion taxonomy — versioned and stable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


TAXONOMY_VERSION = "1.0.0"


@dataclass
class Criterion:
    """A single coded clinical criterion."""
    code: str
    category: str
    short_name: str
    definition: str
    evidence_type: Literal["lab", "imaging", "clinical_note", "score", "medication", "ecg", "demographic"]
    applies_to: list[str] = field(default_factory=list)  # CPT codes
    payers: list[str] = field(default_factory=list)  # Empty = all payers
    guideline_source: str = ""
    severity: Literal["required", "supporting"] = "required"
    introduced_version: str = "1.0.0"


# ────────────────────────────────────────────────────────────────────────
# Categories
# ────────────────────────────────────────────────────────────────────────
CATEGORIES = {
    "NDX": "Non-diagnostic / equivocal prior testing",
    "SX":  "Symptom characterization and timeline",
    "MED": "Failed or maximal medical therapy",
    "BMI": "Body habitus / BMI considerations",
    "ECG": "Baseline ECG abnormalities",
    "LVEF": "Left ventricular ejection fraction",
    "RISK": "Cardiovascular risk and surgical risk",
    "HT":  "Heart Team / multidisciplinary evaluation",
    "ANTI": "Anticoagulation and stroke risk",
    "IMG": "Prior imaging timing and frequency",
    "FREQ": "Procedure frequency limits",
    "EX":  "Exercise capacity / functional limitation",
    "GUI": "Guideline appropriate use criteria",
    "DOC": "Documentation completeness",
    "DEM": "Demographic and eligibility",
}


# ────────────────────────────────────────────────────────────────────────
# Criterion library
# Each criterion is a stable, coded clinical fact the system tracks across
# cases. Adding new criteria is done by appending — never editing in place.
# ────────────────────────────────────────────────────────────────────────
CRITERION_TAXONOMY: dict[str, Criterion] = {

    # ── Non-diagnostic / equivocal prior testing (NDX) ──
    "NDX-001": Criterion(
        code="NDX-001",
        category="NDX",
        short_name="Non-diagnostic prior stress test documented",
        definition="Prior stress test (ETT, SPECT, stress echo) was formally classified as non-diagnostic, equivocal, or technically limited with the limitation explicitly stated by the interpreting physician.",
        evidence_type="imaging",
        applies_to=["78492", "78491", "78452", "78451", "75557", "75574"],
        guideline_source="ACC/ASNC AUC for Cardiac Radionuclide Imaging",
        severity="required",
    ),
    "NDX-002": Criterion(
        code="NDX-002",
        category="NDX",
        short_name="Submaximal exercise stress test (HR <85% MPHR)",
        definition="Exercise stress test achieved less than 85% of maximum predicted heart rate, rendering the test non-diagnostic for ischemia.",
        evidence_type="imaging",
        applies_to=["78492", "78452", "78451"],
        guideline_source="ACC/AHA Stress Testing Guidelines",
        severity="supporting",
    ),
    "NDX-003": Criterion(
        code="NDX-003",
        category="NDX",
        short_name="Equivocal ST changes on prior ETT",
        definition="Prior exercise treadmill test showed non-specific or borderline ST-segment changes that do not definitively confirm or exclude ischemia.",
        evidence_type="imaging",
        applies_to=["78492", "78452"],
        guideline_source="ACC/AHA Stress Testing Guidelines",
        severity="supporting",
    ),
    "NDX-004": Criterion(
        code="NDX-004",
        category="NDX",
        short_name="Technically limited prior echocardiogram",
        definition="Prior echocardiogram (TTE or stress echo) documented as technically limited due to body habitus, COPD, or other patient factors with suboptimal acoustic windows.",
        evidence_type="imaging",
        applies_to=["78492", "78491", "75557", "75574"],
        guideline_source="ACC AUC for Echocardiography",
        severity="supporting",
    ),

    # ── Symptom characterization and timeline (SX) ──
    "SX-001": Criterion(
        code="SX-001",
        category="SX",
        short_name="New or worsening symptoms since baseline",
        definition="Chart documents a clear change in symptoms (new onset, worsening severity, change in character, or change in exertional threshold) compared to baseline or prior study. Critical for repeat or follow-up imaging.",
        evidence_type="clinical_note",
        applies_to=["78492", "78491", "78452", "78451", "75557", "75574", "93306", "93350"],
        guideline_source="Multi-payer common requirement; ACC AUC",
        severity="required",
    ),
    "SX-002": Criterion(
        code="SX-002",
        category="SX",
        short_name="Specific symptom timeline documented",
        definition="Chart includes specific onset date, progression timeline, frequency, severity, and character of cardiac symptoms.",
        evidence_type="clinical_note",
        applies_to=["78492", "78452", "93458", "93656"],
        guideline_source="Payer documentation standard",
        severity="required",
    ),
    "SX-003": Criterion(
        code="SX-003",
        category="SX",
        short_name="Anginal equivalent or chest pain documented",
        definition="Chest pain, dyspnea, or other anginal equivalent clearly documented with characterization (typical/atypical, exertional/rest, duration).",
        evidence_type="clinical_note",
        applies_to=["78492", "78452", "93458"],
        guideline_source="ACC/AHA CCS Guidelines",
        severity="required",
    ),
    "SX-004": Criterion(
        code="SX-004",
        category="SX",
        short_name="Symptom burden quantified (NYHA / CCS / EHRA class)",
        definition="Functional class documented using validated scale (NYHA for HF, CCS for angina, EHRA for AF symptoms). For cardiac imaging, CCS angina class or equivalent symptom severity grading should be documented.",
        evidence_type="clinical_note",
        applies_to=["78492", "78452", "33361", "93656", "93458"],
        guideline_source="ACC/AHA Clinical Practice Guidelines",
        severity="supporting",
    ),

    # ── Failed or maximal medical therapy (MED) ──
    "MED-001": Criterion(
        code="MED-001",
        category="MED",
        short_name="Failed maximally tolerated medical therapy",
        definition="Patient has documented trial of guideline-directed medical therapy at maximally tolerated doses with persistent symptoms or progression.",
        evidence_type="medication",
        applies_to=["93458", "92928", "33361"],
        guideline_source="ACC/AHA CCS / HF / Valvular Guidelines",
        severity="required",
    ),
    "MED-002": Criterion(
        code="MED-002",
        category="MED",
        short_name="Medical therapy duration ≥ 6 weeks documented",
        definition="Current medical regimen documented at minimum 6 weeks duration with specific medication names, doses, and start dates.",
        evidence_type="medication",
        applies_to=["93458", "78492"],
        guideline_source="UnitedHealthcare Commercial Medical Policy",
        severity="supporting",
    ),
    "MED-003": Criterion(
        code="MED-003",
        category="MED",
        short_name="Failed Class I or III antiarrhythmic drug",
        definition="Trial and failure or intolerance of at least one Class I (flecainide, propafenone) or Class III (amiodarone, sotalol, dofetilide, dronedarone) antiarrhythmic drug for AF, with specific reason for failure documented.",
        evidence_type="medication",
        applies_to=["93656"],
        guideline_source="ACC/AHA/HRS AF Guidelines; HRS ablation criteria",
        severity="required",
    ),

    # ── BMI / body habitus (BMI) ──
    "BMI-001": Criterion(
        code="BMI-001",
        category="BMI",
        short_name="BMI ≥ 35 documented (PET-over-SPECT justification)",
        definition="Body Mass Index of 35 kg/m² or greater explicitly documented, supporting PET over SPECT due to soft-tissue attenuation artifact risk.",
        evidence_type="demographic",
        applies_to=["78492", "78491"],
        guideline_source="ACC/ASNC AUC for Cardiac Radionuclide Imaging",
        severity="required",
    ),
    "BMI-002": Criterion(
        code="BMI-002",
        category="BMI",
        short_name="Documented attenuation artifact on prior SPECT",
        definition="Prior SPECT report explicitly notes soft-tissue, breast, or diaphragmatic attenuation artifact limiting interpretation.",
        evidence_type="imaging",
        applies_to=["78492"],
        guideline_source="Payer-specific PET justification",
        severity="supporting",
    ),

    # ── ECG findings (ECG) ──
    "ECG-001": Criterion(
        code="ECG-001",
        category="ECG",
        short_name="LBBB precluding standard stress ECG interpretation",
        definition="Baseline left bundle branch block documented on ECG, which precludes accurate ST-segment interpretation during exercise stress testing and supports nuclear or pharmacologic imaging.",
        evidence_type="ecg",
        applies_to=["78492", "78452", "78451"],
        guideline_source="ACC/AHA Stress Testing Guidelines",
        severity="required",
    ),
    "ECG-002": Criterion(
        code="ECG-002",
        category="ECG",
        short_name="Paced rhythm precluding standard stress ECG",
        definition="Ventricular pacing on baseline ECG, precluding ST-segment interpretation and supporting alternative imaging.",
        evidence_type="ecg",
        applies_to=["78492", "78452"],
        guideline_source="ACC/AHA Stress Testing Guidelines",
        severity="required",
    ),
    "ECG-003": Criterion(
        code="ECG-003",
        category="ECG",
        short_name="WPW or pre-excitation precluding stress ECG",
        definition="Wolff-Parkinson-White syndrome or other pre-excitation pattern on baseline ECG.",
        evidence_type="ecg",
        applies_to=["78492", "78452"],
        guideline_source="ACC/AHA Stress Testing Guidelines",
        severity="supporting",
    ),
    "ECG-004": Criterion(
        code="ECG-004",
        category="ECG",
        short_name="Severe LVH with strain pattern",
        definition="Severe left ventricular hypertrophy with repolarization abnormalities (strain pattern) limiting exercise stress ECG interpretation.",
        evidence_type="ecg",
        applies_to=["78492", "78452"],
        guideline_source="ACC/AHA Stress Testing Guidelines",
        severity="supporting",
    ),

    # ── LVEF (LVEF) ──
    "LVEF-001": Criterion(
        code="LVEF-001",
        category="LVEF",
        short_name="LVEF documented within 90 days",
        definition="Left ventricular ejection fraction measured and documented from echocardiogram, MRI, nuclear imaging, or angiography within 90 days of request.",
        evidence_type="imaging",
        applies_to=["93458", "33361", "92928"],
        guideline_source="UnitedHealthcare Commercial Medical Policy",
        severity="required",
    ),
    "LVEF-002": Criterion(
        code="LVEF-002",
        category="LVEF",
        short_name="LVEF ≤ 40% (reduced)",
        definition="Reduced ejection fraction documented (LVEF ≤ 40%), supporting medical necessity for advanced imaging or intervention.",
        evidence_type="imaging",
        applies_to=["93458", "33361", "78492"],
        guideline_source="ACC/AHA HF Guidelines",
        severity="supporting",
    ),

    # ── Cardiovascular and surgical risk (RISK) ──
    "RISK-001": Criterion(
        code="RISK-001",
        category="RISK",
        short_name="STS-PROM score documented (TAVR)",
        definition="Society of Thoracic Surgeons Predicted Risk of Mortality calculated and documented for TAVR candidates.",
        evidence_type="score",
        applies_to=["33361"],
        guideline_source="ACC/AHA Valvular Heart Disease Guidelines; CMS NCD 20.32",
        severity="required",
    ),
    "RISK-002": Criterion(
        code="RISK-002",
        category="RISK",
        short_name="Cardiovascular risk factors enumerated",
        definition="Risk factor profile documented (DM, HTN, dyslipidemia, smoking, family history, prior CAD).",
        evidence_type="clinical_note",
        applies_to=["78492", "78452", "93458"],
        guideline_source="ACC/AHA CCS Guidelines",
        severity="supporting",
    ),
    "RISK-003": Criterion(
        code="RISK-003",
        category="RISK",
        short_name="Pre-test probability stratification documented",
        definition="Intermediate-to-high pre-test probability of CAD documented (Diamond-Forrester, CAD Consortium model, or equivalent).",
        evidence_type="score",
        applies_to=["78492", "78452"],
        guideline_source="ACC/AHA AUC; ESC Guidelines",
        severity="supporting",
    ),

    # ── Heart Team / multidisciplinary (HT) ──
    "HT-001": Criterion(
        code="HT-001",
        category="HT",
        short_name="Heart Team evaluation completed (TAVR)",
        definition="Multidisciplinary Heart Team note with cardiothoracic surgeon and interventional cardiologist, including TAVR vs SAVR recommendation.",
        evidence_type="clinical_note",
        applies_to=["33361"],
        guideline_source="CMS NCD 20.32; ACC/AHA Valvular Heart Disease Guidelines",
        severity="required",
    ),

    # ── Anticoagulation and stroke risk (ANTI) ──
    "ANTI-001": Criterion(
        code="ANTI-001",
        category="ANTI",
        short_name="CHA₂DS₂-VASc score calculated and documented",
        definition="Stroke risk score for AF patients calculated with anticoagulation plan documented.",
        evidence_type="score",
        applies_to=["93656"],
        guideline_source="ACC/AHA/HRS AF Guidelines",
        severity="required",
    ),
    "ANTI-002": Criterion(
        code="ANTI-002",
        category="ANTI",
        short_name="Pre-procedure TEE or LAA imaging (AF ablation)",
        definition="Transesophageal echocardiogram or cardiac CT documenting absence of left atrial appendage thrombus prior to AF ablation.",
        evidence_type="imaging",
        applies_to=["93656"],
        guideline_source="HRS Catheter Ablation Consensus Statement",
        severity="required",
    ),

    # ── Imaging timing and frequency (IMG / FREQ) ──
    "IMG-001": Criterion(
        code="IMG-001",
        category="IMG",
        short_name="Coronary anatomy assessment within 12 months (TAVR)",
        definition="Coronary angiography or coronary CTA within 12 months ruling out significant untreated CAD prior to TAVR.",
        evidence_type="imaging",
        applies_to=["33361"],
        guideline_source="ACC/AHA Valvular Heart Disease Guidelines",
        severity="required",
    ),
    "IMG-002": Criterion(
        code="IMG-002",
        category="IMG",
        short_name="Pre-procedural CTA for TAVR sizing",
        definition="CT angiography of aortic root and iliofemoral arteries with annular measurements completed prior to TAVR.",
        evidence_type="imaging",
        applies_to=["33361"],
        guideline_source="ACC/AHA Valvular Heart Disease Guidelines",
        severity="required",
    ),
    "FREQ-001": Criterion(
        code="FREQ-001",
        category="FREQ",
        short_name="No prior similar imaging within 12 months (or new symptoms)",
        definition="No identical or similar advanced cardiac imaging study within the prior 12 months, OR documented new clinical change justifying repeat.",
        evidence_type="clinical_note",
        applies_to=["78492", "78491", "78452", "78451", "75557", "75574"],
        guideline_source="Multi-payer frequency limit",
        severity="required",
    ),

    # ── Exercise capacity / functional limitation (EX) ──
    "EX-001": Criterion(
        code="EX-001",
        category="EX",
        short_name="Specific functional limitation preventing exercise",
        definition="Specific diagnosis or condition documented preventing adequate exercise stress testing (orthopedic, neurologic, pulmonary, vascular, dyspnea + obesity, etc.). Generic 'unable to exercise' without a stated reason is insufficient.",
        evidence_type="clinical_note",
        applies_to=["78492", "78491", "78452", "78451"],  # Pharmacologic PET + SPECT both
        guideline_source="Multi-payer pharmacologic stress requirement",
        severity="required",
    ),

    # ── Guideline appropriate use criteria (GUI) ──
    "GUI-001": Criterion(
        code="GUI-001",
        category="GUI",
        short_name="ACC Appropriate Use Criteria score: Appropriate",
        definition="ACC AUC score of 7-9 (Appropriate) documented for the requested study and indication.",
        evidence_type="score",
        applies_to=["78492", "78491", "78452", "78451", "75557", "75574"],
        guideline_source="ACC Appropriate Use Criteria",
        severity="supporting",
    ),

    # ── Documentation completeness (DOC) ──
    "DOC-001": Criterion(
        code="DOC-001",
        category="DOC",
        short_name="Cardiology consultation/office note attached",
        definition="Recent cardiology office note, consultation note, progress note, or H&P included with the request, documenting indication, history, physical exam, and clinical rationale. Evidence: look in clinical_note.office_notes and clinical_note.additional_notes for substantive clinical narrative.",
        evidence_type="clinical_note",
        applies_to=["78492", "78452", "93458", "33361", "93656"],
        guideline_source="Payer documentation standard",
        severity="required",
    ),
}


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
def get_criterion(code: str) -> Criterion | None:
    """Look up a single criterion by code."""
    return CRITERION_TAXONOMY.get(code)


def get_criteria_for_procedure(cpt_code: str, payer: str = "") -> list[Criterion]:
    """Return all criteria applicable to a procedure (and optionally a payer)."""
    results = []
    for c in CRITERION_TAXONOMY.values():
        if cpt_code not in c.applies_to:
            continue
        if c.payers and payer and payer not in c.payers:
            continue
        results.append(c)
    return results


def get_categories() -> dict[str, str]:
    """Return the category code → label mapping."""
    return CATEGORIES.copy()
