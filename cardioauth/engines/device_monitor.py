"""Remote Cardiac Device Monitoring Scheduler.

Manages billing windows and eligibility scheduling for patients
with implantable cardiac devices (ICM, Pacemaker, ICD).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Today's reference date
TODAY = date(2026, 3, 31)

# Device monitoring frequency rules by device type
MONITORING_RULES: dict[str, dict[str, Any]] = {
    "ICM": {"min_interval_days": 30, "cpt": "93297", "description": "Implantable Cardiac Monitor"},
    "Pacemaker": {"min_interval_days": 90, "cpt": "93291", "description": "Pacemaker Remote Monitoring"},
    "ICD": {"min_interval_days": 90, "cpt": "93295", "description": "ICD Remote Monitoring"},
}

# Payer-specific overrides to base monitoring rules
PAYER_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "Cigna": {"ICM": {"min_interval_days": 31}},
    "Humana": {"Pacemaker": {"min_interval_days": 91}},
    "Medicare": {"ICD": {"min_interval_days": 91}},
}

DEMO_DEVICE_PATIENTS: list[dict[str, Any]] = [
    {
        "patient_id": "DEV-001",
        "patient_name": "Thomas Mitchell",
        "device_type": "ICD",
        "device_model": "Medtronic Cobalt XT",
        "implant_date": "2024-06-15",
        "payer": "UnitedHealthcare",
        "last_billed_date": "2026-01-05",
        "last_transmission_date": "2026-03-28",
    },
    {
        "patient_id": "DEV-002",
        "patient_name": "Carol A. Finnegan",
        "device_type": "Pacemaker",
        "device_model": "Abbott Assurity MRI",
        "implant_date": "2023-11-20",
        "payer": "Aetna",
        "last_billed_date": "2026-01-02",
        "last_transmission_date": "2026-03-25",
    },
    {
        "patient_id": "DEV-003",
        "patient_name": "Harold B. Tan",
        "device_type": "ICM",
        "device_model": "Medtronic LINQ II",
        "implant_date": "2025-09-10",
        "payer": "Cigna",
        "last_billed_date": "2026-03-05",
        "last_transmission_date": "2026-03-30",
    },
    {
        "patient_id": "DEV-004",
        "patient_name": "Maria G. Espinoza",
        "device_type": "ICD",
        "device_model": "Boston Scientific Dynagen",
        "implant_date": "2025-01-08",
        "payer": "Blue Cross Blue Shield",
        "last_billed_date": "2025-12-20",
        "last_transmission_date": "2026-03-15",
    },
    {
        "patient_id": "DEV-005",
        "patient_name": "Arthur J. Benton",
        "device_type": "Pacemaker",
        "device_model": "Biotronik Edora 8 SR-T",
        "implant_date": "2024-03-22",
        "payer": "Humana",
        "last_billed_date": "2025-12-15",
        "last_transmission_date": "2026-03-20",
    },
    {
        "patient_id": "DEV-006",
        "patient_name": "Doris P. Nakashima",
        "device_type": "ICM",
        "device_model": "Abbott Confirm Rx",
        "implant_date": "2025-06-01",
        "payer": "UnitedHealthcare",
        "last_billed_date": "2026-02-28",
        "last_transmission_date": "2026-03-29",
    },
    {
        "patient_id": "DEV-007",
        "patient_name": "Frank W. Osei",
        "device_type": "ICD",
        "device_model": "Medtronic Cobalt XT",
        "implant_date": "2023-08-14",
        "payer": "Medicare",
        "last_billed_date": "2026-01-10",
        "last_transmission_date": "2026-03-27",
    },
    {
        "patient_id": "DEV-008",
        "patient_name": "Ruth C. Berger",
        "device_type": "Pacemaker",
        "device_model": "Medtronic Azure XT",
        "implant_date": "2025-04-30",
        "payer": "Cigna",
        "last_billed_date": "2026-03-20",
        "last_transmission_date": "2026-03-30",
    },
    {
        "patient_id": "DEV-009",
        "patient_name": "Kenneth L. Adebayo",
        "device_type": "ICM",
        "device_model": "Medtronic LINQ II",
        "implant_date": "2025-12-01",
        "payer": "Aetna",
        "last_billed_date": "2026-03-01",
        "last_transmission_date": "2026-03-31",
    },
    {
        "patient_id": "DEV-010",
        "patient_name": "Virginia E. Kozlov",
        "device_type": "ICD",
        "device_model": "Boston Scientific Dynagen",
        "implant_date": "2024-10-05",
        "payer": "UnitedHealthcare",
        "last_billed_date": "2025-10-01",
        "last_transmission_date": "2026-03-22",
    },
]


def _get_interval(device_type: str, payer: str) -> int:
    """Return the minimum billing interval in days for a device/payer combo."""
    base = MONITORING_RULES[device_type]["min_interval_days"]
    override = PAYER_OVERRIDES.get(payer, {}).get(device_type, {})
    return override.get("min_interval_days", base)


def _compute_status(patient: dict[str, Any]) -> dict[str, Any]:
    """Enrich a patient record with billing eligibility status."""
    result = dict(patient)
    device_type = patient["device_type"]
    payer = patient["payer"]
    interval = _get_interval(device_type, payer)

    rule = MONITORING_RULES[device_type]
    result["cpt_code"] = rule["cpt"]
    result["device_description"] = rule["description"]
    result["min_interval_days"] = interval

    last_billed = date.fromisoformat(patient["last_billed_date"])
    next_eligible = last_billed + timedelta(days=interval)
    result["next_eligible_date"] = next_eligible.isoformat()

    days_until = (next_eligible - TODAY).days

    if days_until < -30:
        result["status"] = "overdue"
        result["days_until_eligible"] = days_until
    elif days_until <= 0:
        result["status"] = "eligible"
        result["days_until_eligible"] = 0
    elif days_until <= 14:
        result["status"] = "upcoming"
        result["days_until_eligible"] = days_until
    else:
        result["status"] = "too_early"
        result["days_until_eligible"] = days_until

    # Flag if no recent transmission
    last_tx = date.fromisoformat(patient["last_transmission_date"])
    days_since_tx = (TODAY - last_tx).days
    if days_since_tx > 14:
        result["status"] = "needs_review"
        result["transmission_gap_days"] = days_since_tx

    return result


def get_device_patients() -> list[dict[str, Any]]:
    """Return all monitored patients with billing eligibility status."""
    return [_compute_status(p) for p in DEMO_DEVICE_PATIENTS]


def calculate_billing_window(patient_id: str) -> dict[str, Any]:
    """Calculate the exact billing window for a patient.

    Returns earliest eligible date and recommended billing window.
    """
    patient = next((p for p in DEMO_DEVICE_PATIENTS if p["patient_id"] == patient_id), None)
    if not patient:
        return {"error": f"Patient {patient_id} not found"}

    device_type = patient["device_type"]
    payer = patient["payer"]
    interval = _get_interval(device_type, payer)

    last_billed = date.fromisoformat(patient["last_billed_date"])
    earliest = last_billed + timedelta(days=interval)
    # Recommended window: earliest to earliest + 14 days
    window_end = earliest + timedelta(days=14)

    return {
        "patient_id": patient_id,
        "patient_name": patient["patient_name"],
        "device_type": device_type,
        "cpt_code": MONITORING_RULES[device_type]["cpt"],
        "payer": payer,
        "last_billed_date": patient["last_billed_date"],
        "earliest_eligible": earliest.isoformat(),
        "recommended_window_end": window_end.isoformat(),
        "interval_days": interval,
        "currently_eligible": earliest <= TODAY,
    }


def check_submission_timing(patient_id: str, proposed_date: str) -> dict[str, Any]:
    """Validate whether a proposed billing date is within the eligible window.

    Returns approval/rejection with explanation.
    """
    patient = next((p for p in DEMO_DEVICE_PATIENTS if p["patient_id"] == patient_id), None)
    if not patient:
        return {"error": f"Patient {patient_id} not found"}

    device_type = patient["device_type"]
    payer = patient["payer"]
    interval = _get_interval(device_type, payer)

    last_billed = date.fromisoformat(patient["last_billed_date"])
    earliest = last_billed + timedelta(days=interval)
    proposed = date.fromisoformat(proposed_date)

    if proposed < earliest:
        days_early = (earliest - proposed).days
        return {
            "patient_id": patient_id,
            "valid": False,
            "proposed_date": proposed_date,
            "earliest_eligible": earliest.isoformat(),
            "days_too_early": days_early,
            "reason": f"Submission is {days_early} day(s) before minimum interval of {interval} days",
            "recommendation": f"Submit on or after {earliest.isoformat()}",
        }

    return {
        "patient_id": patient_id,
        "valid": True,
        "proposed_date": proposed_date,
        "earliest_eligible": earliest.isoformat(),
        "days_since_last": (proposed - last_billed).days,
        "cpt_code": MONITORING_RULES[device_type]["cpt"],
        "message": "Proposed date is within the eligible billing window",
    }


def get_upcoming_eligible(days: int = 14) -> list[dict[str, Any]]:
    """Return patients becoming billing-eligible within the given number of days.

    Includes patients already eligible or overdue.
    """
    results = []
    for patient in get_device_patients():
        d_until = patient.get("days_until_eligible", 999)
        if d_until <= days:
            results.append(patient)
    return sorted(results, key=lambda p: p.get("days_until_eligible", 0))
