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

    @abstractmethod
    def iter_submissions_with_outcomes(self, payer: str = "", cpt_code: str = "") -> Iterator[dict]:
        """Yield dicts with keys {submission, outcome} for every submission that
        has a recorded outcome. Optional filters on payer + cpt_code.
        Used by the criterion-outcome correlation report.
        """

    @abstractmethod
    def log_cost(
        self,
        *,
        agent: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        duration_ms: int = 0,
        endpoint: str = "",
        case_id: str = "",
    ) -> None: ...

    @abstractmethod
    def summarize_cost(self, window_hours: int = 24, agent: str = "") -> dict: ...

    @abstractmethod
    def cache_get(self, cache_key: str) -> dict | None: ...

    @abstractmethod
    def cache_set(self, cache_key: str, value: dict, ttl_seconds: int) -> None: ...

    # ── Phase C.1: SubmissionPacket archive ──

    @abstractmethod
    def save_packet(self, case_id: str, packet_json: dict, *,
                    payer: str = "", resolved_cpt: str = "",
                    decision: str = "", reviewer_recommendation: str = "",
                    finding_count: int = 0,
                    highest_finding_severity: str = "",
                    taxonomy_version: str = "",
                    form_schema_version: str = "",
                    model_version: str = "") -> None: ...

    @abstractmethod
    def get_packet(self, case_id: str) -> dict | None: ...

    @abstractmethod
    def list_packets(self, *, payer: str = "", resolved_cpt: str = "",
                     decision: str = "", limit: int = 50) -> list[dict]: ...

    # ── Shadow-testing helpers ──

    @abstractmethod
    def save_shadow_review(self, review: dict) -> str:
        """Persist one shadow-testing review. Returns the review_id."""

    @abstractmethod
    def list_shadow_reviews(self, *, limit: int = 50) -> list[dict]:
        """Recent shadow reviews, newest first."""

    @abstractmethod
    def shadow_review_stats(self) -> dict:
        """Aggregate agreement / edit / reject rates across all reviews."""

    # ── Outcome dashboard helpers ──

    @abstractmethod
    def list_pending_outcome_submissions(
        self, *, payer: str = "", cpt_code: str = "", limit: int = 100,
    ) -> list[dict]:
        """Submissions that have been transmitted but have no recorded
        outcome yet. Ordered by oldest-first so staff work the queue."""

    @abstractmethod
    def list_all_rolling_stats(self) -> list[dict]:
        """Every (payer, cpt) rollup with at least one recorded outcome."""

    @abstractmethod
    def count_outcomes(self) -> dict:
        """Total counts across all recorded outcomes — approval rate, denial
        rate, info-requested rate. Backs the headline stat panel."""


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

-- Cost accounting per Claude call. Every agent writes here after every
-- Anthropic request so we can see which feature is burning spend.
CREATE TABLE IF NOT EXISTS cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    endpoint TEXT,
    agent TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    case_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_cost_log_time ON cost_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_cost_log_agent ON cost_log(agent, timestamp DESC);

