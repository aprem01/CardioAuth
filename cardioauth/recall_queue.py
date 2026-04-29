"""Recall queue — patients with prior procedures who may need follow-up.

Peter Apr 28: "patients who had testing over a defined time frame but have
not been seen recently. The scheduler could review whether they need
follow-up, changed doctors, or should be removed from reminders."

Tier 1 (this module): operates on `submissions` and `outcomes` we already
persist. last_encounter_date starts as the submission date (proxy for
"most recent contact") and is updated by the back-office UI. Status
transitions reflect outreach attempts.

Tier 2 (later): replace the manual last_encounter_date with Epic FHIR
Encounter queries; "Mark scheduled" writes Appointment back to Epic.

Design notes
- Source of truth is the `recall_queue` table (separate from submissions
  so the recall lifecycle doesn't mutate the immutable submission
  record).
- The queue is *derived* from approved submissions but is not a view —
  back-office actions need to persist (status, last_action_at, etc.),
  so we keep state on dedicated rows.
- `compute_recall_state` is pure given (today, expected_followup, status,
  last_encounter): runs locally without DB access, easy to test.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Procedure → follow-up window (months) ──────────────────────────────
# Conservative defaults. Cardiology is procedure-specific:
#   - TAVR / device implants → tight follow-up window (1 month)
#   - Cath / revasc → 3 months for medication titration & next imaging
#   - Stress imaging → 6 months (next clinical decision point)
#   - Echo / structural → 12 months
_PROCEDURE_FOLLOWUP_MONTHS: dict[str, int] = {
    # Cardiac PET
    "78491": 6, "78492": 6,
    # Cardiac SPECT
    "78451": 6, "78452": 6,
    # Stress echo
    "93350": 6, "93351": 6,
    # Left heart cath
    "93458": 3, "93459": 3, "93454": 3, "93460": 3,
    # TAVR family
    "33361": 1, "33362": 1, "33363": 1, "33364": 1, "33365": 1,
    # Device implants / ablation
    "93656": 3, "93650": 3, "33208": 3, "33207": 3,
    # Cardiac MRI / CCTA
    "75557": 12, "75561": 12, "75571": 12, "75572": 12, "75573": 12, "75574": 12,
    # Echo
    "93306": 12, "93307": 12, "93308": 12,
}
_DEFAULT_FOLLOWUP_MONTHS = 6


# ── Procedure → estimated practice value (USD) ─────────────────────────
# Rough Medicare allowable reference points, used purely for "revenue at
# risk" headline. Commercial often higher; not load-bearing for clinical
# decisions, just for the dashboard's value column.
_PROCEDURE_VALUE_USD: dict[str, float] = {
    "78491": 1500, "78492": 1500,
    "78451": 800,  "78452": 800,
    "93350": 600,  "93351": 600,
    "93458": 3500, "93459": 3500, "93454": 3000, "93460": 3500,
    "33361": 15000, "33362": 15000, "33363": 16000, "33364": 16000, "33365": 16000,
    "93656": 8000, "93650": 6000, "33208": 12000, "33207": 12000,
    "75557": 600, "75561": 700, "75571": 400, "75572": 500, "75573": 500, "75574": 600,
    "93306": 300, "93307": 300, "93308": 250,
}
_DEFAULT_VALUE_USD = 500.0


VALID_STATUSES = {
    "pending",              # newly queued, follow-up window not yet elapsed
    "due",                  # follow-up window elapsed, no encounter since
    "overdue",              # 30+ days past follow-up date
    "outreach_attempted",   # office tried to reach the patient
    "scheduled",            # appointment booked
    "removed",              # excluded from queue (patient request, deceased, etc.)
}


def follow_up_months_for(cpt_code: str) -> int:
    """Return the follow-up window for a CPT (default 6 months)."""
    return _PROCEDURE_FOLLOWUP_MONTHS.get(cpt_code or "", _DEFAULT_FOLLOWUP_MONTHS)


def value_estimate_for(cpt_code: str) -> float:
    """Return a rough practice-value estimate (USD) for a CPT."""
    return float(_PROCEDURE_VALUE_USD.get(cpt_code or "", _DEFAULT_VALUE_USD))


def compute_expected_followup(submission_date: str, cpt_code: str) -> str:
    """Submission ISO date + procedure window → expected follow-up ISO date."""
    months = follow_up_months_for(cpt_code)
    try:
        sd = datetime.fromisoformat(submission_date.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        sd = date.today()
    # Naive month math via 30-day approximation; fine for queue triage.
    exp = sd + timedelta(days=30 * months)
    return exp.isoformat()


def compute_recall_state(
    today: date,
    expected_followup: str,
    last_encounter_date: str | None,
    current_status: str,
) -> str:
    """Pure function returning the new recall_status given context.

    Terminal states (`outreach_attempted`, `scheduled`, `removed`) are
    sticky — only manual actions transition out of them. This function
    only auto-transitions `pending` → `due` → `overdue`.
    """
    if current_status in ("outreach_attempted", "scheduled", "removed"):
        return current_status

    # If the patient has been seen STRICTLY AFTER the expected follow-up
    # date, we don't need to recall them — they came in. Strict > matters:
    # last_encounter defaults to the submission date (the procedure itself),
    # so equality means "haven't been seen since the procedure" which is
    # exactly the recall case, not the scheduled case.
    if last_encounter_date:
        try:
            le = datetime.fromisoformat(last_encounter_date).date()
            ef = datetime.fromisoformat(expected_followup).date()
            if le > ef:
                return "scheduled"
        except (ValueError, TypeError):
            pass

    try:
        ef = datetime.fromisoformat(expected_followup).date()
    except (ValueError, TypeError):
        return current_status or "pending"

    if today < ef:
        return "pending"
    if (today - ef).days >= 30:
        return "overdue"
    return "due"


# ── DB helpers ──────────────────────────────────────────────────────────

@dataclass
class RecallEntry:
    submission_id: str
    patient_id: str
    patient_name: str
    procedure_code: str
    procedure_name: str
    payer: str
    submission_date: str
    expected_followup_date: str
    last_encounter_date: str | None
    recall_status: str
    practice_value_estimate: float
    notes: str
    last_action_at: str | None
    last_action_by: str | None
    days_overdue: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def upsert_recall_entry(
    *,
    submission_id: str,
    patient_id: str,
    patient_name: str,
    procedure_code: str,
    procedure_name: str,
    payer: str,
    submission_date: str,
    last_encounter_date: str | None = None,
) -> None:
    """Create or refresh the recall row for a submission. Idempotent."""
    from cardioauth.persistence import get_store

    store = get_store()
    expected = compute_expected_followup(submission_date, procedure_code)
    value = value_estimate_for(procedure_code)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Default last_encounter to submission date as a proxy until Tier 2 (FHIR sync)
    last_enc = last_encounter_date or submission_date

    # Compute initial status based on today
    initial_status = compute_recall_state(
        today=date.today(),
        expected_followup=expected,
        last_encounter_date=last_enc,
        current_status="pending",
    )

    with store._conn() as conn:  # type: ignore[attr-defined]
        existing = conn.execute(
            "SELECT recall_status, last_encounter_date FROM recall_queue WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        if existing:
            # Don't clobber manual overrides; refresh only the non-state fields.
            conn.execute(
                """UPDATE recall_queue SET
                    patient_name = ?, procedure_name = ?, payer = ?,
                    expected_followup_date = ?, practice_value_estimate = ?,
                    updated_at = ?
                WHERE submission_id = ?""",
                (patient_name, procedure_name, payer, expected, value,
                 now_iso, submission_id),
            )
        else:
            conn.execute(
                """INSERT INTO recall_queue (
                    submission_id, patient_id, patient_name, procedure_code,
                    procedure_name, payer, submission_date, expected_followup_date,
                    last_encounter_date, recall_status, practice_value_estimate,
                    notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (submission_id, patient_id, patient_name, procedure_code,
                 procedure_name, payer, submission_date, expected,
                 last_enc, initial_status, value, "", now_iso, now_iso),
            )


