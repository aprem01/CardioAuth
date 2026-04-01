"""ICD-10 Specificity Checker — CPT + ICD-10 code pairing validation engine.

Validates that diagnosis codes paired with cardiology procedures are specific
enough to support medical necessity and minimize claim denials.
"""

from __future__ import annotations

from typing import Any, Optional

# ICD-10 code descriptions for reference
ICD10_DESCRIPTIONS: dict[str, str] = {
    # Heart failure
    "I50.22": "Chronic systolic (systolic) heart failure",
    "I50.32": "Chronic diastolic (diastolic) heart failure",
    "I50.42": "Chronic combined systolic and diastolic heart failure",
    "I50.9": "Heart failure, unspecified",
    # Coronary artery disease
    "I25.10": "Atherosclerotic heart disease of native coronary artery without angina pectoris",
    "I25.110": "Atherosclerotic heart disease of native coronary artery with unstable angina pectoris",
    "I25.111": "Atherosclerotic heart disease of native coronary artery with angina pectoris with documented spasm",
    "I25.118": "Atherosclerotic heart disease of native coronary artery with other forms of angina pectoris",
    "I25.119": "Atherosclerotic heart disease of native coronary artery with unspecified angina pectoris",
    "I25.5": "Ischemic cardiomyopathy",
    # Acute MI
    "I21.01": "ST elevation MI involving left main coronary artery",
    "I21.02": "ST elevation MI involving left anterior descending coronary artery",
    "I21.09": "ST elevation MI involving other coronary artery of anterior wall",
    "I21.11": "ST elevation MI involving right coronary artery",
    "I21.19": "ST elevation MI involving other coronary artery of inferior wall",
    "I21.21": "ST elevation MI involving left circumflex coronary artery",
    "I21.4": "Non-ST elevation (NSTEMI) myocardial infarction",
    # Angina
    "I20.0": "Unstable angina",
    "I20.9": "Angina pectoris, unspecified",
    "I20.1": "Angina pectoris with documented spasm",
    # Cardiomyopathy
    "I42.0": "Dilated cardiomyopathy",
    "I42.1": "Obstructive hypertrophic cardiomyopathy",
    "I42.2": "Other hypertrophic cardiomyopathy",
    "I42.9": "Cardiomyopathy, unspecified",
    # Arrhythmias
    "I48.0": "Paroxysmal atrial fibrillation",
    "I48.1": "Persistent atrial fibrillation",
    "I48.11": "Longstanding persistent atrial fibrillation",
    "I48.2": "Chronic atrial fibrillation",
    "I48.20": "Chronic atrial fibrillation, unspecified",
    "I48.91": "Unspecified atrial fibrillation",
    "I47.1": "Supraventricular tachycardia",
    "I47.2": "Ventricular tachycardia",
    "I49.01": "Ventricular fibrillation",
    "I49.9": "Cardiac arrhythmia, unspecified",
    "I44.1": "Atrioventricular block, second degree",
    "I44.2": "Atrioventricular block, complete",
    "I49.5": "Sick sinus syndrome",
    "R00.1": "Bradycardia, unspecified",
    # Valvular
    "I35.0": "Nonrheumatic aortic (valve) stenosis",
    "I35.1": "Nonrheumatic aortic (valve) insufficiency",
    "I35.2": "Nonrheumatic aortic (valve) stenosis with insufficiency",
    "I34.0": "Nonrheumatic mitral (valve) insufficiency",
    "I34.1": "Nonrheumatic mitral (valve) prolapse",
    # Congenital
    "Q21.1": "Atrial septal defect",
    "Q21.0": "Ventricular septal defect",
    "Q23.0": "Congenital stenosis of aortic valve",
    # Pulmonary hypertension
    "I27.0": "Primary pulmonary hypertension",
    "I27.20": "Pulmonary hypertension, unspecified",
    "I27.21": "Secondary pulmonary arterial hypertension",
    # Symptoms (non-specific)
    "R07.9": "Chest pain, unspecified",
    "R07.89": "Other chest pain",
    "R00.0": "Tachycardia, unspecified",
    "R06.00": "Dyspnea, unspecified",
    "R06.02": "Shortness of breath",
    "R55": "Syncope and collapse",
    "R42": "Dizziness and giddiness",
    "R94.31": "Abnormal electrocardiogram [ECG] [EKG]",
    # Other
    "I10": "Essential (primary) hypertension",
    "E11.9": "Type 2 diabetes mellitus without complications",
    "E78.5": "Hyperlipidemia, unspecified",
    "Z86.73": "Personal history of transient ischemic attack (TIA)",
    "I63.9": "Cerebral infarction, unspecified",
    "D86.85": "Sarcoid myocarditis",
    "E85.4": "Organ-limited amyloidosis (cardiac amyloidosis)",
    "I40.9": "Acute myocarditis, unspecified",
    "I41": "Myocarditis in diseases classified elsewhere",
    "I51.7": "Cardiomegaly",
}

