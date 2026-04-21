"""Durable storage for reviews, submissions, and outcomes.

Replaces the in-memory `_reviews: dict` that lost state on container restart.

Backend selection:
  - If DATABASE_URL is set → PostgresStore (Supabase / Railway Postgres).
  - Otherwise → SQLiteStore at CARDIOAUTH_DB_PATH (default data/cardioauth.db).

Both backends expose the same Store interface so the caller is
backend-agnostic. SQLite is the default because it works anywhere with
zero config — including the current Railway deployment while Supabase
creds are still broken.

Design rules:
  - PHI-aware. Values are JSON-serialized and encrypted-at-rest is the
    caller's responsibility (disk-level on SQLite, built-in on Postgres).
  - Append-only for audit-critical tables. No UPDATE on the audit log.
  - Every write is idempotent on primary key.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Store interface
# ────────────────────────────────────────────────────────────────────────


class Store(ABC):
    """Abstract storage interface shared by all backends."""

    @abstractmethod
    def save_review(self, review_id: str, review_json: dict, user_id: str = "") -> None: ...

    @abstractmethod
    def get_review(self, review_id: str) -> dict | None: ...

    @abstractmethod
    def list_reviews(self, user_id: str | None = None, limit: int = 50) -> list[dict]: ...

    @abstractmethod
    def save_submission(self, submission_id: str, submission_json: dict, review_id: str = "") -> None: ...

    @abstractmethod
    def get_submission(self, submission_id: str) -> dict | None: ...

    @abstractmethod
    def update_submission_status(self, submission_id: str, status: str, note: str = "") -> None: ...

    @abstractmethod
    def save_outcome(self, submission_id: str, outcome_json: dict) -> None: ...

    @abstractmethod
    def get_outcome(self, submission_id: str) -> dict | None: ...

    @abstractmethod
    def append_audit(self, actor: str, action: str, subject_id: str, detail: str = "") -> None: ...

    @abstractmethod
    def record_outcome_for_stats(self, payer: str, cpt_code: str, outcome: str) -> None: ...

    @abstractmethod
    def get_rolling_stats(self, payer: str, cpt_code: str, window_days: int = 90) -> dict | None: ...


# ────────────────────────────────────────────────────────────────────────
# SQLite backend (default)
# ────────────────────────────────────────────────────────────────────────


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    user_id TEXT,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_user ON reviews(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS submissions (
    submission_id TEXT PRIMARY KEY,
    review_id TEXT,
    data_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_review ON submissions(review_id);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status, created_at DESC);

CREATE TABLE IF NOT EXISTS outcomes (
    submission_id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    outcome TEXT NOT NULL,
    payer TEXT,
    cpt_code TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_payer_cpt ON outcomes(payer, cpt_code, created_at DESC);

-- Append-only audit log.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_subject ON audit_log(subject_id, timestamp DESC);

-- Rolling stats derived from outcomes. Not the source of truth, a cache.
CREATE TABLE IF NOT EXISTS stats_rollup (
    payer TEXT NOT NULL,
    cpt_code TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    approved INTEGER NOT NULL DEFAULT 0,
    denied INTEGER NOT NULL DEFAULT 0,
    pending INTEGER NOT NULL DEFAULT 0,
    last_outcome_at TEXT,
    PRIMARY KEY (payer, cpt_code)
);
"""


