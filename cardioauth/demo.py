"""Demo/mock mode for CardioAuth.

Provides realistic cardiology patient data and payer policy data so the
full pipeline can run end-to-end without Epic FHIR sandbox auth or a
live Pinecone index.
"""

from __future__ import annotations

from cardioauth.models.chart import ChartData, ImagingResult, LabResult, Medication
from cardioauth.models.reasoning import CriterionEvaluation, CriterionGap, ReasoningResult
from cardioauth.models.policy import ClinicalCriterion, PolicyData

# ---------------------------------------------------------------------------
# Demo Patients
# ---------------------------------------------------------------------------

DEMO_PATIENTS: dict[str, dict] = {
    # ------------------------------------------------------------------
    # Patient 1 — 67M, severe CAD, left heart catheterization
    # ------------------------------------------------------------------
    "DEMO-001": {
        "name": "Robert J. Harmon",
        "dob": "1958-11-14",
        "sex": "M",
        "age": 67,
        "mrn": "DEMO-001",
        "payer": "UnitedHealthcare",
        "insurance_id": "UHC-88431927",
        "attending": "Dr. Priya Mehta, MD, FACC",
        "procedure": "Left Heart Catheterization with Coronary Angiography",
        "cpt": "93458",
        "diagnoses": [
            {"code": "I25.10", "description": "Atherosclerotic heart disease of native coronary artery without angina pectoris"},
            {"code": "I25.110", "description": "Atherosclerotic heart disease of native coronary artery with unstable angina pectoris"},
            {"code": "I50.22", "description": "Chronic systolic (systolic) heart failure"},
            {"code": "E11.9", "description": "Type 2 diabetes mellitus without complications"},
            {"code": "I10", "description": "Essential (primary) hypertension"},
            {"code": "E78.5", "description": "Dyslipidemia, unspecified"},
        ],
        "labs": [
            {"name": "Troponin-I (high-sensitivity)", "value": "0.08", "unit": "ng/mL", "date": "2025-12-18", "flag": "HIGH"},
            {"name": "BNP", "value": "842", "unit": "pg/mL", "date": "2025-12-18", "flag": "HIGH"},
            {"name": "LDL Cholesterol", "value": "68", "unit": "mg/dL", "date": "2025-12-10", "flag": ""},
            {"name": "HbA1c", "value": "7.2", "unit": "%", "date": "2025-11-20", "flag": "HIGH"},
            {"name": "Creatinine", "value": "1.3", "unit": "mg/dL", "date": "2025-12-18", "flag": ""},
            {"name": "eGFR", "value": "58", "unit": "mL/min/1.73m2", "date": "2025-12-18", "flag": "LOW"},
            {"name": "Hemoglobin", "value": "13.1", "unit": "g/dL", "date": "2025-12-18", "flag": ""},
            {"name": "Platelet Count", "value": "198", "unit": "K/uL", "date": "2025-12-18", "flag": ""},
        ],
        "imaging": [
            {
                "type": "Transthoracic Echocardiogram",
                "date": "2025-12-15",
                "result_summary": (
                    "LVEF 35% (severely reduced). Moderate global hypokinesis with "
                    "akinesis of the inferior and inferolateral walls. Mild-to-moderate "
                    "mitral regurgitation (functional). LV end-diastolic dimension 5.8 cm. "
                    "No significant valvular stenosis. Diastolic dysfunction Grade II."
                ),
                "ordering_provider": "Dr. Priya Mehta",
            },
            {
                "type": "Nuclear Stress Test (Regadenoson SPECT MPI)",
                "date": "2025-12-02",
                "result_summary": (
                    "POSITIVE for reversible ischemia. Large reversible perfusion defect "
                    "involving the inferior and inferolateral walls (approximately 18% of "
                    "LV myocardium). Fixed defect in the basal inferior wall consistent "
                    "with prior infarction. Stress LVEF 30%, rest LVEF 34%. Transient "
                    "ischemic dilation ratio 1.22 (abnormal)."
                ),
                "ordering_provider": "Dr. Priya Mehta",
            },
            {
                "type": "Chest X-Ray (PA and Lateral)",
                "date": "2025-12-18",
                "result_summary": (
                    "Mild cardiomegaly. Cephalization of pulmonary vasculature suggesting "
                    "mild pulmonary venous congestion. No pleural effusion. No acute "
                    "infiltrate."
                ),
                "ordering_provider": "Dr. Priya Mehta",
            },
        ],
        "medications": [
            {"name": "Aspirin", "dose": "81 mg PO daily", "start_date": "2023-03-01", "indication": "CAD secondary prevention"},
            {"name": "Atorvastatin", "dose": "80 mg PO daily", "start_date": "2023-03-01", "indication": "Dyslipidemia / CAD"},
            {"name": "Metoprolol Succinate (Toprol-XL)", "dose": "200 mg PO daily", "start_date": "2024-01-15", "indication": "HFrEF / rate control"},
            {"name": "Sacubitril-Valsartan (Entresto)", "dose": "97/103 mg PO BID", "start_date": "2024-06-10", "indication": "HFrEF"},
            {"name": "Spironolactone", "dose": "25 mg PO daily", "start_date": "2024-06-10", "indication": "HFrEF"},
            {"name": "Empagliflozin (Jardiance)", "dose": "10 mg PO daily", "start_date": "2024-08-01", "indication": "HFrEF / T2DM"},
            {"name": "Isosorbide Mononitrate", "dose": "60 mg PO daily", "start_date": "2025-09-20", "indication": "Angina"},
            {"name": "Clopidogrel (Plavix)", "dose": "75 mg PO daily", "start_date": "2025-10-01", "indication": "ACS / post-stent (prior PCI 2023)"},
            {"name": "Metformin", "dose": "1000 mg PO BID", "start_date": "2020-05-15", "indication": "T2DM"},
        ],
        "prior_treatments": [
            "PCI with DES to RCA (2023-03-15) — in-stent restenosis suspected",
            "Optimal medical therapy for HFrEF (ARNI, beta-blocker, MRA, SGLT2i) — on max tolerated doses",
            "Cardiac rehabilitation program completed (2023)",
            "Regadenoson SPECT MPI (2025-12-02) — positive for large reversible defect",
            "Failed trial of ranolazine — discontinued due to QTc prolongation",
        ],
        "comorbidities": [
            "Type 2 diabetes mellitus (HbA1c 7.2%)",
            "Essential hypertension (controlled)",
            "CKD Stage 3a (eGFR 58)",
            "Dyslipidemia (on max-dose statin, LDL at goal)",
            "Obesity (BMI 31.4)",
            "Former smoker (30 pack-year history, quit 2020)",
        ],
    },

    # ------------------------------------------------------------------
    # Patient 2 — 72F, severe aortic stenosis, TAVR candidate
    # ------------------------------------------------------------------
    "DEMO-002": {
        "name": "Margaret A. Chen",
        "dob": "1953-06-22",
        "sex": "F",
        "age": 72,
        "mrn": "DEMO-002",
        "payer": "UnitedHealthcare",
        "insurance_id": "UHC-55128764",
        "attending": "Dr. Samuel K. Ortiz, MD, FACC, FSCAI",
        "procedure": "Transcatheter Aortic Valve Replacement (TAVR)",
        "cpt": "33361",
        "diagnoses": [
            {"code": "I35.0", "description": "Nonrheumatic aortic (valve) stenosis"},
            {"code": "I35.2", "description": "Nonrheumatic aortic (valve) stenosis with insufficiency"},
            {"code": "R55", "description": "Syncope and collapse"},
            {"code": "R06.00", "description": "Dyspnea, unspecified"},
            {"code": "I10", "description": "Essential (primary) hypertension"},
            {"code": "J44.1", "description": "Chronic obstructive pulmonary disease with acute exacerbation"},
            {"code": "N18.3", "description": "Chronic kidney disease, stage 3 (moderate)"},
        ],
        "labs": [
            {"name": "BNP", "value": "1,284", "unit": "pg/mL", "date": "2025-12-20", "flag": "HIGH"},
            {"name": "Troponin-I (high-sensitivity)", "value": "0.04", "unit": "ng/mL", "date": "2025-12-20", "flag": ""},
            {"name": "Creatinine", "value": "1.5", "unit": "mg/dL", "date": "2025-12-20", "flag": "HIGH"},
            {"name": "eGFR", "value": "42", "unit": "mL/min/1.73m2", "date": "2025-12-20", "flag": "LOW"},
            {"name": "Hemoglobin", "value": "11.2", "unit": "g/dL", "date": "2025-12-20", "flag": "LOW"},
            {"name": "INR", "value": "1.0", "unit": "", "date": "2025-12-20", "flag": ""},
            {"name": "Albumin", "value": "3.4", "unit": "g/dL", "date": "2025-12-20", "flag": "LOW"},
            {"name": "Pro-BNP", "value": "4,210", "unit": "pg/mL", "date": "2025-12-20", "flag": "HIGH"},
        ],
        "imaging": [
            {
                "type": "Transthoracic Echocardiogram",
                "date": "2025-12-12",
                "result_summary": (
                    "SEVERE aortic stenosis. Aortic valve area 0.7 cm2 by continuity "
                    "equation. Peak aortic jet velocity 4.8 m/s. Mean transaortic gradient "
                    "52 mmHg. LVEF 50% (low-normal). Concentric LV hypertrophy (IVSd 1.4 cm). "
                    "Mild aortic regurgitation. Estimated RVSP 48 mmHg (moderate pulmonary "
                    "hypertension). Mild tricuspid regurgitation."
                ),
                "ordering_provider": "Dr. Samuel K. Ortiz",
            },
            {
                "type": "CT Angiography — Aorta and Iliofemoral",
                "date": "2025-12-16",
                "result_summary": (
                    "Aortic annulus perimeter 72.4 mm (area 398 mm2). Heavily calcified "
                    "trileaflet aortic valve. Annulus-to-coronary height: LCA 12.8 mm, "
                    "RCA 16.2 mm. Ascending aorta 3.6 cm (normal). Bilateral iliofemoral "
                    "arteries patent with minimal calcification; minimum diameter 6.2 mm "
                    "bilaterally — adequate for transfemoral access. No abdominal aortic "
                    "aneurysm."
                ),
                "ordering_provider": "Dr. Samuel K. Ortiz",
            },
            {
                "type": "Transesophageal Echocardiogram",
                "date": "2025-12-14",
                "result_summary": (
                    "Confirms severe aortic stenosis with heavily calcified trileaflet "
                    "valve. No left atrial appendage thrombus. Mild aortic regurgitation. "
                    "Interatrial septum intact. Adequate landing zone for TAVR."
                ),
                "ordering_provider": "Dr. Samuel K. Ortiz",
            },
        ],
        "medications": [
            {"name": "Furosemide (Lasix)", "dose": "40 mg PO daily", "start_date": "2025-06-01", "indication": "Volume overload / HF symptoms"},
            {"name": "Amlodipine", "dose": "5 mg PO daily", "start_date": "2022-01-10", "indication": "Hypertension"},
            {"name": "Losartan", "dose": "50 mg PO daily", "start_date": "2022-01-10", "indication": "Hypertension"},
            {"name": "Aspirin", "dose": "81 mg PO daily", "start_date": "2020-03-15", "indication": "Primary prevention"},
            {"name": "Tiotropium (Spiriva)", "dose": "18 mcg INH daily", "start_date": "2019-11-01", "indication": "COPD"},
            {"name": "Albuterol (ProAir)", "dose": "90 mcg INH PRN", "start_date": "2019-11-01", "indication": "COPD rescue"},
            {"name": "Atorvastatin", "dose": "40 mg PO daily", "start_date": "2018-05-20", "indication": "Hyperlipidemia"},
        ],
        "prior_treatments": [
            "Managed medically with serial echocardiograms since 2022 — progressive stenosis (AVA declined from 1.2 to 0.7 cm2)",
            "Heart Team evaluation (2025-12-18) — deemed high surgical risk (STS-PROM 6.8%), TAVR recommended",
            "Balloon aortic valvuloplasty considered but deferred in favor of definitive TAVR",
            "Pulmonary function testing (FEV1 52% predicted) — contributes to high surgical risk",
            "Two hospitalizations for decompensated heart failure in past 6 months",
        ],
        "comorbidities": [
            "COPD (GOLD Stage III, FEV1 52% predicted)",
            "CKD Stage 3b (eGFR 42)",
            "Hypertension (controlled)",
            "Mild anemia of chronic disease",
            "Moderate pulmonary hypertension (RVSP 48 mmHg)",
            "Frailty (5-meter gait speed 0.65 m/s)",
            "Osteoporosis (vertebral compression fracture T12 — 2024)",
        ],
    },

    # ------------------------------------------------------------------
    # Patient 3 — 55M, atrial fibrillation, catheter ablation
    # ------------------------------------------------------------------
    "DEMO-003": {
        "name": "David L. Thompson",
        "dob": "1970-09-03",
        "sex": "M",
        "age": 55,
        "mrn": "DEMO-003",
        "payer": "UnitedHealthcare",
        "insurance_id": "UHC-33987621",
        "attending": "Dr. Anika R. Patel, MD, FHRS",
        "procedure": "Catheter Ablation of Atrial Fibrillation (Pulmonary Vein Isolation)",
        "cpt": "93656",
        "diagnoses": [
            {"code": "I48.0", "description": "Paroxysmal atrial fibrillation"},
            {"code": "I48.91", "description": "Unspecified atrial fibrillation"},
            {"code": "R00.0", "description": "Tachycardia, unspecified"},
            {"code": "R42", "description": "Dizziness and giddiness"},
            {"code": "I10", "description": "Essential (primary) hypertension"},
            {"code": "E66.01", "description": "Morbid (severe) obesity due to excess calories"},
        ],
        "labs": [
            {"name": "TSH", "value": "2.14", "unit": "mIU/L", "date": "2025-12-05", "flag": ""},
            {"name": "Free T4", "value": "1.1", "unit": "ng/dL", "date": "2025-12-05", "flag": ""},
            {"name": "BNP", "value": "210", "unit": "pg/mL", "date": "2025-12-10", "flag": "HIGH"},
            {"name": "Creatinine", "value": "0.9", "unit": "mg/dL", "date": "2025-12-10", "flag": ""},
            {"name": "eGFR", "value": "92", "unit": "mL/min/1.73m2", "date": "2025-12-10", "flag": ""},
            {"name": "Potassium", "value": "4.2", "unit": "mEq/L", "date": "2025-12-10", "flag": ""},
            {"name": "Magnesium", "value": "2.0", "unit": "mg/dL", "date": "2025-12-10", "flag": ""},
            {"name": "INR", "value": "2.4", "unit": "", "date": "2025-12-10", "flag": ""},
            {"name": "Hemoglobin", "value": "14.8", "unit": "g/dL", "date": "2025-12-10", "flag": ""},
        ],
        "imaging": [
            {
                "type": "Transthoracic Echocardiogram",
                "date": "2025-11-20",
                "result_summary": (
                    "LVEF 55% (normal). Left atrial volume index 38 mL/m2 (moderately "
                    "dilated). No significant valvular disease. Normal RV size and "
                    "function. No pericardial effusion. LV wall thickness normal."
                ),
                "ordering_provider": "Dr. Anika R. Patel",
            },
            {
                "type": "Cardiac MRI with Late Gadolinium Enhancement",
                "date": "2025-12-01",
                "result_summary": (
                    "LVEF 54%. Left atrial volume 42 mL/m2. No late gadolinium "
                    "enhancement (no myocardial fibrosis or scar). Pulmonary vein "
                    "anatomy: 4 standard pulmonary veins, no common trunk. Left "
                    "atrial appendage patent, no thrombus. No structural "
                    "abnormalities."
                ),
                "ordering_provider": "Dr. Anika R. Patel",
            },
            {
                "type": "14-Day Continuous Ambulatory ECG Monitor (Zio Patch)",
                "date": "2025-11-15",
                "result_summary": (
                    "AF burden 34% over 14-day monitoring period. 47 episodes of "
                    "paroxysmal AF, longest sustained episode 14.2 hours. Maximum "
                    "ventricular rate during AF 156 bpm. Three pauses >2.5 seconds "
                    "(longest 3.1 seconds, all AF-related). No ventricular "
                    "tachycardia."
                ),
                "ordering_provider": "Dr. Anika R. Patel",
            },
        ],
        "medications": [
            {"name": "Apixaban (Eliquis)", "dose": "5 mg PO BID", "start_date": "2024-02-01", "indication": "Anticoagulation for AF (CHA2DS2-VASc 2)"},
            {"name": "Metoprolol Succinate (Toprol-XL)", "dose": "100 mg PO daily", "start_date": "2024-06-15", "indication": "AF rate control"},
            {"name": "Lisinopril", "dose": "20 mg PO daily", "start_date": "2021-04-01", "indication": "Hypertension"},
            {"name": "Atorvastatin", "dose": "20 mg PO daily", "start_date": "2022-08-10", "indication": "Hyperlipidemia"},
        ],
        "prior_treatments": [
            "Flecainide 100 mg BID (2024-03 to 2024-08) — discontinued due to recurrent AF breakthrough and proarrhythmic concern (non-sustained wide complex tachycardia on Holter)",
            "Amiodarone 200 mg daily (2024-09 to 2025-06) — discontinued due to hepatotoxicity (ALT peaked at 198 U/L, resolved after discontinuation)",
            "Sotalol 120 mg BID (2025-07 to 2025-10) — discontinued due to QTc prolongation (QTc 522 ms) and symptomatic bradycardia",
            "Electrical cardioversion x3 (2024-02, 2024-05, 2025-01) — sinus rhythm maintained <4 weeks each time",
            "Rate control strategy with metoprolol — symptoms persist (palpitations, exercise intolerance, dizziness)",
        ],
        "comorbidities": [
            "Essential hypertension (controlled on lisinopril)",
            "Obesity (BMI 34.8)",
            "Obstructive sleep apnea (on CPAP, AHI 24 at diagnosis, compliant)",
            "Hyperlipidemia",
        ],
    },
}