# Strong and weak pairings per CPT code
CODE_PAIRINGS: dict[str, dict[str, Any]] = {
    "93306": {  # TTE
        "strong": ["I50.22", "I50.32", "I50.42", "I25.10", "I42.0", "I42.1", "I42.9", "Q21.1", "I35.0", "I35.2", "I34.0", "I27.0"],
        "weak": ["R07.9", "R00.0", "R00.1", "R06.00", "I10"],
        "suggestions": {
            "R07.9": ["I20.9", "I25.10", "R07.89"],
            "R00.0": ["I49.9", "I47.1", "I48.91"],
            "R00.1": ["I49.5", "I44.1", "I44.2"],
            "R06.00": ["I50.22", "I50.32", "R06.02"],
            "I10": ["I50.32", "I51.7"],
        },
    },
    "93350": {  # Stress echo
        "strong": ["I25.10", "I25.110", "I25.118", "I20.0", "I20.9", "I50.22", "I42.0"],
        "weak": ["R07.9", "R06.00", "I10", "R94.31"],
        "suggestions": {
            "R07.9": ["I20.9", "I25.10", "I25.119"],
            "R06.00": ["I50.22", "I50.32", "R06.02"],
            "I10": ["I25.10", "I50.32"],
            "R94.31": ["I25.10", "I25.119"],
        },
    },
    "93458": {  # Left heart cath
        "strong": ["I25.10", "I25.110", "I20.0", "I21.01", "I21.02", "I21.4", "I25.5", "I50.22"],
        "weak": ["R07.9", "R06.00", "I10", "R94.31"],
        "suggestions": {
            "R07.9": ["I20.0", "I20.9", "I25.10"],
            "R06.00": ["I50.22", "I50.32", "I25.5"],
            "I10": ["I25.10", "I50.32"],
            "R94.31": ["I25.10", "I25.119"],
        },
    },
    "93460": {  # Right and left heart cath
        "strong": ["I27.0", "I27.20", "I27.21", "I50.22", "I50.42", "I35.0", "I35.2", "I42.0", "I42.1"],
        "weak": ["R07.9", "R06.00", "I10", "I50.9"],
        "suggestions": {
            "R07.9": ["I25.10", "I20.0", "I20.9"],
            "R06.00": ["I27.0", "I50.22", "I50.32"],
            "I10": ["I27.21", "I50.32"],
            "I50.9": ["I50.22", "I50.32", "I50.42"],
        },
    },
    "33361": {  # TAVR
        "strong": ["I35.0", "I35.2", "Q23.0"],
        "weak": ["I35.1", "R06.00", "R55"],
        "suggestions": {
            "I35.1": ["I35.0", "I35.2"],
            "R06.00": ["I35.0", "I50.22"],
            "R55": ["I35.0", "I35.2"],
        },
    },
    "93656": {  # AF ablation
        "strong": ["I48.0", "I48.1", "I48.11", "I48.2", "I48.20"],
        "weak": ["I48.91", "I49.9", "R00.0", "R42"],
        "suggestions": {
            "I48.91": ["I48.0", "I48.1", "I48.2"],
            "I49.9": ["I48.0", "I47.1"],
            "R00.0": ["I48.0", "I47.1", "I49.9"],
            "R42": ["I48.0", "I48.1"],
        },
    },
    "78452": {  # Nuclear stress test (SPECT MPI)
        "strong": ["I25.10", "I25.110", "I25.118", "I20.0", "I20.9", "I50.22", "I25.5"],
        "weak": ["R07.9", "R06.00", "I10", "R94.31", "E11.9"],
        "suggestions": {
            "R07.9": ["I20.9", "I25.10", "I25.119"],
            "R06.00": ["I50.22", "I50.32", "I25.5"],
            "I10": ["I25.10", "I50.32"],
            "R94.31": ["I25.10", "I25.119"],
            "E11.9": ["I25.10", "E11.9"],  # DM alone is weak; pair with CAD
        },
    },
    "75574": {  # Cardiac CTA
        "strong": ["I25.10", "I25.110", "I20.0", "I20.9", "I25.119"],
        "weak": ["R07.9", "R06.00", "I10", "R94.31"],
        "suggestions": {
            "R07.9": ["I20.9", "I25.10", "R07.89"],
            "R06.00": ["I50.22", "I25.5", "R06.02"],
            "I10": ["I25.10", "I25.119"],
            "R94.31": ["I25.10", "I20.9"],
        },
    },
    "75557": {  # Cardiac MRI
        "strong": ["I42.0", "I42.1", "I42.2", "D86.85", "E85.4", "I40.9", "I41", "I50.22", "I25.5"],
        "weak": ["R07.9", "I42.9", "R06.00", "I51.7"],
        "suggestions": {
            "R07.9": ["I42.0", "I40.9", "I20.9"],
            "I42.9": ["I42.0", "I42.1", "I42.2"],
            "R06.00": ["I42.0", "I50.22", "I50.32"],
            "I51.7": ["I42.0", "I42.9", "I50.22"],
        },
    },
    "92928": {  # PCI with stent, single vessel
        "strong": ["I25.10", "I25.110", "I25.118", "I20.0", "I21.4"],
        "weak": ["R07.9", "I10", "R94.31"],
        "suggestions": {
            "R07.9": ["I20.0", "I25.10", "I25.110"],
            "I10": ["I25.10", "I25.110"],
            "R94.31": ["I25.10", "I25.119"],
        },
    },
    "92941": {  # PCI acute MI
        "strong": ["I21.01", "I21.02", "I21.09", "I21.11", "I21.19", "I21.21", "I21.4"],
        "weak": ["I25.10", "R07.9", "I20.0"],
        "suggestions": {
            "I25.10": ["I21.4", "I21.01", "I21.11"],
            "R07.9": ["I21.4", "I20.0", "I21.01"],
            "I20.0": ["I21.4", "I21.01"],
        },
    },
    "33249": {  # ICD implant
        "strong": ["I50.22", "I50.42", "I42.0", "I25.5", "I47.2", "I49.01"],
        "weak": ["I50.9", "I42.9", "I49.9", "R55"],
        "suggestions": {
            "I50.9": ["I50.22", "I50.42"],
            "I42.9": ["I42.0", "I25.5"],
            "I49.9": ["I47.2", "I49.01"],
            "R55": ["I47.2", "I44.2", "I49.01"],
        },
    },
    "33208": {  # Pacemaker implant
        "strong": ["I44.1", "I44.2", "I49.5", "R00.1"],
        "weak": ["I49.9", "R55", "R42", "R00.0"],
        "suggestions": {
            "I49.9": ["I44.2", "I49.5", "I44.1"],
            "R55": ["I44.2", "I49.5"],
            "R42": ["I49.5", "I44.1"],
            "R00.0": ["I47.1", "I48.0"],
        },
    },
    "93291": {  # Remote monitoring, pacemaker
        "strong": ["I44.1", "I44.2", "I49.5", "R00.1"],
        "weak": ["I49.9", "Z95.0"],
        "suggestions": {
            "I49.9": ["I44.2", "I49.5"],
        },
    },
    "93295": {  # Remote monitoring, ICD
        "strong": ["I50.22", "I50.42", "I42.0", "I25.5", "I47.2", "I49.01"],
        "weak": ["I50.9", "I49.9"],
        "suggestions": {
            "I50.9": ["I50.22", "I50.42"],
            "I49.9": ["I47.2", "I49.01"],
        },
    },
    "93297": {  # Remote monitoring, ICM
        "strong": ["I48.0", "I48.1", "I63.9", "Z86.73", "R55"],
        "weak": ["I49.9", "R42", "R00.0"],
        "suggestions": {
            "I49.9": ["I48.0", "I47.1"],
            "R42": ["I48.0", "R55"],
            "R00.0": ["I48.0", "I47.1"],
        },
    },
    "93351": {  # Stress echo with contrast
        "strong": ["I25.10", "I25.110", "I20.0", "I20.9", "I50.22"],
        "weak": ["R07.9", "R06.00", "I10", "R94.31"],
        "suggestions": {
            "R07.9": ["I20.9", "I25.10", "I25.119"],
            "R06.00": ["I50.22", "I50.32", "R06.02"],
            "I10": ["I25.10", "I50.32"],
            "R94.31": ["I25.10", "I25.119"],
        },
    },
}

