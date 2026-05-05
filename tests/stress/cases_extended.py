"""Expanded stress-test corpus — diverse scenarios beyond the seed 10.

Organized by category. Each case probes a specific failure mode
the pipeline is likely to hit in production. Pair this with the
seed corpus in cases.py.

Categories covered:
  A. Additional procedure families (TEE / EP / Carotid US / MRI / CT angio / PCI / LHC / Cardiac MRI w/o contrast)
  B. Format/extraction stress (dictation prose, all caps, mixed case, OCR artifacts, redacted, ALL-CAPS, copy-paste from EMR)
  C. Adversarial data quality (CPT typo, ICD/CPT mismatch, contradictory BMI, wrong-patient sections, future dates)
  D. Comorbidity-driven (CKD/contrast contraindication, contrast allergy, claustrophobia, ICD compatibility, pregnancy)
  E. Payer-specific (UHC-MA vs commercial, Aetna eviCore, Medicare LCD, Tricare, Medicaid)
  F. Approval-logic edges (resubmission, P2P required, capitated, dual coverage, frequency override w/ valid reason)
  G. Borderline + ambiguous (tied criteria, alternate pathway available, criteria-just-barely-met)
  H. Demographic edge cases (very elderly, young woman with symptoms, ethnically diverse names)
"""

from __future__ import annotations

from tests.stress.cases import StressCase


# ─── A. Additional procedure families ───────────────────────────────────────