# ---------------------------------------------------------------------------
# ICD-10 Code Descriptions — lookup dict for all codes used by demo patients
# ---------------------------------------------------------------------------

ICD10_DESCRIPTIONS: dict[str, str] = {
    # Patient 1 — CAD / Heart failure
    "I25.10": "Atherosclerotic heart disease of native coronary artery without angina pectoris",
    "I25.110": "Atherosclerotic heart disease of native coronary artery with unstable angina pectoris",
    "I50.22": "Chronic systolic (systolic) heart failure",
    "E11.9": "Type 2 diabetes mellitus without complications",
    "I10": "Essential (primary) hypertension",
    "E78.5": "Dyslipidemia, unspecified",
    # Patient 2 — Aortic stenosis / TAVR
    "I35.0": "Nonrheumatic aortic (valve) stenosis",
    "I35.2": "Nonrheumatic aortic (valve) stenosis with insufficiency",
    "R55": "Syncope and collapse",
    "R06.00": "Dyspnea, unspecified",
    "J44.1": "Chronic obstructive pulmonary disease with acute exacerbation",
    "N18.3": "Chronic kidney disease, stage 3 (moderate)",
    # Patient 3 — Atrial fibrillation / Ablation
    "I48.0": "Paroxysmal atrial fibrillation",
    "I48.91": "Unspecified atrial fibrillation",
    "R00.0": "Tachycardia, unspecified",
    "R42": "Dizziness and giddiness",
    "E66.01": "Morbid (severe) obesity due to excess calories",
}

