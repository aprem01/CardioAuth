"""Training data store — gold-standard labeled cases for CardioAuth.

The goal: build a dataset of physician-verified "correct" PA evaluations
that can be used to:

  1. Regression-test every code change (automatic)
  2. Few-shot prompt examples (in-context)
  3. Fine-tune a classifier (supervised learning) at scale

Each training case contains:
  - The full clinical note
  - Procedure, payer, CPT
  - Gold-standard criterion labels (per criterion: met/not_met/not_applicable)
  - Gold-standard evidence quotes
  - Actual payer outcome (approved/denied) if known
  - Who labeled it + when

Storage: Postgres `training_cases` + Pinecone `training-gold` namespace +
local JSONL backup at data/training_cases.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TRAINING_FILE = Path(os.environ.get(
    "TRAINING_PATH",
    Path(__file__).parent.parent / "data" / "training_cases.jsonl",
))


@dataclass
class CriterionLabel:
    code: str                           # e.g. "EX-001"
    gold_status: str                    # "met" | "not_met" | "not_applicable"
    gold_evidence: str = ""             # Verbatim quote from note supporting the label
    physician_note: str = ""            # Free-text explanation


@dataclass
class TrainingCase:
    case_id: str
    title: str                          # Short human-readable label
    procedure_code: str
    procedure_name: str
    payer: str
    raw_note: str                       # Full clinical note
    actual_outcome: str = "unknown"     # "approved" | "denied" | "unknown"
    gold_approval_label: str = ""       # Physician-assigned label (HIGH/MEDIUM/LOW)
    gold_approval_score: float = 0.0    # 0-1 if known
    criterion_labels: list[CriterionLabel] = field(default_factory=list)
    labeled_by: str = "unknown"         # User ID of labeler
    labeled_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "manual"              # "manual" | "peter-v2" | "import" | "historical"
    notes: str = ""                     # Physician's free-form notes about the case

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ────────────────────────────────────────────────────────────────────────
# Persistence
# ────────────────────────────────────────────────────────────────────────


def _ensure_table():
    """Create training_cases table if it doesn't exist. Best-effort."""
    try:
        from cardioauth.db import _get_conn, is_db_available
        if not is_db_available():
            return
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS training_cases (
                    case_id         TEXT PRIMARY KEY,
                    title           TEXT,
                    procedure_code  TEXT,
                    procedure_name  TEXT,
                    payer           TEXT,
                    raw_note        TEXT,
                    actual_outcome  TEXT DEFAULT 'unknown',
                    gold_approval_label TEXT DEFAULT '',
                    gold_approval_score FLOAT DEFAULT 0,
                    criterion_labels JSONB DEFAULT '[]',
                    labeled_by      TEXT DEFAULT 'unknown',
                    labeled_at      TIMESTAMPTZ DEFAULT NOW(),
                    created_at      TIMESTAMPTZ DEFAULT NOW(),
                    source          TEXT DEFAULT 'manual',
                    notes           TEXT DEFAULT ''
                )
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Failed to ensure training_cases table: %s", e)


