"""Enhanced denial pattern analytics engine for CardioAuth.

Provides comprehensive denial analytics with rich demo data spanning 6 months,
pattern detection, revenue impact calculation, and at-risk identification.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Demo denial history — 6 months of realistic cardiology PA denial data
# ---------------------------------------------------------------------------

DEMO_DENIAL_HISTORY: list[dict] = [
    # --- October 2025 ---
    {
        "denial_id": "DEN-001",
        "date": "2025-10-05",
        "patient_id": "P-1001",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "AUTH-001",
        "denial_reason": "Missing ejection fraction documentation",
        "category": "documentation",
        "amount_at_risk": 3200,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 18,
    },
    {
        "denial_id": "DEN-002",
        "date": "2025-10-11",
        "patient_id": "P-1002",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "Aetna",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "MED-002",
        "denial_reason": "Non-invasive testing not exhausted prior to cath",
        "category": "medical_necessity",
        "amount_at_risk": 12500,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 25,
    },
    {
        "denial_id": "DEN-003",
        "date": "2025-10-15",
        "patient_id": "P-1003",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "DOC-003",
        "denial_reason": "Risk factor documentation incomplete",
        "category": "documentation",
        "amount_at_risk": 2800,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-004",
        "date": "2025-10-20",
        "patient_id": "P-1004",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "AUTH-004",
        "denial_reason": "Prior authorization request submitted after procedure",
        "category": "timing",
        "amount_at_risk": 4500,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-005",
        "date": "2025-10-28",
        "patient_id": "P-1005",
        "procedure": "Echocardiogram (TTE)",
        "cpt_code": "93306",
        "payer": "Cigna",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "COD-001",
        "denial_reason": "Incorrect CPT code — should be 93303 for limited echo",
        "category": "coding",
        "amount_at_risk": 1800,
        "appealed": True,
        "appeal_outcome": "upheld",
        "resolution_days": 14,
    },
    # --- November 2025 ---
    {
        "denial_id": "DEN-006",
        "date": "2025-11-03",
        "patient_id": "P-1006",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "AUTH-001",
        "denial_reason": "Missing ejection fraction documentation",
        "category": "documentation",
        "amount_at_risk": 3200,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 16,
    },
    {
        "denial_id": "DEN-007",
        "date": "2025-11-08",
        "patient_id": "P-1007",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "DOC-005",
        "denial_reason": "Stress test results not included in submission",
        "category": "documentation",
        "amount_at_risk": 14200,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 22,
    },
    {
        "denial_id": "DEN-008",
        "date": "2025-11-12",
        "patient_id": "P-1008",
        "procedure": "PCI — Percutaneous Coronary Intervention",
        "cpt_code": "92928",
        "payer": "Aetna",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "MED-003",
        "denial_reason": "Medical necessity not established — stable angina",
        "category": "medical_necessity",
        "amount_at_risk": 38500,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 30,
    },
    {
        "denial_id": "DEN-009",
        "date": "2025-11-18",
        "patient_id": "P-1009",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Aetna",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "MED-006",
        "denial_reason": "Echo sufficient — MRI not medically necessary",
        "category": "medical_necessity",
        "amount_at_risk": 4800,
        "appealed": True,
        "appeal_outcome": "upheld",
        "resolution_days": 20,
    },
    {
        "denial_id": "DEN-010",
        "date": "2025-11-22",
        "patient_id": "P-1010",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "DOC-003",
        "denial_reason": "Risk factor documentation incomplete",
        "category": "documentation",
        "amount_at_risk": 2900,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-011",
        "date": "2025-11-28",
        "patient_id": "P-1011",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "Cigna",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "ELG-001",
        "denial_reason": "Patient eligibility lapsed at time of service",
        "category": "eligibility",
        "amount_at_risk": 3100,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    # --- December 2025 ---
    {
        "denial_id": "DEN-012",
        "date": "2025-12-02",
        "patient_id": "P-1012",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "DOC-005",
        "denial_reason": "Stress test results not included in submission",
        "category": "documentation",
        "amount_at_risk": 13800,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 19,
    },
    {
        "denial_id": "DEN-013",
        "date": "2025-12-06",
        "patient_id": "P-1013",
        "procedure": "Echocardiogram (TTE)",
        "cpt_code": "93306",
        "payer": "Aetna",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "COD-002",
        "denial_reason": "Duplicate billing — echo already performed within 30 days",
        "category": "coding",
        "amount_at_risk": 1650,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-014",
        "date": "2025-12-10",
        "patient_id": "P-1014",
        "procedure": "PCI — Percutaneous Coronary Intervention",
        "cpt_code": "92928",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "MED-003",
        "denial_reason": "Medical necessity not established — stable angina",
        "category": "medical_necessity",
        "amount_at_risk": 42000,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 28,
    },
    {
        "denial_id": "DEN-015",
        "date": "2025-12-14",
        "patient_id": "P-1015",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "AUTH-001",
        "denial_reason": "Missing ejection fraction documentation",
        "category": "documentation",
        "amount_at_risk": 3400,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 15,
    },
    {
        "denial_id": "DEN-016",
        "date": "2025-12-19",
        "patient_id": "P-1016",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "AUTH-004",
        "denial_reason": "Prior authorization request submitted after procedure",
        "category": "timing",
        "amount_at_risk": 4600,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-017",
        "date": "2025-12-23",
        "patient_id": "P-1017",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "payer": "Cigna",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "DOC-003",
        "denial_reason": "Risk factor documentation incomplete",
        "category": "documentation",
        "amount_at_risk": 2750,
        "appealed": True,
        "appeal_outcome": "upheld",
        "resolution_days": 12,
    },
    # --- January 2026 ---
    {
        "denial_id": "DEN-018",
        "date": "2026-01-04",
        "patient_id": "P-1018",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Aetna",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "MED-006",
        "denial_reason": "Echo sufficient — MRI not medically necessary",
        "category": "medical_necessity",
        "amount_at_risk": 4700,
        "appealed": True,
        "appeal_outcome": "upheld",
        "resolution_days": 21,
    },
    {
        "denial_id": "DEN-019",
        "date": "2026-01-09",
        "patient_id": "P-1019",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Aetna",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "MED-006",
        "denial_reason": "Echo sufficient — MRI not medically necessary",
        "category": "medical_necessity",
        "amount_at_risk": 4500,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 24,
    },
    {
        "denial_id": "DEN-020",
        "date": "2026-01-14",
        "patient_id": "P-1020",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "AUTH-001",
        "denial_reason": "Missing ejection fraction documentation",
        "category": "documentation",
        "amount_at_risk": 3300,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 17,
    },
    {
        "denial_id": "DEN-021",
        "date": "2026-01-18",
        "patient_id": "P-1021",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "Aetna",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "DOC-005",
        "denial_reason": "Stress test results not included in submission",
        "category": "documentation",
        "amount_at_risk": 13200,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-022",
        "date": "2026-01-22",
        "patient_id": "P-1022",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Aetna",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "MED-006",
        "denial_reason": "Echo sufficient — MRI not medically necessary",
        "category": "medical_necessity",
        "amount_at_risk": 4900,
        "appealed": True,
        "appeal_outcome": "upheld",
        "resolution_days": 19,
    },
    {
        "denial_id": "DEN-023",
        "date": "2026-01-27",
        "patient_id": "P-1023",
        "procedure": "Echocardiogram (TTE)",
        "cpt_code": "93306",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "COD-001",
        "denial_reason": "Incorrect CPT code — should be 93303 for limited echo",
        "category": "coding",
        "amount_at_risk": 1900,
        "appealed": True,
        "appeal_outcome": "upheld",
        "resolution_days": 10,
    },
    # --- February 2026 ---
    {
        "denial_id": "DEN-024",
        "date": "2026-02-03",
        "patient_id": "P-1024",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "AUTH-001",
        "denial_reason": "Missing ejection fraction documentation",
        "category": "documentation",
        "amount_at_risk": 3350,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 14,
    },
    {
        "denial_id": "DEN-025",
        "date": "2026-02-07",
        "patient_id": "P-1025",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "MED-002",
        "denial_reason": "Non-invasive testing not exhausted prior to cath",
        "category": "medical_necessity",
        "amount_at_risk": 15000,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 26,
    },
    {
        "denial_id": "DEN-026",
        "date": "2026-02-12",
        "patient_id": "P-1026",
        "procedure": "PCI — Percutaneous Coronary Intervention",
        "cpt_code": "92928",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "MED-003",
        "denial_reason": "Medical necessity not established — stable angina",
        "category": "medical_necessity",
        "amount_at_risk": 44500,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 32,
    },
    {
        "denial_id": "DEN-027",
        "date": "2026-02-17",
        "patient_id": "P-1027",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "payer": "Aetna",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "ELG-002",
        "denial_reason": "Out-of-network provider — not covered",
        "category": "eligibility",
        "amount_at_risk": 2600,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-028",
        "date": "2026-02-21",
        "patient_id": "P-1028",
        "procedure": "Echocardiogram (TTE)",
        "cpt_code": "93306",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "COD-002",
        "denial_reason": "Duplicate billing — echo already performed within 30 days",
        "category": "coding",
        "amount_at_risk": 1700,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-029",
        "date": "2026-02-25",
        "patient_id": "P-1029",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Cigna",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "DOC-007",
        "denial_reason": "Prior echo results not attached to submission",
        "category": "documentation",
        "amount_at_risk": 4400,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 13,
    },
    # --- March 2026 ---
    {
        "denial_id": "DEN-030",
        "date": "2026-03-03",
        "patient_id": "P-1030",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "AUTH-001",
        "denial_reason": "Missing ejection fraction documentation",
        "category": "documentation",
        "amount_at_risk": 3150,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 16,
    },
    {
        "denial_id": "DEN-031",
        "date": "2026-03-07",
        "patient_id": "P-1031",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "Aetna",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "DOC-005",
        "denial_reason": "Stress test results not included in submission",
        "category": "documentation",
        "amount_at_risk": 13500,
        "appealed": True,
        "appeal_outcome": "upheld",
        "resolution_days": 20,
    },
    {
        "denial_id": "DEN-032",
        "date": "2026-03-11",
        "patient_id": "P-1032",
        "procedure": "PCI — Percutaneous Coronary Intervention",
        "cpt_code": "92928",
        "payer": "Cigna",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "MED-003",
        "denial_reason": "Medical necessity not established — stable angina",
        "category": "medical_necessity",
        "amount_at_risk": 41000,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 29,
    },
    {
        "denial_id": "DEN-033",
        "date": "2026-03-15",
        "patient_id": "P-1033",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "DOC-003",
        "denial_reason": "Risk factor documentation incomplete",
        "category": "documentation",
        "amount_at_risk": 2850,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 11,
    },
    {
        "denial_id": "DEN-034",
        "date": "2026-03-19",
        "patient_id": "P-1034",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Sarah Chen",
        "denial_reason_code": "AUTH-004",
        "denial_reason": "Prior authorization request submitted after procedure",
        "category": "timing",
        "amount_at_risk": 4750,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
    {
        "denial_id": "DEN-035",
        "date": "2026-03-23",
        "patient_id": "P-1035",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Michael Ross",
        "denial_reason_code": "MED-002",
        "denial_reason": "Non-invasive testing not exhausted prior to cath",
        "category": "medical_necessity",
        "amount_at_risk": 14800,
        "appealed": True,
        "appeal_outcome": "overturned",
        "resolution_days": 23,
    },
    {
        "denial_id": "DEN-036",
        "date": "2026-03-27",
        "patient_id": "P-1036",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "Aetna",
        "physician": "Dr. Amita Patel",
        "denial_reason_code": "AUTH-001",
        "denial_reason": "Missing ejection fraction documentation",
        "category": "documentation",
        "amount_at_risk": 3250,
        "appealed": False,
        "appeal_outcome": None,
        "resolution_days": None,
    },
]

# ---------------------------------------------------------------------------
# Pending requests that may be at risk based on denial patterns
# ---------------------------------------------------------------------------

DEMO_PENDING_REQUESTS: list[dict] = [
    {
        "request_id": "PA-2026-101",
        "patient_id": "P-1040",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Sarah Chen",
        "submitted_date": "2026-03-28",
        "risk_flags": ["Missing ejection fraction in attached records"],
        "matched_pattern": "DEN-001/006/015/024/030",
    },
    {
        "request_id": "PA-2026-102",
        "patient_id": "P-1041",
        "procedure": "Cardiac Catheterization",
        "cpt_code": "93458",
        "payer": "Blue Cross Blue Shield",
        "physician": "Dr. Michael Ross",
        "submitted_date": "2026-03-28",
        "risk_flags": ["Stress test results PDF not attached"],
        "matched_pattern": "DEN-007/012/021/031",
    },
    {
        "request_id": "PA-2026-103",
        "patient_id": "P-1042",
        "procedure": "Cardiac MRI",
        "cpt_code": "75561",
        "payer": "Aetna",
        "physician": "Dr. Amita Patel",
        "submitted_date": "2026-03-29",
        "risk_flags": [
            "Aetna cardiac MRI denial rate: 60%",
            "Echo results not referenced in clinical narrative",
        ],
        "matched_pattern": "DEN-009/018/019/022",
    },
    {
        "request_id": "PA-2026-104",
        "patient_id": "P-1043",
        "procedure": "PCI — Percutaneous Coronary Intervention",
        "cpt_code": "92928",
        "payer": "Cigna",
        "physician": "Dr. Amita Patel",
        "submitted_date": "2026-03-29",
        "risk_flags": ["Stable angina noted — high denial risk for PCI"],
        "matched_pattern": "DEN-008/014/026/032",
    },
    {
        "request_id": "PA-2026-105",
        "patient_id": "P-1044",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Michael Ross",
        "submitted_date": "2026-03-30",
        "risk_flags": ["Missing ejection fraction in attached records"],
        "matched_pattern": "DEN-001/006/015/024/030",
    },
    {
        "request_id": "PA-2026-106",
        "patient_id": "P-1045",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "payer": "UnitedHealthcare",
        "physician": "Dr. Sarah Chen",
        "submitted_date": "2026-03-30",
        "risk_flags": ["Risk factor checklist only 4/8 fields populated"],
        "matched_pattern": "DEN-003/010/017/033",
    },
]


# ---------------------------------------------------------------------------
# Analytics functions
# ---------------------------------------------------------------------------


def _parse_date(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d")


def get_denial_summary() -> dict:
    """Overall denial statistics."""
    total = len(DEMO_DENIAL_HISTORY)
    total_amount = sum(d["amount_at_risk"] for d in DEMO_DENIAL_HISTORY)
    appealed = [d for d in DEMO_DENIAL_HISTORY if d["appealed"]]
    overturned = [d for d in appealed if d["appeal_outcome"] == "overturned"]
    not_appealed = [d for d in DEMO_DENIAL_HISTORY if not d["appealed"]]

    avg_resolution = 0
    resolved = [d for d in DEMO_DENIAL_HISTORY if d["resolution_days"] is not None]
    if resolved:
        avg_resolution = round(sum(d["resolution_days"] for d in resolved) / len(resolved), 1)

    return {
        "total_denials": total,
        "total_amount_at_risk": total_amount,
        "appeal_rate": round(len(appealed) / total, 3) if total else 0,
        "appeals_filed": len(appealed),
        "appeals_overturned": len(overturned),
        "appeals_upheld": len(appealed) - len(overturned),
        "overturn_rate": round(len(overturned) / len(appealed), 3) if appealed else 0,
        "not_appealed": len(not_appealed),
        "revenue_lost_no_appeal": sum(d["amount_at_risk"] for d in not_appealed),
        "revenue_recovered_via_appeal": sum(d["amount_at_risk"] for d in overturned),
        "avg_resolution_days": avg_resolution,
        "period": "2025-10 to 2026-03",
        "annualized_denial_amount": round(total_amount * 2),  # 6 months -> annualized
    }


def get_denials_by_payer() -> list[dict]:
    """Breakdown of denials by insurance payer."""
    by_payer: dict[str, list[dict]] = defaultdict(list)
    for d in DEMO_DENIAL_HISTORY:
        by_payer[d["payer"]].append(d)

    results = []
    for payer, denials in sorted(by_payer.items(), key=lambda x: -len(x[1])):
        appealed = [d for d in denials if d["appealed"]]
        overturned = [d for d in appealed if d["appeal_outcome"] == "overturned"]
        total_amount = sum(d["amount_at_risk"] for d in denials)

        # Top denial reasons for this payer
        reason_counts: dict[str, int] = defaultdict(int)
        for d in denials:
            reason_counts[d["denial_reason"]] += 1
        top_reasons = sorted(reason_counts.items(), key=lambda x: -x[1])[:3]

        results.append({
            "payer": payer,
            "total_denials": len(denials),
            "total_amount": total_amount,
            "appeal_rate": round(len(appealed) / len(denials), 3) if denials else 0,
            "overturn_rate": round(len(overturned) / len(appealed), 3) if appealed else 0,
            "top_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
        })

    return results


def get_denials_by_procedure() -> list[dict]:
    """Breakdown of denials by procedure type."""
    by_proc: dict[str, list[dict]] = defaultdict(list)
    for d in DEMO_DENIAL_HISTORY:
        by_proc[d["procedure"]].append(d)

    results = []
    for proc, denials in sorted(by_proc.items(), key=lambda x: -sum(d["amount_at_risk"] for d in x[1])):
        appealed = [d for d in denials if d["appealed"]]
        overturned = [d for d in appealed if d["appeal_outcome"] == "overturned"]
        total_amount = sum(d["amount_at_risk"] for d in denials)

        results.append({
            "procedure": proc,
            "cpt_code": denials[0]["cpt_code"],
            "total_denials": len(denials),
            "total_amount": total_amount,
            "avg_amount": round(total_amount / len(denials)),
            "appeal_rate": round(len(appealed) / len(denials), 3) if denials else 0,
            "overturn_rate": round(len(overturned) / len(appealed), 3) if appealed else 0,
        })

    return results


def get_denials_by_physician() -> list[dict]:
    """Breakdown of denials by physician."""
    by_phys: dict[str, list[dict]] = defaultdict(list)
    for d in DEMO_DENIAL_HISTORY:
        by_phys[d["physician"]].append(d)

    # Simulated total request counts per physician for denial rate context
    total_requests_by_physician = {
        "Dr. Sarah Chen": 52,
        "Dr. Michael Ross": 48,
        "Dr. Amita Patel": 45,
    }

    results = []
    for phys, denials in sorted(by_phys.items(), key=lambda x: -len(x[1])):
        appealed = [d for d in denials if d["appealed"]]
        overturned = [d for d in appealed if d["appeal_outcome"] == "overturned"]
        total_amount = sum(d["amount_at_risk"] for d in denials)
        total_reqs = total_requests_by_physician.get(phys, 50)

        # Documentation completeness score (simulated)
        doc_scores = {
            "Dr. Sarah Chen": 91,
            "Dr. Michael Ross": 72,
            "Dr. Amita Patel": 86,
        }

        results.append({
            "physician": phys,
            "total_denials": len(denials),
            "total_requests": total_reqs,
            "denial_rate": round(len(denials) / total_reqs, 3),
            "total_amount": total_amount,
            "appeal_rate": round(len(appealed) / len(denials), 3) if denials else 0,
            "overturn_rate": round(len(overturned) / len(appealed), 3) if appealed else 0,
            "documentation_completeness_score": doc_scores.get(phys, 80),
        })

    return results


def get_denials_by_reason() -> list[dict]:
    """Denials grouped by denial reason category."""
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for d in DEMO_DENIAL_HISTORY:
        by_cat[d["category"]].append(d)

    category_labels = {
        "documentation": "Incomplete Documentation",
        "medical_necessity": "Medical Necessity Not Established",
        "coding": "Coding / Billing Errors",
        "timing": "Authorization Timing Issues",
        "eligibility": "Patient Eligibility",
    }

    results = []
    for cat, denials in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        appealed = [d for d in denials if d["appealed"]]
        overturned = [d for d in appealed if d["appeal_outcome"] == "overturned"]
        total_amount = sum(d["amount_at_risk"] for d in denials)

        # Specific reasons within category
        reason_counts: dict[str, int] = defaultdict(int)
        for d in denials:
            reason_counts[d["denial_reason"]] += 1

        results.append({
            "category": cat,
            "category_label": category_labels.get(cat, cat.title()),
            "total_denials": len(denials),
            "total_amount": total_amount,
            "percentage_of_all": round(len(denials) / len(DEMO_DENIAL_HISTORY) * 100, 1),
            "preventable": cat in ("documentation", "coding", "timing"),
            "appeal_rate": round(len(appealed) / len(denials), 3) if denials else 0,
            "overturn_rate": round(len(overturned) / len(appealed), 3) if appealed else 0,
            "specific_reasons": [
                {"reason": r, "count": c}
                for r, c in sorted(reason_counts.items(), key=lambda x: -x[1])
            ],
        })

    return results


def get_denial_trends(months: int = 6) -> list[dict]:
    """Monthly trend data for denial volume and amounts."""
    by_month: dict[str, list[dict]] = defaultdict(list)
    for d in DEMO_DENIAL_HISTORY:
        month_key = d["date"][:7]  # "YYYY-MM"
        by_month[month_key].append(d)

    # Simulated total submissions per month for rate calculation
    monthly_submissions = {
        "2025-10": 24,
        "2025-11": 26,
        "2025-12": 25,
        "2026-01": 28,
        "2026-02": 27,
        "2026-03": 29,
    }

    results = []
    for month_key in sorted(by_month.keys())[-months:]:
        denials = by_month[month_key]
        appealed = [d for d in denials if d["appealed"]]
        overturned = [d for d in appealed if d["appeal_outcome"] == "overturned"]
        total_amount = sum(d["amount_at_risk"] for d in denials)
        total_subs = monthly_submissions.get(month_key, 25)

        # Category breakdown for the month
        cats: dict[str, int] = defaultdict(int)
        for d in denials:
            cats[d["category"]] += 1

        dt = datetime.strptime(month_key, "%Y-%m")
        results.append({
            "month": month_key,
            "month_label": dt.strftime("%b %Y"),
            "total_submissions": total_subs,
            "total_denials": len(denials),
            "denial_rate": round(len(denials) / total_subs, 3),
            "total_amount": total_amount,
            "appeals_filed": len(appealed),
            "appeals_overturned": len(overturned),
            "category_breakdown": dict(cats),
        })

    return results


def identify_patterns() -> list[dict]:
    """AI-style pattern detection — surfaces actionable insights from denial data."""
    patterns: list[dict] = []

    # --- Pattern 1: UHC nuclear stress test + missing EF ---
    uhc_nst = [
        d for d in DEMO_DENIAL_HISTORY
        if d["payer"] == "UnitedHealthcare"
        and d["cpt_code"] == "78452"
    ]
    uhc_nst_ef = [d for d in uhc_nst if "ejection fraction" in d["denial_reason"].lower()]
    pending_ef = [
        p for p in DEMO_PENDING_REQUESTS
        if p["payer"] == "UnitedHealthcare"
        and p["cpt_code"] == "78452"
        and any("ejection fraction" in f.lower() for f in p["risk_flags"])
    ]
    # Total UHC nuclear requests (simulated): 18
    uhc_nst_total = 18
    patterns.append({
        "pattern_id": "PAT-001",
        "severity": "high",
        "type": "payer_procedure_specific",
        "title": "UnitedHealthcare Nuclear Stress Test Denials",
        "insight": (
            f"UnitedHealthcare denies your nuclear stress tests "
            f"{round(len(uhc_nst) / uhc_nst_total * 100)}% of the time. "
            f"Most common reason: missing ejection fraction documentation. "
            f"{len(pending_ef)} pending requests have this gap."
        ),
        "affected_denials": len(uhc_nst),
        "amount_at_risk": sum(d["amount_at_risk"] for d in uhc_nst),
        "pending_at_risk": len(pending_ef),
        "recommendation": (
            "Add ejection fraction to nuclear stress test order template. "
            "Flag any submission to UHC missing EF before sending."
        ),
    })

    # --- Pattern 2: Dr. Ross cardiac cath denial rate ---
    ross_cath = [
        d for d in DEMO_DENIAL_HISTORY
        if d["physician"] == "Dr. Michael Ross"
        and "Catheterization" in d["procedure"]
    ]
    ross_total_cath = 12  # simulated total cath by Ross
    practice_avg_doc = 89
    ross_doc_score = 72
    patterns.append({
        "pattern_id": "PAT-002",
        "severity": "high",
        "type": "physician_specific",
        "title": "Dr. Ross Cardiac Catheterization Denial Rate",
        "insight": (
            f"Dr. Ross has {round(len(ross_cath) / ross_total_cath * 100)}% denial rate for "
            f"cardiac catheterization — documentation completeness is {ross_doc_score}% "
            f"vs practice average of {practice_avg_doc}%."
        ),
        "affected_denials": len(ross_cath),
        "amount_at_risk": sum(d["amount_at_risk"] for d in ross_cath),
        "pending_at_risk": len([
            p for p in DEMO_PENDING_REQUESTS
            if p["physician"] == "Dr. Michael Ross" and "Catheterization" in p["procedure"]
        ]),
        "recommendation": (
            "Schedule documentation review with Dr. Ross. "
            "Ensure stress test results are always attached to cath PA submissions."
        ),
    })

    # --- Pattern 3: Aetna cardiac MRI spike ---
    aetna_mri = [
        d for d in DEMO_DENIAL_HISTORY
        if d["payer"] == "Aetna" and d["cpt_code"] == "75561"
    ]
    aetna_mri_jan_plus = [d for d in aetna_mri if d["date"] >= "2026-01-01"]
    aetna_mri_before = [d for d in aetna_mri if d["date"] < "2026-01-01"]
    patterns.append({
        "pattern_id": "PAT-003",
        "severity": "medium",
        "type": "payer_policy_change",
        "title": "Aetna Cardiac MRI Denial Spike",
        "insight": (
            f"Aetna cardiac MRI denials spiked {len(aetna_mri_jan_plus)} denials in Jan-Mar 2026 "
            f"vs {len(aetna_mri_before)} in Oct-Dec 2025 — likely after Q1 2026 policy update "
            f"requiring echo insufficiency documentation before MRI approval."
        ),
        "affected_denials": len(aetna_mri),
        "amount_at_risk": sum(d["amount_at_risk"] for d in aetna_mri),
        "pending_at_risk": len([
            p for p in DEMO_PENDING_REQUESTS
            if p["payer"] == "Aetna" and p["cpt_code"] == "75561"
        ]),
        "recommendation": (
            "Update Aetna cardiac MRI submission template to include echo "
            "insufficiency justification. Review Aetna's Q1 2026 policy bulletin."
        ),
    })

    # --- Pattern 4: Documentation denials are preventable ---
    doc_denials = [d for d in DEMO_DENIAL_HISTORY if d["category"] == "documentation"]
    doc_appealed = [d for d in doc_denials if d["appealed"]]
    doc_overturned = [d for d in doc_appealed if d["appeal_outcome"] == "overturned"]
    patterns.append({
        "pattern_id": "PAT-004",
        "severity": "high",
        "type": "systemic",
        "title": "Documentation Gaps Are Your #1 Denial Driver",
        "insight": (
            f"{len(doc_denials)} of {len(DEMO_DENIAL_HISTORY)} denials ({round(len(doc_denials)/len(DEMO_DENIAL_HISTORY)*100)}%) "
            f"are documentation-related and preventable. "
            f"When appealed, {round(len(doc_overturned)/len(doc_appealed)*100) if doc_appealed else 0}% are overturned — "
            f"but ${sum(d['amount_at_risk'] for d in doc_denials if not d['appealed']):,} was never appealed."
        ),
        "affected_denials": len(doc_denials),
        "amount_at_risk": sum(d["amount_at_risk"] for d in doc_denials),
        "pending_at_risk": len([
            p for p in DEMO_PENDING_REQUESTS
            if any("missing" in f.lower() or "not attached" in f.lower() or "not populated" in f.lower()
                   for f in p["risk_flags"])
        ]),
        "recommendation": (
            "Implement pre-submission documentation checklist. "
            "CardioAuth can auto-flag missing fields before submission."
        ),
    })

    # --- Pattern 5: PCI stable angina pattern ---
    pci_denials = [
        d for d in DEMO_DENIAL_HISTORY
        if d["cpt_code"] == "92928" and "stable angina" in d["denial_reason"].lower()
    ]
    pci_appealed = [d for d in pci_denials if d["appealed"]]
    pci_overturned = [d for d in pci_appealed if d["appeal_outcome"] == "overturned"]
    patterns.append({
        "pattern_id": "PAT-005",
        "severity": "critical",
        "type": "high_value_procedure",
        "title": "PCI Denials — Highest Revenue Impact",
        "insight": (
            f"PCI denials account for ${sum(d['amount_at_risk'] for d in pci_denials):,} in at-risk revenue. "
            f"All {len(pci_denials)} denials cite stable angina — but {len(pci_overturned)} of {len(pci_appealed)} "
            f"appeals were overturned when symptom progression was documented."
        ),
        "affected_denials": len(pci_denials),
        "amount_at_risk": sum(d["amount_at_risk"] for d in pci_denials),
        "pending_at_risk": len([
            p for p in DEMO_PENDING_REQUESTS
            if p["cpt_code"] == "92928"
        ]),
        "recommendation": (
            "For PCI requests, always include: symptom progression timeline, "
            "failed medical therapy documentation, and functional limitation assessment."
        ),
    })

    # --- Pattern 6: Appeals left on the table ---
    not_appealed = [d for d in DEMO_DENIAL_HISTORY if not d["appealed"]]
    summary = get_denial_summary()
    patterns.append({
        "pattern_id": "PAT-006",
        "severity": "high",
        "type": "revenue_recovery",
        "title": "Revenue Left on the Table — Unappealed Denials",
        "insight": (
            f"{len(not_appealed)} denials (${sum(d['amount_at_risk'] for d in not_appealed):,}) were never appealed. "
            f"Your overturn rate is {round(summary['overturn_rate'] * 100)}% — "
            f"applying that rate, an estimated ${round(sum(d['amount_at_risk'] for d in not_appealed) * summary['overturn_rate']):,} "
            f"could have been recovered."
        ),
        "affected_denials": len(not_appealed),
        "amount_at_risk": sum(d["amount_at_risk"] for d in not_appealed),
        "pending_at_risk": 0,
        "recommendation": (
            "Appeal every denial over $2,000. CardioAuth can auto-generate appeal "
            "letters with supporting documentation in under 2 minutes."
        ),
    })

    return patterns


def get_pending_at_risk() -> list[dict]:
    """Current pending requests that match historical denial patterns."""
    results = []
    for p in DEMO_PENDING_REQUESTS:
        # Estimate denial probability based on historical match
        matching_denials = [
            d for d in DEMO_DENIAL_HISTORY
            if d["payer"] == p["payer"] and d["cpt_code"] == p["cpt_code"]
        ]

        # Simulated total requests for this payer+procedure combo
        combo_totals = {
            ("UnitedHealthcare", "78452"): 18,
            ("Blue Cross Blue Shield", "93458"): 8,
            ("Aetna", "75561"): 7,
            ("Cigna", "92928"): 5,
            ("UnitedHealthcare", "75574"): 6,
        }
        total = combo_totals.get((p["payer"], p["cpt_code"]), 10)
        denial_probability = round(len(matching_denials) / total, 2) if total else 0.1

        results.append({
            "request_id": p["request_id"],
            "patient_id": p["patient_id"],
            "procedure": p["procedure"],
            "cpt_code": p["cpt_code"],
            "payer": p["payer"],
            "physician": p["physician"],
            "submitted_date": p["submitted_date"],
            "denial_probability": min(denial_probability, 0.85),
            "risk_flags": p["risk_flags"],
            "matched_pattern": p["matched_pattern"],
            "estimated_amount_at_risk": {
                "78452": 3200,
                "93458": 13800,
                "75561": 4700,
                "92928": 41000,
                "75574": 2850,
            }.get(p["cpt_code"], 5000),
            "recommended_action": _get_recommended_action(p),
        })

    return results


def _get_recommended_action(pending: dict) -> str:
    """Generate a recommended action for an at-risk pending request."""
    flags = " ".join(pending["risk_flags"]).lower()
    if "ejection fraction" in flags:
        return "Add ejection fraction measurement to clinical documentation before payer review."
    if "stress test" in flags or "not attached" in flags:
        return "Attach stress test results PDF to the submission immediately."
    if "echo" in flags and "mri" in pending["procedure"].lower():
        return "Add echo insufficiency justification — document why echo was non-diagnostic."
    if "stable angina" in flags:
        return "Document symptom progression, failed medical therapy, and functional limitations."
    if "not populated" in flags or "checklist" in flags:
        return "Complete all required fields in the risk factor documentation checklist."
    return "Review submission for completeness against payer-specific requirements."


def calculate_revenue_impact() -> dict:
    """Total revenue impact analysis — lost, recovered, and preventable."""
    total_at_risk = sum(d["amount_at_risk"] for d in DEMO_DENIAL_HISTORY)

    appealed = [d for d in DEMO_DENIAL_HISTORY if d["appealed"]]
    overturned = [d for d in appealed if d["appeal_outcome"] == "overturned"]
    upheld = [d for d in appealed if d["appeal_outcome"] == "upheld"]
    not_appealed = [d for d in DEMO_DENIAL_HISTORY if not d["appealed"]]

    recovered = sum(d["amount_at_risk"] for d in overturned)
    lost_upheld = sum(d["amount_at_risk"] for d in upheld)
    lost_no_appeal = sum(d["amount_at_risk"] for d in not_appealed)
    total_lost = lost_upheld + lost_no_appeal

    # Preventable = documentation + coding + timing categories
    preventable_denials = [
        d for d in DEMO_DENIAL_HISTORY
        if d["category"] in ("documentation", "coding", "timing")
    ]
    preventable_amount = sum(d["amount_at_risk"] for d in preventable_denials)

    # Pending at-risk
    pending = get_pending_at_risk()
    pending_total = sum(p["estimated_amount_at_risk"] for p in pending)

    # Annualized projections
    annualized_at_risk = round(total_at_risk * 2)
    annualized_preventable = round(preventable_amount * 2)
    overturn_rate = len(overturned) / len(appealed) if appealed else 0

    return {
        "period": "2025-10 to 2026-03 (6 months)",
        "total_denial_amount": total_at_risk,
        "revenue_recovered_via_appeal": recovered,
        "revenue_lost_upheld_appeals": lost_upheld,
        "revenue_lost_no_appeal": lost_no_appeal,
        "total_revenue_lost": total_lost,
        "estimated_recoverable_if_all_appealed": round(lost_no_appeal * overturn_rate),
        "preventable_denial_amount": preventable_amount,
        "preventable_denial_count": len(preventable_denials),
        "current_pending_at_risk": pending_total,
        "current_pending_count": len(pending),
        "annualized_projections": {
            "total_at_risk": annualized_at_risk,
            "preventable_savings": annualized_preventable,
            "appeal_recovery_potential": round((lost_no_appeal * 2) * overturn_rate),
            "cardioauth_estimated_savings": round(annualized_preventable * 0.80),
        },
        "key_metrics": {
            "appeal_rate": round(len(appealed) / len(DEMO_DENIAL_HISTORY), 3),
            "overturn_rate": round(overturn_rate, 3),
            "preventable_percentage": round(
                len(preventable_denials) / len(DEMO_DENIAL_HISTORY) * 100, 1
            ),
            "avg_denial_amount": round(total_at_risk / len(DEMO_DENIAL_HISTORY)),
            "avg_appeal_resolution_days": round(
                sum(d["resolution_days"] for d in appealed if d["resolution_days"]) / len(appealed), 1
            ) if appealed else 0,
        },
    }
