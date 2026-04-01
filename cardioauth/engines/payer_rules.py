"""Payer Rules Engine — living payer-specific authorization rules matrix.

Maps CPT codes to payer-specific authorization requirements for common
cardiology procedures. Data reflects realistic payer variability.
"""

from __future__ import annotations

from typing import Any, Optional

# Structure: {cpt_code: {payer: {auth_required, conditions, notes, last_updated}}}
PAYER_AUTH_MATRIX: dict[str, dict[str, dict[str, Any]]] = {
    "93306": {  # TTE (Transthoracic Echocardiogram)
        "UnitedHealthcare": {
            "auth_required": False,
            "conditions": "No auth needed for diagnostic TTE. Auth required if >2 TTEs in 12 months for the same diagnosis.",
            "notes": "Repeat studies within 6 months require clinical justification letter.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": False,
            "conditions": "No auth for initial diagnostic TTE. Repeat TTE within 12 months requires auth if same diagnosis.",
            "notes": "Aetna CPB 0715 applies. Document change in clinical status for repeats.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "All TTE studies require prior auth for commercial plans. Medicare Advantage exempt.",
            "notes": "BCBS uses eviCore for cardiology imaging management. Submit via eviCore portal.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": False,
            "conditions": "No auth for initial TTE. Auth required for repeat TTE within 90 days unless acute change.",
            "notes": "Cigna coverage policy 0512 — document new symptoms or clinical change for repeats.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": False,
            "conditions": "No auth for diagnostic TTE. Auth required if performed in outpatient hospital setting (place of service 22).",
            "notes": "Office-based TTE (POS 11) exempt. Hospital outpatient requires auth via Availity.",
            "last_updated": "2025-12-10",
        },
    },
    "93350": {  # Stress echo
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required for all stress echocardiography. Must document symptoms and pre-test probability.",
            "notes": "UHC uses AIM Specialty Health. Appropriate use criteria (AUC) must be reported.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Patient must have intermediate pre-test probability for CAD or known CAD with new symptoms.",
            "notes": "Low-risk asymptomatic screening not covered. Aetna CPB 0002.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required via eviCore. Must meet ACC Appropriate Use Criteria.",
            "notes": "Submit AUC score with auth request. Denials common when AUC is 'rarely appropriate'.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. Must document inability to exercise or need for pharmacologic stress.",
            "notes": "Cigna requires documentation of Duke Treadmill Score or equivalent risk stratification.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required for all stress imaging. Exercise stress ECG preferred as first-line.",
            "notes": "Must document why exercise stress ECG alone is insufficient. Humana clinical policy CL-026.",
            "last_updated": "2025-12-10",
        },
    },
    "93458": {  # Left heart catheterization
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required for elective catheterization. Emergency/urgent cath exempt with notification within 48 hours.",
            "notes": "Must document positive non-invasive testing or high clinical suspicion. ACS presentations exempt.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required for elective. Must have completed non-invasive workup first unless ACS.",
            "notes": "Aetna requires documentation of LVEF, symptom timeline, and failed medical therapy.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required. Non-invasive testing must be completed and abnormal. Document ACC/AHA Class I or IIa indication.",
            "notes": "BCBS requires peer-to-peer if initial auth denied. 30-day appeal window.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required for all elective diagnostic catheterizations. Emergent cath requires notification within 24 hours.",
            "notes": "Cigna uses InterQual criteria. Positive stress test or EF <40% with symptoms generally approved.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Must document ACC/AHA appropriate use criteria score.",
            "notes": "Humana denies if only indication is 'chest pain' without supporting objective data.",
            "last_updated": "2025-12-10",
        },
    },
    "93460": {  # Right and left heart catheterization
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Must justify need for right heart cath in addition to left. Pulmonary hypertension or valvular disease evaluation.",
            "notes": "Document hemodynamic indication — suspected PH, constrictive vs restrictive, or pre-transplant evaluation.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Right heart cath must be medically necessary beyond diagnostic left cath.",
            "notes": "Pre-TAVR evaluation, transplant workup, and suspected PH are accepted indications.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required via eviCore. Must document specific indication for bilateral catheterization.",
            "notes": "Valvular heart disease workup and cardiomyopathy evaluation are standard approved indications.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. Must document why right heart hemodynamics are needed.",
            "notes": "Elevated BNP alone insufficient — need echo findings suggestive of elevated filling pressures.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Accepted for pre-surgical evaluation, pulmonary hypertension workup, transplant evaluation.",
            "notes": "Humana frequently denies for 'routine' combined cath. Document specific clinical question.",
            "last_updated": "2025-12-10",
        },
    },
    "33361": {  # TAVR (Transcatheter Aortic Valve Replacement)
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Patient must meet STS/ACC TVT criteria. Heart team evaluation documented.",
            "notes": "STS-PROM score required. Intermediate or high surgical risk. Must be performed at certified TAVR center.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Severe symptomatic aortic stenosis (AVA <1.0 cm2 or mean gradient >40 mmHg). Heart team evaluation mandatory.",
            "notes": "Aetna CPB 0790. Low-risk TAVR covered per FDA label. Bicuspid valve requires additional justification.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required. Must meet NCD 20.32 criteria for Medicare or commercial equivalent.",
            "notes": "BCBS requires documented heart team meeting note, STS score, and CT angiography measurements.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. Severe aortic stenosis with NYHA Class II-IV symptoms. Heart team concurrence required.",
            "notes": "Cigna requires 30-day pre-procedure documentation package including echo, CT, and heart team note.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Must meet CMS NCD criteria. Facility must be in Humana's TAVR center network.",
            "notes": "Humana requires submission of STS score, heart team note, and pre-procedure imaging within 60 days.",
            "last_updated": "2025-12-10",
        },
    },
    "93656": {  # AF ablation (comprehensive EP study + ablation)
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Must document failure of at least one antiarrhythmic drug (AAD) or intolerance.",
            "notes": "UHC policy 2024T0515Y. Rate control failure alone may not suffice — document AAD trial.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Failed or intolerant to at least one Class I or III AAD. Symptomatic AF documented.",
            "notes": "Aetna CPB 0399. First-line ablation considered for paroxysmal AF per HRS 2024 guidelines.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required. Document AF type (paroxysmal/persistent), symptom burden, and failed medical therapy.",
            "notes": "BCBS increasingly approving first-line ablation for paroxysmal AF per EARLY-AF and STOP-AF trials.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. Must document symptomatic AF refractory to at least one AAD. Left atrial size must be documented.",
            "notes": "Cigna requires pre-procedure TEE or CT to assess LA appendage thrombus. LA >55mm may trigger additional review.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Symptomatic AF with failed rate or rhythm control. CHA2DS2-VASc score must be documented.",
            "notes": "Humana requires documentation of anticoagulation plan and stroke risk assessment.",
            "last_updated": "2025-12-10",
        },
    },
    "78452": {  # Nuclear stress test (SPECT MPI)
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required via AIM Specialty Health. Must report AUC (Appropriate Use Criteria) decision support.",
            "notes": "QIM (Qualified Clinical Decision Support Mechanism) consultation required. G-codes must be reported.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Patient must have intermediate pre-test probability. Low-risk asymptomatic screening denied.",
            "notes": "Aetna CPB 0002. Stress echo preferred over SPECT for initial evaluation if available.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required via eviCore. Must meet ACC AUC — 'appropriate' or 'may be appropriate' rating.",
            "notes": "eviCore denials increasing for 'rarely appropriate' indications. Document clinical rationale thoroughly.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. Symptomatic patients with intermediate risk or known CAD with change in status.",
            "notes": "Cigna prefers exercise stress when patient can exercise. Pharmacologic stress requires justification.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Must document why stress echo or exercise ECG is insufficient.",
            "notes": "Humana step-therapy: exercise ECG first, then stress echo, then nuclear if prior tests non-diagnostic.",
            "last_updated": "2025-12-10",
        },
    },
    "75574": {  # Cardiac CTA (Coronary CT Angiography)
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Appropriate for intermediate risk chest pain. Must document pre-test probability.",
            "notes": "UHC covers CCTA as first-line for stable chest pain per NICE/ACC guidelines.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Intermediate pre-test probability for CAD. Not covered for asymptomatic screening.",
            "notes": "Aetna CPB 0228. Calcium scoring (75571) alone generally not covered for screening.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required via eviCore. Acute chest pain in ED may be exempt per local plan.",
            "notes": "BCBS covers CCTA for stable chest pain evaluation per SCOT-HEART and PROMISE trial evidence.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required for outpatient. ED chest pain evaluation exempt with notification.",
            "notes": "Cigna requires documentation that patient can achieve adequate heart rate control for quality imaging.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Covered for symptomatic patients with low-to-intermediate risk for CAD.",
            "notes": "Humana increasingly covering CCTA as alternative to stress testing per 2024 chest pain guidelines.",
            "last_updated": "2025-12-10",
        },
    },
    "75557": {  # Cardiac MRI (without contrast)
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Must document specific clinical question not answerable by echocardiography.",
            "notes": "Approved indications: cardiomyopathy workup, myocarditis, infiltrative disease, viability assessment.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Must document why echocardiography is insufficient for the clinical question.",
            "notes": "Aetna CPB 0654. Viability assessment, ARVC evaluation, and cardiac mass characterization approved.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required via eviCore. Specific clinical indication must align with ACC AUC.",
            "notes": "BCBS frequently denies for 'further evaluation of cardiomyopathy' without specific clinical question.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. Accepted for tissue characterization, infiltrative disease, and congenital heart disease.",
            "notes": "Cigna requires documentation of prior echo results showing need for advanced imaging.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Limited coverage — must demonstrate echo was non-diagnostic or insufficient.",
            "notes": "Humana covers cardiac MRI for sarcoidosis, amyloidosis, myocarditis, and pre-ablation planning.",
            "last_updated": "2025-12-10",
        },
    },
    "92928": {  # PCI with stent, single vessel
        "UnitedHealthcare": {
            "auth_required": False,
            "conditions": "No auth for emergent PCI (STEMI/NSTEMI). Elective PCI requires auth with documentation of ischemia.",
            "notes": "Elective PCI: document FFR <0.80 or iFR <0.89 for non-culprit lesions. ISCHEMIA trial criteria apply.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": False,
            "conditions": "No auth for emergent PCI. Elective PCI requires auth with positive ischemia testing.",
            "notes": "Aetna requires FFR/iFR documentation for intermediate stenoses (40-70%). Anatomic-only PCI reviewed closely.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required for elective PCI. Emergent PCI requires notification within 48 hours.",
            "notes": "BCBS requires angiographic images and hemodynamic data. FFR documentation for borderline lesions.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": False,
            "conditions": "No auth for emergent PCI. Elective single-vessel PCI requires auth if no prior positive stress test.",
            "notes": "Cigna uses ISCHEMIA trial criteria — medical therapy first for stable ischemic heart disease.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required for elective PCI. Emergent PCI exempt with retrospective notification.",
            "notes": "Humana requires documentation of >70% stenosis or FFR <0.80. Heart team discussion for left main/multivessel.",
            "last_updated": "2025-12-10",
        },
    },
    "92941": {  # PCI for acute MI
        "UnitedHealthcare": {
            "auth_required": False,
            "conditions": "No auth required. Emergent procedure — notify payer within 48 hours of admission.",
            "notes": "Retrospective review only. Document door-to-balloon time and clinical presentation.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": False,
            "conditions": "No auth required for acute MI intervention. Post-procedure notification within 48 hours.",
            "notes": "Ensure ICD-10 reflects STEMI/NSTEMI type (I21.xx series). Non-culprit staged PCI requires separate auth.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": False,
            "conditions": "No auth required. Emergency procedure. Retrospective notification required within 72 hours.",
            "notes": "Staged non-culprit PCI during same admission may require concurrent auth.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": False,
            "conditions": "No auth required for acute MI PCI. Notification within 24 hours recommended.",
            "notes": "Cigna requires admission notification for inpatient stay. PCI itself is exempt from auth.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": False,
            "conditions": "No auth required. Emergency procedure exempt. Notify within 48 hours of admission.",
            "notes": "Document STEMI activation or NSTEMI risk score (TIMI/GRACE) for medical record.",
            "last_updated": "2025-12-10",
        },
    },
    "33249": {  # ICD implant
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Must meet CMS NCD 20.4 criteria — EF ≤35%, NYHA II-III, ≥40 days post-MI, optimal medical therapy ≥90 days.",
            "notes": "Primary prevention: document EF, NYHA class, GDMT optimization. Secondary prevention: document VT/VF event.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. NCD 20.4 criteria. EF must be measured ≥40 days post-MI and ≥90 days after revascularization.",
            "notes": "Aetna CPB 0532. CRT-D requires additional QRS duration criteria (≥150ms LBBB preferred).",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required. Strict adherence to CMS NCD criteria. Document 90-day GDMT trial.",
            "notes": "BCBS frequently requests medication reconciliation showing 90 days of target-dose GDMT.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. NCD 20.4 criteria plus documentation of shared decision-making.",
            "notes": "Cigna requires shared decision-making note documenting patient understanding of ICD therapy.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. CMS NCD criteria. Must document life expectancy >1 year.",
            "notes": "Humana denials common when GDMT optimization not documented. Include medication list with doses and duration.",
            "last_updated": "2025-12-10",
        },
    },
    "33208": {  # Pacemaker implant (dual chamber)
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Must meet ACC/AHA/HRS indications — symptomatic bradycardia, high-grade AV block, or sinus node dysfunction.",
            "notes": "Document ECG/Holter showing arrhythmia correlation with symptoms. Medication-induced bradycardia reviewed separately.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. ACC/AHA Class I or IIa indication. Asymptomatic first-degree AV block not covered.",
            "notes": "Aetna requires Holter or event monitor documentation for intermittent bradycardia.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required. Must document symptomatic bradycardia with ECG correlation.",
            "notes": "BCBS requires justification for dual-chamber vs. single-chamber selection.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. HRS 2018 bradycardia guideline criteria. Document symptom-rhythm correlation.",
            "notes": "Cigna covers leadless pacemaker (Micra) for selected patients — separate auth pathway.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Must meet CMS criteria for pacemaker implantation.",
            "notes": "Humana requires pre-op cardiology consultation note documenting indication and device selection rationale.",
            "last_updated": "2025-12-10",
        },
    },
    "93291": {  # Remote monitoring, pacemaker
        "UnitedHealthcare": {
            "auth_required": False,
            "conditions": "No auth required. Covered for established pacemaker patients with compatible devices.",
            "notes": "Bill once per 90-day period. Must have documented device interrogation report on file.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": False,
            "conditions": "No auth required. Covered per CMS guidelines for remote interrogation monitoring.",
            "notes": "Aetna covers 93291 (tech component) and 93294 (professional component) per 90-day period.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": False,
            "conditions": "No auth required for remote monitoring of implanted pacemakers.",
            "notes": "BCBS requires active remote monitoring enrollment. Annual in-person interrogation still needed.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": False,
            "conditions": "No auth required. Standard benefit for patients with implanted cardiac devices.",
            "notes": "Cigna covers remote monitoring per CMS frequency guidelines. Document device transmission dates.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": False,
            "conditions": "No auth required for remote device monitoring.",
            "notes": "Humana requires documentation of each 90-day monitoring period with transmission logs.",
            "last_updated": "2025-12-10",
        },
    },
    "93295": {  # Remote monitoring, ICD
        "UnitedHealthcare": {
            "auth_required": False,
            "conditions": "No auth required. Standard coverage for ICD remote monitoring per CMS guidelines.",
            "notes": "Bill per 90-day period. Document arrhythmia alerts and device parameter trends.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": False,
            "conditions": "No auth required. Covered for all patients with implanted ICDs.",
            "notes": "Must document device interrogation findings including battery status and lead impedance.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": False,
            "conditions": "No auth required for ICD remote monitoring.",
            "notes": "BCBS allows concurrent 93295 with 93289 (ICD interrogation) per CMS billing guidelines.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": False,
            "conditions": "No auth required. Standard ICD monitoring benefit.",
            "notes": "Document any clinically actionable alerts from remote transmissions.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": False,
            "conditions": "No auth required for ICD remote monitoring.",
            "notes": "Humana covers per 90-day period. Maintain transmission log for audit purposes.",
            "last_updated": "2025-12-10",
        },
    },
    "93297": {  # Remote monitoring, ICM (Implantable Cardiac Monitor)
        "UnitedHealthcare": {
            "auth_required": False,
            "conditions": "No auth required for remote monitoring. Note: ICM implant (33285) requires separate auth.",
            "notes": "Bill per 30-day period for ICM monitoring (differs from pacemaker/ICD 90-day cycle).",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": False,
            "conditions": "No auth for monitoring. ICM device implant requires separate authorization.",
            "notes": "Aetna covers 93297 per 30-day period. Ensure the ICM implant was previously authorized.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": False,
            "conditions": "No auth required for ICM remote monitoring.",
            "notes": "BCBS covers per 30-day monitoring period. Annual review of continued medical necessity.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": False,
            "conditions": "No auth required for ICM monitoring. Coverage continues for duration of clinical indication.",
            "notes": "Document ongoing clinical indication at each office visit (e.g., cryptogenic stroke workup).",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": False,
            "conditions": "No auth required for ICM remote monitoring.",
            "notes": "Humana covers per 30-day period. Document clinical indication for continued monitoring.",
            "last_updated": "2025-12-10",
        },
    },
    "93351": {  # Stress echo with contrast
        "UnitedHealthcare": {
            "auth_required": True,
            "conditions": "Auth required. Must document suboptimal baseline echo windows requiring contrast enhancement.",
            "notes": "UHC requires documentation of ≥2 non-visualized segments on baseline images before contrast use.",
            "last_updated": "2026-01-15",
        },
        "Aetna": {
            "auth_required": True,
            "conditions": "Auth required. Contrast use must be justified by suboptimal image quality.",
            "notes": "Aetna CPB 0002. Routine use of contrast not covered — must document image quality limitation.",
            "last_updated": "2026-02-01",
        },
        "Blue Cross Blue Shield": {
            "auth_required": True,
            "conditions": "Auth required via eviCore. Document technically limited baseline images.",
            "notes": "BCBS covers echo contrast agents when ≥2 contiguous segments not visualized. Document BMI if applicable.",
            "last_updated": "2026-01-20",
        },
        "Cigna": {
            "auth_required": True,
            "conditions": "Auth required. Must document suboptimal image quality on non-contrast images.",
            "notes": "Cigna requires real-time documentation of image quality assessment before contrast administration.",
            "last_updated": "2026-03-01",
        },
        "Humana": {
            "auth_required": True,
            "conditions": "Auth required. Contrast agent use must be medically necessary and documented.",
            "notes": "Humana covers echo contrast per ASE guidelines. Document number of non-visualized segments.",
            "last_updated": "2025-12-10",
        },
    },
}