def list_recall_queue(
    *,
    status: str = "",
    procedure_code: str = "",
    payer: str = "",
    refresh_status: bool = True,
) -> list[RecallEntry]:
    """Return recall rows, optionally filtered by status/procedure/payer.

    `refresh_status=True` recomputes status from today's date for non-terminal
    entries before returning — keeps the list "live" without a cron job.
    """
    from cardioauth.persistence import get_store
    store = get_store()
    today = date.today()

    sql = "SELECT * FROM recall_queue"
    conditions: list[str] = []
    params: list = []
    if status:
        conditions.append("recall_status = ?")
        params.append(status)
    if procedure_code:
        conditions.append("procedure_code = ?")
        params.append(procedure_code)
    if payer:
        conditions.append("payer = ?")
        params.append(payer)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY expected_followup_date ASC"

    with store._conn() as conn:  # type: ignore[attr-defined]
        rows = conn.execute(sql, tuple(params)).fetchall()

    out: list[RecallEntry] = []
    for r in rows:
        new_status = r["recall_status"]
        if refresh_status:
            new_status = compute_recall_state(
                today=today,
                expected_followup=r["expected_followup_date"],
                last_encounter_date=r["last_encounter_date"],
                current_status=r["recall_status"],
            )
            if new_status != r["recall_status"]:
                # Persist the auto-transition so subsequent queries are fast.
                with store._conn() as conn2:  # type: ignore[attr-defined]
                    conn2.execute(
                        "UPDATE recall_queue SET recall_status = ?, updated_at = ? WHERE submission_id = ?",
                        (new_status, datetime.now(timezone.utc).isoformat(), r["submission_id"]),
                    )
        try:
            ef = datetime.fromisoformat(r["expected_followup_date"]).date()
            days_overdue = max(0, (today - ef).days)
        except (ValueError, TypeError):
            days_overdue = 0
        out.append(RecallEntry(
            submission_id=r["submission_id"],
            patient_id=r["patient_id"],
            patient_name=r["patient_name"] or "",
            procedure_code=r["procedure_code"],
            procedure_name=r["procedure_name"] or "",
            payer=r["payer"] or "",
            submission_date=r["submission_date"],
            expected_followup_date=r["expected_followup_date"],
            last_encounter_date=r["last_encounter_date"],
            recall_status=new_status,
            practice_value_estimate=float(r["practice_value_estimate"] or 0),
            notes=r["notes"] or "",
            last_action_at=r["last_action_at"],
            last_action_by=r["last_action_by"],
            days_overdue=days_overdue,
        ))
    return out


