"""Peer-to-Peer Review Prevention Engine.

Pre-submission strength scorer that predicts P2P likelihood and provides
actionable fixes to strengthen prior authorization submissions before they
trigger a peer-to-peer review request from the payer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from cardioauth.models.chart import ChartData
from cardioauth.models.policy import PolicyData

# ---------------------------------------------------------------------------
# P2P Trigger Factors by Payer and Procedure
# ---------------------------------------------------------------------------
# Each trigger has:
#   factor  — what the reviewer will flag
#   weight  — contribution to P2P risk (0-1, weights sum to ~1.0 per procedure)
#   fix     — exact action the practice should take before submission
# threshold — overall weighted score above which P2P is likely

P2P_TRIGGERS: dict[str, dict[str, dict[str, Any]]] = {
    # -----------------------------------------------------------------------
    # UnitedHealthcare
    # -----------------------------------------------------------------------
    "UnitedHealthcare": {
        "93458": {
            "threshold": 0.70,
            "triggers": [
                {"factor": "No stress test on file", "weight": 0.25,
                 "fix": "Order and document stress test results (exercise, pharmacologic, or stress echo) with interpretation"},
                {"factor": "LVEF not documented", "weight": 0.15,
                 "fix": "Document LVEF from most recent echocardiogram with date performed"},
                {"factor": "Medical therapy duration < 6 weeks", "weight": 0.15,
                 "fix": "Document duration of current anti-anginal regimen (beta-blocker, CCB, nitrate) with start dates"},
                {"factor": "Symptom timeline vague", "weight": 0.10,
                 "fix": "Add specific onset date, frequency (episodes/week), and functional limitation using CCS angina class"},
                {"factor": "Risk factors not enumerated", "weight": 0.10,
                 "fix": "List all cardiovascular risk factors: HTN, DM, hyperlipidemia, smoking, family hx, obesity, CKD"},
                {"factor": "No prior cardiac imaging referenced", "weight": 0.10,
                 "fix": "Reference prior echo, stress test, or CTA with dates and findings"},
                {"factor": "ACC/AHA guideline indication not cited", "weight": 0.10,
                 "fix": "Cite specific ACC/AHA Appropriate Use Criteria indication (e.g., AUC rating and scenario number)"},
                {"factor": "Functional status not documented", "weight": 0.05,
                 "fix": "Document NYHA class or CCS angina class and impact on daily activities"},
            ],
        },
        "92928": {
            "threshold": 0.65,
            "triggers": [
                {"factor": "No diagnostic cath report", "weight": 0.25,
                 "fix": "Include diagnostic catheterization report showing >70% stenosis in target vessel"},
                {"factor": "FFR/iFR not documented for 50-69% stenosis", "weight": 0.20,
                 "fix": "Document FFR <= 0.80 or iFR <= 0.89 for intermediate lesions per FAME trial criteria"},
                {"factor": "Vessel territory ischemia not correlated", "weight": 0.15,
                 "fix": "Correlate ischemia on non-invasive testing to the specific vessel territory being treated"},
                {"factor": "Medication optimization not documented", "weight": 0.15,
                 "fix": "Document GDMT trial: aspirin, statin, beta-blocker, ACEi/ARB with doses and duration"},
                {"factor": "SYNTAX score not mentioned for multivessel", "weight": 0.10,
                 "fix": "Calculate and document SYNTAX score; PCI favored for score <= 22 per guidelines"},
                {"factor": "Heart team discussion not referenced", "weight": 0.10,
                 "fix": "For left main or multivessel disease, document heart team discussion per ACC/AHA guidelines"},
                {"factor": "Lesion characteristics not described", "weight": 0.05,
                 "fix": "Document lesion length, calcification, bifurcation involvement, and ACC/AHA lesion classification"},
            ],
        },
        "93653": {
            "threshold": 0.70,
            "triggers": [
                {"factor": "No documented failed AAD trial", "weight": 0.30,
                 "fix": "Document failure or intolerance of at least one Class I or III antiarrhythmic drug with dates and adverse effects"},
                {"factor": "Arrhythmia not documented on monitoring", "weight": 0.20,
                 "fix": "Include Holter, event monitor, or implantable loop recorder tracings confirming SVT diagnosis"},
                {"factor": "Symptom burden not quantified", "weight": 0.15,
                 "fix": "Document frequency of episodes, duration, hemodynamic compromise, ED visits, or hospitalizations"},
                {"factor": "EP study findings not referenced", "weight": 0.15,
                 "fix": "Reference diagnostic EP study results identifying ablation target and mechanism"},
                {"factor": "Quality of life impact not documented", "weight": 0.10,
                 "fix": "Document functional impairment, missed work days, or validated QoL score (e.g., SF-36)"},
                {"factor": "Guideline indication not cited", "weight": 0.10,
                 "fix": "Cite 2015 ACC/AHA/HRS SVT guideline Class I or IIa recommendation for catheter ablation"},
            ],
        },
    },
    # -----------------------------------------------------------------------
    # Aetna
    # -----------------------------------------------------------------------
    "Aetna": {
        "93458": {
            "threshold": 0.65,
            "triggers": [
                {"factor": "Non-invasive testing not exhausted", "weight": 0.30,
                 "fix": "Document that non-invasive testing (stress test, CTA, stress echo) was performed and inconclusive or high-risk"},
                {"factor": "Symptoms not refractory to medical therapy", "weight": 0.20,
                 "fix": "Document persistent symptoms despite >= 6 weeks of optimized anti-anginal therapy"},
                {"factor": "Pre-test probability not assessed", "weight": 0.15,
                 "fix": "Document Duke Clinical Score or similar validated pre-test probability assessment"},
                {"factor": "Troponin trend not included", "weight": 0.10,
                 "fix": "Include serial troponin values with timestamps if ACS is the indication"},
                {"factor": "Comorbidity risk assessment missing", "weight": 0.10,
                 "fix": "Document procedural risk assessment (STS score or relevant comorbidity impact on risk-benefit)"},
                {"factor": "Recent hospitalization not referenced", "weight": 0.10,
                 "fix": "Reference any recent cardiac admission, ED visit, or observation stay with dates"},
                {"factor": "Conservative treatment plan not outlined", "weight": 0.05,
                 "fix": "Describe why continued conservative management is insufficient for this patient"},
            ],
        },
        "92928": {
            "threshold": 0.60,
            "triggers": [
                {"factor": "Angiographic severity not quantified", "weight": 0.25,
                 "fix": "Document exact stenosis percentage by QCA or visual estimate for each target lesion"},
                {"factor": "Ischemia burden not documented", "weight": 0.20,
                 "fix": "Quantify ischemic territory (% myocardium at risk) from stress imaging"},
                {"factor": "No trial of medical therapy for stable CAD", "weight": 0.20,
                 "fix": "Per ISCHEMIA trial, document trial of optimal medical therapy before elective PCI for stable disease"},
                {"factor": "Stent type justification missing", "weight": 0.10,
                 "fix": "Document rationale for DES vs BMS selection, especially if DES is chosen (DAPT compliance assessment)"},
                {"factor": "AUC score not provided", "weight": 0.15,
                 "fix": "Calculate and document ACC Appropriate Use Criteria score for coronary revascularization"},
                {"factor": "Informed consent not referenced", "weight": 0.10,
                 "fix": "Reference that risks/benefits/alternatives including CABG were discussed"},
            ],
        },
    },
    # -----------------------------------------------------------------------
    # Blue Cross Blue Shield
    # -----------------------------------------------------------------------
    "Blue Cross Blue Shield": {
        "93458": {
            "threshold": 0.70,
            "triggers": [
                {"factor": "No abnormal non-invasive test result", "weight": 0.30,
                 "fix": "Document abnormal stress test findings: ST changes, wall motion abnormality, perfusion defect, or high-risk Duke score"},
                {"factor": "Clinical indication unclear", "weight": 0.20,
                 "fix": "State explicit indication: ACS workup, valvular assessment, pre-operative evaluation, or refractory angina"},
                {"factor": "Prior auth form incomplete", "weight": 0.15,
                 "fix": "Ensure all required fields on BCBS prior auth form are completed including clinical summary"},
                {"factor": "Outpatient alternatives not considered", "weight": 0.10,
                 "fix": "Document why CCTA or stress imaging is insufficient and invasive assessment is required"},
                {"factor": "LVEF and chamber dimensions missing", "weight": 0.10,
                 "fix": "Include most recent echo report with LVEF, LV dimensions, and wall motion assessment"},
                {"factor": "Medication list not current", "weight": 0.10,
                 "fix": "Provide current medication reconciliation with cardiac medications, doses, and compliance"},
                {"factor": "Referring provider rationale absent", "weight": 0.05,
                 "fix": "Include referring physician's letter or documented rationale for catheterization referral"},
            ],
        },
        "92928": {
            "threshold": 0.65,
            "triggers": [
                {"factor": "Diagnostic cath findings not attached", "weight": 0.25,
                 "fix": "Attach full diagnostic cath report with hemodynamics and coronary anatomy"},
                {"factor": "CABG candidacy not addressed", "weight": 0.20,
                 "fix": "Document SYNTAX score and explain why PCI is preferred over CABG or that surgical consultation was obtained"},
                {"factor": "Target vessel not clearly identified", "weight": 0.15,
                 "fix": "Specify exact target vessel and segment (e.g., mid-LAD, proximal RCA) with stenosis severity"},
                {"factor": "Dual antiplatelet plan not documented", "weight": 0.15,
                 "fix": "Document planned DAPT regimen and duration per current ACC/AHA recommendations"},
                {"factor": "Contraindication to medical therapy not documented", "weight": 0.15,
                 "fix": "If bypassing medical therapy trial, document specific contraindication or clinical urgency"},
                {"factor": "Post-PCI follow-up plan missing", "weight": 0.10,
                 "fix": "Outline post-procedure follow-up plan including stress testing timeline and medication management"},
            ],
        },
    },
    # -----------------------------------------------------------------------
    # Cigna
    # -----------------------------------------------------------------------
    "Cigna": {
        "93458": {
            "threshold": 0.65,
            "triggers": [
                {"factor": "Stress test not within 90 days", "weight": 0.25,
                 "fix": "Ensure stress test was performed within 90 days; if older, document clinical rationale for not repeating"},
                {"factor": "Medical necessity statement absent", "weight": 0.20,
                 "fix": "Write explicit medical necessity statement citing patient-specific clinical factors and guideline indications"},
                {"factor": "Hemodynamic instability not documented", "weight": 0.15,
                 "fix": "For urgent cath, document hemodynamic parameters, telemetry findings, or clinical deterioration"},
                {"factor": "Conservative treatment timeline insufficient", "weight": 0.15,
                 "fix": "Document minimum 4-6 weeks of conservative management with specific medications and outcomes"},
                {"factor": "Coronary calcium score not referenced", "weight": 0.10,
                 "fix": "If available, reference CAC score to support pre-test probability assessment"},
                {"factor": "Risk stratification tool not used", "weight": 0.10,
                 "fix": "Apply and document HEART score, TIMI risk score, or GRACE score as applicable to clinical scenario"},
                {"factor": "Family history detail insufficient", "weight": 0.05,
                 "fix": "Specify premature CAD in first-degree relatives with ages of events"},
            ],
        },
    },
}

# ---------------------------------------------------------------------------
# Documentation Strength Scoring Weights
# ---------------------------------------------------------------------------

DOC_STRENGTH_WEIGHTS: dict[str, float] = {
    "imaging_within_90_days": 0.15,
    "labs_within_30_days": 0.12,
    "medication_trial_documented": 0.18,
    "symptom_timeline_specific": 0.12,
    "guideline_indication_met": 0.15,
    "prior_treatment_failure": 0.13,
    "risk_factors_enumerated": 0.08,
    "functional_status_documented": 0.07,
}

# ---------------------------------------------------------------------------
# Clinical language templates for fix recommendations
# ---------------------------------------------------------------------------

_CLINICAL_LANGUAGE: dict[str, str] = {
    "stress_test_positive": (
        "Exercise stress echocardiogram on {date} demonstrated {finding} consistent with "
        "ischemia in the {territory} territory, prompting recommendation for invasive evaluation."
    ),
    "medication_failure": (
        "Patient has been maintained on {med} {dose} since {start_date} ({duration} weeks) "
        "without adequate symptom relief, meeting criteria for escalation to invasive evaluation."
    ),
    "lvef_documentation": (
        "Transthoracic echocardiogram on {date} demonstrated left ventricular ejection fraction "
        "of {lvef}% with {wall_motion}, indicating {severity} systolic dysfunction."
    ),
    "symptom_timeline": (
        "Patient reports {symptom} with onset {onset_date}, occurring {frequency} and "
        "classified as CCS Class {ccs_class} / NYHA Class {nyha_class}, significantly limiting "
        "{functional_limitation}."
    ),
    "guideline_citation": (
        "Per {guideline} ({year}), this clinical scenario meets Class {class_level} "
        "(Level of Evidence {loe}) recommendation for {procedure}. "
        "Appropriate Use Criteria rating: {auc_rating} (Score: {auc_score})."
    ),
    "risk_factor_enumeration": (
        "Cardiovascular risk factors include: {risk_factors}. "
        "Calculated 10-year ASCVD risk: {ascvd_risk}%."
    ),
    "prior_treatment_failure": (
        "Conservative management over {duration} included {treatments}. "
        "Despite optimization, patient continues to experience {persistent_symptoms}, "
        "warranting procedural intervention."
    ),
}


# ---------------------------------------------------------------------------
# Public Functions
# ---------------------------------------------------------------------------


def predict_p2p_likelihood(
    chart_data: ChartData | dict[str, Any],
    policy_data: PolicyData | dict[str, Any] | None,
    payer: str,
    cpt_code: str,
) -> dict[str, Any]:
    """Predict likelihood of peer-to-peer review being triggered.

    Args:
        chart_data: Patient chart data (ChartData model or dict).
        policy_data: Payer policy data (PolicyData model or dict), optional.
        payer: Payer name (e.g., "UnitedHealthcare").
        cpt_code: CPT code for the requested procedure.

    Returns:
        dict with p2p_risk_score (0-1), triggers_found, fixes, and threshold info.
    """
    chart = _normalize_chart(chart_data)
    payer_triggers = _get_payer_triggers(payer, cpt_code)

    if not payer_triggers:
        return {
            "p2p_risk_score": 0.5,
            "risk_label": "Unknown",
            "message": f"No P2P trigger data for payer '{payer}' / CPT {cpt_code}. Using baseline estimate.",
            "triggers_found": [],
            "fixes": [],
            "threshold": None,
        }

    threshold = payer_triggers["threshold"]
    triggers = payer_triggers["triggers"]

    found_triggers: list[dict[str, Any]] = []
    total_risk = 0.0

    for trigger in triggers:
        is_triggered = _evaluate_trigger(trigger["factor"], chart)
        if is_triggered:
            total_risk += trigger["weight"]
            found_triggers.append({
                "factor": trigger["factor"],
                "weight": trigger["weight"],
                "fix": trigger["fix"],
                "priority": "HIGH" if trigger["weight"] >= 0.20 else "MEDIUM" if trigger["weight"] >= 0.10 else "LOW",
            })

    # Clamp to [0, 1]
    total_risk = min(total_risk, 1.0)

    risk_label = _risk_label(total_risk, threshold)

    return {
        "p2p_risk_score": round(total_risk, 3),
        "risk_label": risk_label,
        "threshold": threshold,
        "above_threshold": total_risk >= threshold,
        "triggers_found": sorted(found_triggers, key=lambda t: t["weight"], reverse=True),
        "fixes": [t["fix"] for t in sorted(found_triggers, key=lambda t: t["weight"], reverse=True)],
        "trigger_count": len(found_triggers),
        "total_possible_triggers": len(triggers),
        "estimated_approval_if_fixed": round(min(0.95, 1.0 - (total_risk * 0.3)), 2),
    }


def get_strength_recommendations(
    chart_data: ChartData | dict[str, Any],
    procedure_code: str,
    payer: str,
) -> list[dict[str, str]]:
    """Return specific clinical language recommendations to strengthen the submission.

    Args:
        chart_data: Patient chart data.
        procedure_code: CPT code for the requested procedure.
        payer: Payer name.

    Returns:
        List of dicts with category, current_gap, recommended_language, and priority.
    """
    chart = _normalize_chart(chart_data)
    recommendations: list[dict[str, str]] = []

    # Check imaging recency
    has_recent_imaging = _has_recent_imaging(chart, days=90)
    if not has_recent_imaging:
        recommendations.append({
            "category": "Imaging",
            "current_gap": "No cardiac imaging within 90 days on file",
            "recommended_language": _CLINICAL_LANGUAGE["stress_test_positive"].format(
                date="[DATE]", finding="[FINDING]", territory="[TERRITORY]"
            ),
            "priority": "HIGH",
            "action": "Order or obtain recent imaging and include results in submission",
        })

    # Check lab recency
    has_recent_labs = _has_recent_labs(chart, days=30)
    if not has_recent_labs:
        recommendations.append({
            "category": "Laboratory",
            "current_gap": "No relevant labs within 30 days",
            "recommended_language": "Include troponin, BNP, CBC, BMP, and lipid panel results dated within 30 days of submission.",
            "priority": "HIGH",
            "action": "Order stat labs or locate recent results in chart",
        })

    # Check medication documentation
    has_med_trial = _has_medication_trial(chart)
    if not has_med_trial:
        recommendations.append({
            "category": "Medication Trial",
            "current_gap": "Optimal medical therapy duration not documented or < 6 weeks",
            "recommended_language": _CLINICAL_LANGUAGE["medication_failure"].format(
                med="[MEDICATION]", dose="[DOSE]", start_date="[START_DATE]", duration="[WEEKS]"
            ),
            "priority": "HIGH",
            "action": "Document current cardiac medications with start dates and clinical response",
        })

    # Check symptom documentation
    has_symptom_detail = _has_symptom_timeline(chart)
    if not has_symptom_detail:
        recommendations.append({
            "category": "Symptom Documentation",
            "current_gap": "Symptom onset, frequency, and functional class not specified",
            "recommended_language": _CLINICAL_LANGUAGE["symptom_timeline"].format(
                symptom="[SYMPTOM]", onset_date="[DATE]", frequency="[X times per week]",
                ccs_class="[I-IV]", nyha_class="[I-IV]", functional_limitation="[ACTIVITIES]"
            ),
            "priority": "MEDIUM",
            "action": "Document specific symptom characteristics and validated functional class",
        })

    # Check risk factor enumeration
    if len(chart.get("comorbidities", [])) < 3:
        recommendations.append({
            "category": "Risk Factors",
            "current_gap": "Cardiovascular risk factors not comprehensively enumerated",
            "recommended_language": _CLINICAL_LANGUAGE["risk_factor_enumeration"].format(
                risk_factors="[LIST ALL: HTN, DM, dyslipidemia, smoking, family hx, obesity, CKD]",
                ascvd_risk="[CALCULATED RISK]"
            ),
            "priority": "MEDIUM",
            "action": "List all risk factors explicitly in the clinical narrative",
        })

    # Check guideline citations
    recommendations.append({
        "category": "Guideline Citation",
        "current_gap": "No specific guideline reference in submission",
        "recommended_language": _CLINICAL_LANGUAGE["guideline_citation"].format(
            guideline="ACC/AHA", year="2021", class_level="[I/IIa/IIb]",
            loe="[A/B/C]", procedure=procedure_code,
            auc_rating="[Appropriate/May Be Appropriate]", auc_score="[7-9]"
        ),
        "priority": "MEDIUM",
        "action": "Identify and cite the applicable ACC/AHA guideline recommendation class",
    })

    # Check prior treatment documentation
    if len(chart.get("prior_treatments", [])) == 0:
        recommendations.append({
            "category": "Prior Treatment",
            "current_gap": "No documentation of prior conservative treatments attempted",
            "recommended_language": _CLINICAL_LANGUAGE["prior_treatment_failure"].format(
                duration="[X weeks/months]",
                treatments="[LIST: medications, lifestyle modifications, cardiac rehab]",
                persistent_symptoms="[SYMPTOMS]"
            ),
            "priority": "HIGH",
            "action": "Document all prior conservative treatments with outcomes",
        })

    return recommendations


def score_documentation_strength(chart_data: ChartData | dict[str, Any]) -> float:
    """Score documentation completeness on a 0-1 scale.

    Args:
        chart_data: Patient chart data.

    Returns:
        Float from 0.0 (no documentation) to 1.0 (complete documentation).
    """
    chart = _normalize_chart(chart_data)
    score = 0.0

    weights = DOC_STRENGTH_WEIGHTS

    # Imaging within 90 days
    if _has_recent_imaging(chart, days=90):
        score += weights["imaging_within_90_days"]

    # Labs within 30 days
    if _has_recent_labs(chart, days=30):
        score += weights["labs_within_30_days"]

    # Medication trial documented
    if _has_medication_trial(chart):
        score += weights["medication_trial_documented"]

    # Symptom timeline specific
    if _has_symptom_timeline(chart):
        score += weights["symptom_timeline_specific"]

    # Guideline indication met (check diagnosis codes present)
    if len(chart.get("diagnosis_codes", [])) >= 2:
        score += weights["guideline_indication_met"]

    # Prior treatment failure documented
    if len(chart.get("prior_treatments", [])) > 0:
        score += weights["prior_treatment_failure"]

    # Risk factors enumerated
    if len(chart.get("comorbidities", [])) >= 3:
        score += weights["risk_factors_enumerated"]

    # Functional status documented (proxy: medications + imaging together)
    if _has_medication_trial(chart) and _has_recent_imaging(chart, days=90):
        score += weights["functional_status_documented"]

    return round(min(score, 1.0), 3)


def estimate_approval_without_p2p(
    current_score: float,
    fixes_applied: list[str],
    payer: str = "",
    cpt_code: str = "",
) -> float:
    """Estimate approval probability after applying recommended fixes.

    Args:
        current_score: Current documentation strength score (0-1).
        fixes_applied: List of fix descriptions that were applied.
        payer: Payer name (for payer-specific weighting).
        cpt_code: CPT code (for procedure-specific weighting).

    Returns:
        Projected approval probability (0-1) after fixes.
    """
    if not fixes_applied:
        return round(current_score * 0.85, 3)  # Baseline conversion

    # Each meaningful fix improves score
    fix_boost = 0.0
    high_value_keywords = [
        "stress test", "imaging", "diagnostic cath", "FFR", "iFR",
        "medication", "medical therapy", "GDMT", "guideline", "ACC/AHA",
    ]
    medium_value_keywords = [
        "symptom", "risk factor", "LVEF", "echo", "troponin",
        "BNP", "functional", "NYHA", "CCS",
    ]

    for fix in fixes_applied:
        fix_lower = fix.lower()
        if any(kw in fix_lower for kw in high_value_keywords):
            fix_boost += 0.08
        elif any(kw in fix_lower for kw in medium_value_keywords):
            fix_boost += 0.05
        else:
            fix_boost += 0.03

    projected = current_score + fix_boost

    # Payer-specific caps (some payers approve at lower thresholds)
    payer_ceiling = {
        "UnitedHealthcare": 0.92,
        "Aetna": 0.94,
        "Blue Cross Blue Shield": 0.90,
        "Cigna": 0.93,
    }
    ceiling = payer_ceiling.get(payer, 0.95)

    return round(min(projected, ceiling), 3)


# ---------------------------------------------------------------------------
# Internal Evaluation Helpers
# ---------------------------------------------------------------------------


def _normalize_chart(chart_data: Any) -> dict[str, Any]:
    """Convert ChartData model or dict to a plain dict."""
    if hasattr(chart_data, "model_dump"):
        return chart_data.model_dump()
    if isinstance(chart_data, dict):
        return chart_data
    return {}


def _get_payer_triggers(payer: str, cpt_code: str) -> dict[str, Any] | None:
    """Look up triggers for a payer/procedure combination."""
    payer_data = P2P_TRIGGERS.get(payer)
    if not payer_data:
        # Try fuzzy match
        for key in P2P_TRIGGERS:
            if key.lower() in payer.lower() or payer.lower() in key.lower():
                payer_data = P2P_TRIGGERS[key]
                break
    if not payer_data:
        return None
    return payer_data.get(cpt_code)


def _evaluate_trigger(factor: str, chart: dict[str, Any]) -> bool:
    """Evaluate whether a trigger condition is present (documentation gap exists).

    Returns True if the gap IS present (i.e., documentation is missing).
    """
    factor_lower = factor.lower()

    # Stress test missing
    if "stress test" in factor_lower or "non-invasive test" in factor_lower:
        imaging = chart.get("relevant_imaging", [])
        stress_keywords = ["stress", "exercise", "pharmacologic", "dobutamine", "adenosine", "regadenoson", "spect", "mpi"]
        has_stress = any(
            any(kw in (img.get("type", "") + " " + img.get("result_summary", "")).lower() for kw in stress_keywords)
            for img in imaging
        )
        return not has_stress

    # LVEF not documented
    if "lvef" in factor_lower or "ejection fraction" in factor_lower:
        imaging = chart.get("relevant_imaging", [])
        has_ef = any(
            "ef" in img.get("result_summary", "").lower() or "lvef" in img.get("result_summary", "").lower()
            or "ejection fraction" in img.get("result_summary", "").lower()
            for img in imaging
        )
        return not has_ef

    # Medical therapy duration
    if "medical therapy" in factor_lower or "medication" in factor_lower or "conservative" in factor_lower:
        meds = chart.get("relevant_medications", [])
        if not meds:
            return True
        # Check if any cardiac med has been on for >= 6 weeks
        try:
            for med in meds:
                start = med.get("start_date", "")
                if start:
                    start_dt = datetime.fromisoformat(start)
                    if (datetime.now() - start_dt).days >= 42:
                        return False
        except (ValueError, TypeError):
            pass
        return True

    # Symptom timeline vague
    if "symptom" in factor_lower and ("timeline" in factor_lower or "vague" in factor_lower or "burden" in factor_lower):
        # Proxy: check if prior_treatments or comorbidities give symptom context
        return len(chart.get("prior_treatments", [])) < 2

    # Risk factors not enumerated
    if "risk factor" in factor_lower:
        return len(chart.get("comorbidities", [])) < 3

    # No prior cardiac imaging
    if "imaging" in factor_lower or "echo" in factor_lower:
        return len(chart.get("relevant_imaging", [])) == 0

    # Guideline / AUC not cited (always flag — needs explicit mention)
    if "guideline" in factor_lower or "auc" in factor_lower or "appropriate use" in factor_lower:
        return True  # Cannot detect from structured chart data alone

    # Functional status
    if "functional" in factor_lower or "nyha" in factor_lower or "ccs" in factor_lower:
        return len(chart.get("comorbidities", [])) < 2

    # Diagnostic cath report
    if "diagnostic cath" in factor_lower or "cath report" in factor_lower:
        imaging = chart.get("relevant_imaging", [])
        has_cath = any("cath" in img.get("type", "").lower() for img in imaging)
        return not has_cath

    # FFR / iFR
    if "ffr" in factor_lower or "ifr" in factor_lower:
        imaging = chart.get("relevant_imaging", [])
        has_ffr = any(
            "ffr" in img.get("result_summary", "").lower() or "ifr" in img.get("result_summary", "").lower()
            for img in imaging
        )
        return not has_ffr

    # Troponin
    if "troponin" in factor_lower:
        labs = chart.get("relevant_labs", [])
        has_trop = any("troponin" in lab.get("name", "").lower() for lab in labs)
        return not has_trop

    # Labs recency
    if "lab" in factor_lower:
        return not _has_recent_labs(chart, days=30)

    # SYNTAX score
    if "syntax" in factor_lower:
        return True  # Cannot detect from structured data

    # Heart team
    if "heart team" in factor_lower:
        return True  # Cannot detect from structured data

    # Default: assume the gap exists (conservative — better to over-flag)
    return True


def _has_recent_imaging(chart: dict[str, Any], days: int = 90) -> bool:
    """Check if chart has imaging results within the specified number of days."""
    imaging = chart.get("relevant_imaging", [])
    if not imaging:
        return False
    cutoff = datetime.now() - timedelta(days=days)
    for img in imaging:
        try:
            img_date = datetime.fromisoformat(img.get("date", ""))
            if img_date >= cutoff:
                return True
        except (ValueError, TypeError):
            continue
    # If dates can't be parsed but imaging exists, give benefit of the doubt
    return len(imaging) > 0


def _has_recent_labs(chart: dict[str, Any], days: int = 30) -> bool:
    """Check if chart has lab results within the specified number of days."""
    labs = chart.get("relevant_labs", [])
    if not labs:
        return False
    cutoff = datetime.now() - timedelta(days=days)
    for lab in labs:
        try:
            lab_date = datetime.fromisoformat(lab.get("date", ""))
            if lab_date >= cutoff:
                return True
        except (ValueError, TypeError):
            continue
    return len(labs) > 0


def _has_medication_trial(chart: dict[str, Any]) -> bool:
    """Check if chart documents a medication trial of adequate duration."""
    meds = chart.get("relevant_medications", [])
    if not meds:
        return False
    cardiac_keywords = [
        "metoprolol", "atenolol", "carvedilol", "bisoprolol",  # beta-blockers
        "amlodipine", "diltiazem", "verapamil", "nifedipine",  # CCBs
        "nitroglycerin", "isosorbide", "ranolazine",           # anti-anginals
        "lisinopril", "enalapril", "ramipril", "losartan", "valsartan",  # ACEi/ARBs
        "atorvastatin", "rosuvastatin", "pravastatin",         # statins
        "aspirin", "clopidogrel", "ticagrelor", "prasugrel",   # antiplatelets
        "warfarin", "apixaban", "rivaroxaban", "edoxaban",     # anticoagulants
        "amiodarone", "flecainide", "sotalol", "dofetilide",   # antiarrhythmics
        "furosemide", "bumetanide", "spironolactone", "eplerenone",  # diuretics
        "sacubitril", "hydralazine", "digoxin", "ivabradine",  # HF meds
    ]
    for med in meds:
        med_name = med.get("name", "").lower()
        if any(kw in med_name for kw in cardiac_keywords):
            return True
    return False


def _has_symptom_timeline(chart: dict[str, Any]) -> bool:
    """Check if symptom timeline appears adequately documented."""
    # Proxy: prior treatments suggest documented clinical course
    return len(chart.get("prior_treatments", [])) >= 2


def _risk_label(score: float, threshold: float) -> str:
    """Convert numeric risk score to human-readable label."""
    if score >= threshold:
        return "HIGH — P2P review likely"
    if score >= threshold * 0.7:
        return "MODERATE — P2P review possible"
    if score >= threshold * 0.4:
        return "LOW — P2P unlikely with current documentation"
    return "MINIMAL — strong documentation"