# Denial risk by code strength category
DENIAL_RISK: dict[str, dict[str, Any]] = {
    "strong": {
        "denial_risk": "low",
        "denial_rate_estimate": 0.05,
        "notes": "Strong pairing — supports medical necessity. Low denial risk.",
    },
    "weak": {
        "denial_risk": "high",
        "denial_rate_estimate": 0.35,
        "notes": "Weak/non-specific pairing — payers may deny for insufficient specificity. Upgrade recommended.",
    },
    "unknown": {
        "denial_risk": "moderate",
        "denial_rate_estimate": 0.15,
        "notes": "Code pairing not in validation database. Manual review recommended.",
    },
}


def check_code_pairing(cpt_code: str, icd10_codes: list[str]) -> dict[str, Any]:
    """Validate CPT + ICD-10 code pairings and return strength assessment with suggestions.

    Args:
        cpt_code: The CPT procedure code.
        icd10_codes: List of ICD-10 diagnosis codes paired with the procedure.

    Returns:
        dict with overall_strength, individual code assessments, and suggestions.
    """
    pairing_data = CODE_PAIRINGS.get(cpt_code)

    if not pairing_data:
        return {
            "cpt_code": cpt_code,
            "found": False,
            "error": f"CPT code {cpt_code} not found in code pairing database",
            "icd10_codes": icd10_codes,
        }

    strong_codes = set(pairing_data.get("strong", []))
    weak_codes = set(pairing_data.get("weak", []))
    suggestion_map = pairing_data.get("suggestions", {})

    assessments: list[dict[str, Any]] = []
    has_strong = False
    has_weak = False
    all_suggestions: list[dict[str, Any]] = []

    for code in icd10_codes:
        description = ICD10_DESCRIPTIONS.get(code, "Unknown diagnosis code")

        if code in strong_codes:
            has_strong = True
            assessments.append({
                "icd10_code": code,
                "description": description,
                "strength": "strong",
                "denial_risk": "low",
                "message": "Strong pairing — supports medical necessity",
            })
        elif code in weak_codes:
            has_weak = True
            suggestions = suggestion_map.get(code, [])
            suggestion_details = [
                {"code": s, "description": ICD10_DESCRIPTIONS.get(s, "Unknown")}
                for s in suggestions
            ]
            assessments.append({
                "icd10_code": code,
                "description": description,
                "strength": "weak",
                "denial_risk": "high",
                "message": f"Non-specific code — high denial risk. Consider upgrading.",
                "suggested_alternatives": suggestion_details,
            })
            all_suggestions.append({
                "current_code": code,
                "current_description": description,
                "alternatives": suggestion_details,
            })
        else:
            assessments.append({
                "icd10_code": code,
                "description": description,
                "strength": "unknown",
                "denial_risk": "moderate",
                "message": "Code not in validation database for this procedure. Manual review recommended.",
            })

    # Determine overall strength
    if has_strong and not has_weak:
        overall = "strong"
    elif has_strong and has_weak:
        overall = "mixed"
    elif has_weak and not has_strong:
        overall = "weak"
    else:
        overall = "unknown"

    # Estimate denial risk
    risk_scores = {"strong": 0.05, "weak": 0.35, "unknown": 0.15, "mixed": 0.15}
    estimated_denial_risk = risk_scores.get(overall, 0.15)

    return {
        "cpt_code": cpt_code,
        "found": True,
        "overall_strength": overall,
        "estimated_denial_risk": estimated_denial_risk,
        "code_assessments": assessments,
        "upgrade_suggestions": all_suggestions,
        "total_codes": len(icd10_codes),
        "strong_count": sum(1 for a in assessments if a["strength"] == "strong"),
        "weak_count": sum(1 for a in assessments if a["strength"] == "weak"),
        "unknown_count": sum(1 for a in assessments if a["strength"] == "unknown"),
    }