def apply_action(
    submission_id: str,
    action: str,
    *,
    actor: str = "office",
    note: str = "",
    new_encounter_date: str = "",
) -> RecallEntry | None:
    """Apply a back-office action to a recall row.

    Actions: 'mark_outreach' / 'mark_scheduled' / 'remove' / 'mark_seen' /
    'reset' (back to pending — for testing).

    'mark_seen' updates last_encounter_date so the entry transitions to
    'scheduled' automatically. Other actions force a terminal status.
    """
    from cardioauth.persistence import get_store
    store = get_store()
    now_iso = datetime.now(timezone.utc).isoformat()

    with store._conn() as conn:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT * FROM recall_queue WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        if not row:
            return None

        if action == "mark_outreach":
            new_status = "outreach_attempted"
        elif action == "mark_scheduled":
            new_status = "scheduled"
        elif action == "remove":
            new_status = "removed"
        elif action == "reset":
            new_status = "pending"
        elif action == "mark_seen":
            new_status = "scheduled"
            new_encounter_date = new_encounter_date or date.today().isoformat()
        else:
            raise ValueError(f"Unknown recall action: {action}")

        new_notes = (row["notes"] or "")
        if note:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            new_notes = (new_notes + f"\n[{stamp} {actor}] {note}").strip()

        new_last_enc = new_encounter_date or row["last_encounter_date"]

        conn.execute(
            """UPDATE recall_queue SET
                recall_status = ?, notes = ?, last_encounter_date = ?,
                last_action_at = ?, last_action_by = ?, updated_at = ?
            WHERE submission_id = ?""",
            (new_status, new_notes, new_last_enc, now_iso, actor, now_iso, submission_id),
        )

    # Audit log
    try:
        store.append_audit(
            actor=actor, action=f"recall_{action}",
            subject_id=submission_id,
            detail=note or "",
        )
    except Exception:
        pass

    refreshed = list_recall_queue(refresh_status=False)
    for e in refreshed:
        if e.submission_id == submission_id:
            return e
    return None


# ── Backfill from existing submissions ─────────────────────────────────

