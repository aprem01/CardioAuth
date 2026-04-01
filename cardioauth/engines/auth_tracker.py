"""Authorization Expiration Tracker.

Manages authorization lifecycles, tracks expiration windows, and
initiates renewals for cardiology prior authorizations.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Today's reference date
TODAY = date(2026, 3, 31)

DEMO_AUTHORIZATIONS: list[dict[str, Any]] = [
    {
        "auth_id": "AUTH-2026-001",
        "patient_id": "DEMO-001",
        "patient_name": "Robert J. Harmon",
        "procedure": "Left Heart Catheterization",
        "cpt_code": "93458",
        "payer": "UnitedHealthcare",
        "status": "approved",
        "approved_date": "2026-03-15",
        "expiration_date": "2026-04-14",
        "scheduled_date": "2026-04-10",
        "facility": "St. Mary's Cardiac Center",
    },
    {
        "auth_id": "AUTH-2026-002",
        "patient_id": "DEMO-002",
        "patient_name": "Margaret L. Chen",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "payer": "Aetna",
        "status": "approved",
        "approved_date": "2026-03-20",
        "expiration_date": "2026-04-03",
        "scheduled_date": "2026-04-02",
        "facility": "Heart & Vascular Institute",
    },
    {
        "auth_id": "AUTH-2026-003",
        "patient_id": "DEMO-003",
        "patient_name": "James T. Rivera",
        "procedure": "Transesophageal Echocardiogram",
        "cpt_code": "93312",
        "payer": "Blue Cross Blue Shield",
        "status": "approved",
        "approved_date": "2026-02-15",
        "expiration_date": "2026-03-30",
        "scheduled_date": "2026-04-05",
        "facility": "Midwest Cardiology Associates",
    },
    {
        "auth_id": "AUTH-2026-004",
        "patient_id": "DEMO-004",
        "patient_name": "Patricia A. Kowalski",
        "procedure": "Nuclear Stress Test (SPECT MPI)",
        "cpt_code": "78452",
        "payer": "Cigna",
        "status": "approved",
        "approved_date": "2026-03-25",
        "expiration_date": "2026-05-24",
        "scheduled_date": "2026-04-15",
        "facility": "Cardiovascular Diagnostics Lab",
    },
    {
        "auth_id": "AUTH-2026-005",
        "patient_id": "DEMO-005",
        "patient_name": "William D. Okafor",
        "procedure": "Percutaneous Coronary Intervention",
        "cpt_code": "92928",
        "payer": "UnitedHealthcare",
        "status": "approved",
        "approved_date": "2026-03-01",
        "expiration_date": "2026-03-31",
        "scheduled_date": "2026-04-01",
        "facility": "University Heart Hospital",
    },
    {
        "auth_id": "AUTH-2026-006",
        "patient_id": "DEMO-006",
        "patient_name": "Linda S. Nakamura",
        "procedure": "Cardiac MRI with Contrast",
        "cpt_code": "75561",
        "payer": "Humana",
        "status": "approved",
        "approved_date": "2026-03-10",
        "expiration_date": "2026-04-09",
        "scheduled_date": "2026-04-08",
        "facility": "Advanced Cardiac Imaging",
    },
    {
        "auth_id": "AUTH-2026-007",
        "patient_id": "DEMO-007",
        "patient_name": "George M. Petrov",
        "procedure": "Electrophysiology Study",
        "cpt_code": "93619",
        "payer": "Aetna",
        "status": "approved",
        "approved_date": "2026-02-01",
        "expiration_date": "2026-03-15",
        "scheduled_date": "2026-03-20",
        "facility": "EP & Arrhythmia Center",
    },
    {
        "auth_id": "AUTH-2026-008",
        "patient_id": "DEMO-008",
        "patient_name": "Susan R. Dimitriou",
        "procedure": "Carotid Ultrasound Duplex",
        "cpt_code": "93880",
        "payer": "Blue Cross Blue Shield",
        "status": "approved",
        "approved_date": "2026-03-28",
        "expiration_date": "2026-04-27",
        "scheduled_date": "2026-04-12",
        "facility": "Vascular & Heart Clinic",
    },
    {
        "auth_id": "AUTH-2026-009",
        "patient_id": "DEMO-009",
        "patient_name": "David K. Abubakar",
        "procedure": "TAVR (Transcatheter Aortic Valve)",
        "cpt_code": "33361",
        "payer": "Medicare",
        "status": "approved",
        "approved_date": "2026-03-18",
        "expiration_date": "2026-04-01",
        "scheduled_date": "2026-04-01",
        "facility": "Regional Structural Heart Center",
    },
    {
        "auth_id": "AUTH-2026-010",
        "patient_id": "DEMO-010",
        "patient_name": "Helen F. Johansson",
        "procedure": "Cardiac Catheterization with Biopsy",
        "cpt_code": "93505",
        "payer": "Cigna",
        "status": "pending",
        "approved_date": None,
        "expiration_date": None,
        "scheduled_date": "2026-04-07",
        "facility": "Transplant Cardiology Unit",
    },
]


def _compute_alert(auth: dict[str, Any]) -> dict[str, Any]:
    """Enrich an authorization record with computed alert fields."""
    result = dict(auth)

    if auth["status"] == "pending":
        result["days_until_expiry"] = None
        result["renewal_eligible"] = False
        result["alert_level"] = "warning"
        return result

    exp = date.fromisoformat(auth["expiration_date"])
    days_left = (exp - TODAY).days

    result["days_until_expiry"] = days_left

    if days_left < 0:
        result["alert_level"] = "expired"
        result["renewal_eligible"] = True
    elif days_left <= 3:
        result["alert_level"] = "critical"
        result["renewal_eligible"] = True
    elif days_left <= 10:
        result["alert_level"] = "warning"
        result["renewal_eligible"] = True
    else:
        result["alert_level"] = "green"
        result["renewal_eligible"] = False

    # Flag if scheduled date is after auth expiry
    if auth.get("scheduled_date") and auth["expiration_date"]:
        sched = date.fromisoformat(auth["scheduled_date"])
        if sched > exp:
            result["alert_level"] = "critical"
            result["renewal_eligible"] = True
            result["schedule_conflict"] = True

    return result


def get_all_authorizations() -> list[dict[str, Any]]:
    """Return all tracked authorizations with computed alert fields."""
    return [_compute_alert(a) for a in DEMO_AUTHORIZATIONS]


def get_expiring_soon(days: int = 5) -> list[dict[str, Any]]:
    """Return authorizations expiring within the given number of days.

    Also includes already-expired authorizations.
    """
    results = []
    for auth in get_all_authorizations():
        if auth["days_until_expiry"] is None:
            continue
        if auth["days_until_expiry"] <= days:
            results.append(auth)
    return sorted(results, key=lambda a: a["days_until_expiry"])


def check_schedule_conflict(auth_id: str, scheduled_date: str) -> dict[str, Any]:
    """Check whether a proposed scheduled date falls within the auth window.

    Returns a dict with conflict status and details.
    """
    auth = next((a for a in DEMO_AUTHORIZATIONS if a["auth_id"] == auth_id), None)
    if not auth:
        return {"error": f"Authorization {auth_id} not found"}

    if auth["status"] == "pending":
        return {
            "auth_id": auth_id,
            "conflict": True,
            "reason": "Authorization is still pending approval",
            "recommendation": "Wait for auth approval before scheduling",
        }

    sched = date.fromisoformat(scheduled_date)
    approved = date.fromisoformat(auth["approved_date"])
    expiry = date.fromisoformat(auth["expiration_date"])

    if sched < approved:
        return {
            "auth_id": auth_id,
            "conflict": True,
            "reason": "Scheduled date is before authorization approval date",
            "scheduled_date": scheduled_date,
            "auth_window": f"{auth['approved_date']} to {auth['expiration_date']}",
            "recommendation": "Reschedule to a date within the authorization window",
        }

    if sched > expiry:
        days_over = (sched - expiry).days
        return {
            "auth_id": auth_id,
            "conflict": True,
            "reason": f"Scheduled date is {days_over} day(s) past authorization expiry",
            "scheduled_date": scheduled_date,
            "expiration_date": auth["expiration_date"],
            "recommendation": "Request renewal or reschedule before expiration",
        }

    return {
        "auth_id": auth_id,
        "conflict": False,
        "scheduled_date": scheduled_date,
        "auth_window": f"{auth['approved_date']} to {auth['expiration_date']}",
        "days_remaining": (expiry - sched).days,
        "message": "Scheduled date is within authorization window",
    }


def initiate_renewal(auth_id: str) -> dict[str, Any]:
    """Start the renewal process for an authorization.

    Returns renewal tracking details (simulated).
    """
    auth = next((a for a in DEMO_AUTHORIZATIONS if a["auth_id"] == auth_id), None)
    if not auth:
        return {"error": f"Authorization {auth_id} not found"}

    enriched = _compute_alert(auth)

    if not enriched["renewal_eligible"]:
        return {
            "auth_id": auth_id,
            "renewal_initiated": False,
            "reason": "Authorization is not yet eligible for renewal (more than 10 days remaining)",
        }

    new_auth_id = auth_id.replace("AUTH-2026", "RENEW-2026")
    return {
        "auth_id": auth_id,
        "renewal_id": new_auth_id,
        "renewal_initiated": True,
        "patient_name": auth["patient_name"],
        "procedure": auth["procedure"],
        "payer": auth["payer"],
        "original_expiry": auth.get("expiration_date"),
        "estimated_processing_days": 3,
        "status": "submitted",
        "message": f"Renewal request {new_auth_id} submitted to {auth['payer']}",
    }
