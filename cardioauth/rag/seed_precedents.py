"""Seed Pinecone case-precedents with canonical cardiology PA approvals.

Run this once to bootstrap long-term memory. Without seeded precedents,
the first N real cases have nothing to retrieve against — the seed cases
give the UnifiedReasoner day-one precedents to anchor approval scoring.

Each seed case:
  - Represents a textbook "approved" PA scenario
  - Covers a distinct clinical chain (exercise limitation, attenuation,
    LBBB, failed medical therapy, severe AS, failed antiarrhythmic, etc.)
  - Gets tagged outcome="approved" in metadata
  - Gets upserted to namespace `case-precedents` keyed by a SEED-*** id

Run:
  python3 -m cardioauth.rag.seed_precedents
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cardioauth.case_context import CaseContext
from cardioauth.agents.precedent_retriever import store_case_as_precedent, _get_pinecone

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


SEED_CASES: list[dict] = [

    # ── CPT 78492 — Cardiac Stress PET ──
    {
        "case_id": "SEED-PET-001",
        "cpt_code": "78492",
        "procedure_name": "Cardiac Stress PET",
        "payer": "UnitedHealthcare",
        "summary": (
            "65M with CAD, obesity (BMI 38), and exertional dyspnea. Unable to complete "
            "exercise treadmill test (68% MPHR, non-diagnostic). Prior SPECT showed "
            "attenuation artifact. PET approved given BMI and prior non-diagnostic imaging. "
            "CCS Class III angina documented. Clinical office note attached."
        ),
        "approval_score": 0.88,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["EX-001", "BMI-001", "NDX-001", "NDX-002", "DOC-001", "SX-004", "SX-003"],
    },
    {
        "case_id": "SEED-PET-002",
        "cpt_code": "78492",
        "procedure_name": "Cardiac Stress PET",
        "payer": "Aetna",
        "summary": (
            "72F with ischemic cardiomyopathy, LVEF 30%, LBBB on baseline ECG. "
            "LBBB precludes standard stress ECG interpretation. NYHA Class III "
            "symptoms despite optimal GDMT for 6 months. Cardiology consultation "
            "note documents indication. Approved for viability assessment."
        ),
        "approval_score": 0.92,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["ECG-001", "LVEF-002", "MED-001", "DOC-001", "SX-004"],
    },
    {
        "case_id": "SEED-PET-003",
        "cpt_code": "78492",
        "procedure_name": "Cardiac Stress PET",
        "payer": "BCBS",
        "summary": (
            "68M morbidly obese (BMI 42) with CAD s/p PCI. Prior SPECT limited by "
            "breast attenuation — non-diagnostic. CCS Class II angina on maximal "
            "medical therapy x 8 weeks. Failed medical therapy documented. PET over "
            "SPECT justified. Consultation note provided."
        ),
        "approval_score": 0.85,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["BMI-001", "NDX-001", "NDX-004", "MED-001", "DOC-001", "SX-003"],
    },
    {
        "case_id": "SEED-PET-004",
        "cpt_code": "78492",
        "procedure_name": "Cardiac Stress PET",
        "payer": "UnitedHealthcare",
        "summary": (
            "58F with HFpEF, recurrent typical angina, BMI 36. Paced rhythm on ECG "
            "(biventricular pacing). Standard stress ECG uninterpretable. NYHA Class II, "
            "CCS Class II. On optimal GDMT for HFpEF. Office note with history, exam, "
            "assessment/plan attached."
        ),
        "approval_score": 0.89,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["ECG-002", "BMI-001", "MED-001", "DOC-001", "SX-004", "SX-003"],
    },
    {
        "case_id": "SEED-PET-005",
        "cpt_code": "78492",
        "procedure_name": "Cardiac Stress PET",
        "payer": "Aetna",
        "summary": (
            "71M with multivessel CAD, prior CABG, worsening dyspnea on exertion. "
            "Prior ETT equivocal with submaximal HR (72% MPHR). Unable to exercise "
            "adequately due to severe bilateral osteoarthritis. Consultation note "
            "and prior stress report attached. Approved for pharmacologic stress PET."
        ),
        "approval_score": 0.87,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["EX-001", "NDX-002", "NDX-003", "DOC-001", "SX-003"],
    },

    # ── CPT 78452 — Lexiscan SPECT (Pharmacologic MPI) ──
    {
        "case_id": "SEED-SPECT-001",
        "cpt_code": "78452",
        "procedure_name": "Lexiscan SPECT",
        "payer": "BCBS",
        "summary": (
            "64M with CAD and severe osteoarthritis of bilateral knees preventing "
            "ambulation. Unable to exercise. Paced rhythm on ECG. CCS Class II angina. "
            "On GDMT for 8 weeks. Consultation note attached. Approved for pharmacologic "
            "stress MPI."
        ),
        "approval_score": 0.86,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["EX-001", "ECG-002", "DOC-001", "MED-001", "SX-003"],
    },
    {
        "case_id": "SEED-SPECT-002",
        "cpt_code": "78452",
        "procedure_name": "Lexiscan SPECT",
        "payer": "UnitedHealthcare",
        "summary": (
            "70F with moderate COPD (FEV1 50%) and CAD. Severe dyspnea on minimal "
            "exertion. Unable to complete exercise stress test. NYHA Class III. "
            "Office note documents indication for pharmacologic imaging."
        ),
        "approval_score": 0.84,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["EX-001", "DOC-001", "SX-004"],
    },
    {
        "case_id": "SEED-SPECT-003",
        "cpt_code": "78452",
        "procedure_name": "Lexiscan SPECT",
        "payer": "Aetna",
        "summary": (
            "68M with LBBB on baseline ECG, history of CAD, stable CCS Class II "
            "angina. Standard stress ECG uninterpretable due to LBBB. Consultation "
            "note attached. Approved for pharmacologic nuclear imaging."
        ),
        "approval_score": 0.88,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["ECG-001", "DOC-001", "SX-003"],
    },

    # ── CPT 93458 — Left Heart Catheterization ──
    {
        "case_id": "SEED-CATH-001",
        "cpt_code": "93458",
        "procedure_name": "Left Heart Catheterization",
        "payer": "UnitedHealthcare",
        "summary": (
            "67M with known CAD, worsening CCS Class III angina despite 6 weeks "
            "optimal medical therapy (ASA, statin, beta-blocker, ACEi). LVEF 40%. "
            "Stress echo showed reversible perfusion defect. Failed medical therapy. "
            "Cath indicated to define anatomy for revascularization."
        ),
        "approval_score": 0.91,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["MED-001", "MED-002", "LVEF-001", "LVEF-002", "DOC-001", "SX-003", "SX-004"],
    },
    {
        "case_id": "SEED-CATH-002",
        "cpt_code": "93458",
        "procedure_name": "Left Heart Catheterization",
        "payer": "BCBS",
        "summary": (
            "72F with NSTEMI, troponin 2.4 ng/mL, recurrent chest pain. High-risk ACS "
            "features. LVEF 45%. Guideline Class I indication for invasive evaluation. "
            "Office note with full H&P attached."
        ),
        "approval_score": 0.95,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["SX-003", "LVEF-001", "DOC-001", "RISK-002"],
    },

    # ── CPT 33361 — TAVR ──
    {
        "case_id": "SEED-TAVR-001",
        "cpt_code": "33361",
        "procedure_name": "Transcatheter Aortic Valve Replacement",
        "payer": "UnitedHealthcare",
        "summary": (
            "84M with severe symptomatic aortic stenosis (peak gradient 72 mmHg, AVA "
            "0.7 cm²). NYHA Class III heart failure. STS-PROM 8.2% (intermediate-high "
            "surgical risk). Heart Team evaluation completed with CT surgeon + "
            "interventionalist. Pre-procedural CTA done. Coronary angiography within "
            "6 months. All TAVR criteria met."
        ),
        "approval_score": 0.96,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["RISK-001", "HT-001", "IMG-001", "IMG-002", "DOC-001", "SX-004"],
    },
    {
        "case_id": "SEED-TAVR-002",
        "cpt_code": "33361",
        "procedure_name": "Transcatheter Aortic Valve Replacement",
        "payer": "Aetna",
        "summary": (
            "79F with severe AS, LVEF 35%, NYHA IV symptoms. STS 12.5% (high risk). "
            "Heart Team recommended TAVR over SAVR. CTA and coronary angiography "
            "completed. Consultation note attached. Approved per CMS NCD 20.32."
        ),
        "approval_score": 0.94,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["RISK-001", "HT-001", "IMG-001", "IMG-002", "LVEF-002", "DOC-001", "SX-004"],
    },

    # ── CPT 93656 — AF Catheter Ablation ──
    {
        "case_id": "SEED-AFIB-001",
        "cpt_code": "93656",
        "procedure_name": "Catheter Ablation of Atrial Fibrillation",
        "payer": "BCBS",
        "summary": (
            "58M with symptomatic paroxysmal AF, EHRA Class IIb. Failed flecainide "
            "and amiodarone trials (both discontinued for side effects). CHA₂DS₂-VASc "
            "= 2, on apixaban. Pre-procedure TEE excluded LAA thrombus. Office note "
            "and antiarrhythmic trial documentation attached."
        ),
        "approval_score": 0.93,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["MED-003", "ANTI-001", "ANTI-002", "DOC-001", "SX-004"],
    },
    {
        "case_id": "SEED-AFIB-002",
        "cpt_code": "93656",
        "procedure_name": "Catheter Ablation of Atrial Fibrillation",
        "payer": "UnitedHealthcare",
        "summary": (
            "65F with persistent AF (12 months), EHRA Class III symptoms despite "
            "sotalol 160mg BID. Sotalol failed — recurrent symptomatic episodes. "
            "CHA₂DS₂-VASc = 4, on warfarin with therapeutic INR. Cardiac CT "
            "pre-procedure. Approved for AF ablation."
        ),
        "approval_score": 0.91,
        "approval_label": "HIGH",
        "outcome": "approved",
        "key_criteria_met": ["MED-003", "ANTI-001", "ANTI-002", "DOC-001", "SX-004"],
    },

    # ── Edge case / partial approvals (lower scores but still approved) ──
    {
        "case_id": "SEED-PET-006-BORDER",
        "cpt_code": "78492",
        "procedure_name": "Cardiac Stress PET",
        "payer": "BCBS",
        "summary": (
            "62F with atypical chest pain, intermediate pre-test probability for CAD. "
            "BMI 32 (below 35 threshold). No prior imaging. Able to exercise. "
            "Cardiologist requested PET for risk stratification. Office note attached. "
            "Approved after peer-to-peer — reviewed as Appropriate per ACC AUC."
        ),
        "approval_score": 0.72,
        "approval_label": "MEDIUM",
        "outcome": "approved",
        "key_criteria_met": ["DOC-001", "RISK-003", "GUI-001"],
    },
    {
        "case_id": "SEED-PET-007-APPEAL",
        "cpt_code": "78492",
        "procedure_name": "Cardiac Stress PET",
        "payer": "Aetna",
        "summary": (
            "59M diabetic with abnormal EKG (inferior Q waves), exertional dyspnea. "
            "Prior ETT inconclusive — equivocal ST changes at 85% MPHR. BMI 34. "
            "Initially denied; approved on appeal with additional cardiology note. "
            "DOC-001 emphasized in appeal narrative."
        ),
        "approval_score": 0.75,
        "approval_label": "MEDIUM",
        "outcome": "approved",
        "key_criteria_met": ["NDX-003", "DOC-001", "SX-003", "GUI-001"],
    },
]


def seed():
    """Upsert all seed cases into Pinecone case-precedents."""
    index = _get_pinecone()
    if index is None:
        print("❌ Pinecone unavailable. Set PINECONE_API_KEY and PINECONE_INDEX env vars.")
        return 0

    count = 0
    failed = 0
    for seed_case in SEED_CASES:
        ctx = CaseContext(
            case_id=seed_case["case_id"],
            procedure_code=seed_case["cpt_code"],
            procedure_name=seed_case["procedure_name"],
            payer_name=seed_case["payer"],
            user_id="seed",
            raw_note=seed_case["summary"],
        )
        ctx.approval_score = seed_case["approval_score"]
        ctx.approval_label = seed_case["approval_label"]
        ctx.narrative_draft = seed_case["summary"]
        # Add the case's key met criteria as pseudo-matches for retrieval context
        ctx.criterion_matches = [
            {"code": c, "status": "met", "evidence_quote": "[seed canonical case]"}
            for c in seed_case.get("key_criteria_met", [])
        ]

        ok = store_case_as_precedent(ctx, outcome=seed_case["outcome"])
        if ok:
            print(f"✓ {seed_case['case_id']} ({seed_case['cpt_code']}, {seed_case['payer']}) "
                  f"→ {seed_case['approval_label']} {seed_case['approval_score']:.2f}")
            count += 1
        else:
            print(f"✗ {seed_case['case_id']} — upsert failed")
            failed += 1

    print(f"\nSeeded {count} / {len(SEED_CASES)} cases. {failed} failures.")
    print("\nRetrievable by the UnifiedReasoner on matching CPT code via Pinecone similarity.")
    return count


if __name__ == "__main__":
    seed()