# ---------------------------------------------------------------------------
# Demo Patient Info — quick lookup for demographics
# ---------------------------------------------------------------------------

DEMO_PATIENT_INFO: dict[str, dict] = {
    "DEMO-001": {"name": "Robert J. Harmon", "age": 67, "sex": "M", "mrn": "DEMO-001"},
    "DEMO-002": {"name": "Margaret A. Chen", "age": 72, "sex": "F", "mrn": "DEMO-002"},
    "DEMO-003": {"name": "David L. Thompson", "age": 55, "sex": "M", "mrn": "DEMO-003"},
}

# ---------------------------------------------------------------------------
# Demo Policies — Multi-payer criteria for each procedure
# Keyed by CPT code -> payer name -> policy dict
# UHC: strictest, most documentation required
# Aetna: moderate, focuses on clinical necessity
# BCBS: requires peer-to-peer for certain procedures
# ---------------------------------------------------------------------------

DEMO_POLICIES: dict[str, dict[str, dict]] = {
    # ======================================================================
    # Left heart catheterization (CPT 93458)
    # ======================================================================
    "93458": {
        "UnitedHealthcare": {
            "payer": "UnitedHealthcare",
            "procedure": "Left Heart Catheterization with Coronary Angiography",
            "cpt_code": "93458",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Positive or high-risk non-invasive stress test",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Reversible perfusion defect, ST depression >= 2mm, exercise capacity < 5 METs, Duke treadmill score <= -11",
                },
                {
                    "criterion": "Angina or anginal equivalent refractory to optimal medical therapy",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Documentation of symptoms despite trial of at least 2 anti-anginal agents at adequate doses",
                },
                {
                    "criterion": "LVEF assessment within 90 days",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Echocardiogram or cardiac MRI documenting current LVEF",
                },
                {
                    "criterion": "Relevant lab work within 30 days (CBC, BMP, coagulation)",
                    "required": True,
                    "evidence_type": "lab",
                    "acceptable_values": "Hemoglobin, creatinine/eGFR, platelet count, INR if on anticoagulation",
                },
                {
                    "criterion": "Documentation of coronary artery disease risk factors or known CAD",
                    "required": False,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Diabetes, hypertension, dyslipidemia, smoking history, family history, prior PCI/CABG",
                },
            ],
            "documentation_required": [
                "History and physical with symptom description and functional class",
                "Non-invasive stress test report",
                "Echocardiogram report",
                "Current medication list documenting anti-anginal therapy",
                "Relevant lab results (CBC, BMP, coagulation studies)",
            ],
            "submission_format": "portal",
            "typical_turnaround_days": 5,
            "common_denial_reasons": [
                "No non-invasive testing performed prior to catheterization request",
                "Inadequate trial of medical therapy not documented",
                "Stress test results not attached or not clearly positive",
                "LVEF assessment older than 90 days",
            ],
            "appeal_success_factors": [
                "Peer-to-peer review with cardiologist",
                "Submission of complete stress test images/report",
                "Letter of medical necessity detailing failed medical therapy",
            ],
            "policy_source": "UnitedHealthcare Commercial Medical Policy: Cardiac Catheterization, Policy Number 2024T0478U",
            "policy_last_updated": "2025-07-01",
        },
        "Aetna": {
            "payer": "Aetna",
            "procedure": "Left Heart Catheterization with Coronary Angiography",
            "cpt_code": "93458",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Clinical indication consistent with ACC Appropriate Use Criteria for coronary revascularization",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "ACS presentation, stable angina with positive non-invasive test, or new-onset heart failure with suspected ischemic etiology",
                },
                {
                    "criterion": "Non-invasive functional testing or anatomical assessment",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Stress test (exercise, pharmacologic, or CCTA) with findings suggestive of obstructive CAD",
                },
                {
                    "criterion": "Documentation of symptom burden and functional limitation",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "CCS angina class documented; description of symptom frequency and impact on daily activities",
                },
                {
                    "criterion": "Current cardiac imaging (echocardiogram or equivalent) within 6 months",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "TTE or cardiac MRI with LVEF and wall motion assessment",
                },
                {
                    "criterion": "Basic metabolic panel and CBC within 30 days",
                    "required": False,
                    "evidence_type": "lab",
                    "acceptable_values": "Creatinine/eGFR, hemoglobin, platelet count",
                },
            ],
            "documentation_required": [
                "Cardiology consultation note with clinical indication",
                "Non-invasive test report (stress test or CCTA)",
                "Echocardiogram report",
                "Current medication list",
            ],
            "submission_format": "portal",
            "typical_turnaround_days": 3,
            "common_denial_reasons": [
                "Clinical indication does not meet ACC Appropriate Use Criteria",
                "Non-invasive testing not performed or results equivocal without further justification",
                "Symptom documentation insufficient to establish medical necessity",
            ],
            "appeal_success_factors": [
                "Detailed letter of medical necessity citing ACC/AHA guidelines",
                "Submission of full non-invasive test report with images",
                "Peer-to-peer review with requesting cardiologist",
            ],
            "policy_source": "Aetna Clinical Policy Bulletin: Cardiac Catheterization, CPB 0234",
            "policy_last_updated": "2025-06-15",
        },
        "Blue Cross Blue Shield": {
            "payer": "Blue Cross Blue Shield",
            "procedure": "Left Heart Catheterization with Coronary Angiography",
            "cpt_code": "93458",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Positive non-invasive stress test or acute coronary syndrome presentation",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Reversible perfusion defect, significant ST changes, or ACS with elevated troponin",
                },
                {
                    "criterion": "Symptoms of myocardial ischemia despite medical management",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Angina, dyspnea, or anginal equivalent with documentation of current anti-ischemic regimen",
                },
                {
                    "criterion": "LVEF assessment within 6 months",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Echocardiogram or cardiac MRI documenting LVEF and wall motion",
                },
                {
                    "criterion": "Renal function assessment within 30 days",
                    "required": True,
                    "evidence_type": "lab",
                    "acceptable_values": "Serum creatinine and eGFR; contrast nephropathy risk assessment if eGFR < 60",
                },
            ],
            "documentation_required": [
                "History and physical with ischemic symptom documentation",
                "Non-invasive stress test report",
                "Echocardiogram report",
                "Current medication list",
                "Renal function labs with contrast risk assessment if applicable",
            ],
            "submission_format": "fax",
            "typical_turnaround_days": 7,
            "common_denial_reasons": [
                "Non-invasive testing not performed prior to request",
                "Stress test results equivocal — peer-to-peer required",
                "Renal function assessment missing or outdated",
                "Insufficient documentation of failed medical therapy",
            ],
            "appeal_success_factors": [
                "Mandatory peer-to-peer review for all initial denials",
                "Complete stress test report with interpreting physician attestation",
                "Letter of medical necessity with ACC/AHA guideline citations",
            ],
            "policy_source": "BCBS Medical Policy: Diagnostic Cardiac Catheterization, Policy 2024-CAR-0091",
            "policy_last_updated": "2025-05-01",
        },
    },

    # ======================================================================
    # TAVR (CPT 33361)
    # ======================================================================
    "33361": {
        "UnitedHealthcare": {
            "payer": "UnitedHealthcare",
            "procedure": "Transcatheter Aortic Valve Replacement (TAVR)",
            "cpt_code": "33361",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Severe aortic stenosis confirmed by echocardiography",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "AVA <= 1.0 cm2, mean gradient >= 40 mmHg, or peak jet velocity >= 4.0 m/s",
                },
                {
                    "criterion": "Symptoms attributable to aortic stenosis (NYHA Class II-IV)",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Dyspnea on exertion, syncope, angina, heart failure; documented NYHA functional class",
                },
                {
                    "criterion": "Heart Team evaluation and recommendation for TAVR over SAVR",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Multidisciplinary Heart Team note with cardiac surgeon and interventional cardiologist; STS-PROM score documented",
                },
                {
                    "criterion": "High or prohibitive surgical risk, or >= 65 years of age",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "STS-PROM >= 3% (high risk) or >= 8% (prohibitive), or age >= 65 with intermediate risk and anatomic suitability",
                },
                {
                    "criterion": "Pre-procedural CT angiography for valve sizing and access",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "CTA of aortic root and iliofemoral arteries with annular measurements",
                },
                {
                    "criterion": "Coronary angiography within 12 months or CT coronary assessment",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Coronary angiography or CCTA ruling out significant untreated coronary artery disease",
                },
                {
                    "criterion": "Life expectancy > 12 months with expected quality of life improvement",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Documentation that patient is expected to benefit from valve replacement",
                },
            ],
            "documentation_required": [
                "Transthoracic echocardiogram report with hemodynamic severity data",
                "Heart Team conference note with surgical risk assessment (STS-PROM score)",
                "CT angiography report with annular sizing and access vessel assessment",
                "Coronary angiography or CT coronary angiography report",
                "Cardiology consultation with symptom assessment and NYHA classification",
                "Pulmonary function tests (if COPD or respiratory comorbidity)",
                "Frailty assessment documentation",
            ],
            "submission_format": "portal",
            "typical_turnaround_days": 7,
            "common_denial_reasons": [
                "Heart Team evaluation not documented",
                "Echocardiographic criteria not meeting severe AS thresholds",
                "STS-PROM score not provided or patient deemed low surgical risk without justification",
                "Pre-procedural CTA not performed or report not submitted",
                "Coronary evaluation missing",
            ],
            "appeal_success_factors": [
                "Complete Heart Team note with all required participants",
                "Peer-to-peer review emphasizing comorbidities and surgical risk",
                "Submission of STS risk calculator output",
                "Frailty assessment data (gait speed, grip strength)",
            ],
            "policy_source": "UnitedHealthcare Commercial Medical Policy: Transcatheter Aortic Valve Replacement (TAVR), Policy Number 2024T0512U",
            "policy_last_updated": "2025-09-15",
        },
        "Aetna": {
            "payer": "Aetna",
            "procedure": "Transcatheter Aortic Valve Replacement (TAVR)",
            "cpt_code": "33361",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Severe native aortic stenosis meeting hemodynamic thresholds",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "AVA < 1.0 cm2, mean gradient > 40 mmHg, peak velocity > 4.0 m/s, or low-flow low-gradient with dobutamine stress echo confirmation",
                },
                {
                    "criterion": "Symptomatic aortic stenosis with functional impairment",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "NYHA Class II or greater; documented exertional dyspnea, syncope, presyncope, or heart failure",
                },
                {
                    "criterion": "Multidisciplinary Heart Valve Team consensus for TAVR",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Heart Valve Team note signed by interventional cardiologist and cardiothoracic surgeon; rationale for TAVR vs SAVR",
                },
                {
                    "criterion": "Surgical risk stratification with validated scoring tool",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "STS-PROM score documented; intermediate risk (>= 3%) or high/prohibitive risk (>= 8%), or anatomic contraindications to SAVR",
                },
                {
                    "criterion": "Pre-procedural CT angiography for sizing and access planning",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Gated CTA with annular dimensions, sinus of Valsalva measurements, coronary heights, and iliofemoral access assessment",
                },
                {
                    "criterion": "Expected survival > 12 months post-procedure",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Clinical attestation of anticipated survival and quality of life benefit",
                },
            ],
            "documentation_required": [
                "Echocardiogram with full hemodynamic data (AVA, gradients, LVEF)",
                "Heart Valve Team conference note",
                "STS risk score calculation",
                "Pre-procedural CTA report",
                "Coronary evaluation (angiography or CCTA)",
                "Cardiology consultation with NYHA classification",
            ],
            "submission_format": "portal",
            "typical_turnaround_days": 5,
            "common_denial_reasons": [
                "STS-PROM score below intermediate risk threshold without additional justification",
                "Heart Valve Team note incomplete or missing surgeon co-signature",
                "Hemodynamic data does not confirm severe AS (e.g., moderate AS with borderline gradients)",
                "Pre-procedural imaging incomplete",
            ],
            "appeal_success_factors": [
                "Dobutamine stress echo for low-flow low-gradient cases",
                "Detailed Heart Valve Team note addressing patient-specific anatomy and risk",
                "Peer-to-peer review with structural heart specialist",
            ],
            "policy_source": "Aetna Clinical Policy Bulletin: Transcatheter Aortic Valve Replacement, CPB 0821",
            "policy_last_updated": "2025-08-01",
        },
        "Blue Cross Blue Shield": {
            "payer": "Blue Cross Blue Shield",
            "procedure": "Transcatheter Aortic Valve Replacement (TAVR)",
            "cpt_code": "33361",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Echocardiographic confirmation of severe aortic stenosis",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "AVA <= 1.0 cm2, mean gradient >= 40 mmHg, or peak jet velocity >= 4.0 m/s; indexed AVA if BSA-adjusted",
                },
                {
                    "criterion": "Symptomatic presentation with documented NYHA class",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "NYHA Class II-IV symptoms clearly attributable to aortic stenosis; 6-minute walk test or equivalent functional assessment",
                },
                {
                    "criterion": "Heart Team evaluation with documented surgical risk assessment",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Heart Team note with STS-PROM; patient deemed intermediate, high, or prohibitive surgical risk",
                },
                {
                    "criterion": "Pre-procedural CTA with valve sizing and vascular access evaluation",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "CTA aortic root through iliofemoral arteries; annular perimeter, area, and access vessel diameters",
                },
                {
                    "criterion": "Coronary artery evaluation within 12 months",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "Coronary angiography or CCTA; PCI for significant lesions prior to TAVR if indicated",
                },
                {
                    "criterion": "Peer-to-peer review required for patients under age 65",
                    "required": False,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Mandatory peer-to-peer with BCBS medical director for patients < 65; must document why TAVR preferred over SAVR",
                },
            ],
            "documentation_required": [
                "Transthoracic echocardiogram with hemodynamic severity data",
                "Transesophageal echocardiogram if performed",
                "Heart Team conference note with STS-PROM score",
                "CT angiography report with sizing and access assessment",
                "Coronary angiography report",
                "Cardiology consultation note with NYHA class and symptom history",
                "Frailty assessment (gait speed, grip strength, or clinical frailty scale)",
                "Pulmonary function tests if respiratory comorbidity present",
            ],
            "submission_format": "fax",
            "typical_turnaround_days": 10,
            "common_denial_reasons": [
                "Patient under 65 without mandatory peer-to-peer review",
                "Heart Team note missing cardiothoracic surgeon attestation",
                "Echocardiographic data does not meet severe AS thresholds",
                "Frailty assessment not submitted",
                "Coronary evaluation outdated (> 12 months)",
            ],
            "appeal_success_factors": [
                "Peer-to-peer review with BCBS medical director (mandatory for all denials)",
                "Supplemental frailty data and functional assessment",
                "Complete Heart Team documentation with all signatures",
                "Additional imaging if initial data borderline",
            ],
            "policy_source": "BCBS Medical Policy: Transcatheter Aortic Valve Replacement, Policy 2024-CAR-0145",
            "policy_last_updated": "2025-07-15",
        },
    },

    # ======================================================================
    # Catheter ablation of AFib (CPT 93656)
    # ======================================================================
    "93656": {
        "UnitedHealthcare": {
            "payer": "UnitedHealthcare",
            "procedure": "Catheter Ablation of Atrial Fibrillation (Pulmonary Vein Isolation)",
            "cpt_code": "93656",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Documented atrial fibrillation on ECG or ambulatory monitoring",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "12-lead ECG, Holter monitor, event monitor, or implantable loop recorder documenting AF",
                },
                {
                    "criterion": "Failure of or intolerance to at least one Class I or Class III antiarrhythmic drug",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Trial and failure/intolerance of flecainide, propafenone, sotalol, dofetilide, dronedarone, or amiodarone with specific reason for discontinuation",
                },
                {
                    "criterion": "Symptomatic AF despite rate or rhythm control attempts",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Palpitations, dyspnea, exercise intolerance, fatigue, dizziness, or reduced quality of life attributable to AF",
                },
                {
                    "criterion": "Echocardiogram within 12 months",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "TTE or TEE documenting LVEF, left atrial size, and ruling out structural heart disease",
                },
                {
                    "criterion": "Anticoagulation assessment and management plan",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "CHA2DS2-VASc score documented; appropriate anticoagulation prescribed pre- and post-procedure",
                },
                {
                    "criterion": "Assessment for reversible causes of AF",
                    "required": False,
                    "evidence_type": "lab",
                    "acceptable_values": "Thyroid function tests (TSH), evaluation for sleep apnea, alcohol use assessment",
                },
            ],
            "documentation_required": [
                "ECG or ambulatory monitor report documenting atrial fibrillation",
                "Electrophysiology consultation with treatment history",
                "Documentation of antiarrhythmic drug trials with reasons for failure/discontinuation",
                "Echocardiogram report (TTE within 12 months)",
                "Thyroid function tests",
                "Current medication list",
                "CHA2DS2-VASc score and anticoagulation plan",
            ],
            "submission_format": "portal",
            "typical_turnaround_days": 5,
            "common_denial_reasons": [
                "Fewer than 1 antiarrhythmic drug trial documented",
                "AF episodes not objectively documented on monitoring",
                "Echocardiogram not performed or report not submitted",
                "Symptoms not clearly attributed to AF (vs other causes)",
                "Reversible causes of AF not evaluated (e.g., no TSH)",
            ],
            "appeal_success_factors": [
                "Detailed medication trial history with specific side effects and dates",
                "Ambulatory monitoring data showing AF burden",
                "Peer-to-peer with electrophysiologist",
                "Quality-of-life impact documentation (AFEQT score or equivalent)",
            ],
            "policy_source": "UnitedHealthcare Commercial Medical Policy: Ablation for Atrial Fibrillation, Policy Number 2024T0495U",
            "policy_last_updated": "2025-08-01",
        },
        "Aetna": {
            "payer": "Aetna",
            "procedure": "Catheter Ablation of Atrial Fibrillation (Pulmonary Vein Isolation)",
            "cpt_code": "93656",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Objectively documented atrial fibrillation",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "12-lead ECG, Holter, event monitor, or continuous ambulatory monitor with AF episodes recorded",
                },
                {
                    "criterion": "Clinical necessity: symptomatic AF refractory to at least one antiarrhythmic medication OR first-line ablation with documented rationale",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Failed or intolerant to AAD, OR paroxysmal AF with patient preference for ablation as first-line per 2023 ACC/AHA/HRS guidelines",
                },
                {
                    "criterion": "Symptom documentation with quality-of-life impact",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Palpitations, exercise intolerance, fatigue, or dizziness; AFEQT or EHRA symptom score preferred",
                },
                {
                    "criterion": "Structural cardiac assessment within 12 months",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "TTE documenting LVEF, left atrial size, and absence of significant structural disease; LA volume index preferred",
                },
                {
                    "criterion": "Appropriate anticoagulation management",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "CHA2DS2-VASc score documented; anticoagulation plan for peri-procedural and post-procedural period",
                },
            ],
            "documentation_required": [
                "ECG or ambulatory monitoring report documenting AF",
                "Electrophysiology consultation note",
                "Antiarrhythmic drug trial history (or rationale for first-line ablation)",
                "Echocardiogram report",
                "Anticoagulation management plan",
                "Current medication list",
            ],
            "submission_format": "portal",
            "typical_turnaround_days": 3,
            "common_denial_reasons": [
                "No objective AF documentation submitted",
                "First-line ablation requested without guideline-based rationale",
                "Symptom severity not adequately documented",
                "Echocardiogram not submitted",
            ],
            "appeal_success_factors": [
                "Ambulatory monitoring data with quantified AF burden",
                "Detailed AAD trial history or 2023 ACC/AHA/HRS guideline citation for first-line ablation",
                "AFEQT or equivalent quality-of-life score",
                "Peer-to-peer review with electrophysiologist",
            ],
            "policy_source": "Aetna Clinical Policy Bulletin: Catheter Ablation for Atrial Fibrillation, CPB 0713",
            "policy_last_updated": "2025-09-01",
        },
        "Blue Cross Blue Shield": {
            "payer": "Blue Cross Blue Shield",
            "procedure": "Catheter Ablation of Atrial Fibrillation (Pulmonary Vein Isolation)",
            "cpt_code": "93656",
            "auth_required": True,
            "clinical_criteria": [
                {
                    "criterion": "Documented atrial fibrillation on ECG or ambulatory monitoring",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "12-lead ECG, Holter monitor, event monitor, or implantable loop recorder with AF episodes",
                },
                {
                    "criterion": "Failure or intolerance of at least one Class I or Class III antiarrhythmic drug",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Documented trial with specific drug name, dose, duration, and reason for discontinuation",
                },
                {
                    "criterion": "Persistent symptoms attributable to AF despite treatment",
                    "required": True,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Palpitations, dyspnea, exercise intolerance, syncope, or presyncope with clear attribution to AF",
                },
                {
                    "criterion": "Echocardiogram within 12 months documenting LA size and LVEF",
                    "required": True,
                    "evidence_type": "imaging",
                    "acceptable_values": "TTE with LVEF, LA volume index or diameter; left atrial diameter < 6.0 cm preferred",
                },
                {
                    "criterion": "Thyroid function testing within 6 months",
                    "required": True,
                    "evidence_type": "lab",
                    "acceptable_values": "TSH within normal limits or thyroid disease treated and stable",
                },
                {
                    "criterion": "Peer-to-peer review required for repeat ablation or persistent AF > 12 months",
                    "required": False,
                    "evidence_type": "clinical note",
                    "acceptable_values": "Mandatory peer-to-peer with BCBS medical director for redo ablation or long-standing persistent AF",
                },
            ],
            "documentation_required": [
                "ECG or ambulatory monitor report documenting AF",
                "Electrophysiology consultation with complete treatment history",
                "Detailed antiarrhythmic drug trial documentation",
                "Echocardiogram report (TTE within 12 months)",
                "Thyroid function tests (TSH within 6 months)",
                "CHA2DS2-VASc score and anticoagulation plan",
                "Current medication list",
                "Cardiac MRI or CT if available (for LA anatomy and fibrosis assessment)",
            ],
            "submission_format": "fax",
            "typical_turnaround_days": 7,
            "common_denial_reasons": [
                "Antiarrhythmic drug trial documentation incomplete (missing doses, duration, or reason for failure)",
                "Thyroid function tests not submitted or outdated",
                "Left atrial size > 6.0 cm without peer-to-peer justification",
                "Repeat ablation without mandatory peer-to-peer review",
                "AF episodes not objectively documented",
            ],
            "appeal_success_factors": [
                "Peer-to-peer review with BCBS medical director (mandatory for all denials)",
                "Complete ambulatory monitoring data with AF burden quantification",
                "Detailed medication trial chronology with dates and adverse effects",
                "Cardiac MRI showing LA anatomy and fibrosis burden if available",
            ],
            "policy_source": "BCBS Medical Policy: Catheter Ablation for Atrial Fibrillation, Policy 2024-EP-0067",
            "policy_last_updated": "2025-06-01",
        },
    },
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_demo_chart(patient_id: str, procedure_code: str) -> ChartData:
    """Return a fully-populated ChartData object for a demo patient.

    Parameters
    ----------
    patient_id:
        One of ``DEMO-001``, ``DEMO-002``, ``DEMO-003``.
    procedure_code:
        CPT code (used for validation only; each patient maps to one procedure).

    Raises
    ------
    KeyError
        If *patient_id* is not found in ``DEMO_PATIENTS``.
    """
    pt = DEMO_PATIENTS[patient_id]

    return ChartData(
        patient_id=pt["mrn"],
        procedure_requested=pt["procedure"],
        procedure_code=pt["cpt"],
        diagnosis_codes=[d["code"] for d in pt["diagnoses"]],
        relevant_labs=[LabResult(**lab) for lab in pt["labs"]],
        relevant_imaging=[ImagingResult(**img) for img in pt["imaging"]],
        relevant_medications=[Medication(**med) for med in pt["medications"]],
        prior_treatments=pt["prior_treatments"],
        comorbidities=pt["comorbidities"],
        attending_physician=pt["attending"],
        insurance_id=pt["insurance_id"],
        payer_name=pt["payer"],
        confidence_score=0.98,
        missing_fields=[],
    )


