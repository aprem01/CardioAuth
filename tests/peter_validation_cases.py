"""Peter's 5 validation cases, encoded as regression tests.

Every time the reasoning pipeline changes, run this to verify:
  - DOC-001 is no longer missed (was 5/5 missed before fix)
  - EX-001 is correctly identified when exercise limitation documented
  - NDX-001 catches attenuation artifact / false positive
  - Approval scores are >= 65% for these approved cases

Run with:
  python3 tests/peter_validation_cases.py
  python3 tests/peter_validation_cases.py --live   # hit real Claude API
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Add parent to path so we can import cardioauth
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class TestCase:
    """One of Peter's 5 validation cases with expected outcomes."""
    case_id: str
    payer: str
    cpt_code: str
    procedure_name: str
    patient_name: str
    age: int
    sex: str
    diagnosis_codes: list[str]
    comorbidities: list[str]
    relevant_labs: list[dict]
    relevant_imaging: list[dict]
    relevant_medications: list[dict]
    prior_treatments: list[str]
    ecg_findings: str
    ejection_fraction: str
    additional_notes: str

    # Expected outcomes
    expected_approval: bool = True
    expect_met: list[str] = None          # Criteria that MUST be marked met
    expect_not_missed: list[str] = None   # Criteria that MUST NOT be missed


# ────────────────────────────────────────────────────────────────────────
# Peter's 5 cases (reconstructed from Excel matrix notes)
# All 5 were actually approved by the payer
# ────────────────────────────────────────────────────────────────────────

