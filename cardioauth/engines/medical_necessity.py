"""Medical Necessity Documentation Engine — identifies missing clinical documentation.

Analyzes patient chart data against payer requirements for cardiology procedures
and identifies gaps that could lead to denials.
"""

from __future__ import annotations

from typing import Any, Optional

# What payers want to see per procedure
PROCEDURE_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "93306": {  # TTE
        "procedure_name": "Transthoracic Echocardiogram (TTE)",
        "required_elements": [
            {"element": "Clinical indication", "source": "order/clinical note", "example": "Evaluate LV function in new heart failure diagnosis", "weight": 0.30},
            {"element": "Relevant symptoms", "source": "clinical note", "example": "Progressive dyspnea on exertion x 4 weeks", "weight": 0.25},
            {"element": "Prior echo results (if repeat)", "source": "imaging records", "example": "Prior TTE 6 months ago showed EF 45%", "weight": 0.20},
            {"element": "Change in clinical status (if repeat)", "source": "clinical note", "example": "New onset peripheral edema, weight gain 8 lbs in 2 weeks", "weight": 0.25},
        ],
        "common_gaps": [
            "No documented clinical indication beyond 'screening'",
            "Repeat TTE without documented change in clinical status",
            "Missing prior echo comparison data",
        ],
    },
    "93350": {  # Stress echo
        "procedure_name": "Stress Echocardiography",
        "required_elements": [
            {"element": "Symptoms description", "source": "clinical note", "example": "Exertional chest pressure with radiation to left arm", "weight": 0.20},
            {"element": "Pre-test probability assessment", "source": "clinical note", "example": "Intermediate pre-test probability based on age, sex, symptoms", "weight": 0.20},
            {"element": "AUC (Appropriate Use Criteria) score", "source": "decision support", "example": "AUC score: Appropriate (Score 8)", "weight": 0.15},
            {"element": "Risk factor documentation", "source": "problem list", "example": "HTN, DM2, hyperlipidemia, smoking history 20 pack-years", "weight": 0.15},
            {"element": "Exercise capacity assessment", "source": "clinical note", "example": "Patient able to achieve 5 METs on treadmill", "weight": 0.15},
            {"element": "Reason for imaging (vs. exercise ECG alone)", "source": "clinical note", "example": "Baseline LBBB precludes exercise ECG interpretation", "weight": 0.15},
        ],
        "common_gaps": [
            "Pre-test probability not documented",
            "AUC score not reported",
            "No justification for imaging over exercise ECG alone",
            "Risk factors listed but not linked to clinical decision",
        ],
    },
    "93458": {  # Left heart cath
        "procedure_name": "Left Heart Catheterization",
        "required_elements": [
            {"element": "Ejection fraction (LVEF)", "source": "echocardiogram", "example": "LVEF 35% by Simpson's biplane method", "weight": 0.15},
            {"element": "Symptom onset timeline", "source": "clinical note", "example": "Chest pain began 3 weeks ago, progressive in frequency", "weight": 0.15},
            {"element": "Failed conservative treatment", "source": "medication list", "example": "Max dose metoprolol 200mg daily x 6 weeks, aspirin, atorvastatin 80mg", "weight": 0.20},
            {"element": "Non-invasive test results", "source": "stress test report", "example": "Positive SPECT MPI with 18% reversible defect in LAD territory", "weight": 0.20},
            {"element": "Risk factor documentation", "source": "problem list", "example": "DM2, HTN, hyperlipidemia, family hx premature CAD", "weight": 0.10},
            {"element": "ACC/AHA indication class", "source": "clinical note", "example": "Class I indication per 2021 ACC/AHA Chest Pain Guidelines", "weight": 0.10},
            {"element": "Informed consent documentation", "source": "consent form", "example": "Risks, benefits, and alternatives discussed. Patient consents.", "weight": 0.10},
        ],
        "common_gaps": [
            "LVEF not documented within 90 days",
            "Symptom timeline absent or vague",
            "No documentation of failed medical therapy",
            "Non-invasive test results not referenced",
            "Risk factors listed but pre-test probability not stated",
        ],
    },
    "93460": {  # Right and left heart cath
        "procedure_name": "Right and Left Heart Catheterization",
        "required_elements": [
            {"element": "Indication for right heart catheterization", "source": "clinical note", "example": "Evaluate pulmonary hypertension severity and reversibility", "weight": 0.20},
            {"element": "Hemodynamic question to be answered", "source": "clinical note", "example": "Assess pulmonary vascular resistance pre-transplant", "weight": 0.20},
            {"element": "Echo findings suggesting elevated pressures", "source": "echocardiogram", "example": "Estimated RVSP 65 mmHg, dilated RV, moderate TR", "weight": 0.20},
            {"element": "Left heart cath indication", "source": "clinical note", "example": "Positive stress test with large reversible perfusion defect", "weight": 0.15},
            {"element": "BNP/NT-proBNP level", "source": "lab results", "example": "NT-proBNP 3,400 pg/mL", "weight": 0.10},
            {"element": "Prior workup results", "source": "medical records", "example": "PFTs normal, V/Q scan low probability, CT angiogram negative for PE", "weight": 0.15},
        ],
        "common_gaps": [
            "No specific indication for right heart catheterization",
            "Echo not documenting elevated right-sided pressures",
            "Hemodynamic question not clearly stated",
            "Missing PH workup documentation",
        ],
    },
    "33361": {  # TAVR
        "procedure_name": "Transcatheter Aortic Valve Replacement (TAVR)",
        "required_elements": [
            {"element": "Severe aortic stenosis documentation", "source": "echocardiogram", "example": "AVA 0.7 cm2, mean gradient 48 mmHg, peak velocity 4.5 m/s", "weight": 0.15},
            {"element": "NYHA functional class", "source": "clinical note", "example": "NYHA Class III — symptoms with less than ordinary activity", "weight": 0.10},
            {"element": "STS-PROM score", "source": "risk calculator", "example": "STS-PROM 5.2% (intermediate risk)", "weight": 0.15},
            {"element": "Heart team evaluation note", "source": "multidisciplinary note", "example": "Heart team meeting 1/15/2026: consensus for TAVR over SAVR given frailty and comorbidities", "weight": 0.15},
            {"element": "CT angiography measurements", "source": "CT report", "example": "Annular area 480 mm2, perimeter 78mm, suitable for 26mm valve", "weight": 0.10},
            {"element": "Symptom documentation", "source": "clinical note", "example": "Syncope with exertion, progressive dyspnea, unable to climb one flight of stairs", "weight": 0.10},
            {"element": "Surgical risk factors", "source": "clinical note", "example": "Prior sternotomy, porcelain aorta, severe COPD (FEV1 0.9L)", "weight": 0.10},
            {"element": "Life expectancy assessment", "source": "clinical note", "example": "Expected survival >12 months with successful intervention", "weight": 0.05},
            {"element": "Shared decision-making", "source": "consent note", "example": "Patient and family understand risks/benefits of TAVR vs. medical therapy. Chooses TAVR.", "weight": 0.10},
        ],
        "common_gaps": [
            "STS-PROM score not calculated or documented",
            "Heart team note missing or not signed by all team members",
            "NYHA class not clearly stated",
            "CT measurements not current (>90 days old)",
            "No documentation of why TAVR preferred over surgical AVR",
        ],
    },
    "93656": {  # AF ablation
        "procedure_name": "Atrial Fibrillation Ablation",
        "required_elements": [
            {"element": "AF type classification", "source": "clinical note", "example": "Paroxysmal atrial fibrillation, documented on Holter monitor", "weight": 0.15},
            {"element": "Symptom burden documentation", "source": "clinical note", "example": "Palpitations, fatigue, exercise intolerance despite rate control. EHRA score III.", "weight": 0.15},
            {"element": "Failed AAD therapy", "source": "medication records", "example": "Failed flecainide 100mg BID x 3 months (breakthrough AF episodes), intolerant to amiodarone (thyroid toxicity)", "weight": 0.20},
            {"element": "Left atrial size", "source": "echocardiogram", "example": "LA diameter 4.2 cm, LA volume index 38 mL/m2", "weight": 0.10},
            {"element": "LVEF", "source": "echocardiogram", "example": "LVEF 50%", "weight": 0.05},
            {"element": "Anticoagulation plan", "source": "clinical note", "example": "CHA2DS2-VASc 3, on apixaban 5mg BID, to continue peri-procedurally", "weight": 0.10},
            {"element": "Pre-procedure imaging", "source": "TEE or CT report", "example": "TEE: No LA appendage thrombus. Mild spontaneous echo contrast.", "weight": 0.10},
            {"element": "Stroke risk assessment", "source": "clinical note", "example": "CHA2DS2-VASc score 3 (HTN, DM, age 67)", "weight": 0.10},
            {"element": "Rhythm documentation", "source": "ECG/Holter", "example": "Holter: 15 episodes paroxysmal AF, longest 8 hours, AF burden 22%", "weight": 0.05},
        ],
        "common_gaps": [
            "AF type not specified (paroxysmal vs. persistent)",
            "AAD failure not documented (drug name, dose, duration, reason for failure)",
            "LA size not measured or documented",
            "CHA2DS2-VASc score not calculated",
            "No pre-procedure TEE or CT to rule out thrombus",
        ],
    },
    "78452": {  # Nuclear stress test (SPECT MPI)
        "procedure_name": "Nuclear Stress Test (SPECT MPI)",
        "required_elements": [
            {"element": "Symptoms and clinical indication", "source": "clinical note", "example": "New-onset exertional chest pain in 62-year-old male with DM and HTN", "weight": 0.20},
            {"element": "Pre-test probability", "source": "clinical note", "example": "Intermediate pre-test probability per ACC/AHA guidelines", "weight": 0.20},
            {"element": "AUC consultation", "source": "decision support", "example": "AUC: Appropriate (Score 7) via CareSelect QCDSM", "weight": 0.15},
            {"element": "Reason for nuclear over other modalities", "source": "clinical note", "example": "BMI 38, poor acoustic windows on prior echo, LBBB on baseline ECG", "weight": 0.15},
            {"element": "Stress protocol justification", "source": "clinical note", "example": "Pharmacologic stress with regadenoson — patient unable to exercise due to severe OA", "weight": 0.15},
            {"element": "Risk factor profile", "source": "problem list", "example": "DM2, HTN, hyperlipidemia, family hx CAD (father MI age 52)", "weight": 0.15},
        ],
        "common_gaps": [
            "Pre-test probability not explicitly stated",
            "AUC G-codes not reported on claim",
            "No justification for nuclear imaging vs. stress echo",
            "Pharmacologic stress without documenting exercise limitation",
        ],
    },
    "75574": {  # Cardiac CTA
        "procedure_name": "Coronary CT Angiography (CCTA)",
        "required_elements": [
            {"element": "Symptoms description", "source": "clinical note", "example": "Acute chest pain, low-to-intermediate risk per HEART score 4", "weight": 0.20},
            {"element": "Pre-test probability", "source": "clinical note", "example": "Low-to-intermediate pre-test probability for obstructive CAD", "weight": 0.20},
            {"element": "Heart rate assessment", "source": "clinical note", "example": "Resting HR 62, sinus rhythm, appropriate for CCTA", "weight": 0.10},
            {"element": "Renal function", "source": "lab results", "example": "eGFR 78 mL/min/1.73m2, adequate for contrast", "weight": 0.10},
            {"element": "Clinical decision pathway", "source": "clinical note", "example": "CCTA chosen per 2021 ACC/AHA Chest Pain Guideline as first-line for stable chest pain", "weight": 0.20},
            {"element": "Contrast allergy screening", "source": "allergy list", "example": "No known contrast allergy, no history of anaphylaxis", "weight": 0.05},
            {"element": "Prior testing (if any)", "source": "medical records", "example": "Exercise ECG non-diagnostic due to submaximal HR", "weight": 0.15},
        ],
        "common_gaps": [
            "Pre-test probability not documented",
            "No documentation of heart rate suitability for CT",
            "Renal function not checked before contrast study",
            "No explanation of why CCTA chosen over stress testing",
        ],
    },
    "75557": {  # Cardiac MRI
        "procedure_name": "Cardiac MRI",
        "required_elements": [
            {"element": "Specific clinical question", "source": "clinical note", "example": "Evaluate for cardiac sarcoidosis given unexplained cardiomyopathy and hilar lymphadenopathy", "weight": 0.25},
            {"element": "Prior echo results and limitations", "source": "echocardiogram", "example": "Echo shows EF 38%, dilated LV, but etiology unclear. No regional wall motion abnormalities.", "weight": 0.20},
            {"element": "Why echo is insufficient", "source": "clinical note", "example": "Echo cannot differentiate ischemic vs. non-ischemic cardiomyopathy; CMR needed for tissue characterization", "weight": 0.20},
            {"element": "MRI safety screening", "source": "screening form", "example": "No implanted devices, no metallic foreign bodies, GFR >30 if gadolinium planned", "weight": 0.10},
            {"element": "Differential diagnosis", "source": "clinical note", "example": "DDx: cardiac sarcoidosis, myocarditis, arrhythmogenic cardiomyopathy", "weight": 0.15},
            {"element": "Expected impact on management", "source": "clinical note", "example": "CMR results will determine need for endomyocardial biopsy and immunosuppression", "weight": 0.10},
        ],
        "common_gaps": [
            "Specific clinical question not stated (just 'further evaluation')",
            "Prior echo results not referenced",
            "No documentation of why echo is insufficient",
            "Missing explanation of how CMR will change management",
        ],
    },
    "92928": {  # PCI with stent, single vessel
        "procedure_name": "PCI with Stent (Single Vessel)",
        "required_elements": [
            {"element": "Angiographic findings", "source": "catheterization report", "example": "90% stenosis proximal LAD, TIMI flow 2", "weight": 0.20},
            {"element": "Ischemia documentation", "source": "stress test or FFR", "example": "FFR 0.72 in LAD territory; SPECT showing 15% reversible anterior defect", "weight": 0.20},
            {"element": "Symptom documentation", "source": "clinical note", "example": "Refractory angina despite optimal medical therapy, CCS Class III", "weight": 0.15},
            {"element": "Medical therapy documentation", "source": "medication list", "example": "Aspirin, atorvastatin 80mg, metoprolol 100mg BID, amlodipine 10mg, isosorbide mononitrate 60mg", "weight": 0.15},
            {"element": "ACC/AHA indication", "source": "clinical note", "example": "Class I indication: significant stenosis with positive ischemia testing", "weight": 0.10},
            {"element": "Informed consent", "source": "consent form", "example": "Discussed PCI vs. continued medical therapy. ISCHEMIA trial results reviewed.", "weight": 0.10},
            {"element": "Antiplatelet plan", "source": "clinical note", "example": "Plan for 12 months DAPT: aspirin + clopidogrel", "weight": 0.10},
        ],
        "common_gaps": [
            "FFR/iFR not performed for intermediate stenosis (40-70%)",
            "No documentation of failed medical therapy for stable disease",
            "Ischemia testing results not referenced in decision-making",
            "Stenosis severity not clearly documented",
        ],
    },
    "92941": {  # PCI acute MI
        "procedure_name": "PCI for Acute Myocardial Infarction",
        "required_elements": [
            {"element": "STEMI/NSTEMI classification", "source": "ECG/troponin", "example": "STEMI with ST elevation V1-V4, peak troponin I 45 ng/mL", "weight": 0.20},
            {"element": "Symptom onset time", "source": "clinical note", "example": "Chest pain onset 2 hours prior to ED arrival", "weight": 0.15},
            {"element": "ECG findings", "source": "ECG report", "example": "ST elevation 3mm in V1-V4 with reciprocal changes in II, III, aVF", "weight": 0.15},
            {"element": "Cardiac biomarkers", "source": "lab results", "example": "Troponin I: initial 2.4, peak 45.0 ng/mL (normal <0.04)", "weight": 0.10},
            {"element": "Door-to-balloon time", "source": "cath lab record", "example": "Door-to-balloon time: 54 minutes", "weight": 0.10},
            {"element": "Angiographic findings", "source": "catheterization report", "example": "100% occlusion proximal LAD with thrombus, TIMI 0 flow", "weight": 0.15},
            {"element": "Hemodynamic status", "source": "clinical note", "example": "BP 95/60, HR 110, Killip Class II", "weight": 0.10},
            {"element": "Post-PCI result", "source": "catheterization report", "example": "Successful PCI with DES to proximal LAD, TIMI 3 flow restored", "weight": 0.05},
        ],
        "common_gaps": [
            "Door-to-balloon time not documented",
            "ECG not in medical record",
            "Troponin trend not documented",
            "STEMI vs NSTEMI not clearly classified",
        ],
    },
    "33249": {  # ICD implant
        "procedure_name": "ICD Implantation",
        "required_elements": [
            {"element": "Ejection fraction ≤35%", "source": "echocardiogram", "example": "LVEF 30% by Simpson's biplane, measured 1/15/2026", "weight": 0.20},
            {"element": "NYHA functional class", "source": "clinical note", "example": "NYHA Class II — symptoms with ordinary physical activity", "weight": 0.10},
            {"element": "40+ days post-MI", "source": "medical records", "example": "MI occurred 11/20/2025 (58 days ago). No revascularization within 90 days.", "weight": 0.10},
            {"element": "90+ days GDMT at target doses", "source": "medication records", "example": "Sacubitril/valsartan 97/103mg BID since 10/1/2025, carvedilol 25mg BID since 9/15/2025, spironolactone 25mg since 10/1/2025", "weight": 0.20},
            {"element": "Repeat EF after GDMT optimization", "source": "echocardiogram", "example": "Repeat echo after 90 days GDMT: LVEF 28%, no improvement", "weight": 0.10},
            {"element": "Life expectancy >1 year", "source": "clinical note", "example": "No terminal illness. Expected survival >12 months with good functional status.", "weight": 0.10},
            {"element": "Shared decision-making", "source": "clinical note", "example": "Discussed SCD risk, ICD benefits/risks, and alternative of medical therapy alone. Patient elects ICD.", "weight": 0.10},
            {"element": "NCD 20.4 criteria documentation", "source": "clinical note", "example": "Patient meets NCD 20.4 for primary prevention ICD: ischemic CM, EF ≤35%, NYHA II, on GDMT ≥90 days", "weight": 0.10},
        ],
        "common_gaps": [
            "GDMT optimization period <90 days or not documented",
            "Repeat EF after GDMT not performed",
            "Medication doses not at target or not documented",
            "Life expectancy assessment missing",
            "No documentation of 40-day post-MI waiting period",
            "Shared decision-making note absent",
        ],
    },
    "33208": {  # Pacemaker implant
        "procedure_name": "Pacemaker Implantation (Dual Chamber)",
        "required_elements": [
            {"element": "Bradyarrhythmia documentation", "source": "ECG/Holter", "example": "Complete heart block on ECG with ventricular rate 34 bpm", "weight": 0.25},
            {"element": "Symptom-rhythm correlation", "source": "clinical note", "example": "Syncope during documented complete heart block on telemetry", "weight": 0.25},
            {"element": "ACC/AHA/HRS indication", "source": "clinical note", "example": "Class I indication: symptomatic complete AV block (HRS 2018 Bradycardia Guidelines)", "weight": 0.15},
            {"element": "Reversible causes ruled out", "source": "clinical note", "example": "No reversible cause identified. Not on AV nodal blocking agents. Thyroid function normal.", "weight": 0.10},
            {"element": "Device selection rationale", "source": "clinical note", "example": "Dual-chamber selected: intact sinus node function with AV block, need for AV synchrony", "weight": 0.15},
            {"element": "Informed consent", "source": "consent form", "example": "Risks including infection, lead dislodgement, pneumothorax discussed. Patient consents.", "weight": 0.10},
        ],
        "common_gaps": [
            "ECG/Holter documenting bradyarrhythmia not in chart",
            "Symptom-rhythm correlation not established",
            "No documentation of reversible cause evaluation",
            "Dual-chamber selection not justified over single-chamber",
        ],
    },
    "93351": {  # Stress echo with contrast
        "procedure_name": "Stress Echocardiography with Contrast",
        "required_elements": [
            {"element": "Stress echo indication", "source": "clinical note", "example": "Evaluate for ischemia in patient with exertional chest pain", "weight": 0.15},
            {"element": "Suboptimal image quality documentation", "source": "echo report", "example": "Baseline images: 4/17 segments not visualized, suboptimal apical views", "weight": 0.25},
            {"element": "Number of non-visualized segments", "source": "echo report", "example": "≥2 contiguous segments not adequately visualized without contrast", "weight": 0.20},
            {"element": "Patient factors limiting imaging", "source": "clinical note", "example": "BMI 42, COPD with hyperinflated lungs limiting acoustic windows", "weight": 0.15},
            {"element": "AUC score", "source": "decision support", "example": "AUC: Appropriate (Score 8)", "weight": 0.10},
            {"element": "Pre-test probability", "source": "clinical note", "example": "Intermediate pre-test probability for CAD", "weight": 0.15},
        ],
        "common_gaps": [
            "No documentation of suboptimal baseline image quality",
            "Number of non-visualized segments not recorded",
            "Contrast use documented as 'routine' without clinical justification",
            "BMI or limiting factors not mentioned",
        ],
    },
}