def save_training_case(case: TrainingCase) -> bool:
    """Persist a training case across backends. Returns True if any succeeded."""
    success = False

    # JSONL (always — local backup)
    try:
        _TRAINING_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Append or update by case_id — load, upsert, rewrite
        existing = get_all_training_cases()
        existing_dict = {c["case_id"]: c for c in existing}
        existing_dict[case.case_id] = case.to_dict()
        with open(_TRAINING_FILE, "w") as f:
            for c in existing_dict.values():
                f.write(json.dumps(c) + "\n")
        success = True
    except Exception as e:
        logger.warning("Failed to write training JSONL: %s", e)

    # Postgres
    try:
        from cardioauth.db import _get_conn, is_db_available
        if is_db_available():
            _ensure_table()
            conn = _get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO training_cases (
                        case_id, title, procedure_code, procedure_name, payer,
                        raw_note, actual_outcome, gold_approval_label, gold_approval_score,
                        criterion_labels, labeled_by, labeled_at, source, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (case_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        gold_approval_label = EXCLUDED.gold_approval_label,
                        gold_approval_score = EXCLUDED.gold_approval_score,
                        criterion_labels = EXCLUDED.criterion_labels,
                        labeled_by = EXCLUDED.labeled_by,
                        labeled_at = EXCLUDED.labeled_at,
                        notes = EXCLUDED.notes
                """, (
                    case.case_id, case.title, case.procedure_code, case.procedure_name,
                    case.payer, case.raw_note, case.actual_outcome,
                    case.gold_approval_label, case.gold_approval_score,
                    json.dumps([asdict(l) for l in case.criterion_labels]),
                    case.labeled_by, case.labeled_at, case.source, case.notes,
                ))
                conn.commit()
                success = True
            finally:
                conn.close()
    except Exception as e:
        logger.warning("Failed to write training case to DB: %s", e)

    return success


def get_all_training_cases() -> list[dict]:
    """Load all training cases from JSONL."""
    if not _TRAINING_FILE.exists():
        return []
    out = []
    try:
        with open(_TRAINING_FILE) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("Failed to read training JSONL: %s", e)
    return out


def get_training_case(case_id: str) -> dict | None:
    """Fetch a single training case by id."""
    for c in get_all_training_cases():
        if c.get("case_id") == case_id:
            return c
    return None


# ────────────────────────────────────────────────────────────────────────
# Evaluation: how well does the system match physician gold standard?
# ────────────────────────────────────────────────────────────────────────


def evaluate_case_against_gold(
    training_case: dict,
    reasoner_output: dict,
) -> dict:
    """Compare reasoner output against gold labels. Returns per-criterion scorecard."""
    gold_labels = {
        lbl["code"]: lbl["gold_status"]
        for lbl in training_case.get("criterion_labels", [])
    }

    reasoner_matches = reasoner_output.get("criterion_matches", [])
    reasoner_labels = {
        m.get("code"): m.get("status")
        for m in reasoner_matches
    }

    agreement = 0
    disagreement = 0
    missing = 0
    details = []
    for code, gold_status in gold_labels.items():
        reasoner_status = reasoner_labels.get(code)
        if reasoner_status is None:
            missing += 1
            details.append({"code": code, "verdict": "missing", "gold": gold_status, "reasoner": None})
        elif reasoner_status == gold_status:
            agreement += 1
            details.append({"code": code, "verdict": "match", "gold": gold_status, "reasoner": reasoner_status})
        else:
            disagreement += 1
            details.append({"code": code, "verdict": "mismatch", "gold": gold_status, "reasoner": reasoner_status})

    total = len(gold_labels)
    accuracy = agreement / total if total > 0 else 0.0

    gold_score = training_case.get("gold_approval_score", 0.0)
    reasoner_score = reasoner_output.get("approval_likelihood", {}).get("score", 0.0)
    score_delta = abs(gold_score - reasoner_score)

    return {
        "case_id": training_case.get("case_id"),
        "total_criteria": total,
        "agreement": agreement,
        "disagreement": disagreement,
        "missing": missing,
        "accuracy": round(accuracy, 3),
        "gold_approval_score": gold_score,
        "reasoner_approval_score": reasoner_score,
        "score_delta": round(score_delta, 3),
        "details": details,
    }


def get_training_accuracy_stats() -> dict:
    """Aggregate statistics across all labeled training cases."""
    cases = get_all_training_cases()
    by_criterion = {}  # code -> {"total": N, "met": M, "not_met": M, "not_applicable": M}
    for c in cases:
        for lbl in c.get("criterion_labels", []):
            code = lbl.get("code")
            if not code:
                continue
            by_criterion.setdefault(code, {"total": 0, "met": 0, "not_met": 0, "not_applicable": 0})
            by_criterion[code]["total"] += 1
            status = lbl.get("gold_status", "")
            if status in by_criterion[code]:
                by_criterion[code][status] += 1

    return {
        "total_cases": len(cases),
        "total_labels": sum(v["total"] for v in by_criterion.values()),
        "by_criterion": by_criterion,
        "by_procedure": _count_by_key(cases, "procedure_code"),
        "by_outcome": _count_by_key(cases, "actual_outcome"),
    }


def _count_by_key(items: list[dict], key: str) -> dict:
    out = {}
    for item in items:
        v = item.get(key, "unknown") or "unknown"
        out[v] = out.get(v, 0) + 1
    return out
