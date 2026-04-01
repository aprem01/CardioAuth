"""Demo/mock mode for CardioAuth.

Provides realistic cardiology patient data and payer policy data so the
full pipeline can run end-to-end without Epic FHIR sandbox auth or a
live Pinecone index.
"""

from __future__ import annotations

from cardioauth.models.chart import ChartData, ImagingResult, LabResult, Medication
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
            "I25.10",   # Atherosclerotic heart disease of native coronary artery without angina pectoris
            "I25.110",  # Atherosclerotic heart disease of native coronary artery with unstable angina pectoris
            "I50.22",   # Chronic systolic (systolic) heart failure
            "E11.9",    # Type 2 diabetes mellitus without complications
            "I10",      # Essential hypertension
            "E78.5",    # Dyslipidemia, unspecified
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
            "I35.0",    # Nonrheumatic aortic (valve) stenosis
            "I35.2",    # Nonrheumatic aortic (valve) stenosis with insufficiency
            "R55",      # Syncope and collapse
            "R06.00",   # Dyspnea, unspecified
            "I10",      # Essential hypertension
            "J44.1",    # Chronic obstructive pulmonary disease with acute exacerbation
            "N18.3",    # Chronic kidney disease, stage 3
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
            "I48.0",    # Paroxysmal atrial fibrillation
            "I48.91",   # Unspecified atrial fibrillation
            "R00.0",    # Tachycardia, unspecified
            "R42",      # Dizziness and giddiness
            "I10",      # Essential hypertension
            "E66.01",   # Morbid obesity due to excess calories
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
# Demo Policies — UnitedHealthcare criteria for each procedure
# ---------------------------------------------------------------------------

DEMO_POLICIES: dict[str, dict] = {
    # Left heart catheterization (CPT 93458)
    "93458": {
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

    # TAVR (CPT 33361)
    "33361": {
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

    # Catheter ablation of AFib (CPT 93656)
    "93656": {
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
        diagnosis_codes=pt["diagnoses"],
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
    """Return a fully-populated PolicyData object for a demo procedure.

    Parameters
    ----------
    procedure_code:
        CPT code — one of ``93458``, ``33361``, ``93656``.
    payer_name:
        Payer name (currently only ``UnitedHealthcare`` stocked).

    Raises
    ------
    KeyError
        If *procedure_code* is not found in ``DEMO_POLICIES``.
    """
    pol = DEMO_POLICIES[procedure_code]

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
