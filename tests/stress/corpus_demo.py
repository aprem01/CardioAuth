"""The "treadmill 3 years ago" killer-feature demo.

A SPECT case where the CURRENT encounter note is intentionally thin
(does not document the prior nondiagnostic treadmill, does not
mention LBBB, does not quantify exercise limitation). The patient's
LONGITUDINAL CORPUS contains those facts in historical documents
(stress test report from 2023, prior ECG narrative from 2021,
prior encounter note from 2024). The pipeline should retrieve them,
the unified call should cite them, and the resulting submission
package should include facts that the current note alone wouldn't
have.

This is what makes the product real: the AI is reading the WHOLE
chart, not just what's in front of the physician today.
"""

from __future__ import annotations

from cardioauth.patient_corpus import CorpusDocument, PatientCorpus


# Thin current note — does NOT mention LBBB, nondiagnostic prior, or
# quantified exercise limitation. A reasoner working off only this
# note would have to mark several criteria not_met or not_evaluated.
CURRENT_NOTE = """\
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
"""


# Historical documents — facts buried here that strengthen the package.
# The pipeline should find them via BM25 retrieval over the criteria
# keywords and surface them to the LLM.
_PRIOR_TREADMILL = """\
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
"""

_PRIOR_ECG = """\
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
"""

_PRIOR_ENCOUNTER_2024 = """\
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
"""

_PRIOR_LHC_2019 = """\
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
"""


def build_demo_corpus() -> PatientCorpus:
    """The killer-feature demo: thin current note + rich history."""
    return PatientCorpus(
        patient_id="DEMO-WHITFORD",
        documents=[
            CorpusDocument(
                doc_id="current",
                doc_type="current_note",
                date="2026-05-06",
                title="Current encounter note",
                text=CURRENT_NOTE,
            ),
            CorpusDocument(
                doc_id="stress-2023-06-18",
                doc_type="stress_test",
                date="2023-06-18",
                title="Exercise treadmill stress test",
                text=_PRIOR_TREADMILL,
                source="Cardiology Stress Lab",
            ),
            CorpusDocument(
                doc_id="ecg-2021-11-03",
                doc_type="ecg_report",
                date="2021-11-03",
                title="12-lead ECG narrative",
                text=_PRIOR_ECG,
                source="Cardiology Department",
            ),
            CorpusDocument(
                doc_id="encounter-2024-09-22",
                doc_type="prior_encounter",
                date="2024-09-22",
                title="Cardiology follow-up note",
                text=_PRIOR_ENCOUNTER_2024,
                source="Cardiology Outpatient",
            ),
            CorpusDocument(
                doc_id="lhc-2019-10-15",
                doc_type="cath_report",
                date="2019-10-15",
                title="Left heart catheterization",
                text=_PRIOR_LHC_2019,
                source="Cath Lab",
            ),
        ],
    )


# For the live demo, also export the current note alone so we can run
# the SAME case through the lean pipeline WITHOUT the corpus and
# compare the two outputs side by side.
DEMO_REQUEST = {
    "patient_id": "DEMO-WHITFORD",
    "procedure_code": "78452",
    "payer_name": "UnitedHealthcare",
    "raw_note": CURRENT_NOTE,
}