def get_demo_policy(procedure_code: str, payer_name: str) -> PolicyData:
    """Return a fully-populated PolicyData object for a demo procedure and payer.

    Parameters
    ----------
    procedure_code:
        CPT code — one of ``93458``, ``33361``, ``93656``.
    payer_name:
        Payer name — ``UnitedHealthcare``, ``Aetna``, or ``Blue Cross Blue Shield``.
        Falls back to the first available payer if *payer_name* is not found.

    Raises
    ------
    KeyError
        If *procedure_code* is not found in ``DEMO_POLICIES``.
    """
    proc_policies = DEMO_POLICIES.get(procedure_code, {})
    pol = proc_policies.get(payer_name) or next(iter(proc_policies.values()), None)
    if not pol:
        raise KeyError(f"No policy for procedure code {procedure_code}")

    return PolicyData(
        payer=pol["payer"],
        procedure=pol["procedure"],
        cpt_code=pol["cpt_code"],
        auth_required=pol["auth_required"],
        clinical_criteria=[ClinicalCriterion(**c) for c in pol["clinical_criteria"]],
        documentation_required=pol["documentation_required"],
        submission_format=pol["submission_format"],
        typical_turnaround_days=pol["typical_turnaround_days"],
        common_denial_reasons=pol["common_denial_reasons"],
        appeal_success_factors=pol["appeal_success_factors"],
        policy_source=pol["policy_source"],
        policy_last_updated=pol["policy_last_updated"],
    )


