"""PostgreSQL database client for CardioAuth (Supabase).

Handles:
  - PA submission persistence with field-level encryption for PHI
  - Audit log writes
  - Submission history queries for analytics
  - Data retention / auto-purge

PHI Encryption:
  Patient names are encrypted at rest using pgcrypto's pgp_sym_encrypt().
  The encryption key is the DATABASE_ENCRYPTION_KEY env var (or falls back
  to a derived key from DATABASE_URL). Patient IDs are stored as SHA-256
  hashes — never in plaintext.

  Fields encrypted: patient_name_enc (BYTEA)
  Fields hashed: patient_id_hash (TEXT)
  Fields NOT encrypted: procedure_code, payer, scores (needed for analytics)
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Encryption key for PHI fields — in production, use a dedicated secret
_ENC_KEY = os.environ.get("DATABASE_ENCRYPTION_KEY", "")
if not _ENC_KEY and DATABASE_URL:
    # Derive a key from the DB URL as fallback (not ideal, but better than plaintext)
    _ENC_KEY = hashlib.sha256(DATABASE_URL.encode()).hexdigest()[:32]


def _get_conn():
    """Get a PostgreSQL connection."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not configured")
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def _hash_patient_id(patient_id: str) -> str:
    """One-way hash of patient ID — never store plaintext."""
    return hashlib.sha256(patient_id.encode()).hexdigest()


# ────────────────────────────────────────────────────────────────────────
# PA Submissions
# ────────────────────────────────────────────────────────────────────────


def save_pa_submission(
    user_id: str,
    patient_id: str,
    patient_name: str,
    age: int,
    sex: str,
    payer: str,
    procedure_code: str,
    procedure_name: str,
    icd10_codes: list[str],
    extraction_engine: str = "claude",
    approval_score: float = 0.0,
    approval_label: str = "",
    criteria_met: int = 0,
    criteria_not_met: int = 0,
    criteria_total: int = 0,
    narrative_draft: str = "",
    status: str = "analyzed",
) -> str:
    """Save a PA submission to the database. Returns the submission UUID."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pa_submissions (
                user_id, patient_id_hash, patient_name_enc, age, sex,
                payer, procedure_code, procedure_name, icd10_codes,
                extraction_engine, approval_score, approval_label,
                criteria_met, criteria_not_met, criteria_total,
                narrative_draft, status
            ) VALUES (
                %s, %s, pgp_sym_encrypt(%s, %s), %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s
            ) RETURNING id
        """, (
            user_id,
            _hash_patient_id(patient_id),
            patient_name, _ENC_KEY,
            age, sex,
            payer, procedure_code, procedure_name, icd10_codes,
            extraction_engine, approval_score, approval_label,
            criteria_met, criteria_not_met, criteria_total,
            narrative_draft, status,
        ))
        row = cur.fetchone()
        submission_id = str(row[0])
        conn.commit()
        logger.info("DB: saved PA submission %s", submission_id[:8] + "***")
        return submission_id
    except Exception as e:
        conn.rollback()
        logger.error("DB: failed to save PA submission: %s", e)
        raise
    finally:
        conn.close()


def update_submission_status(
    submission_id: str,
    status: str,
    determination_at: Optional[datetime] = None,
    denial_reason: str = "",
    denial_code: str = "",
    availity_ref: str = "",
) -> bool:
    """Update the status of a PA submission (e.g., submitted → approved)."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pa_submissions SET
                status = %s,
                determination_at = %s,
                denial_reason = %s,
                denial_code = %s,
                availity_ref = %s,
                turnaround_hrs = CASE
                    WHEN %s IS NOT NULL AND submitted_at IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (%s - submitted_at)) / 3600.0
                    ELSE turnaround_hrs
                END
            WHERE id = %s::uuid
        """, (
            status, determination_at, denial_reason, denial_code,
            availity_ref, determination_at, determination_at,
            submission_id,
        ))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        conn.rollback()
        logger.error("DB: failed to update submission %s: %s", submission_id[:8], e)
        return False
    finally:
        conn.close()