PETER_CASES: list[TestCase] = [
    TestCase(
        case_id="C1",
        payer="UnitedHealthcare",
        cpt_code="78492",
        procedure_name="Cardiac Stress PET",
        patient_name="Case 1 Patient",
        age=68,
        sex="M",
        diagnosis_codes=["I25.10", "R07.89", "E11.65", "I10", "E66.9"],
        comorbidities=[
            "Coronary artery disease",
            "Type 2 diabetes mellitus",
            "Essential hypertension",
            "Obesity (BMI 38)",
            "Exertional dyspnea",
        ],
        relevant_labs=[
            {"name": "HbA1c", "value": "7.8", "unit": "%", "date": "2026-01-15"},
            {"name": "BNP", "value": "245", "unit": "pg/mL", "date": "2026-01-15"},
            {"name": "LDL-C", "value": "118", "unit": "mg/dL", "date": "2026-01-15"},
        ],
        relevant_imaging=[
            {"type": "Exercise Treadmill Test", "date": "2025-11-10",
             "result_summary": "Unable to complete — achieved 68% MPHR, equivocal non-diagnostic result"},
        ],
        relevant_medications=[
            {"name": "Metoprolol", "dose": "50mg BID", "indication": "CAD"},
            {"name": "Atorvastatin", "dose": "80mg daily", "indication": "Dyslipidemia"},
            {"name": "Aspirin", "dose": "81mg daily", "indication": "CAD"},
            {"name": "Lisinopril", "dose": "20mg daily", "indication": "HTN"},
        ],
        prior_treatments=["6 weeks optimal medical therapy"],
        ecg_findings="Normal sinus rhythm, no acute changes",
        ejection_fraction="55%",
        additional_notes=(
            "CARDIOLOGY OFFICE NOTE\n"
            "67-year-old male with known CAD presents for PA evaluation.\n"
            "Patient reports CCS Class III exertional angina, worsening over 3 months "
            "despite optimal medical therapy. Unable to do TST due to dyspnea and obesity. "
            "Prior exercise treadmill test was non-diagnostic, achieving only 68% of maximum "
            "predicted heart rate. Clinical indication for pharmacologic stress PET to evaluate "
            "ischemic burden and guide revascularization strategy."
        ),
        expect_met=["EX-001", "NDX-002", "BMI-001", "DOC-001", "SX-004", "SX-003"],
        expect_not_missed=["DOC-001", "EX-001"],
    ),

    TestCase(
        case_id="C2",
        payer="BCBS",
        cpt_code="78492",
        procedure_name="Cardiac Stress PET",
        patient_name="Case 2 Patient",
        age=72,
        sex="F",
        diagnosis_codes=["I25.10", "I20.0", "E11.9", "I10"],
        comorbidities=[
            "CAD with prior PCI",
            "Type 2 diabetes",
            "Hypertension",
            "Dyslipidemia",
            "BMI 36",
        ],
        relevant_labs=[
            {"name": "HbA1c", "value": "7.4", "unit": "%", "date": "2026-02-01"},
            {"name": "BNP", "value": "180", "unit": "pg/mL", "date": "2026-02-01"},
        ],
        relevant_imaging=[
            {"type": "SPECT Stress Test", "date": "2025-10-15",
             "result_summary": "Attenuation artifact noted in inferior wall — likely false positive, non-diagnostic"},
        ],
        relevant_medications=[
            {"name": "Metoprolol", "dose": "100mg daily", "indication": "CAD"},
            {"name": "Clopidogrel", "dose": "75mg daily", "indication": "Post-PCI"},
            {"name": "Rosuvastatin", "dose": "40mg daily", "indication": "Dyslipidemia"},
        ],
        prior_treatments=["PCI to LAD with DES (2022)", "Optimal medical therapy"],
        ecg_findings="Normal sinus rhythm",
        ejection_fraction="50-55%",
        additional_notes=(
            "CONSULTATION NOTE\n"
            "72F with CAD s/p PCI, referred for cardiac stress PET. Prior SPECT showed "
            "attenuation artifact, likely false positive, rendering it non-diagnostic. "
            "Patient reports recurrent atypical chest pain (CCS Class II). BMI 36 favors "
            "PET over SPECT. Office note and H&P documented."
        ),
        expect_met=["NDX-001", "BMI-001", "DOC-001", "SX-004", "SX-001"],
        expect_not_missed=["DOC-001", "NDX-001"],
    ),

    TestCase(
        case_id="C3",
        payer="BCBS",
        cpt_code="78452",
        procedure_name="Lexiscan SPECT",
        patient_name="Case 3 Patient",
        age=65,
        sex="M",
        diagnosis_codes=["I25.10", "R07.89", "I48.91"],
        comorbidities=[
            "CAD",
            "Paroxysmal atrial fibrillation",
            "Severe osteoarthritis",
            "COPD",
        ],
        relevant_labs=[
            {"name": "Troponin", "value": "0.02", "unit": "ng/mL", "date": "2026-02-10"},
            {"name": "Creatinine", "value": "1.1", "unit": "mg/dL", "date": "2026-02-10"},
        ],
        relevant_imaging=[
            {"type": "TTE", "date": "2025-12-01",
             "result_summary": "LVEF 55%, normal wall motion, technically limited study"},
        ],
        relevant_medications=[
            {"name": "Eliquis", "dose": "5mg BID", "indication": "AFib"},
            {"name": "Metoprolol", "dose": "50mg BID", "indication": "Rate control"},
            {"name": "Atorvastatin", "dose": "40mg daily", "indication": "CAD"},
        ],
        prior_treatments=["Physical therapy for OA"],
        ecg_findings="Paced rhythm — ventricular pacing",
        ejection_fraction="55%",
        additional_notes=(
            "OFFICE VISIT NOTE\n"
            "65M with CAD and severe osteoarthritis of bilateral knees preventing "
            "ambulation. Unable to exercise. Also has baseline paced rhythm on ECG. "
            "Referred for pharmacologic stress imaging. CCS Class II angina. Current on "
            "GDMT for 8 weeks with persistent symptoms."
        ),
        expect_met=["EX-001", "ECG-002", "DOC-001", "MED-001"],
        expect_not_missed=["DOC-001", "EX-001", "ECG-002"],
    ),

    TestCase(
        case_id="C4",
        payer="BCBS",
        cpt_code="78492",
        procedure_name="Cardiac Stress PET",
        patient_name="Case 4 Patient",
        age=70,
        sex="F",
        diagnosis_codes=["I25.10", "I50.9", "E11.65"],
        comorbidities=[
            "CAD",
            "Heart failure with preserved EF",
            "Type 2 diabetes",
            "Obesity class III (BMI 42)",
            "Breast attenuation on prior imaging",
        ],
        relevant_labs=[
            {"name": "BNP", "value": "450", "unit": "pg/mL", "date": "2026-02-20"},
            {"name": "HbA1c", "value": "8.2", "unit": "%", "date": "2026-02-20"},
        ],
        relevant_imaging=[
            {"type": "SPECT MPI", "date": "2025-09-10",
             "result_summary": "Breast attenuation artifact, non-diagnostic for ischemia"},
            {"type": "TTE", "date": "2026-01-05",
             "result_summary": "LVEF 55%, diastolic dysfunction, technically limited due to body habitus"},
        ],
        relevant_medications=[
            {"name": "Metformin", "dose": "1000mg BID", "indication": "T2DM"},
            {"name": "Lisinopril", "dose": "10mg daily", "indication": "HTN"},
            {"name": "Furosemide", "dose": "20mg daily", "indication": "HFpEF"},
            {"name": "Carvedilol", "dose": "12.5mg BID", "indication": "HFpEF"},
        ],
        prior_treatments=["Cardiac rehabilitation", "GDMT for HFpEF"],
        ecg_findings="Normal sinus rhythm, LVH by voltage",
        ejection_fraction="55%",
        additional_notes=(
            "CARDIOLOGY CONSULTATION NOTE\n"
            "70F morbidly obese (BMI 42) with CAD and HFpEF. Prior SPECT limited by "
            "breast attenuation artifact and was non-diagnostic. Prior echo technically "
            "limited due to body habitus. PET strongly favored given BMI. NYHA Class II "
            "functional capacity. Continuing CCS Class II angina despite maximal medical "
            "therapy x 12 weeks."
        ),
        expect_met=["BMI-001", "NDX-001", "NDX-004", "DOC-001", "SX-004", "MED-001", "MED-002"],
        expect_not_missed=["DOC-001", "BMI-001", "NDX-001"],
    ),

    TestCase(
        case_id="C5",
        payer="UnitedHealthcare",
        cpt_code="78492",
        procedure_name="Cardiac Stress PET",
        patient_name="Case 5 Patient",
        age=62,
        sex="M",
        diagnosis_codes=["I25.10", "I50.22", "E11.9"],
        comorbidities=[
            "CAD with multivessel disease",
            "HFrEF (LVEF 35%)",
            "Type 2 diabetes",
            "Left bundle branch block",
        ],
        relevant_labs=[
            {"name": "BNP", "value": "680", "unit": "pg/mL", "date": "2026-03-01"},
            {"name": "Troponin", "value": "0.04", "unit": "ng/mL", "date": "2026-03-01"},
        ],
        relevant_imaging=[
            {"type": "TTE", "date": "2026-01-20",
             "result_summary": "LVEF 35%, global hypokinesis"},
        ],
        relevant_medications=[
            {"name": "Entresto", "dose": "49/51mg BID", "indication": "HFrEF"},
            {"name": "Metoprolol succinate", "dose": "100mg daily", "indication": "HFrEF"},
            {"name": "Empagliflozin", "dose": "10mg daily", "indication": "HFrEF + T2DM"},
            {"name": "Spironolactone", "dose": "25mg daily", "indication": "HFrEF"},
        ],
        prior_treatments=["PCI to RCA (2020)", "GDMT for HFrEF x 6 months"],
        ecg_findings="Left bundle branch block (LBBB), normal sinus rhythm",
        ejection_fraction="35%",
        additional_notes=(
            "CARDIOLOGY OFFICE NOTE — H&P\n"
            "62M with ischemic cardiomyopathy (LVEF 35%), LBBB on baseline ECG. NYHA "
            "Class III symptoms despite maximal GDMT x 6 months. LBBB precludes standard "
            "stress ECG interpretation. Cardiac stress PET requested to evaluate viable "
            "myocardium and ischemic burden."
        ),
        expect_met=["LVEF-002", "ECG-001", "DOC-001", "SX-004", "MED-001", "MED-002"],
        expect_not_missed=["DOC-001", "ECG-001", "LVEF-002"],
    ),
]


