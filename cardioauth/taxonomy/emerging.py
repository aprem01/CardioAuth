"""Emerging criteria queue.

When the LLM matcher surfaces a clinical observation that doesn't fit
the formal taxonomy, it gets queued here. Once the same emerging
criterion appears N times across cases, it can be promoted to a formal
coded criterion in the next taxonomy version.

This is what makes the system learn from real-world payer behavior.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# In-memory queue (in production this would be a DB table)
_QUEUE_FILE = Path(os.environ.get("EMERGING_QUEUE_PATH", "/tmp/cardioauth_emerging.json"))
PROMOTION_THRESHOLD = 3  # appearances before suggesting promotion


@dataclass
class EmergingCriterion:
    """A clinical observation flagged by the matcher that's not in the taxonomy yet."""
    suggested_code: str
    category: str
    description: str
    rationale: str
    case_id: str
    procedure_code: str
    payer: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "suggested_code": self.suggested_code,
            "category": self.category,
            "description": self.description,
            "rationale": self.rationale,
            "case_id": self.case_id,
            "procedure_code": self.procedure_code,
            "payer": self.payer,
            "timestamp": self.timestamp,
        }


def _load_queue() -> list[dict]:
    if not _QUEUE_FILE.exists():
        return []
    try:
        with open(_QUEUE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_queue(queue: list[dict]) -> None:
    try:
        _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2)
    except OSError as e:
        logger.warning("Could not persist emerging queue: %s", e)


def record_emerging_criterion(
    suggested_code: str,
    category: str,
    description: str,
    rationale: str,
    case_id: str,
    procedure_code: str,
    payer: str,
) -> None:
    """Add an emerging criterion to the review queue."""
    queue = _load_queue()
    entry = EmergingCriterion(
        suggested_code=suggested_code,
        category=category,
        description=description,
        rationale=rationale,
        case_id=case_id,
        procedure_code=procedure_code,
        payer=payer,
    ).to_dict()
    queue.append(entry)
    _save_queue(queue)
    logger.info("Recorded emerging criterion: %s (%s)", suggested_code, description[:60])


def get_emerging_queue() -> dict:
    """Return the current emerging criteria with promotion-ready summary."""
    queue = _load_queue()
    if not queue:
        return {
            "total_observations": 0,
            "unique_descriptions": 0,
            "promotion_ready": [],
            "all_observations": [],
        }

    # Group by description (case-insensitive, normalized)
    groups: dict[str, dict] = {}
    for entry in queue:
        key = entry["description"].lower().strip()[:120]
        if key not in groups:
            groups[key] = {
                "description": entry["description"],
                "category": entry["category"],
                "suggested_code": entry["suggested_code"],
                "count": 0,
                "first_seen": entry["timestamp"],
                "last_seen": entry["timestamp"],
                "procedures": set(),
                "payers": set(),
                "case_ids": [],
            }
        g = groups[key]
        g["count"] += 1
        g["last_seen"] = entry["timestamp"]
        g["procedures"].add(entry["procedure_code"])
        g["payers"].add(entry["payer"])
        g["case_ids"].append(entry["case_id"])

    # Convert sets to lists for JSON serialization
    grouped = []
    for g in groups.values():
        grouped.append({
            **g,
            "procedures": sorted(g["procedures"]),
            "payers": sorted(g["payers"]),
            "promotion_ready": g["count"] >= PROMOTION_THRESHOLD,
        })

    grouped.sort(key=lambda x: x["count"], reverse=True)

    promotion_ready = [g for g in grouped if g["promotion_ready"]]

    return {
        "total_observations": len(queue),
        "unique_descriptions": len(grouped),
        "promotion_threshold": PROMOTION_THRESHOLD,
        "promotion_ready": promotion_ready,
        "all_observations": grouped,
    }


def promote_to_taxonomy(suggested_code: str, formal_code: str) -> dict:
    """Mark an emerging criterion as promoted (caller updates taxonomy.py).

    This function only records the promotion intent. Updating
    taxonomy.py with the new criterion is a manual code change to
    preserve versioning discipline.
    """
    queue = _load_queue()
    promoted = []
    remaining = []
    for entry in queue:
        if entry["suggested_code"] == suggested_code:
            entry["promoted_to"] = formal_code
            entry["promoted_at"] = datetime.now(timezone.utc).isoformat()
            promoted.append(entry)
        else:
            remaining.append(entry)
    _save_queue(remaining)

    return {
        "suggested_code": suggested_code,
        "formal_code": formal_code,
        "promoted_count": len(promoted),
        "next_step": (
            f"Add {formal_code} to CRITERION_TAXONOMY in "
            f"cardioauth/taxonomy/taxonomy.py and bump TAXONOMY_VERSION."
        ),
    }