# CPT code descriptions for reference
CPT_DESCRIPTIONS: dict[str, str] = {
    "93306": "Transthoracic echocardiogram (TTE) complete",
    "93350": "Stress echocardiography",
    "93458": "Left heart catheterization",
    "93460": "Right and left heart catheterization",
    "33361": "TAVR (Transcatheter Aortic Valve Replacement)",
    "93656": "AF ablation (comprehensive EP study + ablation)",
    "78452": "Nuclear stress test (SPECT MPI)",
    "75574": "Cardiac CTA (Coronary CT Angiography)",
    "75557": "Cardiac MRI (without contrast)",
    "92928": "PCI with stent, single vessel",
    "92941": "PCI for acute MI",
    "33249": "ICD implant",
    "33208": "Pacemaker implant (dual chamber)",
    "93291": "Remote monitoring, pacemaker",
    "93295": "Remote monitoring, ICD",
    "93297": "Remote monitoring, ICM",
    "93351": "Stress echo with contrast",
}


def check_auth_required(cpt_code: str, payer: str) -> dict[str, Any]:
    """Check whether prior authorization is required for a CPT code + payer combination.

    Returns:
        dict with keys: auth_required, conditions, notes, last_updated, cpt_code,
        cpt_description, payer, found
    """
    cpt_description = CPT_DESCRIPTIONS.get(cpt_code, "Unknown procedure")
    result: dict[str, Any] = {
        "cpt_code": cpt_code,
        "cpt_description": cpt_description,
        "payer": payer,
        "found": False,
    }

    cpt_entry = PAYER_AUTH_MATRIX.get(cpt_code)
    if not cpt_entry:
        result["error"] = f"CPT code {cpt_code} not found in payer rules matrix"
        return result

    payer_entry = cpt_entry.get(payer)
    if not payer_entry:
        # Try partial match
        for known_payer, data in cpt_entry.items():
            if payer.lower() in known_payer.lower() or known_payer.lower() in payer.lower():
                payer_entry = data
                result["payer_matched"] = known_payer
                break

    if not payer_entry:
        result["error"] = f"Payer '{payer}' not found for CPT {cpt_code}"
        result["available_payers"] = list(cpt_entry.keys())
        return result

    result.update(payer_entry)
    result["found"] = True
    return result


