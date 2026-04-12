"""Physician feedback store — RLHF-lite for CardioAuth.

When a physician reviews a case and flags a criterion as incorrectly
evaluated, we record:
  - Which criterion was wrong
  - What the system said vs what it should say
  - The relevant note excerpt
  - The reason (optional)

Storage tiers (in priority order):
  1. Postgres `criterion_corrections` table (if DB is available)
  2. Pinecone `feedback` namespace (vector-embedded for similarity retrieval)
  3. JSONL file fallback (./data/corrections.jsonl)

The corrections are then:
  - Retrieved during new PA cases (via similarity) and injected as
    "past mistakes to avoid" in the UnifiedReasoner prompt
  - Over time, enough corrections form a training dataset for a
    supervised fine-tuned criterion classifier

This is what makes CardioAuth learn from every cardiologist review.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_FEEDBACK_FILE = Path(os.environ.get(
    "FEEDBACK_PATH",
    Path(__file__).parent.parent / "data" / "corrections.jsonl",
))


@dataclass
class CriterionCorrection:
    """One physician correction on a single criterion evaluation."""
    correction_id: str
    case_id: str
    user_id: str
    procedure_code: str
    payer: str
    criterion_code: str
    system_said: str                # "met" | "not_met" | "not_applicable"
    physician_said: str             # what it should be
    system_evidence: str = ""       # what evidence system cited
    correct_evidence: str = ""      # verbatim note excerpt that actually supports
    reason: str = ""                # physician's free-text explanation
    note_context: str = ""          # surrounding clinical context (anonymized)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ────────────────────────────────────────────────────────────────────────
# Write — called from /api/feedback endpoint
# ────────────────────────────────────────────────────────────────────────


def record_correction(correction: CriterionCorrection) -> bool:
    """Persist a physician correction across all available backends.

    Writes to:
      1. Postgres `criterion_corrections` (if DB available)
      2. Pinecone `feedback` namespace (if Pinecone available)
      3. Local JSONL (always, as backup)

    Returns True if any backend succeeded.
    """
    success = False

    # JSONL (always — local backup, cheap)
    try:
        _FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(_FEEDBACK_FILE, "a") as f:
            f.write(json.dumps(correction.__dict__) + "\n")
        success = True
        logger.info("Correction saved to JSONL: %s on %s", correction.correction_id[:8], correction.criterion_code)
    except Exception as e:
        logger.warning("Failed to write correction JSONL: %s", e)

    # Postgres
    try:
        from cardioauth.db import _get_conn, is_db_available
        if is_db_available():
            conn = _get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS criterion_corrections (
                        correction_id   TEXT PRIMARY KEY,
                        case_id         TEXT NOT NULL,
                        user_id         TEXT NOT NULL,
                        procedure_code  TEXT NOT NULL,
                        payer           TEXT NOT NULL,
                        criterion_code  TEXT NOT NULL,
                        system_said     TEXT NOT NULL,
                        physician_said  TEXT NOT NULL,
                        system_evidence TEXT DEFAULT '',
                        correct_evidence TEXT DEFAULT '',
                        reason          TEXT DEFAULT '',
                        note_context    TEXT DEFAULT '',
                        created_at      TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    INSERT INTO criterion_corrections
                        (correction_id, case_id, user_id, procedure_code, payer,
                         criterion_code, system_said, physician_said, system_evidence,
                         correct_evidence, reason, note_context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (correction_id) DO NOTHING
                """, (
                    correction.correction_id, correction.case_id, correction.user_id,
                    correction.procedure_code, correction.payer, correction.criterion_code,
                    correction.system_said, correction.physician_said,
                    correction.system_evidence, correction.correct_evidence,
                    correction.reason, correction.note_context,
                ))
                conn.commit()
                success = True
            finally:
                conn.close()
    except Exception as e:
        logger.warning("Failed to write correction to DB: %s", e)

    # Pinecone (for similarity retrieval)
    try:
        from cardioauth.agents.precedent_retriever import _get_pinecone, _embed
        index = _get_pinecone()
        if index is not None:
            text = (
                f"Criterion {correction.criterion_code} correction: "
                f"system said {correction.system_said}, physician said "
                f"{correction.physician_said}. Context: {correction.note_context[:500]}. "
                f"Reason: {correction.reason[:200]}."
            )
            vec = _embed(text, input_type="passage")
            if vec:
                index.upsert(
                    vectors=[(correction.correction_id, vec, {
                        "type": "correction",
                        "criterion_code": correction.criterion_code,
                        "procedure_code": correction.procedure_code,
                        "payer": correction.payer,
                        "system_said": correction.system_said,
                        "physician_said": correction.physician_said,
                        "reason": correction.reason[:500],
                        "note_context": correction.note_context[:500],
                        "created_at": correction.created_at,
                    })],
                    namespace="feedback",
                )
                success = True
    except Exception as e:
        logger.warning("Failed to write correction to Pinecone: %s", e)

    return success


# ────────────────────────────────────────────────────────────────────────
# Retrieve — called during new PA cases to inject past mistakes
# ────────────────────────────────────────────────────────────────────────


def retrieve_relevant_corrections(
    case_summary: str,
    procedure_code: str,
    payer: str = "",
    top_k: int = 3,
) -> list[dict]:
    """Find past corrections similar to the current case.

    Used to inject "past mistakes to avoid" into the UnifiedReasoner prompt.
    Returns list of dicts with correction details.
    """
    try:
        from cardioauth.agents.precedent_retriever import _get_pinecone, _embed
        index = _get_pinecone()
        if index is None:
            return []

        vec = _embed(case_summary, input_type="query")
        if vec is None:
            return []

        filter_dict = {"type": "correction", "procedure_code": procedure_code}
        result = index.query(
            vector=vec,
            top_k=top_k,
            namespace="feedback",
            filter=filter_dict,
            include_metadata=True,
        )

        out = []
        for match in result.get("matches", []):
            meta = match.get("metadata", {}) or {}
            if meta.get("type") != "correction":
                continue
            out.append({
                "criterion_code": meta.get("criterion_code", ""),
                "system_said": meta.get("system_said", ""),
                "physician_said": meta.get("physician_said", ""),
                "reason": meta.get("reason", ""),
                "similarity": float(match.get("score", 0.0)),
            })
        return out
    except Exception as e:
        logger.warning("Correction retrieval failed: %s", e)
        return []


def get_all_corrections(limit: int = 100) -> list[dict]:
    """Return all corrections from JSONL (for debugging / dataset export)."""
    if not _FEEDBACK_FILE.exists():
        return []
    out = []
    try:
        with open(_FEEDBACK_FILE) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning("Failed to read corrections JSONL: %s", e)
    return out[-limit:]
