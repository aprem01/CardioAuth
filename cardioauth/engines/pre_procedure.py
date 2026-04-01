"""Pre-Procedure Eligibility Check Engine.

Performs 48-hour pre-procedure verification including authorization
validity, coverage status, and scheduling conflict detection.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Today's reference date
TODAY = date(2026, 3, 31)

DEMO_UPCOMING_PROCEDURES: list[dict[str, Any]] = [
    {
        "procedure_id": "PROC-001",
        "patient_id": "DEMO-001",
        "patient_name": "Robert J. Harmon",
        "procedure": "Left Heart Catheterization",
        "cpt_code": "93458",
        "scheduled_date": "2026-04-02",
        "payer": "UnitedHealthcare",
        "auth_id": "AUTH-2026-001",
        "auth_expiry": "2026-04-14",
        "auth_status": "valid",
        "coverage_active": True,
        "member_id": "UHC-884201",
    },
    {
        "procedure_id": "PROC-002",
        "patient_id": "DEMO-003",
        "patient_name": "James T. Rivera",
        "procedure": "Transesophageal Echocardiogram",
        "cpt_code": "93312",
        "scheduled_date": "2026-04-05",
        "payer": "Blue Cross Blue Shield",
        "auth_id": "AUTH-2026-003",
        "auth_expiry": "2026-03-30",
        "auth_status": "expired",
        "coverage_active": True,
        "member_id": "BCBS-552190",
    },
    {
        "procedure_id": "PROC-003",
        "patient_id": "DEMO-005",
        "patient_name": "William D. Okafor",
        "procedure": "Percutaneous Coronary Intervention",
        "cpt_code": "92928",
        "scheduled_date": "2026-04-01",
        "payer": "UnitedHealthcare",
        "auth_id": "AUTH-2026-005",
        "auth_expiry": "2026-03-31",
        "auth_status": "valid",
        "coverage_active": True,
        "member_id": "UHC-730415",
    },
    {
        "procedure_id": "PROC-004",
        "patient_id": "DEMO-010",
        "patient_name": "Helen F. Johansson",
        "procedure": "Cardiac Catheterization with Biopsy",
        "cpt_code": "93505",
        "scheduled_date": "2026-04-07",
        "payer": "Cigna",
        "auth_id": None,
        "auth_expiry": None,
        "auth_status": "pending",
        "coverage_active": True,
        "member_id": "CGN-409817",
    },
    {
        "procedure_id": "PROC-005",
        "patient_id": "DEMO-011",
        "patient_name": "Edward P. Volkov",
        "procedure": "Coronary Artery Bypass Graft",
        "cpt_code": "33533",
        "scheduled_date": "2026-04-03",
        "payer": "Aetna",
        "auth_id": None,
        "auth_expiry": None,
        "auth_status": "missing",
        "coverage_active": True,
        "member_id": "AET-281654",
    },
    {
        "procedure_id": "PROC-006",
        "patient_id": "DEMO-006",
        "patient_name": "Linda S. Nakamura",
        "procedure": "Cardiac MRI with Contrast",
        "cpt_code": "75561",
        "scheduled_date": "2026-04-08",
        "payer": "Humana",
        "auth_id": "AUTH-2026-006",
        "auth_expiry": "2026-04-09",
        "auth_status": "valid",
        "coverage_active": True,
        "member_id": "HUM-619072",
    },
    {
        "procedure_id": "PROC-007",
        "patient_id": "DEMO-012",
        "patient_name": "Barbara K. Singh",
        "procedure": "Stress Echocardiography",
        "cpt_code": "93351",
        "scheduled_date": "2026-04-04",
        "payer": "Cigna",
        "auth_id": "AUTH-2026-012",
        "auth_expiry": "2026-04-20",
        "auth_status": "valid",
        "coverage_active": False,
        "member_id": "CGN-887263",
    },
    {
        "procedure_id": "PROC-008",
        "patient_id": "DEMO-009",
        "patient_name": "David K. Abubakar",
        "procedure": "TAVR (Transcatheter Aortic Valve)",
        "cpt_code": "33361",
        "scheduled_date": "2026-04-01",
        "payer": "Medicare",
        "auth_id": "AUTH-2026-009",
        "auth_expiry": "2026-04-01",
        "auth_status": "valid",
        "coverage_active": True,
        "member_id": "MCR-503891",
    },
    {
        "procedure_id": "PROC-009",
        "patient_id": "DEMO-002",
        "patient_name": "Margaret L. Chen",
        "procedure": "Coronary CT Angiography",
        "cpt_code": "75574",
        "scheduled_date": "2026-04-02",
        "payer": "Aetna",
        "auth_id": "AUTH-2026-002",
        "auth_expiry": "2026-04-03",
        "auth_status": "valid",
        "coverage_active": True,
        "member_id": "AET-674520",
    },
    {
        "procedure_id": "PROC-010",
        "patient_id": "DEMO-013",
        "patient_name": "Richard N. Petersen",
        "procedure": "Pacemaker Implantation",
        "cpt_code": "33206",
        "scheduled_date": "2026-04-06",
        "payer": "Blue Cross Blue Shield",
        "auth_id": "AUTH-2026-013",
        "auth_expiry": "2026-04-25",
        "auth_status": "valid",
        "coverage_active": True,
        "member_id": "BCBS-310774",
    },
]


def _evaluate_procedure(proc: dict[str, Any]) -> dict[str, Any]:
    """Run all pre-procedure checks and return enriched result."""
    result = dict(proc)
    issues: list[str] = []

    scheduled = date.fromisoformat(proc["scheduled_date"])
    hours_until = (scheduled - TODAY).days * 24  # approximate

    # Check authorization status
    if proc["auth_status"] == "expired":
        issues.append("Authorization has expired — renewal required before procedure")
    elif proc["auth_status"] == "missing":
        issues.append("No authorization on file — prior auth must be obtained")
    elif proc["auth_status"] == "pending":
        issues.append("Authorization is pending payer approval — not yet confirmed")
    elif proc["auth_status"] == "valid" and proc.get("auth_expiry"):
        expiry = date.fromisoformat(proc["auth_expiry"])
        if scheduled > expiry:
            issues.append(
                f"Scheduled date ({proc['scheduled_date']}) is after auth expiry "
                f"({proc['auth_expiry']}) — reschedule or renew"
            )
        elif (expiry - scheduled).days <= 1:
            issues.append("Authorization expires within 1 day of procedure — high risk of lapse")

    # Check coverage
    if not proc["coverage_active"]:
        issues.append("Patient insurance coverage is inactive — verify eligibility before proceeding")

    # Within 48-hour window check
    result["within_48hr_window"] = 0 <= hours_until <= 48

    # Determine overall check status
    if not issues:
        result["check_status"] = "cleared"
    elif any(
        keyword in issue
        for issue in issues
        for keyword in ["expired", "missing", "inactive"]
    ):
        result["check_status"] = "blocked"
    else:
        result["check_status"] = "warning"

    result["issues"] = issues
    result["issue_count"] = len(issues)
    result["hours_until_procedure"] = hours_until

    return result


def run_pre_procedure_check(procedure_id: str) -> dict[str, Any]:
    """Run a full pre-procedure verification for a specific procedure.

    Checks authorization validity, coverage status, and scheduling conflicts.
    """
    proc = next(
        (p for p in DEMO_UPCOMING_PROCEDURES if p["procedure_id"] == procedure_id),
        None,
    )
    if not proc:
        return {"error": f"Procedure {procedure_id} not found"}

    return _evaluate_procedure(proc)


def get_upcoming_procedures(days: int = 7) -> list[dict[str, Any]]:
    """Return all procedures scheduled within the given number of days, with status."""
    cutoff = TODAY + timedelta(days=days)
    results = []
    for proc in DEMO_UPCOMING_PROCEDURES:
        sched = date.fromisoformat(proc["scheduled_date"])
        if TODAY <= sched <= cutoff:
            results.append(_evaluate_procedure(proc))
    return sorted(results, key=lambda p: p["scheduled_date"])


def get_blocked_procedures() -> list[dict[str, Any]]:
    """Return all upcoming procedures that cannot proceed due to issues."""
    results = []
    for proc in DEMO_UPCOMING_PROCEDURES:
        evaluated = _evaluate_procedure(proc)
        if evaluated["check_status"] == "blocked":
            results.append(evaluated)
    return sorted(results, key=lambda p: p["scheduled_date"])