def get_payer_matrix(payer: Optional[str] = None) -> dict[str, Any]:
    """Return the full payer authorization matrix, optionally filtered by payer.

    Args:
        payer: If provided, filter matrix to only this payer's rules.

    Returns:
        dict with structure {cpt_code: {payer: rules}} and metadata.
    """
    if payer is None:
        return {
            "matrix": PAYER_AUTH_MATRIX,
            "cpt_descriptions": CPT_DESCRIPTIONS,
            "payers": ["UnitedHealthcare", "Aetna", "Blue Cross Blue Shield", "Cigna", "Humana"],
            "total_codes": len(PAYER_AUTH_MATRIX),
        }

    filtered: dict[str, dict[str, Any]] = {}
    matched_payer = None

    for cpt_code, payer_rules in PAYER_AUTH_MATRIX.items():
        for known_payer, rules in payer_rules.items():
            if payer.lower() in known_payer.lower() or known_payer.lower() in payer.lower():
                matched_payer = known_payer
                filtered[cpt_code] = {known_payer: rules}
                break

    return {
        "matrix": filtered,
        "cpt_descriptions": CPT_DESCRIPTIONS,
        "payer_filter": matched_payer or payer,
        "total_codes": len(filtered),
    }


def flag_at_order_time(cpt_code: str, payer: str) -> dict[str, Any]:
    """Generate an alert at procedure order time with auth requirements and documentation guidance.

    This function is designed to be called when a physician orders a procedure,
    providing real-time decision support about authorization requirements.

    Returns:
        dict with keys: alert_level (red/yellow/green), auth_required, message,
        documentation_checklist, suggested_actions
    """
    auth_info = check_auth_required(cpt_code, payer)
    cpt_description = CPT_DESCRIPTIONS.get(cpt_code, "Unknown procedure")

    if not auth_info.get("found"):
        return {
            "alert_level": "yellow",
            "cpt_code": cpt_code,
            "cpt_description": cpt_description,
            "payer": payer,
            "auth_required": None,
            "message": f"Unable to determine auth requirements for {cpt_code} with {payer}. Recommend verifying with payer directly.",
            "documentation_checklist": [],
            "suggested_actions": ["Contact payer to verify auth requirements", "Check payer portal for current policy"],
        }

    auth_required = auth_info.get("auth_required", False)
    conditions = auth_info.get("conditions", "")
    notes = auth_info.get("notes", "")

    if auth_required:
        # Build documentation checklist based on procedure type
        checklist = _get_documentation_checklist(cpt_code)
        return {
            "alert_level": "red",
            "cpt_code": cpt_code,
            "cpt_description": cpt_description,
            "payer": payer,
            "auth_required": True,
            "message": f"PRIOR AUTH REQUIRED: {conditions}",
            "payer_notes": notes,
            "documentation_checklist": checklist,
            "suggested_actions": [
                "Initiate prior authorization before scheduling procedure",
                "Ensure all required documentation is current and complete",
                "Verify patient eligibility and benefits",
                "Document medical necessity in clinical note",
            ],
            "last_updated": auth_info.get("last_updated"),
        }
    else:
        return {
            "alert_level": "green",
            "cpt_code": cpt_code,
            "cpt_description": cpt_description,
            "payer": payer,
            "auth_required": False,
            "message": f"No prior auth required. {conditions}",
            "payer_notes": notes,
            "documentation_checklist": [],
            "suggested_actions": [
                "Proceed with scheduling",
                "Ensure accurate ICD-10 coding at time of billing",
            ],
            "last_updated": auth_info.get("last_updated"),
        }