def run_test(case: TestCase, live: bool = False) -> dict:
    """Run a single test case through the unified reasoner pipeline."""
    from cardioauth.case_context import CaseContext
    from cardioauth.agents.relationship_extractor import extract_relationships

    # Build CaseContext
    ctx = CaseContext(
        case_id=f"{case.case_id}-{case.cpt_code}",
        procedure_code=case.cpt_code,
        procedure_name=case.procedure_name,
        payer_name=case.payer,
        user_id="test",
        chart_data={
            "patient_id": f"TEST-{case.case_id}",
            "patient_name": case.patient_name,
            "age": case.age,
            "sex": case.sex,
            "diagnosis_codes": case.diagnosis_codes,
            "comorbidities": case.comorbidities,
            "relevant_labs": case.relevant_labs,
            "relevant_imaging": case.relevant_imaging,
            "relevant_medications": case.relevant_medications,
            "prior_treatments": case.prior_treatments,
            "ecg_findings": case.ecg_findings,
            "ejection_fraction": case.ejection_fraction,
            "additional_notes": case.additional_notes,
        },
    )
    ctx.build_clinical_narrative()

    # Extract relationships (rule-based, no API call)
    extract_relationships(ctx)

    result = {
        "case_id": case.case_id,
        "narrative_preview": ctx.raw_note[:200] + "...",
        "relationships_found": [
            {"supports": r.supports_criterion, "conclusion": r.conclusion[:80]}
            for r in ctx.relationships
        ],
        "expected_met": case.expect_met or [],
        "expected_not_missed": case.expect_not_missed or [],
    }

    # Check that each expected relationship was detected
    detected_criteria = {r.supports_criterion for r in ctx.relationships}
    expected_criteria = set(case.expect_not_missed or [])
    result["not_missed_detected"] = sorted(expected_criteria & detected_criteria)
    result["not_missed_still_missing"] = sorted(expected_criteria - detected_criteria)

    if live:
        from cardioauth.config import Config
        from cardioauth.agents.unified_reasoner import reason_with_unified_agent
        config = Config()
        if config.anthropic_api_key:
            reason_with_unified_agent(ctx, config)
            result["approval_score"] = ctx.approval_score
            result["approval_label"] = ctx.approval_label
            result["criterion_matches"] = [
                {"code": m.get("code"), "status": m.get("status"), "quote": m.get("evidence_quote", "")[:80]}
                for m in ctx.criterion_matches
            ]

    return result