# Remote monitoring codes have lighter documentation requirements
for _code, _name in [("93291", "Pacemaker"), ("93295", "ICD"), ("93297", "ICM")]:
    PROCEDURE_REQUIREMENTS[_code] = {
        "procedure_name": f"Remote Monitoring — {_name}",
        "required_elements": [
            {"element": "Device implant documentation", "source": "operative note", "example": f"{_name} implanted on 6/15/2025", "weight": 0.25},
            {"element": "Remote monitoring enrollment", "source": "device clinic", "example": "Enrolled in Medtronic CareLink remote monitoring", "weight": 0.25},
            {"element": "Monitoring period documentation", "source": "transmission log", "example": f"{'90' if _code != '93297' else '30'}-day monitoring period: 1/1/2026 — {'3/31' if _code != '93297' else '1/30'}/2026", "weight": 0.25},
            {"element": "Device interrogation findings", "source": "device report", "example": "Battery adequate, leads intact, no arrhythmia alerts", "weight": 0.25},
        ],
        "common_gaps": [
            "Monitoring period dates not documented",
            "Transmission log not maintained",
            "Device interrogation report not signed by physician",
        ],
    }


def analyze_documentation(
    chart_data: dict[str, Any], procedure_code: str
) -> dict[str, Any]:
    """Analyze chart documentation against procedure requirements.

    Args:
        chart_data: Patient chart data dict. Expected keys vary by procedure but
            commonly include: symptoms, diagnosis_codes, medications, test_results,
            ejection_fraction, clinical_notes, problem_list, imaging_results, etc.
        procedure_code: CPT code for the planned procedure.

    Returns:
        dict with present_elements, missing_elements, documentation_score,
        and actionable recommendations.
    """
    requirements = PROCEDURE_REQUIREMENTS.get(procedure_code)
    if not requirements:
        return {
            "procedure_code": procedure_code,
            "found": False,
            "error": f"Procedure code {procedure_code} not found in requirements database",
        }

    required_elements = requirements["required_elements"]
    present: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    chart_text = _flatten_chart_to_text(chart_data)

    for element in required_elements:
        element_name = element["element"]
        found = _check_element_present(element_name, chart_data, chart_text)

        entry = {
            "element": element_name,
            "source": element["source"],
            "example": element["example"],
            "weight": element["weight"],
        }

        if found:
            entry["status"] = "present"
            entry["evidence"] = found
            present.append(entry)
        else:
            entry["status"] = "missing"
            missing.append(entry)

    # Calculate score
    total_weight = sum(e["weight"] for e in required_elements)
    present_weight = sum(e["weight"] for e in present)
    score = present_weight / total_weight if total_weight > 0 else 0.0

    return {
        "procedure_code": procedure_code,
        "procedure_name": requirements["procedure_name"],
        "found": True,
        "documentation_score": round(score, 3),
        "documentation_grade": _score_to_grade(score),
        "present_elements": present,
        "missing_elements": missing,
        "common_gaps": requirements.get("common_gaps", []),
        "present_count": len(present),
        "missing_count": len(missing),
        "total_required": len(required_elements),
    }