def _get_documentation_checklist(cpt_code: str) -> list[str]:
    """Return a documentation checklist for common cardiology procedures."""
    checklists: dict[str, list[str]] = {
        "93306": [
            "Clinical indication for echocardiogram",
            "Prior echo results if repeat study",
            "Change in clinical status documentation",
        ],
        "93350": [
            "Symptoms (chest pain, dyspnea, etc.)",
            "Pre-test probability assessment",
            "AUC (Appropriate Use Criteria) score",
            "Reason pharmacologic stress if applicable",
        ],
        "93458": [
            "Non-invasive test results (stress test, CTA, etc.)",
            "Ejection fraction (recent echo)",
            "Symptom description and timeline",
            "Failed medical therapy documentation",
            "Risk factor documentation",
        ],
        "93460": [
            "Non-invasive test results",
            "Indication for right heart catheterization",
            "Hemodynamic question to be answered",
            "Recent echo with valve assessment",
        ],
        "33361": [
            "Echo showing severe aortic stenosis (AVA, gradients)",
            "STS-PROM score calculation",
            "Heart team evaluation note",
            "CT angiography measurements",
            "NYHA functional class documentation",
            "Surgical risk assessment",
        ],
        "93656": [
            "AF type and duration documentation",
            "Failed AAD therapy (drug, dose, duration, reason for failure)",
            "Symptom burden documentation",
            "Left atrial size (echo measurement)",
            "Anticoagulation status and plan",
            "Pre-procedure imaging (TEE or CT)",
        ],
        "78452": [
            "Symptoms and pre-test probability",
            "AUC decision support documentation",
            "G-code reporting (AUC compliance)",
            "Reason for nuclear over stress echo if applicable",
        ],
        "75574": [
            "Symptoms and clinical indication",
            "Pre-test probability assessment",
            "Heart rate and ability to achieve rate control",
            "Renal function (contrast consideration)",
        ],
        "75557": [
            "Specific clinical question for MRI",
            "Prior echo results and limitations",
            "Why echo is insufficient",
            "MRI-specific contraindication screening",
        ],
        "92928": [
            "Positive ischemia testing results",
            "Angiographic findings (stenosis severity)",
            "FFR/iFR for intermediate lesions",
            "Failed medical therapy documentation",
        ],
        "33249": [
            "Ejection fraction ≤35% (date and method)",
            "NYHA functional class",
            "40+ days post-MI documentation",
            "90+ days GDMT at target doses",
            "Medication list with doses and dates started",
            "Life expectancy >1 year assessment",
        ],
        "33208": [
            "ECG/Holter showing bradyarrhythmia",
            "Symptom-rhythm correlation",
            "Indication per ACC/AHA/HRS guidelines",
            "Justification for dual vs. single chamber",
        ],
        "93351": [
            "Suboptimal baseline echo image quality documentation",
            "Number of non-visualized segments",
            "Body habitus or other limiting factors",
            "AUC score for stress echo",
        ],
    }
    return checklists.get(cpt_code, ["Document clinical indication", "Verify payer-specific requirements"])