def main():
    live = "--live" in sys.argv
    print(f"Running Peter's 5 validation cases (live={live})\n")

    results = []
    for case in PETER_CASES:
        print(f"=== {case.case_id}: {case.procedure_name} / {case.payer} ===")
        result = run_test(case, live=live)

        print(f"  Relationships found: {len(result['relationships_found'])}")
        for r in result["relationships_found"][:5]:
            print(f"    - [{r['supports']}] {r['conclusion']}")

        if result["not_missed_still_missing"]:
            print(f"  ❌ STILL MISSING: {result['not_missed_still_missing']}")
        else:
            print(f"  ✓ All expected criteria detected")

        if live and "approval_score" in result:
            print(f"  Score: {result['approval_score']:.2f} ({result['approval_label']})")
            matches_by_status = {}
            for m in result["criterion_matches"]:
                s = m.get("status", "?")
                matches_by_status.setdefault(s, 0)
                matches_by_status[s] += 1
            print(f"  Status breakdown: {matches_by_status}")

        results.append(result)
        print()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_expected = sum(len(r["expected_not_missed"]) for r in results)
    all_detected = sum(len(r["not_missed_detected"]) for r in results)
    print(f"Critical criteria detected: {all_detected}/{all_expected} "
          f"({100 * all_detected / all_expected:.0f}%)")

    if live:
        scores = [r["approval_score"] for r in results if "approval_score" in r]
        if scores:
            avg = sum(scores) / len(scores)
            print(f"Avg approval score: {avg:.2f} (target: ≥0.65 for all-approved cases)")
            passing = sum(1 for s in scores if s >= 0.65)
            print(f"Cases scoring ≥0.65: {passing}/{len(scores)}")


if __name__ == "__main__":
    main()