def get_submission_history(
    user_id: str = "",
    payer: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Query PA submission history with optional filters."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        conditions = []
        params: list[Any] = []

        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        if payer:
            conditions.append("payer = %s")
            params.append(payer)
        if status:
            conditions.append("status = %s")
            params.append(status)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        cur.execute(f"""
            SELECT
                id, user_id,
                pgp_sym_decrypt(patient_name_enc, %s) as patient_name,
                age, sex, payer, procedure_code, procedure_name,
                icd10_codes, extraction_engine,
                approval_score, approval_label,
                criteria_met, criteria_not_met, criteria_total,
                status, submitted_at, determination_at,
                denial_reason, turnaround_hrs,
                created_at
            FROM pa_submissions
            {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, [_ENC_KEY] + params + [limit, offset])

        columns = [desc[0] for desc in cur.description]
        rows = []
        for row in cur.fetchall():
            d = dict(zip(columns, row))
            # Convert datetimes to ISO strings
            for k in ('submitted_at', 'determination_at', 'created_at'):
                if d.get(k):
                    d[k] = d[k].isoformat()
            d['id'] = str(d['id'])
            rows.append(d)
        return rows
    except Exception as e:
        logger.error("DB: failed to query submissions: %s", e)
        return []
    finally:
        conn.close()


def get_analytics_from_db(user_id: str = "") -> dict:
    """Compute real analytics from the submissions table."""
    conn = _get_conn()
    try:
        cur = conn.cursor()

        # Overall stats
        where = "WHERE user_id = %s" if user_id else ""
        params = [user_id] if user_id else []

        cur.execute(f"SELECT COUNT(*) FROM pa_submissions {where}", params)
        total = cur.fetchone()[0]

        cur.execute(f"SELECT COUNT(*) FROM pa_submissions {where} {'AND' if where else 'WHERE'} status = 'approved'",
                    params)
        approved = cur.fetchone()[0]

        cur.execute(f"SELECT COUNT(*) FROM pa_submissions {where} {'AND' if where else 'WHERE'} status = 'denied'",
                    params)
        denied = cur.fetchone()[0]

        cur.execute(f"SELECT COUNT(*) FROM pa_submissions {where} {'AND' if where else 'WHERE'} status IN ('analyzed', 'submitted', 'pending')",
                    params)
        pending = cur.fetchone()[0]

        cur.execute(f"SELECT AVG(turnaround_hrs) FROM pa_submissions {where} {'AND' if where else 'WHERE'} turnaround_hrs IS NOT NULL",
                    params)
        avg_hrs = cur.fetchone()[0]

        # By payer
        cur.execute(f"""
            SELECT payer, COUNT(*) as total,
                   SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved
            FROM pa_submissions {where}
            GROUP BY payer ORDER BY total DESC
        """, params)
        payer_breakdown = [{"payer": r[0], "total": r[1], "approved": r[2]} for r in cur.fetchall()]

        # Denial reasons
        cur.execute(f"""
            SELECT denial_reason, COUNT(*) as cnt
            FROM pa_submissions {where} {'AND' if where else 'WHERE'} denial_reason != '' AND denial_reason IS NOT NULL
            GROUP BY denial_reason ORDER BY cnt DESC LIMIT 10
        """, params)
        denial_reasons = [{"reason": r[0], "count": r[1]} for r in cur.fetchall()]

        return {
            "total_requests": total,
            "approved": approved,
            "denied": denied,
            "pending": pending,
            "approval_rate": approved / total if total > 0 else 0,
            "avg_turnaround_days": round(avg_hrs / 24, 1) if avg_hrs else 0,
            "payer_breakdown": payer_breakdown,
            "denial_reasons": denial_reasons,
            "source": "database",
        }
    except Exception as e:
        logger.error("DB: analytics query failed: %s", e)
        return {"source": "error", "error": str(e)}
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────
# Audit logs
# ────────────────────────────────────────────────────────────────────────


def save_audit_log(
    user_id: str,
    user_email: str = "",
    user_role: str = "demo",
    action: str = "",
    resource: str = "",
    detail: str = "",
    ip_address: str = "",
    method: str = "",
    path: str = "",
    status_code: int = 0,
    latency_ms: int = 0,
) -> None:
    """Write an audit log entry to the database."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO audit_logs (
                user_id, user_email, user_role, action, resource, detail,
                ip_address, method, path, status_code, latency_ms
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, user_email, user_role, action, resource, detail,
            ip_address, method, path, status_code, latency_ms,
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("DB: failed to write audit log: %s", e)
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────
# Data retention
# ────────────────────────────────────────────────────────────────────────

RETENTION_DAYS = int(os.environ.get("DATA_RETENTION_DAYS", "365"))


def purge_old_data() -> dict:
    """Delete PA submissions and audit logs older than RETENTION_DAYS.

    Call this from a scheduled task (cron / ARQ worker).
    Returns count of deleted records.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM pa_submissions
            WHERE created_at < NOW() - INTERVAL '%s days'
            RETURNING id
        """, (RETENTION_DAYS,))
        pa_deleted = cur.rowcount

        cur.execute("""
            DELETE FROM audit_logs
            WHERE timestamp < NOW() - INTERVAL '%s days'
            RETURNING id
        """, (RETENTION_DAYS,))
        audit_deleted = cur.rowcount

        conn.commit()
        logger.info("DB: purged %d submissions and %d audit logs (>%d days old)",
                     pa_deleted, audit_deleted, RETENTION_DAYS)
        return {"pa_deleted": pa_deleted, "audit_deleted": audit_deleted, "retention_days": RETENTION_DAYS}
    except Exception as e:
        conn.rollback()
        logger.error("DB: purge failed: %s", e)
        return {"error": str(e)}
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────
# Connection test
# ────────────────────────────────────────────────────────────────────────


def is_db_available() -> bool:
    """Check if the database is reachable."""
    if not DATABASE_URL:
        return False
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False