def generate_recommendations(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate specific documentation recommendations for identified gaps.

    Args:
        gaps: List of missing element dicts from analyze_documentation().

    Returns:
        List of actionable recommendations with priority and suggested language.
    """
    recommendations = []

    for i, gap in enumerate(gaps):
        element = gap.get("element", "Unknown element")
        source = gap.get("source", "clinical note")
        example = gap.get("example", "")
        weight = gap.get("weight", 0.1)

        # Determine priority based on weight
        if weight >= 0.20:
            priority = "high"
        elif weight >= 0.10:
            priority = "medium"
        else:
            priority = "low"

        recommendation = {
            "priority": priority,
            "element": element,
            "action": _get_action_text(element, source),
            "suggested_language": _get_suggested_language(element, example),
            "source_document": source,
            "impact": f"Adding this documentation addresses {weight * 100:.0f}% of the documentation score.",
        }
        recommendations.append(recommendation)

    # Sort by priority: high > medium > low
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: priority_order.get(r["priority"], 3))

    return recommendations


def score_documentation_strength(
    chart_data: dict[str, Any], procedure_code: str
) -> float:
    """Calculate a 0-1 completeness score for documentation.

    Args:
        chart_data: Patient chart data dictionary.
        procedure_code: CPT code for the planned procedure.

    Returns:
        Float 0.0 to 1.0 representing documentation completeness.
    """
    result = analyze_documentation(chart_data, procedure_code)
    if not result.get("found"):
        return 0.0
    return result["documentation_score"]


def _flatten_chart_to_text(chart_data: dict[str, Any]) -> str:
    """Flatten chart data dict into a searchable text string."""
    parts: list[str] = []

    def _recurse(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                _recurse(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                _recurse(item, prefix)
        elif obj is not None:
            parts.append(f"{prefix}: {obj}")

    _recurse(chart_data)
    return " ".join(parts).lower()


def _check_element_present(element_name: str, chart_data: dict[str, Any], chart_text: str) -> Optional[str]:
    """Check whether a required documentation element is present in chart data.

    Returns evidence string if found, None if not.
    """
    element_lower = element_name.lower()

    # Define keyword patterns for each element type
    element_keywords: dict[str, list[str]] = {
        "ejection fraction": ["ejection fraction", "lvef", "ef ", "ef:", "ef=", "ef%"],
        "symptom": ["symptom", "chest pain", "dyspnea", "shortness of breath", "palpitation", "syncope", "fatigue", "exertional"],
        "symptom onset timeline": ["onset", "began", "started", "duration", "weeks ago", "days ago", "months ago", "progressive"],
        "failed conservative treatment": ["failed", "refractory", "despite", "max dose", "intolerant", "ineffective", "trial of"],
        "failed medical therapy": ["failed", "refractory", "despite", "max dose", "intolerant", "ineffective", "trial of"],
        "non-invasive test": ["stress test", "spect", "mpi", "exercise test", "treadmill", "nuclear", "stress echo", "cta", "ccta"],
        "risk factor": ["diabetes", "dm2", "hypertension", "htn", "hyperlipidemia", "smoking", "family history", "obesity"],
        "clinical indication": ["indication", "evaluate", "assess", "rule out", "suspect"],
        "auc": ["auc", "appropriate use", "decision support", "score"],
        "pre-test probability": ["pre-test probability", "pretest", "risk assessment", "intermediate risk", "high risk", "low risk"],
        "lvef": ["lvef", "ejection fraction", "ef ", "ef:", "ef%"],
        "nyha": ["nyha", "functional class", "class i", "class ii", "class iii", "class iv"],
        "sts": ["sts", "sts-prom", "surgical risk"],
        "heart team": ["heart team", "multidisciplinary", "team meeting", "consensus"],
        "ct angiography": ["ct angiography", "cta", "annular", "perimeter"],
        "aad": ["antiarrhythmic", "aad", "flecainide", "amiodarone", "sotalol", "dofetilide", "dronedarone"],
        "failed aad": ["failed", "intolerant", "breakthrough", "recurrent af despite", "ineffective"],
        "left atrial size": ["la diameter", "la size", "left atrial", "la volume"],
        "anticoagulation": ["anticoagulation", "apixaban", "rivaroxaban", "warfarin", "edoxaban", "dabigatran", "coumadin"],
        "cha2ds2": ["cha2ds2", "chads", "stroke risk"],
        "bradyarrhythmia": ["bradycardia", "heart block", "av block", "sinus pause", "sinus arrest", "slow rate"],
        "symptom-rhythm correlation": ["syncope during", "presyncope with", "symptoms correlate", "symptom-rhythm"],
        "gdmt": ["sacubitril", "entresto", "carvedilol", "metoprolol", "bisoprolol", "spironolactone", "eplerenone", "dapagliflozin", "empagliflozin"],
        "life expectancy": ["life expectancy", "survival", "prognosis", ">1 year", ">12 months"],
        "ncd": ["ncd 20.4", "ncd criteria", "national coverage"],
        "shared decision": ["shared decision", "informed consent", "discussed", "patient elects", "risks and benefits"],
        "device implant": ["implant", "device", "pacemaker", "icd", "icm", "loop recorder"],
        "remote monitoring enrollment": ["remote monitoring", "carelink", "merlin", "latitude", "enrolled"],
        "monitoring period": ["monitoring period", "90-day", "30-day", "transmission"],
        "device interrogation": ["interrogation", "battery", "lead impedance", "sensing", "threshold"],
        "angiographic findings": ["stenosis", "occlusion", "timi", "lesion", "lad", "rca", "lcx"],
        "ffr": ["ffr", "ifr", "fractional flow", "instantaneous wave-free"],
        "stemi": ["stemi", "st elevation", "nstemi", "non-st elevation"],
        "door-to-balloon": ["door-to-balloon", "d2b", "first medical contact"],
        "troponin": ["troponin", "cardiac biomarker", "hstni", "hs-tni"],
        "ecg": ["ecg", "ekg", "electrocardiogram", "st elevation", "st depression"],
        "suboptimal image": ["suboptimal", "non-visualized", "poor acoustic", "limited windows"],
        "contrast justification": ["non-visualized segments", "poor windows", "bmi", "copd", "hyperinflated"],
        "renal function": ["gfr", "egfr", "creatinine", "renal function"],
        "differential diagnosis": ["differential", "ddx", "rule out"],
        "impact on management": ["change management", "will determine", "guide therapy", "if positive"],
        "informed consent": ["consent", "risks discussed", "benefits discussed", "alternatives discussed"],
        "bnt-probnp": ["bnp", "nt-probnp", "pro-bnp", "natriuretic peptide"],
        "hemodynamic": ["hemodynamic", "cardiac output", "wedge pressure", "pulmonary artery pressure", "pvr"],
        "pulmonary hypertension": ["pulmonary hypertension", "rvsp", "pa pressure", "ph workup"],
        "severe aortic stenosis": ["aortic stenosis", "ava", "mean gradient", "peak velocity", "aortic valve area"],
        "acc/aha": ["acc/aha", "guideline", "class i", "class iia", "indication"],
    }

    # Find matching keywords for this element
    matched_keywords = []
    for key, keywords in element_keywords.items():
        if key in element_lower:
            matched_keywords.extend(keywords)

    # If no specific keywords found, use the element name words themselves
    if not matched_keywords:
        matched_keywords = [w for w in element_lower.split() if len(w) > 3]

    # Search chart text for keywords
    for keyword in matched_keywords:
        if keyword in chart_text:
            # Find the context around the match
            idx = chart_text.find(keyword)
            start = max(0, idx - 50)
            end = min(len(chart_text), idx + len(keyword) + 50)
            evidence = chart_text[start:end].strip()
            return f"...{evidence}..."

    return None


def _score_to_grade(score: float) -> str:
    """Convert numeric score to letter grade."""
    if score >= 0.90:
        return "A"
    elif score >= 0.80:
        return "B"
    elif score >= 0.70:
        return "C"
    elif score >= 0.60:
        return "D"
    else:
        return "F"


def _get_action_text(element: str, source: str) -> str:
    """Generate action text for a missing documentation element."""
    return f"Add '{element}' to the {source}. This is required for prior authorization and medical necessity documentation."


def _get_suggested_language(element: str, example: str) -> str:
    """Generate suggested clinical language for a missing element."""
    if not example:
        return f"Document {element} with specific clinical details."

    templates: dict[str, str] = {
        "ejection fraction": f"Include in note: '{example}' — Document EF value, method, and date measured.",
        "symptom": f"Include in HPI: '{example}' — Describe onset, character, duration, and functional impact.",
        "failed": f"Include in assessment/plan: '{example}' — List drug name, dose, duration, and reason for failure.",
        "non-invasive": f"Reference in note: '{example}' — Include test type, date, and key findings.",
        "risk factor": f"Include in problem list: '{example}' — List all cardiovascular risk factors.",
        "auc": f"Document: '{example}' — Include AUC consultation result and G-codes.",
        "pre-test": f"State explicitly: '{example}' — Document risk category per guideline criteria.",
        "heart team": f"Document: '{example}' — Include date, attendees, and consensus recommendation.",
        "sts": f"Include: '{example}' — Calculate and document STS-PROM score.",
        "gdmt": f"Document: '{example}' — List each GDMT medication with dose and start date.",
    }

    for key, template in templates.items():
        if key in element.lower():
            return template

    return f"Suggested documentation: '{example}'"