def suggest_stronger_codes(
    cpt_code: str, weak_code: str, chart_data: Optional[dict[str, Any]] = None
) -> list[dict[str, Any]]:
    """Suggest stronger ICD-10 codes to replace a weak code for a given procedure.

    Args:
        cpt_code: The CPT procedure code.
        weak_code: The weak ICD-10 code to find alternatives for.
        chart_data: Optional clinical chart data to inform suggestions.

    Returns:
        List of suggested replacement codes with descriptions and clinical guidance.
    """
    pairing_data = CODE_PAIRINGS.get(cpt_code, {})
    suggestion_map = pairing_data.get("suggestions", {})
    alternatives = suggestion_map.get(weak_code, [])

    if not alternatives:
        # Fall back to returning all strong codes for this CPT
        strong_codes = pairing_data.get("strong", [])
        return [
            {
                "code": code,
                "description": ICD10_DESCRIPTIONS.get(code, "Unknown"),
                "source": "strong_pairing_list",
                "clinical_guidance": "Review chart for documentation supporting this diagnosis.",
            }
            for code in strong_codes[:5]
        ]

    results = []
    for code in alternatives:
        description = ICD10_DESCRIPTIONS.get(code, "Unknown")
        guidance = _get_clinical_guidance(weak_code, code, chart_data)
        results.append({
            "code": code,
            "description": description,
            "replaces": weak_code,
            "replaces_description": ICD10_DESCRIPTIONS.get(weak_code, "Unknown"),
            "source": "suggestion_map",
            "clinical_guidance": guidance,
        })

    return results


