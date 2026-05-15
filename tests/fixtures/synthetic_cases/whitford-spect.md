---
patient_id: DEMO-WHITFORD
patient_name: Eleanor R. Whitford
dob: 1957-04-12
sex: female
member_id: UHC-66432198
payer: UnitedHealthcare
ordering_physician: Yuki R. Tanaka, MD
ordering_npi: 1029384765
encounter_date: 2026-05-06
procedure_code: 78452
procedure_name: Cardiac SPECT MPI (pharmacologic stress)
diagnoses:
  - code: I25.10
    text: Atherosclerotic heart disease of native coronary artery
  - code: I44.7
    text: Left bundle-branch block, unspecified
  - code: E11.9
    text: Type 2 diabetes mellitus without complications
  - code: M17.0
    text: Bilateral primary osteoarthritis of knee
---

# Current Encounter Note
type: progress_note
date: 2026-05-06
author: Yuki R. Tanaka, MD

Patient: Eleanor R. Whitford
DOB: 04/12/1957
Sex: Female
Insurance Member ID: UHC-66432198

Date of Service: 05/06/2026

History of Present Illness
68-year-old female with known coronary artery disease and diabetes
presents with 6 weeks of recurrent exertional chest pressure. Symptoms
are pressure-like, occur with light walking, relieved with rest.

She has chronic knee pain and reports she can't walk far before
stopping.

Past Medical History: CAD, DM, HTN, HLD, knee OA.

Plan: SPECT MPI (CPT 78452) for evaluation of ischemia.

Ordering: Yuki R. Tanaka, MD
NPI: 1029384765

# Exercise Treadmill Stress Test
type: stress_test
date: 2023-06-18
author: David L. Carrasco, MD, FACC
format: pdf

Cardiology Department Stress Test Report
Date of Study: 06/18/2023
Patient: Eleanor R. Whitford
DOB: 04/12/1957
Referring: Yuki R. Tanaka, MD

Indication: Recurrent exertional chest pain. Evaluate for inducible ischemia.

Test: Exercise treadmill stress test, Bruce protocol.

Result: SUBMAXIMAL. Patient exercised 4 minutes 12 seconds, achieving
6.2 METs and 76% of maximum predicted heart rate. Test was stopped
early due to bilateral knee pain and dyspnea. Baseline ECG showed
chronic left bundle branch block (LBBB) with QRS duration 154 ms,
making the ECG portion nondiagnostic for ischemia. No sustained
arrhythmias. No chest pain during exercise.

Impression: NONDIAGNOSTIC stress test due to (1) submaximal exercise
(78% MPHR was target; only 76% achieved), and (2) baseline LBBB
limiting ECG interpretation for ischemia.

Recommendation: Pharmacologic stress imaging would be the appropriate
next test if symptoms recur or worsen.

Electronically signed by: David L. Carrasco, MD, FACC

# 12-Lead ECG Interpretation
type: ecg_report
date: 2021-11-03
author: Patricia M. O'Neill, MD
format: pdf

Cardiology Department — ECG Report
Date: 11/03/2021
Patient: Eleanor R. Whitford
DOB: 04/12/1957

12-Lead ECG Interpretation

Rate: 78 bpm
Rhythm: Normal sinus rhythm
PR interval: 168 ms
QRS duration: 152 ms (wide)
QT/QTc: 410 / 467 ms

Findings:
- Sinus rhythm at 78 bpm
- Chronic left bundle branch block (LBBB)
- Nonspecific ST-T abnormalities secondary to LBBB conduction delay
- No acute ischemic changes
- No prior comparison available

Impression: Chronic LBBB. Nondiagnostic for ischemia on baseline ECG
given conduction abnormality.

Electronically signed by: Patricia M. O'Neill, MD

# Cardiology Follow-up Note
type: prior_encounter
date: 2024-09-22
author: Yuki R. Tanaka, MD

Cardiology Follow-up Note
Date of Service: 09/22/2024
Patient: Eleanor R. Whitford
DOB: 04/12/1957
Provider: Yuki R. Tanaka, MD

History of Present Illness
67yo F with CAD s/p PCI to RCA (2019), T2DM, HTN, HLD, and chronic
LBBB returns for routine cardiology follow-up.

She reports stable mild dyspnea with exertion. Her bilateral knee
osteoarthritis continues to limit her ambulation; she can walk approximately
half a block before needing to stop because of knee pain. She does not
exercise routinely. Status post bilateral knee arthroscopy 2018;
discussing potential right total knee arthroplasty.

ECG today: Normal sinus rhythm at 74. Chronic LBBB unchanged from
prior, QRS 154 ms.

Assessment:
1. Stable angina (CCS Class II) — no escalation needed
2. Chronic LBBB — baseline finding, makes exercise ECG nondiagnostic
3. Severe bilateral knee OA — limits exercise tolerance significantly
4. T2DM — A1c 7.1
5. HLD — LDL 78 on atorvastatin 80

Plan: Continue current meds. Return in 6 months or sooner for any
change in symptoms. Discussed that future stress testing will need
to be pharmacologic given inability to exercise.

Electronically signed by: Yuki R. Tanaka, MD

# Left Heart Catheterization
type: cath_report
date: 2019-10-15
author: Hector M. Padilla, MD, FSCAI
format: pdf

Left Heart Catheterization Report
Date: 10/15/2019
Patient: Eleanor R. Whitford

Indication: Abnormal exercise stress test with anterior wall ischemia.

Findings:
- Left main: No significant disease
- LAD: 30% mid LAD stenosis, nonobstructive
- LCx: 40% mid-vessel stenosis
- RCA: 85% proximal RCA stenosis — culprit lesion

Intervention:
Successful PCI to proximal RCA with 3.0 x 18 mm Resolute Onyx
drug-eluting stent. Final TIMI-3 flow. No complications.

LVEDP: 14 mmHg
LVEF on ventriculogram: 55%

Electronically signed by: Hector M. Padilla, MD, FSCAI