-- TTL response cache. Used by POLICY_AGENT and similar idempotent-ish
-- calls to skip redundant Claude round-trips within a short window.
CREATE TABLE IF NOT EXISTS response_cache (
    cache_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_response_cache_expiry ON response_cache(expires_at);

-- Recall queue: patients who had a procedure and may need follow-up.
-- Computed from approved submissions + procedure-specific follow-up windows
-- + (Tier 1) manually maintained encounter dates. Tier 2 will replace
-- last_encounter_date with Epic FHIR Encounter sync.
CREATE TABLE IF NOT EXISTS recall_queue (
    submission_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    patient_name TEXT,
    procedure_code TEXT NOT NULL,
    procedure_name TEXT,
    payer TEXT,
    submission_date TEXT NOT NULL,
    expected_followup_date TEXT NOT NULL,
    last_encounter_date TEXT,
    recall_status TEXT NOT NULL DEFAULT 'pending',
    practice_value_estimate REAL DEFAULT 0,
    notes TEXT,
    last_action_at TEXT,
    last_action_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recall_status ON recall_queue(recall_status, expected_followup_date);
CREATE INDEX IF NOT EXISTS idx_recall_patient ON recall_queue(patient_id, expected_followup_date DESC);

-- Frozen submission packets: one row per assembled SubmissionPacket.
-- Phase C.1 — every verdict is reproducible from this row alone.
-- The full typed packet (including evidence graph, form fields, findings,
-- reviewer verdict) lives in packet_json. Indexed columns are projected
-- out for query speed.
CREATE TABLE IF NOT EXISTS submission_packets (
    case_id TEXT PRIMARY KEY,
    packet_json TEXT NOT NULL,
    payer TEXT,
    resolved_cpt TEXT,
    decision TEXT,                       -- transmit | hold_for_review | block
    reviewer_recommendation TEXT,        -- transmit | hold | block | "" (empty when reviewer skipped)
    finding_count INTEGER NOT NULL DEFAULT 0,
    highest_finding_severity TEXT,       -- info | low | medium | high | blocking
    taxonomy_version TEXT,
    form_schema_version TEXT,
    model_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_packets_payer_cpt ON submission_packets(payer, resolved_cpt, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_packets_decision ON submission_packets(decision, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_packets_severity ON submission_packets(highest_finding_severity);

-- Shadow-testing reviews. Staff runs a real case through the pipeline,
-- reviews the produced packet against what they would have submitted
-- manually, and logs the diff. Aggregate over 20-25 cases gives us the
-- agreement rate Peter wants to see before production rollout. Captures
-- staff feedback, never submits anything, never modifies the chart.
CREATE TABLE IF NOT EXISTS shadow_reviews (
    review_id TEXT PRIMARY KEY,
    case_id TEXT,                        -- links to lean_run_result if known
    payer TEXT,
    cpt_code TEXT,
    patient_id TEXT,
    cardio_decision TEXT,                -- what CardioAuth recommended (transmit/hold/block)
    cardio_score REAL,
    cardio_label TEXT,                   -- HIGH/MEDIUM/LOW/INSUFFICIENT
    submission_outcome TEXT NOT NULL,    -- submitted_as_is | submitted_with_edits | did_not_submit
    edits_summary TEXT,                  -- what staff changed (if any)
    not_submitted_reason TEXT,           -- why staff didn't submit (if any)
    confidence_score INTEGER,            -- staff's 1-5 confidence in CardioAuth's output
    notes TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_reviewer ON shadow_reviews(reviewer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_payer_cpt ON shadow_reviews(payer, cpt_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_outcome ON shadow_reviews(submission_outcome, created_at DESC);
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

    # ── criterion-outcome correlation feed ─────────────────────────────

    # ── cost logging ───────────────────────────────────────────────────

    def log_cost(
        self,
        *,
        agent: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        duration_ms: int = 0,
        endpoint: str = "",
        case_id: str = "",
    ) -> None:
        """Append a cost-log row. Called from every agent after a Claude call."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO cost_log (timestamp, endpoint, agent, model,
                     input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                     duration_ms, case_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._now(), endpoint, agent, model,
                 int(input_tokens), int(output_tokens),
                 int(cache_read_tokens), int(cache_creation_tokens),
                 int(duration_ms), case_id),
            )

    def summarize_cost(self, window_hours: int = 24, agent: str = "") -> dict:
        """Roll up cost-log rows within the given time window."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        params: list[Any] = [cutoff]
        agent_clause = ""
        if agent:
            agent_clause = " AND agent = ?"
            params.append(agent)
        with self._conn() as conn:
            totals = conn.execute(
                f"""SELECT
                      COUNT(*) AS n,
                      COALESCE(SUM(input_tokens),0) AS in_tok,
                      COALESCE(SUM(output_tokens),0) AS out_tok,
                      COALESCE(SUM(cache_read_tokens),0) AS cache_read,
                      COALESCE(SUM(cache_creation_tokens),0) AS cache_create,
                      COALESCE(AVG(duration_ms),0) AS avg_ms
                    FROM cost_log WHERE timestamp >= ?{agent_clause}""",
                tuple(params),
            ).fetchone()
            per_agent = conn.execute(
                f"""SELECT agent,
                      COUNT(*) AS n,
                      COALESCE(SUM(input_tokens),0) AS in_tok,
                      COALESCE(SUM(output_tokens),0) AS out_tok,
                      COALESCE(SUM(cache_read_tokens),0) AS cache_read,
                      COALESCE(SUM(cache_creation_tokens),0) AS cache_create
                    FROM cost_log WHERE timestamp >= ?{agent_clause}
                    GROUP BY agent ORDER BY in_tok DESC""",
                tuple(params),
            ).fetchall()
        agents = [
            {
                "agent": r["agent"],
                "calls": r["n"],
                "input_tokens": r["in_tok"],
                "output_tokens": r["out_tok"],
                "cache_read_tokens": r["cache_read"],
                "cache_creation_tokens": r["cache_create"],
                # Hit rate: fraction of input tokens served from cache
                "cache_hit_rate": round(
                    r["cache_read"] / (r["in_tok"] + r["cache_read"]) if (r["in_tok"] + r["cache_read"]) else 0.0,
                    3,
                ),
            }
            for r in per_agent
        ]
        return {
            "window_hours": window_hours,
            "agent_filter": agent or "ALL",
            "total_calls": totals["n"],
            "total_input_tokens": totals["in_tok"],
            "total_output_tokens": totals["out_tok"],
            "total_cache_read_tokens": totals["cache_read"],
            "total_cache_creation_tokens": totals["cache_create"],
            "avg_duration_ms": round(totals["avg_ms"], 1) if totals["avg_ms"] else 0,
            "per_agent": agents,
        }

    # ── TTL response cache ─────────────────────────────────────────────

    def cache_get(self, cache_key: str) -> dict | None:
        """Return the cached value if still within TTL, else None."""
        now = self._now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value_json, expires_at FROM response_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= now:
            # Expired — delete and return None
            with self._conn() as conn:
                conn.execute("DELETE FROM response_cache WHERE cache_key = ?", (cache_key,))
            return None
        return json.loads(row["value_json"])

    def cache_set(self, cache_key: str, value: dict, ttl_seconds: int) -> None:
        """Write a cache entry. Overwrites any existing entry at the same key."""
        from datetime import timedelta
        now_dt = datetime.now(timezone.utc)
        expires = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO response_cache (cache_key, value_json, created_at, expires_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                     value_json = excluded.value_json,
                     created_at = excluded.created_at,
                     expires_at = excluded.expires_at""",
                (cache_key, json.dumps(value, default=str), now_dt.isoformat(), expires),
            )

    # ── Phase C.1: SubmissionPacket archive ──

    def save_packet(
        self, case_id: str, packet_json: dict, *,
        payer: str = "", resolved_cpt: str = "",
        decision: str = "", reviewer_recommendation: str = "",
        finding_count: int = 0,
        highest_finding_severity: str = "",
        taxonomy_version: str = "",
        form_schema_version: str = "",
        model_version: str = "",
    ) -> None:
        """Freeze a SubmissionPacket. Indexed projections are derived
        once at write time; the full packet lives in packet_json."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO submission_packets (
                       case_id, packet_json, payer, resolved_cpt, decision,
                       reviewer_recommendation, finding_count,
                       highest_finding_severity, taxonomy_version,
                       form_schema_version, model_version,
                       created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(case_id) DO UPDATE SET
                       packet_json = excluded.packet_json,
                       payer = excluded.payer,
                       resolved_cpt = excluded.resolved_cpt,
                       decision = excluded.decision,
                       reviewer_recommendation = excluded.reviewer_recommendation,
                       finding_count = excluded.finding_count,
                       highest_finding_severity = excluded.highest_finding_severity,
                       taxonomy_version = excluded.taxonomy_version,
                       form_schema_version = excluded.form_schema_version,
                       model_version = excluded.model_version,
                       updated_at = excluded.updated_at""",
                (
                    case_id, json.dumps(packet_json, default=str),
                    payer, resolved_cpt, decision, reviewer_recommendation,
                    finding_count, highest_finding_severity,
                    taxonomy_version, form_schema_version, model_version,
                    now_iso, now_iso,
                ),
            )

    def get_packet(self, case_id: str) -> dict | None:
        """Return the frozen packet (full JSON + indexed columns) or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submission_packets WHERE case_id = ?",
                (case_id,),
            ).fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out["packet"] = json.loads(out.pop("packet_json"))
        except (json.JSONDecodeError, TypeError):
            out["packet"] = None
        return out

    def list_packets(
        self, *, payer: str = "", resolved_cpt: str = "",
        decision: str = "", limit: int = 50,
    ) -> list[dict]:
        """List frozen packets, most-recent first. Returns the indexed
        projections + a small preview (first 200 chars of packet_json);
        callers fetch the full packet via get_packet(case_id)."""
        sql = "SELECT * FROM submission_packets"
        conditions: list[str] = []
        params: list[Any] = []
        if payer:
            conditions.append("payer = ?")
            params.append(payer)
        if resolved_cpt:
            conditions.append("resolved_cpt = ?")
            params.append(resolved_cpt)
        if decision:
            conditions.append("decision = ?")
            params.append(decision)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        out: list[dict] = []
        for row in rows:
            d = dict(row)
            d.pop("packet_json", None)  # don't ship the full payload in list views
            out.append(d)
        return out

    def iter_submissions_with_outcomes(
        self,
        payer: str = "",
        cpt_code: str = "",
    ) -> Iterator[dict]:
        """Yield {submission, outcome} for every submission that has an outcome."""
        query = """
            SELECT s.data_json AS sub_json, o.data_json AS out_json, o.outcome, o.payer, o.cpt_code
            FROM outcomes o
            JOIN submissions s ON s.submission_id = o.submission_id
        """
        conditions = []
        params: list[Any] = []
        if payer:
            conditions.append("o.payer = ?")
            params.append(payer)
        if cpt_code:
            conditions.append("o.cpt_code = ?")
            params.append(cpt_code)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        for row in rows:
            yield {
                "submission": json.loads(row["sub_json"]),
                "outcome": json.loads(row["out_json"]),
            }

    # ── outcome dashboard helpers ──────────────────────────────────────

    def list_pending_outcome_submissions(
        self, *, payer: str = "", cpt_code: str = "", limit: int = 100,
    ) -> list[dict]:
        sql = """
            SELECT s.submission_id, s.data_json, s.status, s.created_at
            FROM submissions s
            LEFT JOIN outcomes o ON o.submission_id = s.submission_id
            WHERE o.submission_id IS NULL
              AND s.status IN ('submitted', 'pending', 'info_requested')
        """
        params: list[Any] = []
        if payer or cpt_code:
            sql += " AND ("
            cond: list[str] = []
            if payer:
                cond.append("json_extract(s.data_json, '$.payer') = ?")
                params.append(payer)
            if cpt_code:
                cond.append(
                    "(json_extract(s.data_json, '$.cpt_code') = ? "
                    "OR json_extract(s.data_json, '$.procedure_code') = ?)"
                )
                params.extend([cpt_code, cpt_code])
            sql += " AND ".join(cond) + ")"
        sql += " ORDER BY s.created_at ASC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        out: list[dict] = []
        now = datetime.now(timezone.utc)
        for row in rows:
            data = json.loads(row["data_json"])
            created_at = row["created_at"]
            age_days: int | None = None
            try:
                age_days = (now - datetime.fromisoformat(created_at)).days
            except Exception:
                age_days = None
            out.append({
                "submission_id": row["submission_id"],
                "status": row["status"],
                "submitted_at": created_at,
                "age_days": age_days,
                "patient_id": data.get("patient_id", ""),
                "patient_name": data.get("patient_name", ""),
                "payer": data.get("payer", ""),
                "cpt_code": data.get("cpt_code", "") or data.get("procedure_code", ""),
                "procedure": data.get("procedure", ""),
                "approval_label": data.get("approval_label", ""),
                "approval_score": data.get("approval_score"),
            })
        return out

    def list_all_rolling_stats(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT payer, cpt_code, total, approved, denied, pending,
                          last_outcome_at
                   FROM stats_rollup
                   WHERE total > 0
                   ORDER BY total DESC""",
            ).fetchall()
        return [
            {
                "payer": r["payer"],
                "cpt_code": r["cpt_code"],
                "total": r["total"],
                "approved": r["approved"],
                "denied": r["denied"],
                "pending": r["pending"],
                "approval_rate": round(r["approved"] / r["total"], 3) if r["total"] else None,
                "last_outcome_at": r["last_outcome_at"],
            }
            for r in rows
        ]

    def count_outcomes(self) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*) AS total,
                     SUM(CASE WHEN outcome = 'APPROVED' THEN 1 ELSE 0 END) AS approved,
                     SUM(CASE WHEN outcome = 'DENIED' THEN 1 ELSE 0 END) AS denied,
                     SUM(CASE WHEN outcome IN ('PENDING','INFO_REQUESTED') THEN 1 ELSE 0 END) AS pending,
                     MAX(created_at) AS last_outcome_at
                   FROM outcomes""",
            ).fetchone()
        total = int(row["total"] or 0)
        approved = int(row["approved"] or 0)
        denied = int(row["denied"] or 0)
        pending = int(row["pending"] or 0)
        return {
            "total": total,
            "approved": approved,
            "denied": denied,
            "pending": pending,
            "approval_rate": round(approved / total, 3) if total else None,
            "denial_rate": round(denied / total, 3) if total else None,
            "last_outcome_at": row["last_outcome_at"],
        }

    # ── shadow-testing helpers ─────────────────────────────────────────

    def save_shadow_review(self, review: dict) -> str:
        import uuid as _uuid
        review_id = review.get("review_id") or _uuid.uuid4().hex[:12]
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO shadow_reviews
                   (review_id, case_id, payer, cpt_code, patient_id,
                    cardio_decision, cardio_score, cardio_label,
                    submission_outcome, edits_summary, not_submitted_reason,
                    confidence_score, notes, reviewer_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id,
                    review.get("case_id", ""),
                    review.get("payer", ""),
                    review.get("cpt_code", ""),
                    review.get("patient_id", ""),
                    review.get("cardio_decision", ""),
                    float(review.get("cardio_score") or 0),
                    review.get("cardio_label", ""),
                    review.get("submission_outcome", ""),
                    review.get("edits_summary", ""),
                    review.get("not_submitted_reason", ""),
                    int(review.get("confidence_score") or 0),
                    review.get("notes", ""),
                    review.get("reviewer_id", ""),
                    self._now(),
                ),
            )
        return review_id

    def list_shadow_reviews(self, *, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM shadow_reviews
                   ORDER BY created_at DESC LIMIT ?""",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def shadow_review_stats(self) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*) AS total,
                     SUM(CASE WHEN submission_outcome = 'submitted_as_is' THEN 1 ELSE 0 END) AS as_is,
                     SUM(CASE WHEN submission_outcome = 'submitted_with_edits' THEN 1 ELSE 0 END) AS edited,
                     SUM(CASE WHEN submission_outcome = 'did_not_submit' THEN 1 ELSE 0 END) AS rejected,
                     AVG(confidence_score) AS avg_confidence,
                     MAX(created_at) AS last_review_at
                   FROM shadow_reviews""",
            ).fetchone()
            by_payer = conn.execute(
                """SELECT payer,
                          COUNT(*) AS total,
                          SUM(CASE WHEN submission_outcome = 'submitted_as_is' THEN 1 ELSE 0 END) AS as_is
                   FROM shadow_reviews
                   WHERE payer != ''
                   GROUP BY payer
                   ORDER BY total DESC""",
            ).fetchall()
        total = int(row["total"] or 0)
        as_is = int(row["as_is"] or 0)
        edited = int(row["edited"] or 0)
        rejected = int(row["rejected"] or 0)
        return {
            "total": total,
            "submitted_as_is": as_is,
            "submitted_with_edits": edited,
            "did_not_submit": rejected,
            "agreement_rate": round(as_is / total, 3) if total else None,
            "edit_rate": round(edited / total, 3) if total else None,
            "reject_rate": round(rejected / total, 3) if total else None,
            "avg_confidence": round(float(row["avg_confidence"] or 0), 2) if total else None,
            "last_review_at": row["last_review_at"],
            "by_payer": [
                {"payer": r["payer"], "total": r["total"],
                 "agreement_rate": round(r["as_is"] / r["total"], 3) if r["total"] else None}
                for r in by_payer
            ],
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