def get_demo_reasoning(chart_data: ChartData, policy_data: PolicyData) -> ReasoningResult:
    """Generate a realistic demo reasoning result by mapping chart against policy."""
    criteria_met = []
    criteria_not_met = []

    # Map each policy criterion against chart data
    for criterion in policy_data.clinical_criteria:
        evidence = _find_evidence(chart_data, criterion.criterion)
        if evidence:
            criteria_met.append(CriterionEvaluation(
                criterion=criterion.criterion,
                met=True,
                evidence=evidence,
                confidence=0.95,
            ))
        else:
            criteria_not_met.append(CriterionGap(
                criterion=criterion.criterion,
                gap=f"No direct evidence found in chart for: {criterion.criterion}",
                recommendation=f"Add documentation for {criterion.evidence_type}: {criterion.acceptable_values}",
            ))

    total = len(policy_data.clinical_criteria)
    met_count = len(criteria_met)
    score = met_count / total if total > 0 else 0.0

    if score >= 0.8:
        label = "HIGH"
    elif score >= 0.6:
        label = "MEDIUM"
    elif score >= 0.4:
        label = "LOW"
    else:
        label = "DO NOT SUBMIT"

    # Build narrative from chart data
    narrative = _build_narrative(chart_data, policy_data, criteria_met)

    return ReasoningResult(
        criteria_met=criteria_met,
        criteria_not_met=criteria_not_met,
        approval_likelihood_score=round(score, 2),
        approval_likelihood_label=label,
        missing_documentation=[],
        pa_narrative_draft=narrative,
        narrative_tone="clinical",
        guideline_citations=[
            "ACC/AHA 2021 Guideline for Coronary Artery Revascularization",
            "ACC/AHA 2020 Guideline for Management of Valvular Heart Disease",
            "2023 ACC/AHA/ACCP/HRS Guideline for Diagnosis and Management of Atrial Fibrillation",
        ],
        cardiologist_review_flags=[],
    )