def _get_clinical_guidance(weak_code: str, suggested_code: str, chart_data: Optional[dict[str, Any]] = None) -> str:
    """Generate clinical guidance for upgrading from a weak to a strong code."""
    guidance_map: dict[str, dict[str, str]] = {
        "R07.9": {
            "I20.0": "If patient has crescendo angina or rest pain, document unstable angina (I20.0) with onset, frequency, and severity.",
            "I20.9": "If chest pain is exertional and typical for angina, code as angina pectoris (I20.9). Document character, location, radiation, and exertional nature.",
            "I25.10": "If patient has known CAD or positive stress test, code as atherosclerotic heart disease (I25.10).",
            "I25.110": "If known CAD with new unstable pattern, use I25.110 (ASHD with unstable angina).",
            "R07.89": "At minimum, specify chest pain type (R07.89) rather than unspecified.",
        },
        "R06.00": {
            "I50.22": "If dyspnea is due to heart failure with reduced EF, code systolic heart failure (I50.22). Document EF and NYHA class.",
            "I50.32": "If dyspnea with preserved EF and diastolic dysfunction, code diastolic heart failure (I50.32).",
            "R06.02": "At minimum, code as shortness of breath (R06.02) rather than dyspnea unspecified.",
            "I25.5": "If dyspnea is from ischemic cardiomyopathy, code I25.5 with supporting cath or imaging data.",
        },
        "R00.0": {
            "I48.0": "If tachycardia is atrial fibrillation, specify paroxysmal (I48.0), persistent (I48.1), or chronic (I48.2).",
            "I47.1": "If SVT documented on ECG or monitor, code I47.1 (supraventricular tachycardia).",
            "I49.9": "If specific arrhythmia identified, code to the specific rhythm disturbance.",
        },
        "I48.91": {
            "I48.0": "Specify AF type: paroxysmal (I48.0) if self-terminating <7 days.",
            "I48.1": "Persistent AF (I48.1) if continuous >7 days requiring intervention to terminate.",
            "I48.2": "Chronic/permanent AF (I48.2) if decision made to not pursue rhythm control.",
        },
        "I50.9": {
            "I50.22": "Specify as systolic (I50.22) if EF ≤40%. Document EF value and date measured.",
            "I50.32": "Specify as diastolic (I50.32) if EF preserved with diastolic dysfunction on echo.",
            "I50.42": "Combined systolic and diastolic (I50.42) if both present.",
        },
        "I42.9": {
            "I42.0": "If dilated cardiomyopathy confirmed on imaging, code I42.0. Document LV dimensions.",
            "I42.1": "If obstructive HCM with LVOT gradient, code I42.1. Document gradient.",
            "I42.2": "If non-obstructive HCM, code I42.2.",
        },
    }

    weak_guidance = guidance_map.get(weak_code, {})
    specific_guidance = weak_guidance.get(suggested_code)
    if specific_guidance:
        return specific_guidance

    return f"Review chart documentation to determine if {suggested_code} ({ICD10_DESCRIPTIONS.get(suggested_code, 'Unknown')}) is supported. Document clinical findings that establish this diagnosis."


