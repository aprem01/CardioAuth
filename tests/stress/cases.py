"""Stress-test corpus for the live pipeline.

10 deidentified-style synthetic cases covering the diversity that
real PA traffic produces. Used to shake out edge-case failures with
real LLM calls — not for unit testing.

Each case carries:
  - case_id: stable identifier
  - request_cpt: the CPT the office submits
  - payer: payer name
  - note: deidentified clinical note text
  - expectation: what we think the pipeline SHOULD do (not a hard
    ground truth — a sanity check for the human reviewer)

Edge cases covered:
  1. Strong PET case (BMI≥35 + pacemaker + nondiagnostic)         → APPROVE
  2. Strong SPECT case (LBBB + inability to exercise)             → APPROVE
  3. CPT divergence (note orders SPECT, request says PET)         → resolve to SPECT, flag
  4. Borderline BMI=35.0                                          → either path defensible
  5. No Member ID                                                 → BLOCK
  6. Terse Cerner-style abbreviations                             → extract correctly
  7. Multi-CPT mention (note mentions both 78452 and 78492)       → resolve cleanly
  8. Insufficient documentation (single sentence)                 → HOLD
  9. Strong contraindication (recent stress test approved)        → DENY/HOLD (frequency)
 10. TAVR case (different procedure family)                       → APPROVE under structural-heart criteria
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StressCase:
    case_id: str
    request_cpt: str
    payer: str
    note: str
    expectation: str  # human-readable description


CASES: list[StressCase] = [
    StressCase(
        case_id="STRESS-01-pet-strong",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: Robert L. Martinez
DOB: 03/22/1962
Age: 64
Sex: Male
MRN: BHV-556921
Insurance Member ID: UHC-99218374
Insurance: UnitedHealthcare PPO
Date of Service: 04/24/2026

Reason for Consultation
Progressive exertional chest pain and dyspnea in a patient with known coronary
artery disease and pacemaker. Evaluate for ischemia.

History of Present Illness
64-year-old male with CAD, prior PCI (RCA 2019), dual chamber pacemaker (2022),
diabetes, HTN, HLD, obesity (BMI 37.4). 4 weeks of worsening exertional chest
pressure, 7/10 severity, radiates to jaw, lasts 5-15 minutes, relieved with rest.
Associated dyspnea and diaphoresis. No syncope. Unable to perform treadmill
testing due to fatigue and pacemaker rhythm.

Past Medical History
CAD, PCI RCA 2019; Pacemaker 2022; Type 2 DM; HTN; HLD; Obesity BMI 37.4

ECG
Paced ventricular rhythm at 68 bpm.

Prior Testing
Stress test 2021: nondiagnostic due to pacing.
Echo 2025: LVEF 50-55%.

Assessment
High suspicion for ischemia with multiple risk factors, BMI >35, pacemaker rhythm.

Plan
Order Cardiac PET Myocardial Perfusion Imaging (CPT 78492) for ischemia evaluation.

ICD-10 Codes
I25.118, R07.9, R06.09, Z95.0, E11.9

Ordering MD: Andrew K. Patel, MD
NPI: 1734567890
""",
        expectation="APPROVE: BMI≥35 + pacemaker + nondiagnostic prior + diabetes + CAD = clear PET indication.",
    ),

    StressCase(
        case_id="STRESS-02-spect-strong",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
Patient: Margaret S. Hayes
DOB: 02/14/1957
Sex: Female
Insurance Member ID: UHC-77321456

History of Present Illness
69-year-old female with longstanding coronary artery disease, status post LAD PCI
in 2020, diabetes, hypertension, and chronic left bundle branch block, presents
with 8 weeks of progressive substernal chest pressure on exertion. Symptoms
described as 5/10 pressure-like, 5-10 minutes in duration, relieved with rest
and nitroglycerin SL.

Severe bilateral knee osteoarthritis limits her exercise tolerance; she reports
inability to walk more than half a block before stopping. Prior treadmill stress
test in 2024 was nondiagnostic — only 3 minutes 20 seconds on Bruce protocol,
6.5 METs, 70% MPHR.

ECG
Normal sinus rhythm at 76 bpm. Chronic LBBB. QRS 148 ms. Nonspecific ST-T
abnormalities consistent with conduction delay.

BMI: 27.8 kg/m²

Prior Cardiac Testing
LHC 03/2020: 90% proximal LAD stenosis. Successful PCI with DES.
Echo 03/2025: LVEF 55%. No regional wall motion abnormalities.
Treadmill 06/2024: nondiagnostic, submaximal.

Assessment
Recurrent exertional chest pain with high pretest probability for ischemia.
Baseline LBBB and inability to exercise adequately make exercise treadmill
testing nondiagnostic. Pharmacologic SPECT MPI is the appropriate next step.

Plan
Order Lexiscan SPECT Myocardial Perfusion Imaging (CPT 78452) for evaluation
of inducible ischemia.

ICD-10
I25.118, R07.9, R06.09, R94.31, I44.7, Z95.5, E11.9

Ordering: Lisa Chen, MD
NPI: 1234567890
""",
        expectation="APPROVE: LBBB + inability to exercise + nondiagnostic prior treadmill + known CAD with prior PCI + diabetes. Strong SPECT indication. BMI <35 so SPECT (not PET).",
    ),

    StressCase(
        case_id="STRESS-03-cpt-divergence",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: Thomas R. Jenkins
DOB: 11/03/1965
Sex: Male
Insurance Member ID: UHC-66432198

61-year-old male with hypertension, hyperlipidemia, family history of premature CAD
(brother with MI at age 49), presents with 6 weeks of atypical chest discomfort
on moderate exertion. BMI 28.4. No diabetes. Active and able to exercise without
limitation.

ECG: Normal sinus rhythm. No conduction abnormalities. No prior ischemic ECG changes.

No prior cardiac testing. No prior PCI or CABG.

Assessment
Intermediate pretest probability for CAD. Patient is able to exercise adequately;
ECG is interpretable. Exercise SPECT MPI is the appropriate first-line test.

Plan
Order Lexiscan SPECT MPI (CPT 78452) — wait, patient can exercise, switch to
exercise SPECT MPI (CPT 78452) for evaluation of inducible ischemia.

ICD-10: R07.9, Z82.49, I10

Ordering: David K. Ahmed, MD
NPI: 1856473829
""",
        expectation="DIVERGENCE: request is 78492 (PET), but note clearly orders 78452 (SPECT). cpt_resolver should flag, narrative should match resolved_cpt, hold for physician.",
    ),

    StressCase(
        case_id="STRESS-04-bmi-borderline",
        request_cpt="78452",
        payer="Aetna",
        note="""\
Patient: Sarah M. Williams
DOB: 07/19/1968
Sex: Female
Insurance Member ID: AET-W7765432

58-year-old female. BMI 35.0 (5'5", 210 lb). Hypertension, hyperlipidemia,
prediabetes (A1c 6.3%). Mother with MI at age 64.

3 months of intermittent exertional chest pressure, 4/10 severity, lasting
2-5 minutes, relieved with rest. No syncope. Mild SOB on exertion.

ECG: Normal sinus rhythm. No ischemic changes.

Active, exercises 3x/week on stationary bike, can complete 30 minutes.

No prior cardiac testing.

Assessment
Intermediate pretest probability. BMI is borderline 35.0. Patient is able to
exercise. Exercise SPECT MPI is reasonable first-line.

Plan
Order Lexiscan SPECT MPI (CPT 78452).

ICD-10: R07.9, I10, E66.9

Ordering: Jennifer R. Park, MD
NPI: 1948372615
""",
        expectation="BORDERLINE: BMI exactly 35.0. Either SPECT or PET is defensible. The system should flag the borderline-but-not-blocking; don't pivot the request.",
    ),

    StressCase(
        case_id="STRESS-05-no-member-id",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: James L. O'Brien
DOB: 09/14/1959
Sex: Male
MRN: HSP-552847

67-year-old male with CAD, BMI 36, recent ED visit for chest pain. Pacemaker.
Plan: PET MPI (CPT 78492).

Ordering: Rachel S. Murphy, MD
NPI: 1502938475
""",
        expectation="BLOCK: no Member ID anywhere in note. Should not submit. Flag missing essential.",
    ),

    StressCase(
        case_id="STRESS-06-cerner-terse",
        request_cpt="78452",
        payer="Anthem",
        note="""\
Pt: Christopher D. Wells
DOB: 12/22/1961
M / 64yo
Member#: ANT-44582019

CC: chest pain x 4wk
HPI: exertional CP 6/10, radiates L arm, relief w/ rest. Hx CAD s/p LAD stent
2018. T2DM. HTN. HLD.
ROS: + CP, + DOE. - syncope. - palpitations.
PMHx: CAD, T2DM, HTN, HLD, knee OA bil (severe), s/p R knee replacement 2023
Meds: ASA 81, atorva 80, metop 50, losartan 50, metformin 1000 BID
Exam: HR 72 reg. BP 142/86. Lungs CTA. Heart RRR no MRG. No JVD. No edema.
ECG: NSR. Chronic LBBB QRS 152.
BMI: 31.2

Prior tests:
- LHC 2018: 95% LAD → DES
- Treadmill 2023: nondx, submaximal d/t knee pain. 5.2 METs.

A/P:
1. Recurrent angina, hi pretest prob ischemia
2. Cannot exercise (knee OA bil, s/p TKA, prior submax stress)
3. LBBB → ECG nondx
4. Order: pharm SPECT MPI 78452

Sign: Daniel J. Kovacs MD NPI 1029384756
""",
        expectation="APPROVE: terse Cerner-style note. Has all essentials (Member#, NPI), all clinical signals (LBBB + inability to exercise + nondiagnostic + CAD). Tests extraction robustness on abbreviated format.",
    ),

    StressCase(
        case_id="STRESS-07-multi-cpt-mention",
        request_cpt="78452",
        payer="Medicare",
        note="""\
Patient: Eleanor M. Bratton
DOB: 04/30/1955
Sex: Female
Medicare MBI: 1XB7-Y58-MK29

71-year-old female with longstanding CAD, prior LAD PCI 2019, hypertension,
CKD stage 3a, chronic atrial fibrillation on warfarin. Presents with 3 months
of progressive exertional chest pressure and dyspnea.

Severe bilateral hip osteoarthritis with planned right total hip arthroplasty
in 2 weeks. Cannot complete exercise treadmill due to hip pain. BMI 33.6.

ECG: Atrial fibrillation, controlled rate at 78 bpm. No acute ST changes.

Discussion with patient regarding imaging choice:
"We discussed exercise treadmill (not feasible given hip), exercise SPECT
(CPT 78452) (also not feasible — same exercise limitation), pharmacologic
SPECT (CPT 78452), and Cardiac PET (CPT 78492). Given her CKD (limits
gadolinium-based testing), atrial fibrillation, and inability to exercise,
pharmacologic SPECT MPI is the appropriate test. Note: PET (78492) would
also be acceptable for diagnostic accuracy but SPECT is the patient's choice."

Plan: Pharmacologic SPECT MPI (CPT 78452).

ICD-10: I25.118, R07.9, I48.91, N18.30, M16.0

Ordering: Patricia G. Reilly, MD
NPI: 1763829014
""",
        expectation="APPROVE: note mentions both 78452 and 78492 in clinical discussion, but the ordered code is clearly 78452. cpt_resolver must NOT get confused.",
    ),

    StressCase(
        case_id="STRESS-08-insufficient",
        request_cpt="78492",
        payer="UnitedHealthcare",
        note="""\
Patient: Mark D. Singh
DOB: 06/14/1970
Member ID: UHC-55892014

55yo M with chest pain. PMH: HTN. Order PET stress.

Ordering: Anita L. Roberts, MD
NPI: 1284756093
""",
        expectation="HOLD: insufficient clinical documentation. No diabetes, no CAD, no nondiagnostic prior, no inability to exercise, no BMI, no ECG abnormality. Reasonable to hold for more documentation, not auto-block.",
    ),

    StressCase(
        case_id="STRESS-09-frequency-violation",
        request_cpt="78452",
        payer="UnitedHealthcare",
        note="""\
Patient: Linda K. Anderson
DOB: 03/08/1958
Member ID: UHC-99124467

68-year-old female with hypertension, hyperlipidemia, mild dyspnea on exertion.
Recent SPECT MPI in February 2026 (3 months ago) — NORMAL. No reversible defects.
LVEF 60%. No new symptoms since prior testing.

BMI: 29.4. ECG: NSR, no acute changes.

Patient asks for repeat imaging because she "wants to be sure." No new clinical
indication.

Plan: Repeat SPECT MPI (CPT 78452).

ICD-10: R07.9, I10

Ordering: Maria T. Hernandez, MD
NPI: 1837491562
""",
        expectation="HOLD/DENY: prior SPECT 3 months ago, no new clinical indication, normal prior result. Most payer policies require 12-month interval unless new symptoms or events. Frequency-violation flag should fire.",
    ),

    StressCase(
        case_id="STRESS-10-tavr",
        request_cpt="33361",
        payer="Medicare",
        note="""\
Patient: Walter J. Holloway
DOB: 11/22/1942
Sex: Male
Medicare MBI: 8KK4-T26-PR91

83-year-old male with severe symptomatic aortic stenosis. NYHA Class III dyspnea
and exertional syncope x 2 episodes in past 3 months.

PMH: Severe aortic stenosis (AVA 0.7 cm², peak gradient 65 mmHg, mean 42 mmHg
on most recent echo 04/2026). HTN. Stage 3 CKD. Mild COPD. Frailty score 6/9.

STS-PROM 7.2% (high-risk operative mortality). Heart Team review 04/15/2026:
high surgical risk, suitable TAVR candidate. Annular sizing favorable for
transfemoral approach.

Prior cardiac testing
LHC 04/2026: minimal CAD (40% mid LAD, otherwise unobstructive). Coronary
anatomy compatible with TAVR.
Echo 04/2026: severe AS as above. LVEF 55%. Mild LV hypertrophy.

Plan
Transcatheter Aortic Valve Replacement (TAVR), CPT 33361. Procedure scheduled
05/22/2026 with Dr. Kim and Dr. Petros (Heart Team).

ICD-10: I35.0, I50.9 (functional class III), Z91.81

Ordering: Heart Team — Stephen Kim, MD (interventional cardiologist)
NPI: 1192837465
""",
        expectation="APPROVE: severe symptomatic AS, NYHA III, Heart Team review, high surgical risk, all TAVR criteria met. Tests whether the system handles a non-MPI procedure (33361 has totally different criterion taxonomy).",
    ),
]


def case_by_id(case_id: str) -> StressCase:
    for c in CASES:
        if c.case_id == case_id:
            return c
    raise KeyError(case_id)


def all_case_ids() -> list[str]:
    return [c.case_id for c in CASES]
