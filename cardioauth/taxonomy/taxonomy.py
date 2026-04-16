"""Fixed clinical criterion taxonomy — versioned and stable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


TAXONOMY_VERSION = "1.0.0"


@dataclass
class RequiredElement:
    """One atomic fact that must be documented for a criterion to be 'met'.

    Introduced after Peter's Apr 13 feedback: the reasoner was treating
    "feature present" as "criterion satisfied" when the definition actually
    required multiple elements (e.g., MED-002 needs med name AND start date
    AND ≥6-week duration). Breaking each criterion into explicit elements
    lets us enforce conjunctive completeness deterministically.
    """
    key: str                    # e.g. "trial_duration_gte_6wk"
    description: str            # Human-readable — what evidence satisfies it
    evidence_hint: str = ""     # Where to look in the note (free text)


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
    # All elements must be explicitly documented for the criterion to be 'met'.
    # Empty list = legacy behavior (single-feature check). Populate for any
    # criterion whose definition has multiple conjunctive requirements.
    required_elements: list[RequiredElement] = field(default_factory=list)


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
        required_elements=[
            RequiredElement(
                key="prior_stress_modality_named",
                description="Specific prior stress modality documented (ETT, SPECT, stress echo, stress MRI)",
                evidence_hint="Imaging list",
            ),
            RequiredElement(
                key="explicit_nondiagnostic_finding",
                description="Interpretation explicitly states 'non-diagnostic', 'equivocal', 'technically limited', 'attenuation artifact', 'suboptimal', or 'false positive'. A normal or abnormal result alone does NOT satisfy.",
                evidence_hint="Read the impression line of the stress test report",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="ett_performed",
                description="Exercise treadmill / bicycle stress test was attempted",
                evidence_hint="Stress test report",
            ),
            RequiredElement(
                key="hr_below_85_pct_mphr",
                description="Documented percent of maximum predicted heart rate < 85% (e.g. '68% MPHR', 'submaximal HR response'). A general 'poor exercise tolerance' does NOT satisfy.",
                evidence_hint="Numeric %MPHR in the stress report",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="prior_ett_done",
                description="Prior exercise treadmill test reported",
                evidence_hint="Stress test history",
            ),
            RequiredElement(
                key="equivocal_st_language",
                description="Impression uses 'equivocal', 'non-specific ST changes', 'borderline', or 'indeterminate ST segments'. A clearly positive or clearly negative ETT does NOT satisfy.",
                evidence_hint="ETT impression line",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="prior_echo_performed",
                description="Prior TTE or stress echocardiogram documented",
                evidence_hint="Imaging list",
            ),
            RequiredElement(
                key="technical_limitation_stated",
                description="Explicit language: 'technically limited', 'suboptimal acoustic windows', 'poor image quality', 'body habitus limited'. A normal echo does NOT satisfy.",
                evidence_hint="Echo report technical quality note",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="symptom_documented",
                description="A specific symptom (angina, dyspnea, syncope, etc.) is named",
                evidence_hint="Look in HPI / chief complaint / assessment",
            ),
            RequiredElement(
                key="change_vs_baseline",
                description="Explicit statement of change vs. prior state — 'new', 'worsening', 'progressed', 'increased frequency', or comparison to a prior specific state. 'No new complaints' or single fleeting episode without comparison does NOT satisfy.",
                evidence_hint="Look for 'new', 'worsening', 'progression', 'change from baseline', or direct comparison to a prior visit/imaging",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="onset",
                description="Specific onset documented — a date, duration, or relative timing ('X weeks ago', 'since March', 'over 3 months')",
                evidence_hint="Look for temporal anchors near the symptom mention",
            ),
            RequiredElement(
                key="frequency",
                description="How often symptoms occur — 'daily', 'weekly', 'with every flight of stairs', 'episodic'",
                evidence_hint="Look for frequency descriptors",
            ),
            RequiredElement(
                key="progression_or_character",
                description="Progression pattern (stable / worsening / improving) OR character (typical / atypical / exertional / rest)",
                evidence_hint="Look for progression or character language",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="symptom_named",
                description="Specific symptom named (chest pain, dyspnea, angina, jaw pain, arm pain, anginal equivalent)",
                evidence_hint="HPI",
            ),
            RequiredElement(
                key="symptom_characterization",
                description="Characterization present — typical / atypical, exertional / rest, or duration. 'Chest pain' alone without any descriptor does NOT satisfy.",
                evidence_hint="Descriptors in HPI or assessment",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="validated_scale_used",
                description="A named validated scale — NYHA, CCS, EHRA, or equivalent — explicitly cited",
                evidence_hint="Look for 'NYHA Class', 'CCS Class', 'EHRA'",
            ),
            RequiredElement(
                key="specific_class_value",
                description="A specific class/grade value (I, II, III, IV or 1–4). 'Symptomatic' without a numeric class does NOT satisfy.",
                evidence_hint="Look for roman numerals or numeric grades after the scale",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="gdmt_medications_listed",
                description="Specific guideline-directed medications named (beta blocker, ACE/ARB, statin, nitrate, etc.)",
                evidence_hint="Medication list or narrative",
            ),
            RequiredElement(
                key="maximal_or_tolerated_dose",
                description="Explicit statement of 'maximally tolerated', 'max-dose', 'titrated to max', OR specific dose at/near guideline max",
                evidence_hint="Look for titration language or specific doses",
            ),
            RequiredElement(
                key="persistent_symptoms_or_progression",
                description="Documented persistent symptoms or disease progression despite therapy",
                evidence_hint="Assessment / plan",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="medication_name",
                description="Specific medication name (generic or brand) listed",
                evidence_hint="Medication list",
            ),
            RequiredElement(
                key="dose",
                description="Specific dose documented (e.g. 'metoprolol 50 mg BID')",
                evidence_hint="Medication list dose field",
            ),
            RequiredElement(
                key="start_date_or_duration",
                description="Start date documented OR explicit duration of therapy ≥ 6 weeks ('started Jan 2024', 'on therapy for 8 weeks', 'x 6 weeks'). Presence of a medication in a list WITHOUT duration does NOT satisfy.",
                evidence_hint="Start date, 'since', 'x N weeks', 'for N months'",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="class_I_or_III_drug_named",
                description="Specific Class I (flecainide, propafenone) or Class III (amiodarone, sotalol, dofetilide, dronedarone) agent named",
                evidence_hint="Medication list / HPI",
            ),
            RequiredElement(
                key="failure_or_intolerance_documented",
                description="Explicit language: 'failed', 'persistent AF despite', 'intolerance', 'adverse effect', or 'discontinued due to'. Simply discontinuing a drug does NOT satisfy without a reason.",
                evidence_hint="Assessment / plan",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="bmi_value_documented",
                description="Specific numeric BMI value documented (e.g. 'BMI 38'). 'Obese' or 'morbidly obese' alone does NOT satisfy — need the number.",
                evidence_hint="Demographics / problem list",
            ),
            RequiredElement(
                key="bmi_at_or_above_35",
                description="Numeric value is ≥ 35. BMI < 35 does NOT satisfy regardless of any narrative.",
                evidence_hint="Compare the extracted BMI to the 35 threshold",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="prior_spect_report",
                description="Prior SPECT study with available report",
                evidence_hint="Imaging list",
            ),
            RequiredElement(
                key="attenuation_explicitly_stated",
                description="Explicit 'attenuation artifact', 'soft-tissue attenuation', 'breast attenuation', or 'diaphragmatic attenuation' in impression. A questionable finding without attenuation language does NOT satisfy.",
                evidence_hint="SPECT impression",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="baseline_ecg_result_present",
                description="Baseline ECG result available in the chart (not just ordered — actually reported)",
                evidence_hint="ECG findings / cardiology note",
            ),
            RequiredElement(
                key="lbbb_explicitly_present",
                description="'LBBB' or 'left bundle branch block' explicitly stated. Non-specific intraventricular conduction delay does NOT satisfy.",
                evidence_hint="ECG impression",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="baseline_ecg_result_present",
                description="Baseline ECG result available in the chart",
                evidence_hint="ECG findings",
            ),
            RequiredElement(
                key="ventricular_pacing_present",
                description="'Ventricular paced', 'V-paced', 'pacemaker rhythm', or 'A-V paced' explicitly documented. Presence of a pacemaker device alone (without paced rhythm) does NOT satisfy.",
                evidence_hint="ECG impression or rhythm strip interpretation",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="baseline_ecg_result_present",
                description="Baseline ECG result available",
                evidence_hint="ECG findings",
            ),
            RequiredElement(
                key="wpw_or_preexcitation_stated",
                description="'WPW', 'Wolff-Parkinson-White', 'pre-excitation', or 'delta wave' explicitly present. A short PR interval alone does NOT satisfy.",
                evidence_hint="ECG impression",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="lvh_present",
                description="Left ventricular hypertrophy documented on ECG",
                evidence_hint="ECG impression",
            ),
            RequiredElement(
                key="strain_pattern_stated",
                description="'Strain pattern', 'ST-T changes consistent with strain', or 'repolarization abnormality' explicitly documented. LVH alone without repolarization changes does NOT satisfy.",
                evidence_hint="ECG impression",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="lvef_numeric_or_range",
                description="LVEF documented as a specific value or range (e.g. '35%', '40-45%', 'LVEF = 50'). Qualitative 'normal' or 'reduced' alone does NOT satisfy.",
                evidence_hint="Echo / MRI / nuclear imaging report",
            ),
            RequiredElement(
                key="date_within_90_days",
                description="Study date documented AND within 90 days of request date",
                evidence_hint="Imaging date field",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="lvef_numeric",
                description="Numeric LVEF value present",
                evidence_hint="Imaging report",
            ),
            RequiredElement(
                key="value_at_or_below_40",
                description="LVEF value is ≤ 40%. LVEF 45-55% is preserved / mid-range and does NOT satisfy. 41-49% is 'mildly reduced' and does NOT satisfy this criterion.",
                evidence_hint="Compare numeric LVEF to 40% threshold",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="sts_score_named",
                description="'STS-PROM', 'STS score', or 'STS Predicted Risk of Mortality' explicitly referenced",
                evidence_hint="Pre-TAVR evaluation",
            ),
            RequiredElement(
                key="numeric_score_value",
                description="Specific numeric value of the STS-PROM score (e.g. '4.5%', 'STS 6.2'). 'High risk' alone without a number does NOT satisfy.",
                evidence_hint="Pre-TAVR workup / Heart Team note",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="at_least_two_risk_factors_named",
                description="At least two named risk factors (from: DM, HTN, dyslipidemia, smoking, family history of premature CAD, prior CAD/MI, CKD, obesity). A single risk factor alone does NOT satisfy this enumerated-profile criterion.",
                evidence_hint="Problem list or HPI risk factor section",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="stratification_method_named",
                description="Named stratification method — 'Diamond-Forrester', 'CAD Consortium', 'pre-test probability', or equivalent",
                evidence_hint="Cardiology note",
            ),
            RequiredElement(
                key="intermediate_to_high_category",
                description="Category labeled intermediate or high (not low). Low pre-test probability does NOT satisfy for advanced imaging.",
                evidence_hint="Stratification result",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="heart_team_note_present",
                description="Heart Team / multidisciplinary evaluation note documented",
                evidence_hint="Consultation note",
            ),
            RequiredElement(
                key="cardiothoracic_surgeon_attesting",
                description="Cardiothoracic surgeon named or signed the note (explicit)",
                evidence_hint="Note signatories",
            ),
            RequiredElement(
                key="interventional_cardiologist_attesting",
                description="Interventional cardiologist named or signed the note",
                evidence_hint="Note signatories",
            ),
            RequiredElement(
                key="tavr_vs_savr_recommendation",
                description="Explicit TAVR vs SAVR recommendation (not just documentation of severe AS)",
                evidence_hint="Assessment / plan section",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="score_named",
                description="'CHA2DS2-VASc' or 'CHADS2-VASc' explicitly referenced",
                evidence_hint="Cardiology / EP note",
            ),
            RequiredElement(
                key="numeric_value",
                description="Specific numeric score (0–9). Narrative 'high stroke risk' alone does NOT satisfy.",
                evidence_hint="Score calculation",
            ),
            RequiredElement(
                key="anticoagulation_plan_documented",
                description="Anticoagulation decision documented — on anticoagulation, held with reason, or declined by patient with shared decision-making note.",
                evidence_hint="Medication list or plan",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="tee_or_cardiac_ct_performed",
                description="Transesophageal echocardiogram OR cardiac CT documented pre-procedure",
                evidence_hint="Imaging list",
            ),
            RequiredElement(
                key="laa_thrombus_ruled_out",
                description="Explicit absence of LAA thrombus documented ('no LAA thrombus', 'LAA clear', 'no evidence of intracardiac thrombus'). Imaging without this finding stated does NOT satisfy.",
                evidence_hint="TEE/CT impression",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="coronary_study_performed",
                description="Coronary angiography or coronary CTA documented",
                evidence_hint="Imaging list",
            ),
            RequiredElement(
                key="within_12_months",
                description="Study date within 12 months of request",
                evidence_hint="Study date",
            ),
            RequiredElement(
                key="cad_status_reported",
                description="Coronary disease status explicitly reported (obstructive CAD / non-obstructive / normal). A completed study without a reported assessment does NOT satisfy.",
                evidence_hint="Study impression",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="cta_aortic_root_and_access",
                description="CTA covering aortic root AND iliofemoral arteries. Echo alone or partial CT does NOT satisfy.",
                evidence_hint="CTA protocol description",
            ),
            RequiredElement(
                key="annular_measurements_present",
                description="Annular measurement values documented (e.g. annular diameter, area, perimeter)",
                evidence_hint="CTA measurement section",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="no_similar_imaging_or_justification_for_repeat",
                description="Either (a) no similar advanced imaging within 12 months, OR (b) explicit clinical change since prior imaging justifying repeat ('worsening angina since prior PET', 'new symptoms since last SPECT'). Silent repeat with no justification does NOT satisfy.",
                evidence_hint="Imaging history and HPI",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="specific_limiting_condition",
                description="Specific diagnosis or physical barrier named (severe OA, peripheral neuropathy, COPD, dyspnea + obesity, etc.)",
                evidence_hint="Problem list / HPI / assessment",
            ),
            RequiredElement(
                key="explicit_causal_link_to_exercise",
                description="Explicit causal statement: 'unable to exercise due to X', 'cannot perform TST because of X', 'exercise limited by X'. Symptom presence (e.g. dyspnea on exertion) alone does NOT satisfy — the chart must link it to inability to perform the stress test.",
                evidence_hint="Look for 'unable to', 'cannot', 'precludes', 'prevents', 'limits' + exercise/stress",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="auc_referenced",
                description="ACC AUC / Appropriate Use Criteria explicitly referenced",
                evidence_hint="Cardiology note",
            ),
            RequiredElement(
                key="numeric_score_7_to_9",
                description="Specific AUC score in the 7–9 range documented. Lower scores or absence of a numeric score does NOT satisfy.",
                evidence_hint="Score calculation",
            ),
        ],
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
        required_elements=[
            RequiredElement(
                key="substantive_clinical_note_present",
                description="Clinical narrative with indication, history, exam, or assessment present in chart (office_notes or additional_notes). A blank or one-line note does NOT satisfy.",
                evidence_hint="clinical_note.office_notes or additional_notes",
            ),
        ],
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