class SQLiteStore(Store):
    """SQLite-backed store. Safe for single-container deployments."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
            finally:
                conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── reviews ────────────────────────────────────────────────────────

    def save_review(self, review_id: str, review_json: dict, user_id: str = "") -> None:
        now = self._now()
        blob = json.dumps(review_json, default=str)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO reviews (review_id, user_id, data_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(review_id) DO UPDATE SET
                     data_json=excluded.data_json, updated_at=excluded.updated_at""",
                (review_id, user_id, blob, now, now),
            )

    def get_review(self, review_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data_json FROM reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        return json.loads(row["data_json"]) if row else None

    def list_reviews(self, user_id: str | None = None, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT review_id, data_json, created_at FROM reviews WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT review_id, data_json, created_at FROM reviews ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {"review_id": r["review_id"], "created_at": r["created_at"], **json.loads(r["data_json"])}
            for r in rows
        ]

    # ── submissions ────────────────────────────────────────────────────

    def save_submission(self, submission_id: str, submission_json: dict, review_id: str = "") -> None:
        now = self._now()
        blob = json.dumps(submission_json, default=str)
        status = submission_json.get("status", "submitted")
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO submissions (submission_id, review_id, data_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(submission_id) DO UPDATE SET
                     data_json=excluded.data_json, status=excluded.status, updated_at=excluded.updated_at""",
                (submission_id, review_id, blob, status, now, now),
            )

    def get_submission(self, submission_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data_json FROM submissions WHERE submission_id = ?", (submission_id,)
            ).fetchone()
        return json.loads(row["data_json"]) if row else None

    def update_submission_status(self, submission_id: str, status: str, note: str = "") -> None:
        now = self._now()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT data_json FROM submissions WHERE submission_id = ?", (submission_id,)
            ).fetchone()
            if not existing:
                return
            data = json.loads(existing["data_json"])
            data["status"] = status
            audit = data.setdefault("audit_trail", [])
            audit.append({
                "action": f"status_changed_to_{status}",
                "timestamp": now,
                "actor": "SUBMISSION_AGENT",
                "note": note,
            })
            conn.execute(
                "UPDATE submissions SET data_json = ?, status = ?, updated_at = ? WHERE submission_id = ?",
                (json.dumps(data, default=str), status, now, submission_id),
            )

    # ── outcomes ───────────────────────────────────────────────────────

    def save_outcome(self, submission_id: str, outcome_json: dict) -> None:
        now = self._now()
        blob = json.dumps(outcome_json, default=str)
        outcome_status = outcome_json.get("outcome", "UNKNOWN")
        payer = outcome_json.get("payer", "") or ""
        cpt_code = outcome_json.get("cpt_code", "") or ""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO outcomes (submission_id, data_json, outcome, payer, cpt_code, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(submission_id) DO UPDATE SET
                     data_json=excluded.data_json, outcome=excluded.outcome""",
                (submission_id, blob, outcome_status, payer, cpt_code, now),
            )

    def get_outcome(self, submission_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data_json FROM outcomes WHERE submission_id = ?", (submission_id,)
            ).fetchone()
        return json.loads(row["data_json"]) if row else None

    # ── audit ──────────────────────────────────────────────────────────

    def append_audit(self, actor: str, action: str, subject_id: str, detail: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (timestamp, actor, action, subject_id, detail) VALUES (?, ?, ?, ?, ?)",
                (self._now(), actor, action, subject_id, detail),
            )

    # ── rolling stats ──────────────────────────────────────────────────

    def record_outcome_for_stats(self, payer: str, cpt_code: str, outcome: str) -> None:
        now = self._now()
        outcome_upper = (outcome or "").upper()
        is_approved = int(outcome_upper == "APPROVED")
        is_denied = int(outcome_upper == "DENIED")
        is_pending = int(outcome_upper in ("PENDING", "INFO_REQUESTED"))
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO stats_rollup (payer, cpt_code, total, approved, denied, pending, last_outcome_at)
                   VALUES (?, ?, 1, ?, ?, ?, ?)
                   ON CONFLICT(payer, cpt_code) DO UPDATE SET
                     total = total + 1,
                     approved = approved + ?,
                     denied = denied + ?,
                     pending = pending + ?,
                     last_outcome_at = ?""",
                (payer, cpt_code, is_approved, is_denied, is_pending, now,
                 is_approved, is_denied, is_pending, now),
            )

    def get_rolling_stats(self, payer: str, cpt_code: str, window_days: int = 90) -> dict | None:
        """Return the running rollup if any outcomes have been recorded."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT total, approved, denied, pending, last_outcome_at FROM stats_rollup WHERE payer = ? AND cpt_code = ?",
                (payer, cpt_code),
            ).fetchone()
        if not row or row["total"] == 0:
            return None
        total = row["total"]
        return {
            "payer": payer,
            "cpt_code": cpt_code,
            "total": total,
            "approved": row["approved"],
            "denied": row["denied"],
            "pending": row["pending"],
            "approval_rate": round(row["approved"] / total, 3) if total else None,
            "last_outcome_at": row["last_outcome_at"],
            "source": "live_rollup",
        }


# ────────────────────────────────────────────────────────────────────────
# Factory
# ────────────────────────────────────────────────────────────────────────


_store_singleton: Store | None = None


def get_store() -> Store:
    """Return the process-wide Store instance, creating it on first call."""
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton

    # Postgres hook — when Supabase / Railway Postgres creds work.
    # For now we're staying on SQLite since Supabase is broken in prod.
    # When ready, implement PostgresStore and switch here based on env.

    db_path = os.environ.get(
        "CARDIOAUTH_DB_PATH",
        "/app/data/cardioauth.db" if os.path.isdir("/app/data") else "data/cardioauth.db",
    )
    logger.info("Persistence: using SQLiteStore at %s", db_path)
    _store_singleton = SQLiteStore(db_path)
    return _store_singleton