def estimate_clean_claim_impact(
    current_codes: list[str], suggested_codes: list[str]
) -> dict[str, Any]:
    """Estimate the improvement in clean claim rate from upgrading ICD-10 codes.

    Args:
        current_codes: Current ICD-10 codes on the claim.
        suggested_codes: Proposed upgraded ICD-10 codes.

    Returns:
        dict with estimated denial rate improvement and financial impact estimate.
    """
    def _code_specificity_score(code: str) -> float:
        """Rate code specificity 0-1. More specific = higher score."""
        # R-codes (symptoms) are least specific
        if code.startswith("R"):
            return 0.3
        # Unspecified codes (ending in .9, .90, .91)
        if code.endswith(".9") or code.endswith(".90") or code.endswith(".91"):
            return 0.5
        # Specific codes with 4+ characters after decimal
        parts = code.split(".")
        if len(parts) == 2 and len(parts[1]) >= 2:
            return 0.9
        # Standard specific codes
        return 0.7

    current_avg = sum(_code_specificity_score(c) for c in current_codes) / max(len(current_codes), 1)
    suggested_avg = sum(_code_specificity_score(c) for c in suggested_codes) / max(len(suggested_codes), 1)

    improvement = suggested_avg - current_avg
    current_denial_estimate = max(0.05, 0.40 - (current_avg * 0.35))
    suggested_denial_estimate = max(0.05, 0.40 - (suggested_avg * 0.35))

    # Estimated financial impact (average cardiology claim ~$1,500)
    avg_claim_value = 1500.0
    monthly_claims = 200  # Typical cardiology practice monthly claim volume
    monthly_savings = (current_denial_estimate - suggested_denial_estimate) * avg_claim_value * monthly_claims

    return {
        "current_codes": current_codes,
        "suggested_codes": suggested_codes,
        "current_specificity_score": round(current_avg, 3),
        "suggested_specificity_score": round(suggested_avg, 3),
        "specificity_improvement": round(improvement, 3),
        "current_estimated_denial_rate": round(current_denial_estimate, 3),
        "suggested_estimated_denial_rate": round(suggested_denial_estimate, 3),
        "denial_rate_reduction": round(current_denial_estimate - suggested_denial_estimate, 3),
        "estimated_monthly_savings": round(monthly_savings, 2),
        "estimated_annual_savings": round(monthly_savings * 12, 2),
        "assumptions": {
            "avg_claim_value": avg_claim_value,
            "monthly_claim_volume": monthly_claims,
            "note": "Estimates based on code specificity scoring model. Actual results vary by payer and practice.",
        },
    }