_PROC_CASES: list[StressCase] = [

    StressCase(
        case_id="STRESS-A1-tee-stroke-source",
        request_cpt="93312",
        payer="UnitedHealthcare",
        note="""\
Patient: Adelaide R. Coleman
DOB: 04/12/1960
Sex: Female
Insurance Member ID: UHC-67821934

66-year-old female with cryptogenic ischemic stroke 14 days ago. Right MCA
distribution. NIH Stroke Scale 6 at admission, now 2 (residual mild expressive
aphasia). Echo TTE bedside — limited windows, no obvious cardiac source seen
but study suboptimal. Holter 14 days no AF detected. Carotids normal.

Plan: TEE (CPT 93312) to evaluate for cardioembolic source: PFO, atrial
septal aneurysm, LAA thrombus, valve vegetation, aortic atheroma.

Ordering: Vikram S. Krishnan, MD (Stroke Neurology consult)
NPI: 1567832914
ICD-10: I63.512, R47.01
""",
        expectation="APPROVE: standard TEE indication for stroke-source workup. Standard cardiology criterion taxonomy needs TEE-001 type criterion.",
    ),

    StressCase(
        case_id="STRESS-A2-ep-syncope",
        request_cpt="93619",
        payer="Aetna",
        note="""\
Patient: Marcus W. Tanaka-Johnson
DOB: 09/02/1971
Sex: Male
Insurance Member ID: AET-W3318856

54-year-old male with recurrent unexplained syncope (3 episodes in 6 months).
Episodes are sudden, no prodrome, including one while driving (resulting in
minor MVA). Family history: brother died suddenly age 38, presumed cardiac.

ECG: Sinus rhythm, occasional PVCs, no QT prolongation, no Brugada pattern.
Echo: structurally normal heart, LVEF 60%.
Holter 30 days: occasional non-sustained VT (5-7 beat runs), 2 episodes
correlating with palpitations.
Tilt-table: negative.
Stress test: no ischemia.

Concerning for primary arrhythmic cause given family history and NSVT.

Plan: Diagnostic EP study (CPT 93619) to evaluate for inducible VT, abnormal
substrate, accessory pathway. Possible same-session ablation if substrate found.

Ordering: Helena O. Schäfer-Patel, MD
NPI: 1029384765
ICD-10: R55, I47.2, Z82.41
""",
        expectation="APPROVE: classic EP study indication — recurrent unexplained syncope with positive family history of SCD and NSVT.",
    ),

    StressCase(
        case_id="STRESS-A3-carotid-tia",
        request_cpt="93880",
        payer="Medicare",
        note="""\
Patient: Hubert L. Vandermeulen
DOB: 01/30/1948
Medicare MBI: 4WT5-K83-MR21

78yo M with TIA 2 days ago. Right amaurosis fugax 12 minutes, fully resolved.
Hx: HTN, HLD, T2DM (HbA1c 8.2%), former smoker (quit 2010). On ASA 81. Statin.
Right carotid bruit on exam. No lateralizing motor or sensory deficits.

Brain MRI: small chronic infarcts, no acute. Echo: pending.

Order: Carotid duplex ultrasound (CPT 93880) to evaluate for hemodynamically
significant carotid stenosis as TIA source.

Ordering: Margaret C. O'Sullivan, MD (vascular neurology)
NPI: 1842765193
ICD-10: G45.9, I65.21, I10, E11.65
""",
        expectation="APPROVE: TIA workup with carotid bruit; standard indication.",
    ),

    StressCase(
        case_id="STRESS-A4-cardiac-mri-amyloid",
        request_cpt="75561",
        payer="UnitedHealthcare",
        note="""\
Patient: Ngozi C. Adeyemi
DOB: 08/14/1956
Insurance Member ID: UHC-44218763

70yo F with restrictive cardiomyopathy phenotype. Bilateral carpal tunnel s/p
release 6 years ago. Recent echo: thick LV walls (septum 16 mm), preserved
LVEF 60%, low-flow low-gradient pattern, marked diastolic dysfunction with
restrictive filling. Septal sparing on strain. ECG: low voltage, pseudo-
infarct pattern in V1-V3.

PYP scan ordered, awaits insurance auth. NT-proBNP 4,200.

To rule out cardiac amyloidosis: order Cardiac MRI with gadolinium (CPT 75561),
late gadolinium enhancement protocol. eGFR 58, gadolinium acceptable.

Ordering: Kenji M. Park, MD (advanced HF)
NPI: 1772938465
ICD-10: I42.5, I50.812
""",
        expectation="APPROVE: high pretest for amyloid (red flags = bilateral carpal tunnel + restrictive phenotype + low voltage + septal sparing on strain + low-flow low-gradient). MRI w/ LGE is standard indication.",
    ),

    StressCase(
        case_id="STRESS-A5-coronary-cta-low-risk",
        request_cpt="75574",
        payer="Aetna",
        note="""\
Patient: Yusuf J. Hassan
DOB: 11/29/1978
Insurance Member ID: AET-W7782319

47yo M with atypical chest pain x 3 weeks. Pain non-exertional, retrosternal,
relieved with antacids, also brought on by lying flat. No diabetes, no
HTN, no smoking. Father MI age 71, mother alive. BMI 26, exercises regularly.

ECG: normal. Troponins x2 negative in ED 2 weeks ago. Echo: normal.
HEART score: 3. CAD-RADS pretest probability: low.

Plan: Coronary CT angiography (CPT 75574) to rule out anatomic CAD given
intermediate clinical features and concerning family history. CTA preferred
over stress as a first test for low-to-intermediate pretest probability per
2021 chest pain guideline.

Ordering: Adwoa S. Mensah, MD
NPI: 1937284651
ICD-10: R07.9, Z82.49
""",
        expectation="APPROVE under 2021 chest pain guideline. CTA is appropriate first-line for low-intermediate pretest probability.",
    ),

    StressCase(
        case_id="STRESS-A6-pci-stable-angina",
        request_cpt="92928",
        payer="UnitedHealthcare",
        note="""\
Patient: Frederick W. Eriksson
DOB: 02/22/1958
Insurance Member ID: UHC-99821763

68yo M with stable angina CCS Class III despite optimal medical therapy
(ASA, atorvastatin 80, metoprolol 50 BID, isosorbide mononitrate 60 daily,
amlodipine 10). LHC 2 weeks ago: 90% mid-LAD lesion, focal, hemodynamically
significant FFR 0.71. RCA 40% non-flow-limiting. LCx clean.

Plan: PCI (CPT 92928) of mid-LAD lesion with DES.

Ordering: Heart Team review 04/22/2026: PCI recommended over CABG given
single-vessel disease and patient preference.

Ordering: Andreas K. Lindqvist, MD
NPI: 1102938475
ICD-10: I25.110, I25.111
""",
        expectation="APPROVE: failed OMT + flow-limiting LAD + Heart Team review = solid PCI indication.",
    ),

    StressCase(
        case_id="STRESS-A7-watchman-anticoag-contraind",
        request_cpt="33340",  # placeholder for Watchman
        payer="Medicare",
        note="""\
Patient: Beatriz M. Fernández-Ortiz
DOB: 06/03/1944
Medicare MBI: 9KX3-Y29-LM57

82yo F with non-valvular AF, CHA2DS2-VASc 6 (HTN, DM, prior stroke, female,
age >75), HAS-BLED 4. Recurrent GI bleeding x 3 episodes on warfarin in past
2 years; one on apixaban (cannot tolerate either anticoagulant). Hgb 9.4
anemia. Colonoscopy/EGD: angiodysplasia.

GI and Heart Team consensus: long-term anticoagulation is contraindicated.
Order: Left atrial appendage occlusion device (Watchman, CPT 33340).

Echo: LA size mildly enlarged. LAA anatomy favorable. No LAA thrombus on
TEE. LVEF 55%.

Ordering: Heart Team — Robert D. Lee, MD (interventional cardiology)
NPI: 1837465298
ICD-10: I48.0, K92.2, D62
""",
        expectation="APPROVE: classic Watchman indication. Tests whether the system handles a non-traditional CPT and the bleeding/anticoag contraindication framing.",
    ),

]


