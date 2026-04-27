"""Reliability / calibration analysis for approval-likelihood predictions.

When the reasoner says approval_likelihood_score=0.85, does the case
actually approve ~85% of the time? Failure-aware AI review (Apr 25)
flagged this as a load-bearing question we weren't measuring.

Inputs: stream of (predicted_score, actual_outcome) where actual_outcome
is "approved" / "denied" / "pending". Pending cases are excluded from
calibration since the ground truth isn't known yet.

Outputs:
  - reliability bins:        bin_low, bin_high, count, predicted_avg,
                             actual_rate
  - Brier score:             mean squared error of probabilistic prediction
  - ECE (Expected Calibration Error): weighted average bin-level miscal.
  - decisive_count:          total approved+denied (drives confidence band
                             on the dashboard so we don't over-interpret
                             N<20 noise)

The dashboard calls this. With <10 decisive outcomes, the metrics are
returned but flagged unreliable so the UI can render the warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class CalibrationBin:
    bin_low: float
    bin_high: float
    count: int
    approved: int
    denied: int
    predicted_avg: float    # mean predicted score in this bin
    actual_rate: float      # fraction approved
    gap: float              # actual_rate - predicted_avg (positive = under-confident)


@dataclass
class CalibrationReport:
    bins: list[CalibrationBin]
    decisive_count: int
    pending_count: int
    brier_score: float | None       # None when decisive_count == 0
    ece: float | None
    over_confident_score: float     # mean(predicted - actual) — positive = system over-promises
    reliability_warning: str        # "" if N is enough, otherwise a caveat


def compute_calibration(
    rows: Iterable[dict],
    n_bins: int = 10,
    min_decisive_for_reliable: int = 20,
) -> CalibrationReport:
    """Compute reliability bins + Brier + ECE from prediction-outcome rows.

    Each row must have:
      - "score":   float in [0,1]
      - "outcome": one of "approved" / "denied" / "pending" / "approved-with-conditions"
        (case-insensitive; anything not approved/denied is treated as pending)
    """
    decisive: list[tuple[float, int]] = []  # (score, 1 if approved else 0)
    pending = 0
    for row in rows:
        score = row.get("score")
        outcome = (row.get("outcome") or "").lower()
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= score <= 1.0):
            continue
        if outcome.startswith("approved") or outcome == "approve":
            decisive.append((score, 1))
        elif outcome.startswith("denied") or outcome == "deny":
            decisive.append((score, 0))
        else:
            pending += 1

    n = len(decisive)
    if n == 0:
        return CalibrationReport(
            bins=[], decisive_count=0, pending_count=pending,
            brier_score=None, ece=None, over_confident_score=0.0,
            reliability_warning="No decisive outcomes yet — calibration unavailable.",
        )

    # Bin assignment — equal-width bins on [0, 1]
    bin_buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for score, y in decisive:
        # last bin includes 1.0
        idx = min(int(score * n_bins), n_bins - 1)
        bin_buckets[idx].append((score, y))

    bins: list[CalibrationBin] = []
    weighted_gap_total = 0.0
    for i, bucket in enumerate(bin_buckets):
        bin_low = i / n_bins
        bin_high = (i + 1) / n_bins
        if not bucket:
            continue
        approved = sum(y for _, y in bucket)
        denied = len(bucket) - approved
        pred_avg = sum(s for s, _ in bucket) / len(bucket)
        actual_rate = approved / len(bucket)
        gap = actual_rate - pred_avg
        bins.append(CalibrationBin(
            bin_low=bin_low, bin_high=bin_high,
            count=len(bucket), approved=approved, denied=denied,
            predicted_avg=pred_avg, actual_rate=actual_rate, gap=gap,
        ))
        weighted_gap_total += (len(bucket) / n) * abs(gap)

    brier = sum((s - y) ** 2 for s, y in decisive) / n
    ece = weighted_gap_total
    over_conf = sum(s - y for s, y in decisive) / n

    if n < min_decisive_for_reliable:
        warning = (
            f"Only {n} decisive outcomes — calibration metrics are noisy "
            f"below {min_decisive_for_reliable}. Treat as directional."
        )
    else:
        warning = ""

    return CalibrationReport(
        bins=bins,
        decisive_count=n,
        pending_count=pending,
        brier_score=brier,
        ece=ece,
        over_confident_score=over_conf,
        reliability_warning=warning,
    )


def report_to_dict(report: CalibrationReport) -> dict:
    """Serializable form for the API response."""
    return {
        "decisive_count": report.decisive_count,
        "pending_count": report.pending_count,
        "brier_score": report.brier_score,
        "ece": report.ece,
        "over_confident_score": report.over_confident_score,
        "reliability_warning": report.reliability_warning,
        "bins": [
            {
                "bin_low": b.bin_low,
                "bin_high": b.bin_high,
                "count": b.count,
                "approved": b.approved,
                "denied": b.denied,
                "predicted_avg": b.predicted_avg,
                "actual_rate": b.actual_rate,
                "gap": b.gap,
            }
            for b in report.bins
        ],
    }


def collect_rows_from_store(payer: str = "", cpt_code: str = "") -> list[dict]:
    """Stream submission+outcome joins from the persistence layer
    into the row shape compute_calibration expects.
    """
    from cardioauth.persistence import get_store

    store = get_store()
    rows: list[dict] = []
    for joined in store.iter_submissions_with_outcomes(payer=payer, cpt_code=cpt_code):
        sub = joined.get("submission") or {}
        out = joined.get("outcome") or {}
        score = sub.get("approval_score")
        outcome = out.get("outcome") or out.get("decision") or ""
        if score is None:
            continue
        rows.append({"score": score, "outcome": outcome})
    return rows
