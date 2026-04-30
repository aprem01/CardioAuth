"""Phase C.3 — outcome correlator wired to ResolvedCPT + reviewer verdict.

The existing calibration dashboard (5ed60bf) bins approval scores
against actual outcomes. This module adds the dimensions Peter asked
about indirectly via his MVP-workflow framing: track approval/denial
RATES per (resolved CPT, reviewer recommendation, finding profile,
taxonomy version) so we can answer:

  - Does the LLM reviewer's recommendation predict outcomes? When the
    reviewer says "hold", what fraction of cases the office submits
    anyway end up approved vs denied?
  - Are particular Finding kinds (e.g., cpt_family_mismatch,
    extraction_thin) correlated with denial?
  - Do held-for-review packets that are submitted anyway have a
    different approval rate than auto-approved packets?
  - Is the calibration drifting after a taxonomy version bump?

This is the outcome side of the data product: lineage flows back from
the recorded outcome to the frozen packet via case_id ↔ submission_id
linkage. Joins live in packet_archive (frozen packets) ↔ outcomes
(submission outcomes).

Today's implementation reads the persisted outcomes + frozen packets
and computes approval rate breakdowns. The dashboard endpoint is wired
in server.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class CorrelationBucket:
    """One row of the correlation breakdown.

    `key` describes which dimension this bucket represents — e.g.
    `("resolved_cpt", "78492")`, `("reviewer_recommendation", "hold")`,
    `("finding_kind", "cpt_family_mismatch")`. `decisive_count` is
    approved + denied; pending cases are counted separately so a low
    sample doesn't look high-confidence.
    """

    dimension: str
    value: str
    decisive_count: int
    approved: int
    denied: int
    pending: int
    approval_rate: float | None = None  # approved / decisive_count when decisive_count > 0

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "value": self.value,
            "decisive_count": self.decisive_count,
            "approved": self.approved,
            "denied": self.denied,
            "pending": self.pending,
            "approval_rate": self.approval_rate,
        }


@dataclass
class CorrelationReport:
    by_resolved_cpt: list[CorrelationBucket] = field(default_factory=list)
    by_reviewer_recommendation: list[CorrelationBucket] = field(default_factory=list)
    by_decision: list[CorrelationBucket] = field(default_factory=list)
    by_finding_kind: list[CorrelationBucket] = field(default_factory=list)
    by_taxonomy_version: list[CorrelationBucket] = field(default_factory=list)
    by_model_version: list[CorrelationBucket] = field(default_factory=list)
    total_decisive: int = 0
    total_pending: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "by_resolved_cpt": [b.to_dict() for b in self.by_resolved_cpt],
            "by_reviewer_recommendation": [b.to_dict() for b in self.by_reviewer_recommendation],
            "by_decision": [b.to_dict() for b in self.by_decision],
            "by_finding_kind": [b.to_dict() for b in self.by_finding_kind],
            "by_taxonomy_version": [b.to_dict() for b in self.by_taxonomy_version],
            "by_model_version": [b.to_dict() for b in self.by_model_version],
            "total_decisive": self.total_decisive,
            "total_pending": self.total_pending,
            "notes": self.notes,
        }


# ──────────────────────────────────────────────────────────────────────
# Outcome classification
# ──────────────────────────────────────────────────────────────────────


def _classify_outcome(outcome: str) -> str:
    """Normalize outcome strings into approved | denied | pending."""
    o = (outcome or "").strip().lower()
    if o.startswith("approved") or o == "approve":
        return "approved"
    if o.startswith("denied") or o == "deny":
        return "denied"
    return "pending"


# ──────────────────────────────────────────────────────────────────────
# Bucket aggregation helper
# ──────────────────────────────────────────────────────────────────────


def _bucketize(
    rows: Iterable[tuple[str, str]],
    *,
    dimension_label: str,
) -> list[CorrelationBucket]:
    """Given an iterable of (key_value, outcome_label) pairs, group
    into CorrelationBucket per key_value with approved/denied/pending
    counts. Returns sorted by decisive_count DESC then key.
    """
    counts: dict[str, dict[str, int]] = {}
    for key, outcome_label in rows:
        if not key:
            continue
        b = counts.setdefault(key, {"approved": 0, "denied": 0, "pending": 0})
        b[outcome_label] = b.get(outcome_label, 0) + 1

    buckets: list[CorrelationBucket] = []
    for key, c in counts.items():
        decisive = c["approved"] + c["denied"]
        rate = (c["approved"] / decisive) if decisive > 0 else None
        buckets.append(CorrelationBucket(
            dimension=dimension_label, value=key,
            decisive_count=decisive,
            approved=c["approved"], denied=c["denied"], pending=c["pending"],
            approval_rate=rate,
        ))
    buckets.sort(key=lambda b: (-b.decisive_count, b.value))
    return buckets


# ──────────────────────────────────────────────────────────────────────
# Top-level correlator
# ──────────────────────────────────────────────────────────────────────


def correlate_outcomes() -> CorrelationReport:
    """Walk the joined (packet, outcome) data and emit a structured
    correlation report across every dimension we care about.

    Joins frozen submission_packets with the outcomes table on
    case_id ↔ submission_id. Pre-Phase A.5 cases (no frozen packet)
    are skipped; the report's `total_decisive` count reflects only
    cases with both a frozen packet AND a recorded outcome.
    """
    from cardioauth.persistence import get_store

    store = get_store()

    # Pull every packet + look up its outcome by case_id (== submission_id).
    rcpt_rows: list[tuple[str, str]] = []
    rec_rows: list[tuple[str, str]] = []
    decision_rows: list[tuple[str, str]] = []
    finding_rows: list[tuple[str, str]] = []
    tx_version_rows: list[tuple[str, str]] = []
    model_version_rows: list[tuple[str, str]] = []
    total_decisive = 0
    total_pending = 0

    packet_rows = store.list_packets(limit=10_000)
    for prow in packet_rows:
        case_id = prow.get("case_id", "")
        if not case_id:
            continue
        outcome_record = store.get_outcome(case_id)
        if not outcome_record:
            continue
        outcome_label = _classify_outcome(outcome_record.get("outcome", ""))
        if outcome_label == "approved" or outcome_label == "denied":
            total_decisive += 1
        else:
            total_pending += 1

        # Indexed columns are projections of packet fields
        rcpt_rows.append((prow.get("resolved_cpt", "") or "", outcome_label))
        rec_rows.append((prow.get("reviewer_recommendation", "") or "skipped", outcome_label))
        decision_rows.append((prow.get("decision", "") or "", outcome_label))
        tx_version_rows.append((prow.get("taxonomy_version", "") or "", outcome_label))
        model_version_rows.append((prow.get("model_version", "") or "", outcome_label))

        # Finding kinds — needs the full packet JSON to enumerate
        full = store.get_packet(case_id)
        if not full or not full.get("packet"):
            continue
        packet_payload = full["packet"]
        seen_kinds: set[str] = set()
        for f in (packet_payload.get("deterministic_findings") or []):
            kind = f.get("kind") or ""
            if kind:
                seen_kinds.add(kind)
        rv = packet_payload.get("reviewer_verdict") or {}
        for findings in (rv.get("findings_per_question") or {}).values():
            for f in findings:
                kind = f.get("kind") or ""
                if kind:
                    seen_kinds.add(kind)
        for kind in seen_kinds:
            finding_rows.append((kind, outcome_label))

    notes = ""
    if total_decisive < 20:
        notes = (
            f"Only {total_decisive} decisive outcomes — correlations are "
            "directional below ~20 cases. Treat per-bucket rates as noisy."
        )

    return CorrelationReport(
        by_resolved_cpt=_bucketize(rcpt_rows, dimension_label="resolved_cpt"),
        by_reviewer_recommendation=_bucketize(rec_rows, dimension_label="reviewer_recommendation"),
        by_decision=_bucketize(decision_rows, dimension_label="decision"),
        by_finding_kind=_bucketize(finding_rows, dimension_label="finding_kind"),
        by_taxonomy_version=_bucketize(tx_version_rows, dimension_label="taxonomy_version"),
        by_model_version=_bucketize(model_version_rows, dimension_label="model_version"),
        total_decisive=total_decisive,
        total_pending=total_pending,
        notes=notes,
    )