def backfill_from_submissions() -> dict:
    """Populate the recall_queue from approved submissions that aren't there yet.

    Returns counts: {scanned, added, skipped}.
    """
    from cardioauth.persistence import get_store
    store = get_store()
    scanned = 0
    added = 0
    skipped = 0

    with store._conn() as conn:  # type: ignore[attr-defined]
        existing_ids = {
            r[0] for r in conn.execute(
                "SELECT submission_id FROM recall_queue"
            ).fetchall()
        }

    for joined in store.iter_submissions_with_outcomes():
        scanned += 1
        sub = joined.get("submission") or {}
        out = joined.get("outcome") or {}
        sub_id = sub.get("submission_id")
        outcome = (out.get("outcome") or "").lower()
        if not sub_id or not outcome.startswith("approved"):
            skipped += 1
            continue
        if sub_id in existing_ids:
            skipped += 1
            continue

        cpt = sub.get("cpt_code") or sub.get("procedure_code") or ""
        if not cpt:
            skipped += 1
            continue

        sub_date = sub.get("created_at") or sub.get("submitted_at") or datetime.now(timezone.utc).isoformat()
        upsert_recall_entry(
            submission_id=sub_id,
            patient_id=sub.get("patient_id", "") or "",
            patient_name=sub.get("patient_name", "") or "",
            procedure_code=cpt,
            procedure_name=sub.get("procedure", "") or "",
            payer=sub.get("payer", "") or "",
            submission_date=sub_date,
        )
        added += 1

    return {"scanned": scanned, "added": added, "skipped": skipped}


def queue_kpis(entries: list[RecallEntry]) -> dict:
    """Compute headline KPIs for the dashboard."""
    by_status: dict[str, int] = {}
    revenue_at_risk = 0.0
    for e in entries:
        by_status[e.recall_status] = by_status.get(e.recall_status, 0) + 1
        if e.recall_status in ("due", "overdue"):
            revenue_at_risk += e.practice_value_estimate
    return {
        "by_status": by_status,
        "total": len(entries),
        "revenue_at_risk_usd": round(revenue_at_risk, 2),
        "actionable": by_status.get("due", 0) + by_status.get("overdue", 0),
    }


def seed_demo_recalls() -> int:
    """Seed a handful of demo recall rows for the dashboard if none exist.

    Idempotent: skips when the queue already has entries.
    """
    from cardioauth.persistence import get_store
    store = get_store()
    with store._conn() as conn:  # type: ignore[attr-defined]
        existing = conn.execute("SELECT COUNT(*) FROM recall_queue").fetchone()[0]
    if existing:
        return 0

    today = date.today()
    seeds = [
        # Overdue PET — 8 months ago, no follow-up
        dict(submission_id="DEMO-RCL-001", patient_id="DEMO-001",
             patient_name="Robert J. Harmon", procedure_code="78492",
             procedure_name="Cardiac PET", payer="UnitedHealthcare",
             months_ago=8),
        # Overdue cath — 5 months ago
        dict(submission_id="DEMO-RCL-002", patient_id="DEMO-002",
             patient_name="Margaret Chen", procedure_code="93458",
             procedure_name="Left heart catheterization", payer="Aetna",
             months_ago=5),
        # Due (just past window) SPECT — 6 months ago
        dict(submission_id="DEMO-RCL-003", patient_id="DEMO-003",
             patient_name="James Rodriguez", procedure_code="78452",
             procedure_name="Cardiac SPECT", payer="UnitedHealthcare",
             months_ago=6),
        # Pending — recent stress echo, not due yet
        dict(submission_id="DEMO-RCL-004", patient_id="DEMO-004",
             patient_name="Susan Park", procedure_code="93350",
             procedure_name="Stress echocardiogram", payer="Anthem",
             months_ago=2),
        # Pending — recent CCTA
        dict(submission_id="DEMO-RCL-005", patient_id="DEMO-005",
             patient_name="Linda Walsh", procedure_code="75574",
             procedure_name="Cardiac CT angiography", payer="Medicare",
             months_ago=3),
    ]
    created = 0
    for s in seeds:
        sub_date = (today - timedelta(days=30 * s["months_ago"])).isoformat()
        upsert_recall_entry(
            submission_id=s["submission_id"],
            patient_id=s["patient_id"],
            patient_name=s["patient_name"],
            procedure_code=s["procedure_code"],
            procedure_name=s["procedure_name"],
            payer=s["payer"],
            submission_date=sub_date,
            last_encounter_date=sub_date,  # last contact was the procedure itself
        )
        created += 1
    return created