# ─── B. Format / extraction stress ──────────────────────────────────────────


_FORMAT_CASES: list[StressCase] = [

    StressCase(
        case_id="STRESS-B1-dictation-prose",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
this is mr. james patrick ovenshire, date of birth march fourteen nineteen
fifty seven, insurance is united healthcare member id u-h-c-six-six-three-
five-nine-two-eight-four-three. patient is a sixty nine year old gentleman
with known coronary disease he had a stent placed in his right coronary
artery in two thousand twenty one. he has diabetes blood pressure issues
and elevated cholesterol he also has chronic left bundle branch block on
his ekg. he comes to me today complaining of chest pressure that comes on
when he walks more than half a block this has been going on for about
seven weeks now. he has bad arthritis in both knees so he can not really
do a treadmill stress test his last stress test in twenty twenty four was
not diagnostic because he could not get to target heart rate plus he has
that bundle branch block. so today my plan is to send him for a lexiscan
spect myocardial perfusion imaging cpt seventy eight four five two. signed
electronically by doctor sanjay r kapoor n p i one zero two nine three
eight four seven five six.
""",
        expectation="EXTRACT + APPROVE: pure dictation prose, no headers, no punctuation. Tests whether the LLM extractor handles narrative-only format.",
    ),

    StressCase(
        case_id="STRESS-B2-all-caps-emr-export",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
PATIENT NAME: VICTORIA G. MENDOZA
DATE OF BIRTH: 05/18/1962
SEX: F
INSURANCE: UNITED HEALTHCARE
MEMBER ID: UHC-44729183

CHIEF COMPLAINT: CHEST PAIN

HISTORY OF PRESENT ILLNESS:
THIS IS A 63-YEAR-OLD FEMALE WITH HISTORY OF CAD STATUS POST PCI TO LCX
IN 2020, TYPE 2 DIABETES MELLITUS, HYPERTENSION, AND OBESITY (BMI 38.2).
PATIENT REPORTS PROGRESSIVE EXERTIONAL CHEST PRESSURE OVER THE PAST 8
WEEKS. SYMPTOMS ARE 6/10 PRESSURE-LIKE, RADIATE TO LEFT JAW, RELIEVED
WITH REST. HAS DUAL CHAMBER PACEMAKER PLACED 2023 FOR SICK SINUS SYNDROME.

ECG: PACED VENTRICULAR RHYTHM. PRIOR TREADMILL TESTING NONDIAGNOSTIC IN
2024 DUE TO PACED RHYTHM AND INABILITY TO ACHIEVE TARGET HEART RATE.

PLAN: ORDER CARDIAC PET MPI (CPT 78492). BMI ABOVE 35 FAVORS PET OVER
SPECT FOR ATTENUATION CONSIDERATIONS. PACED RHYTHM MAKES ECG NONDIAGNOSTIC.

ORDERING PROVIDER: KOFI A. ASANTE-WILLIAMSON, MD
NPI: 1574839263
""",
        expectation="EXTRACT + APPROVE: all-caps EMR-export format. Tests whether regex + Claude extractor handle case insensitivity correctly.",
    ),

    StressCase(
        case_id="STRESS-B3-redacted-phi",
        request_cpt="78452",
        payer="Aetna",
        note="""\
Patient: [REDACTED]
DOB: [REDACTED]
Member ID: AET-W[REDACTED]
MRN: [REDACTED]

72 year old female with chronic LBBB, severe knee osteoarthritis bilaterally,
known CAD with prior PCI in [REDACTED]. Presents for evaluation of recurrent
exertional chest pain. Cannot perform exercise treadmill due to mobility
limitations.

ECG today: Chronic LBBB, QRS 156 ms, no acute changes.
BMI: 31.4

Prior stress test in [REDACTED]: nondiagnostic, submaximal.

Plan: pharmacologic SPECT MPI (CPT 78452) for ischemia evaluation.

Ordered by: [REDACTED], MD
NPI: [REDACTED]
""",
        expectation="HOLD: heavily redacted PHI — patient name, DOB, member ID, NPI all unavailable. Should block on missing essentials despite clinical content being adequate.",
    ),

    StressCase(
        case_id="STRESS-B4-conflicting-bmi",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: Samantha N. Whitfield
DOB: 10/04/1965
Insurance Member ID: UHC-22817634

61yo F with CAD, T2DM, HTN, obesity. Recurrent angina.

History of Present Illness:
"...patient with morbid obesity, BMI 41.8 by today's measurements (weight 245 lb,
height 5'4")..."

Past Medical History:
1. Coronary artery disease, PCI 2018
2. Type 2 diabetes mellitus
3. Obesity (BMI 32 — patient reports recent weight loss of 35 lb on GLP-1 agonist)
4. Hypertension

Vital Signs (today):
BMI: 35.6 kg/m² (calculated from height 5'4", weight 207 lb)

ECG: Normal sinus rhythm, no acute changes.
Pacemaker: NONE.

Severe knee osteoarthritis limits exercise. Prior treadmill 2023: nondiagnostic.

Plan: Cardiac PET MPI (CPT 78492) given high BMI, inability to exercise,
and recurrent angina with prior CAD.

Ordering: Maximilian E. von Habsburg, MD
NPI: 1029384756
""",
        expectation="EXTRACT + FLAG: BMI mentioned as 41.8, 32, and 35.6 in different sections. Most recent vitals (35.6) is canonical but documentation_quality.extraction_warnings should flag the conflict.",
    ),

    StressCase(
        case_id="STRESS-B5-utf8-international-names",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
Patient: Søren Bjørn-Müller
DOB: 07/22/1959
Insurance Member ID: UHC-77392184

67yo M with longstanding CAD, prior LAD PCI 2019. Diabetes mellitus type 2.
Hypertension. Severe bilateral knee osteoarthritis (s/p R total knee
arthroplasty 2024). Cannot perform treadmill testing.

ECG: NSR with chronic LBBB, QRS 144 ms.
BMI: 28.6.
Prior treadmill 2023: submaximal, nondiagnostic.

Order: Pharmacologic SPECT MPI (CPT 78452) for ischemia evaluation.

Ordering: Aoife Ó Conchobhair, MD
NPI: 1837492637
""",
        expectation="EXTRACT correctly: international characters in names (ø, ø, ü, Ó). Tests Unicode handling in note_essentials regex and extraction.",
    ),

]


# ─── C. Adversarial / data-quality ─────────────────────────────────────────


_ADVERSARIAL_CASES: list[StressCase] = [

    StressCase(
        case_id="STRESS-C1-cpt-typo",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
Patient: Helen B. Carrington
DOB: 04/02/1960
Insurance Member ID: UHC-99284716

66yo F with CAD, LBBB, severe knee OA. Cannot exercise. Prior treadmill
nondiagnostic.

Plan: Lexiscan SPECT MPI (CPT 79452) — typo, intended CPT is 78452.

ICD-10: I25.118, I44.7

Ordering: Tobias R. Schneider, MD
NPI: 1928374651
""",
        expectation="ROBUSTNESS: note has CPT typo (79452 instead of 78452). The request_cpt is correct (78452). Resolver should not be confused by the typo in note text.",
    ),

    StressCase(
        case_id="STRESS-C2-icd-cpt-mismatch",
        request_cpt="78492",
        payer="Aetna",
        note="""\
Patient: Russell A. Chen
DOB: 03/15/1955
Insurance Member ID: AET-W3829174

71yo M with mild persistent asthma. Bronchodilator response on PFTs.
Occasional cough, no chest pain, no exertional symptoms. BMI 24.

No CAD, no diabetes, no hypertension, no family history.

Plan: Cardiac PET MPI (CPT 78492) for pulmonary symptoms.

ICD-10: J45.40 (asthma, mild persistent), R05.9 (cough, unspecified)

Ordering: Wei-Lin Zhang, MD
NPI: 1937284651
""",
        expectation="DENY: CPT and ICD-10 codes are mismatched. Asthma + cough don't justify cardiac PET MPI. The system should either deny or hold for clinical review.",
    ),

    StressCase(
        case_id="STRESS-C3-future-date",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
Patient: Walter J. Kohlmeier
DOB: 12/05/1947
Insurance Member ID: UHC-44928173

Date of Service: 06/15/2027

79yo M with CAD, chronic LBBB, severe knee OA, prior LAD PCI 2018.

Plan: SPECT MPI (CPT 78452).

Ordering: Devi P. Sundaresan, MD
NPI: 1029384756
""",
        expectation="WARNING: date of service is in the future. Should flag, not necessarily block.",
    ),

    StressCase(
        case_id="STRESS-C4-mismatched-name-sections",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: Eleanor Marie Tomlinson
DOB: 08/22/1958
Insurance Member ID: UHC-77382914

History of Present Illness:
This is John Smith, a 67yo male with CAD, BMI 38, pacemaker, presenting
with chest pain.

ECG: paced rhythm.
Plan: PET MPI (CPT 78492).

Ordering: Patricia O'Brien-Lynch, MD
NPI: 1573948261
""",
        expectation="FLAG: header says 'Eleanor Marie Tomlinson' but HPI says 'John Smith' (different name AND different sex). Either copy-paste error or intentional adversarial test. System should flag and hold.",
    ),

    StressCase(
        case_id="STRESS-C5-no-clinical-content",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
Patient: Daniel J. Park
DOB: 02/14/1972
Insurance Member ID: UHC-44729183

Plan: SPECT MPI (CPT 78452).

Ordering: Patricia M. Hennessy, MD
NPI: 1827394651
""",
        expectation="HOLD: no clinical content whatsoever — no symptoms, no PMH, no ECG, no prior testing, no indication. Identity essentials present but reasoner has nothing to evaluate.",
    ),

]


# ─── D. Comorbidity-driven ─────────────────────────────────────────────────


_COMORBIDITY_CASES: list[StressCase] = [

    StressCase(
        case_id="STRESS-D1-ckd-contrast-contraind",
        request_cpt="75574",
        payer="UnitedHealthcare",
        note="""\
Patient: Rosa Anastasia Calderón-Vega
DOB: 11/28/1964
Insurance Member ID: UHC-22817634

62yo F with CKD stage 4 (eGFR 22, baseline creatinine 2.8), T2DM, HTN.
History of contrast-induced nephropathy following catheterization in 2019.

Atypical chest pain x 6 weeks. ECG normal. Echo normal. Stress imaging
preferred over invasive cath given CKD risk.

Plan: Coronary CT angiography (CPT 75574) — but CKD limits IV contrast.
Will discuss with patient and consider alternative (stress CMR or PET).

Ordering: Lakshmi V. Krishnamurthy, MD
NPI: 1374829163
ICD-10: R07.9, N18.4, E11.22
""",
        expectation="HOLD/FLAG: CTA requires contrast; CKD stage 4 with prior CIN is a relative contraindication. Should flag for physician review or suggest alternative (PET, stress CMR).",
    ),

    StressCase(
        case_id="STRESS-D2-pregnant",
        request_cpt="93312",
        payer="Anthem",
        note="""\
Patient: Imani Olufemi-Adetoun
DOB: 03/15/1995
Insurance Member ID: ANT-77382914

29-year-old female, 14 weeks pregnant, with new-onset palpitations and
SVT requiring adenosine in ED 3 days ago. No prior cardiac history.

Echo: structurally normal. ECG: NSR now, prior strip showed AVNRT.

Plan: Transesophageal echocardiogram (CPT 93312) to evaluate for atrial
septal defect or PFO before considering ablation. Sedation will use
fetal-safe agents; OB consulted.

Ordering: Charlotte M. Forsberg, MD
NPI: 1372849261
ICD-10: I47.1, Z34.83
""",
        expectation="APPROVE with consult: pregnancy is a relative consideration for sedation/imaging but not a contraindication. System should NOT auto-approve without flagging the pregnancy + sedation question.",
    ),

    StressCase(
        case_id="STRESS-D3-icd-pacemaker-mri",
        request_cpt="75561",
        payer="UnitedHealthcare",
        note="""\
Patient: Henry-Pierre Lefèvre-Beaumont
DOB: 09/12/1953
Insurance Member ID: UHC-99284716

73yo M with non-ischemic cardiomyopathy LVEF 25%, ICD placed 2018 (Medtronic
Visia AF MRI SureScan, MR-conditional model). New onset NSVT on device
interrogation. Recent ICD shock.

Plan: Cardiac MRI with LGE (CPT 75561) to evaluate for fibrosis/scar burden
that would influence ablation candidacy. Device confirmed MRI-conditional;
pre-MRI device check + post-MRI device check planned with EP.

Ordering: Yamato T. Nakamura-Hayashi, MD (advanced HF)
NPI: 1827394651
ICD-10: I42.0, I47.2, Z95.810
""",
        expectation="APPROVE: device explicitly noted as MR-conditional. Tests whether the system handles ICD/pacemaker MRI eligibility (often rejected automatically without device-model verification).",
    ),

]


# ─── E. Payer-specific ──────────────────────────────────────────────────────


_PAYER_CASES: list[StressCase] = [

    StressCase(
        case_id="STRESS-E1-uhc-medicare-advantage",
        request_cpt="78452",
        payer="UnitedHealthcare Medicare Advantage",
        note="""\
Patient: Constance E. Wojciechowski
DOB: 06/14/1948
Insurance: UnitedHealthcare AARP Medicare Advantage Plan
Member ID: UHC-MA-77382914

77yo F with CAD, prior PCI 2017, LBBB, severe knee OA. Recurrent exertional
chest pain.

ECG: NSR with LBBB QRS 156. BMI 28.

Plan: Pharmacologic SPECT MPI (CPT 78452).

Per UHC MA policy, requires AUC consultation per PAMA. AUC tool consulted:
Appropriate (Score 8/9). Order placed.

Ordering: Daniel S. McCormack, MD
NPI: 1837492637
ICD-10: I25.118, I44.7
""",
        expectation="APPROVE: tests UHC-MA payer routing (different from UHC commercial) and PAMA AUC compliance documentation.",
    ),

    StressCase(
        case_id="STRESS-E2-tricare",
        request_cpt="93312",
        payer="Tricare",
        note="""\
Patient: SSG Marcus J. Holloway
DOB: 11/03/1985
Insurance: Tricare Prime
Sponsor SSN: [redacted]
Member ID: TRC-99182837

40yo active duty Army Sergeant with new-onset embolic stroke 7 days ago,
right MCA distribution. NIH 4 → 1 with intervention. Hx: well-controlled
HTN. Otherwise healthy. Holter no AF in 7 days. Carotid US negative.

Plan: TEE (CPT 93312) to assess for cardiac source of embolism (PFO, ASA,
LAA thrombus, valve vegetation, aortic atheroma).

Ordering: CDR Sandra L. Kim, MD (cardiology, Walter Reed)
NPI: 1392847651
ICD-10: I63.531, R47.01
""",
        expectation="APPROVE: tests Tricare-specific payer routing.",
    ),

]


# ─── F. Approval-logic edges ────────────────────────────────────────────────


_APPROVAL_LOGIC_CASES: list[StressCase] = [

    StressCase(
        case_id="STRESS-F1-resubmission-after-denial",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: Adrienne K. Whitmore
DOB: 04/19/1959
Insurance Member ID: UHC-99284716

68yo F. Initial PA for SPECT MPI denied 04/01/2026 — denial reason: "BMI
not documented; cannot determine if PET would be more appropriate."

Updated documentation: BMI 38.4 (5'3", 217 lb, measured today). Severe
bilateral hip osteoarthritis. Pacemaker (single-chamber 2022) for sick sinus.

Resubmitting as Cardiac PET MPI (CPT 78492) given BMI >35 and pacemaker.

Plan: PET MPI (CPT 78492).

Ordering: Cole G. Bertrand, MD
NPI: 1928374651
ICD-10: I25.118, R07.9, E66.01
""",
        expectation="APPROVE: documents that the prior denial reason has been addressed (BMI now documented at 38.4). Resubmission with corrected justification.",
    ),

    StressCase(
        case_id="STRESS-F2-frequency-with-new-symptoms",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
Patient: Theodore X. Garrison
DOB: 02/27/1962
Insurance Member ID: UHC-44729183

64yo M with CAD, prior LAD PCI 2017. Last SPECT MPI 09/2025 (5 months ago) —
NORMAL. NEW SYMPTOMS since: as of 02/2026, progressive crescendo angina
CCS Class III despite escalation of medical therapy. Two episodes of
unstable angina requiring ED visit (last week).

Repeat SPECT (CPT 78452) ordered urgently given:
1) New crescendo pattern (acute change from baseline)
2) Recent ED visits with negative troponins (not acute MI)
3) Need to determine if catheterization indicated

Note: Frequency exception per payer policy — repeat MPI within 12 months is
permitted for new symptoms / acute change in clinical status.

Ordering: Iolanthe M. Papadopoulos, MD
NPI: 1837465921
ICD-10: I25.110, I20.0
""",
        expectation="APPROVE despite recent prior: documented new symptoms with crescendo pattern justifies frequency exception. Tests whether the system recognizes 'new symptoms' as override for frequency rule.",
    ),

]


# ─── G. Borderline / ambiguous ──────────────────────────────────────────────


_BORDERLINE_CASES: list[StressCase] = [

    StressCase(
        case_id="STRESS-G1-tied-criteria",
        request_cpt="78452",
        payer="Aetna",
        note="""\
Patient: Bridget H. McAllister
DOB: 09/22/1968
Insurance Member ID: AET-W3392184

58yo F with hypertension and hyperlipidemia. No diabetes. No prior CAD.
Family history: father with MI age 68. BMI 27.

5 weeks of intermittent atypical chest pain — burning quality, primarily
at rest, partial response to omeprazole, no clear exertional component.

ECG: Normal sinus rhythm, no ischemic changes.
Treadmill 02/2026: completed full Bruce protocol, 12 METs, 91% MPHR, no
chest pain, no ECG changes, no arrhythmia. Result: NEGATIVE for inducible
ischemia.

Now patient reports continued symptoms; PCP requesting SPECT MPI for
"reassurance."

Plan: SPECT MPI (CPT 78452).

Ordering: Frederick H. Lipinski, MD
NPI: 1493827465
ICD-10: R07.9, I10
""",
        expectation="DENY/HOLD: low pretest probability + recent NORMAL exercise treadmill + atypical features. SPECT not indicated. Tests whether the system correctly DENIES low-utility orders.",
    ),

    StressCase(
        case_id="STRESS-G2-alternate-pathway-available",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: Vincent R. O'Donovan
DOB: 11/14/1956
Insurance Member ID: UHC-77392184

70yo M with longstanding CAD, prior CABG 2010 (LIMA-LAD, SVG-OM, SVG-RCA).
HTN, HLD, T2DM. BMI 27.4.

Recurrent atypical chest pain. ECG: NSR, no ischemic changes (no LBBB,
no paced rhythm).

Active and able to exercise — completes 35 minutes on stationary bike daily.

PCP ordered Cardiac PET MPI (CPT 78492). Note: Patient has NO BMI≥35,
NO conduction abnormality, NO inability to exercise. PET typically reserved
for cases where SPECT would be inadequate.

Plan: Cardiac PET MPI (CPT 78492).

Ordering: Marguerite F. Beaufort, MD
NPI: 1592837465
ICD-10: I25.118, R07.9
""",
        expectation="ALT MODALITY SUGGESTION: PET ordered but patient has no BMI/LBBB/paced/inability-to-exercise that would typically favor PET over SPECT. System should suggest exercise SPECT (78452) as the more appropriate alternative — not block, just flag.",
    ),

]


# ─── Combined ───────────────────────────────────────────────────────────────


EXTENDED_CASES: list[StressCase] = (
    _PROC_CASES + _FORMAT_CASES + _ADVERSARIAL_CASES
    + _COMORBIDITY_CASES + _PAYER_CASES + _APPROVAL_LOGIC_CASES
    + _BORDERLINE_CASES
)


def all_extended_case_ids() -> list[str]:
    return [c.case_id for c in EXTENDED_CASES]