def get_demo_appeal(chart_data: ChartData, policy_data: PolicyData, denial_reason: str) -> str:
    """Generate a clinical appeal letter for a denied prior authorization.

    Parameters
    ----------
    chart_data:
        The patient's chart data from the original PA submission.
    policy_data:
        The payer policy data used in the original review.
    denial_reason:
        The stated reason for denial from the payer.

    Returns
    -------
    str
        A professionally formatted clinical appeal letter (~300 words).
    """
    # Gather clinical evidence from the chart
    dx_list = ", ".join(chart_data.diagnosis_codes[:3]) if chart_data.diagnosis_codes else "cardiac condition"
    comorbidities = ", ".join(chart_data.comorbidities[:3]) if chart_data.comorbidities else "none documented"

    labs_summary = ""
    if chart_data.relevant_labs:
        flagged = [l for l in chart_data.relevant_labs if l.flag in ("HIGH", "LOW")]
        if flagged:
            labs_summary = "; ".join(f"{l.name} {l.value} {l.unit}" for l in flagged[:4])
        else:
            labs_summary = "; ".join(f"{l.name} {l.value} {l.unit}" for l in chart_data.relevant_labs[:3])

    imaging_summary = ""
    if chart_data.relevant_imaging:
        imaging_summary = " ".join(
            f"{img.type} ({img.date}): {img.result_summary}"
            for img in chart_data.relevant_imaging[:2]
        )

    prior_tx = ""
    if chart_data.prior_treatments:
        prior_tx = "; ".join(chart_data.prior_treatments[:3])

    meds_list = ""
    if chart_data.relevant_medications:
        meds_list = ", ".join(f"{m.name} {m.dose}" for m in chart_data.relevant_medications[:4])

    return (
        f"APPEAL OF ADVERSE DETERMINATION — REQUEST FOR RECONSIDERATION\n"
        f"\n"
        f"Re: Prior Authorization for {chart_data.procedure_requested} "
        f"(CPT {chart_data.procedure_code})\n"
        f"Patient: {chart_data.patient_id} | Payer: {policy_data.payer}\n"
        f"Attending Physician: {chart_data.attending_physician}\n"
        f"\n"
        f"Dear Medical Director,\n"
        f"\n"
        f"I am writing to formally appeal the denial of prior authorization for "
        f"{chart_data.procedure_requested} (CPT {chart_data.procedure_code}) for the "
        f"above-referenced patient. The stated reason for denial was: \"{denial_reason}\"\n"
        f"\n"
        f"We respectfully submit that this determination does not reflect the full clinical "
        f"picture and request reconsideration based on the following evidence.\n"
        f"\n"
        f"CLINICAL PRESENTATION: This patient presents with {dx_list} and significant "
        f"comorbidities including {comorbidities}. The clinical severity warrants the "
        f"requested procedure as the appropriate standard of care.\n"
        f"\n"
        f"DIAGNOSTIC EVIDENCE: {imaging_summary}\n"
        f"\n"
        f"PERTINENT LABORATORY DATA: {labs_summary}\n"
        f"\n"
        f"PRIOR TREATMENT HISTORY: The patient has undergone appropriate conservative and "
        f"medical management prior to this request. {prior_tx}. Current medical therapy "
        f"includes {meds_list}. Despite these measures, the patient's condition necessitates "
        f"the requested intervention.\n"
        f"\n"
        f"GUIDELINE SUPPORT: Per current ACC/AHA guidelines, {chart_data.procedure_requested} "
        f"is indicated for patients with this clinical profile. The documented evidence "
        f"satisfies the criteria outlined in {policy_data.policy_source}. The denial reason "
        f"of \"{denial_reason}\" is addressed by the clinical data provided above.\n"
        f"\n"
        f"We request that this case be reconsidered in light of the complete clinical record. "
        f"If further clarification is needed, we are available for a peer-to-peer review at "
        f"your earliest convenience.\n"
        f"\n"
        f"Respectfully,\n"
        f"{chart_data.attending_physician}"
    )


def _find_evidence(chart: ChartData, criterion: str) -> str:
    """Find supporting evidence in chart for a given criterion."""
    criterion_lower = criterion.lower()

    # Check labs
    if any(kw in criterion_lower for kw in ["lab", "bnp", "troponin", "creatinine", "hemoglobin", "tsh", "thyroid", "inr"]):
        for lab in chart.relevant_labs:
            if any(kw in lab.name.lower() for kw in criterion_lower.split()):
                return f"{lab.name}: {lab.value} {lab.unit} ({lab.date})"
        if chart.relevant_labs:
            return f"Labs available: {', '.join(f'{l.name}={l.value} {l.unit}' for l in chart.relevant_labs[:3])}"

    # Check imaging
    if any(kw in criterion_lower for kw in ["echo", "imaging", "ct", "stress", "angiog", "catheter", "monitor", "ecg", "holter"]):
        for img in chart.relevant_imaging:
            return f"{img.type} ({img.date}): {img.result_summary}"

    # Check medications
    if any(kw in criterion_lower for kw in ["medication", "drug", "antiarrhythmic", "therapy", "medical", "anticoagul"]):
        if chart.relevant_medications:
            return f"Current medications: {', '.join(f'{m.name} {m.dose}' for m in chart.relevant_medications[:3])}"

    # Check prior treatments
    if any(kw in criterion_lower for kw in ["fail", "prior", "trial", "intolerance", "attempt"]):
        if chart.prior_treatments:
            return f"Prior treatments: {'; '.join(chart.prior_treatments[:3])}"

    # Check diagnoses and comorbidities
    if any(kw in criterion_lower for kw in ["symptom", "diagnosis", "nyha", "class", "document", "severe", "atrial"]):
        if chart.diagnosis_codes:
            return f"Documented diagnoses: {', '.join(chart.diagnosis_codes[:3])}. Comorbidities: {', '.join(chart.comorbidities[:3])}"

    # Check for structural/assessment criteria
    if any(kw in criterion_lower for kw in ["risk", "life expectancy", "heart team", "assessment", "evaluation"]):
        if chart.relevant_imaging:
            return f"Clinical assessment with imaging: {chart.relevant_imaging[0].result_summary}"

    # Generic fallback — try to find any relevant data
    if chart.relevant_imaging:
        return f"{chart.relevant_imaging[0].type}: {chart.relevant_imaging[0].result_summary}"

    return ""


def _build_narrative(chart: ChartData, policy: PolicyData, criteria_met: list) -> str:
    """Build a clinical PA narrative from chart data."""
    dx = ", ".join(chart.diagnosis_codes[:2]) if chart.diagnosis_codes else "cardiac condition"
    comorbidities = ", ".join(chart.comorbidities[:3]) if chart.comorbidities else "no significant comorbidities"

    labs_text = ""
    if chart.relevant_labs:
        labs_text = " Pertinent laboratory findings include " + ", ".join(
            f"{l.name} of {l.value} {l.unit} ({l.date})" for l in chart.relevant_labs[:4]
        ) + "."

    imaging_text = ""
    if chart.relevant_imaging:
        imaging_text = " " + " ".join(
            f"{img.type} performed on {img.date} demonstrated {img.result_summary}."
            for img in chart.relevant_imaging[:2]
        )

    meds_text = ""
    if chart.relevant_medications:
        meds_text = " Current medical therapy includes " + ", ".join(
            f"{m.name} {m.dose}" for m in chart.relevant_medications[:4]
        ) + "."

    prior_text = ""
    if chart.prior_treatments:
        prior_text = " Prior treatment attempts include " + "; ".join(chart.prior_treatments[:3]) + "."

    return (
        f"This letter is to request prior authorization for {chart.procedure_requested} "
        f"(CPT {chart.procedure_code}) for this patient with {dx}. "
        f"Relevant comorbidities include {comorbidities}."
        f"{labs_text}{imaging_text}{meds_text}{prior_text} "
        f"Based on the clinical presentation, diagnostic findings, and failure of conservative management, "
        f"{chart.procedure_requested} is medically necessary. This request meets the criteria outlined in "
        f"{policy.policy_source}. "
        f"{len(criteria_met)} of {len(policy.clinical_criteria)} payer-specified clinical criteria are satisfied "
        f"by the documented clinical evidence. "
        f"We respectfully request approval for this procedure as it represents the appropriate standard of care "
        f"per current ACC/AHA guidelines."
    )
