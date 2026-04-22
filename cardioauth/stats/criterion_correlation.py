"""Criterion-outcome correlation report.

Peter's explicit ask: "tag each case with its criteria (EX-001, BMI-001, etc.)
and map payer responses back to those — this will tell us if certain codes
are often missed and are essential for approval."

This module reads every submission + outcome pair from the persistent Store
and computes, for each criterion:

  - times evaluated (how often the criterion was applied to a case)
  - times met / times not_met
  - approval rate when the criterion was met
  - approval rate when the criterion was not met
  - predictive weight = approval_rate_when_met - approval_rate_when_not_met
     (positive = satisfying this code increases approval probability;
      large = the code is load-bearing for the payer)

Useless today (we have ~0 real outcomes). Designed to light up the moment
real outcomes start landing via /api/pa/outcome/record. Zero risk to the
existing reasoning path — read-only analysis over persisted data.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


def compute_criterion_correlation(
    payer: str = "",
    cpt_code: str = "",
) -> dict[str, Any]:
    """Compute per-criterion approval correlation from persisted outcomes.

    Args:
        payer: Optional payer filter (e.g. "UnitedHealthcare"). Empty = all.
        cpt_code: Optional CPT filter (e.g. "78492"). Empty = all.

    Returns:
        {
          "filter": {"payer": ..., "cpt_code": ...},
          "total_cases": int,
          "total_approved": int,
          "total_denied": int,
          "criteria": [
            {
              "code": "MED-002",
              "times_evaluated": 47,
              "times_met": 32,
              "times_not_met": 15,
              "approval_rate_when_met": 0.91,
              "approval_rate_when_not_met": 0.47,
              "predictive_weight": 0.44,
              "interpretation": "load_bearing" | "helpful" | "neutral" | "counterproductive",
            }
          ],
          "load_bearing_criteria": [...],  # top predictive_weight
          "note": "" | "insufficient data",
        }

    Interpretation thresholds:
      predictive_weight >= 0.25 → "load_bearing"
      0.05 < weight < 0.25      → "helpful"
      -0.05 <= weight <= 0.05   → "neutral"
      weight < -0.05            → "counterproductive" (rare; likely noise
                                  or a mis-tagged pathway group)
    """
    from cardioauth.persistence import get_store
    store = get_store()

    # Per-criterion counters
    per_code_total: dict[str, int] = defaultdict(int)
    per_code_met: dict[str, int] = defaultdict(int)
    per_code_not_met: dict[str, int] = defaultdict(int)
    per_code_approved_when_met: dict[str, int] = defaultdict(int)
    per_code_approved_when_not_met: dict[str, int] = defaultdict(int)

    total_cases = 0
    total_approved = 0
    total_denied = 0

    for pair in store.iter_submissions_with_outcomes(payer=payer, cpt_code=cpt_code):
        submission = pair.get("submission") or {}
        outcome = pair.get("outcome") or {}
        outcome_status = (outcome.get("outcome") or "").upper()

        if outcome_status not in ("APPROVED", "DENIED"):
            continue  # skip pending / info_requested — not decisive yet

        total_cases += 1
        if outcome_status == "APPROVED":
            total_approved += 1
        else:
            total_denied += 1

        snapshot = submission.get("criterion_matches_snapshot", []) or []
        for entry in snapshot:
            code = entry.get("code")
            status = (entry.get("status") or "").lower()
            if not code:
                continue
            per_code_total[code] += 1
            if status == "met":
                per_code_met[code] += 1
                if outcome_status == "APPROVED":
                    per_code_approved_when_met[code] += 1
            elif status == "not_met":
                per_code_not_met[code] += 1
                if outcome_status == "APPROVED":
                    per_code_approved_when_not_met[code] += 1

    # Assemble per-criterion rows
    criteria_rows: list[dict] = []
    for code in sorted(per_code_total.keys()):
        met_n = per_code_met[code]
        notmet_n = per_code_not_met[code]
        approved_when_met = per_code_approved_when_met[code]
        approved_when_notmet = per_code_approved_when_not_met[code]

        appr_when_met = round(approved_when_met / met_n, 3) if met_n else None
        appr_when_notmet = round(approved_when_notmet / notmet_n, 3) if notmet_n else None

        if appr_when_met is not None and appr_when_notmet is not None:
            predictive_weight: float | None = round(appr_when_met - appr_when_notmet, 3)
        else:
            predictive_weight = None

        if predictive_weight is None:
            interpretation = "insufficient_data"
        elif predictive_weight >= 0.25:
            interpretation = "load_bearing"
        elif predictive_weight > 0.05:
            interpretation = "helpful"
        elif predictive_weight >= -0.05:
            interpretation = "neutral"
        else:
            interpretation = "counterproductive"

        criteria_rows.append({
            "code": code,
            "times_evaluated": per_code_total[code],
            "times_met": met_n,
            "times_not_met": notmet_n,
            "approval_rate_when_met": appr_when_met,
            "approval_rate_when_not_met": appr_when_notmet,
            "predictive_weight": predictive_weight,
            "interpretation": interpretation,
        })

    # Load-bearing criteria, sorted by predictive_weight descending
    load_bearing = sorted(
        [c for c in criteria_rows if c["interpretation"] == "load_bearing"],
        key=lambda c: c["predictive_weight"] or 0,
        reverse=True,
    )

    note = ""
    if total_cases < 10:
        note = (
            f"Only {total_cases} decisive outcomes recorded — numbers below are "
            "directional at best. Report becomes meaningful at ~20+ decisive cases "
            "per (payer, CPT) filter."
        )

    return {
        "filter": {"payer": payer or "ALL", "cpt_code": cpt_code or "ALL"},
        "total_cases": total_cases,
        "total_approved": total_approved,
        "total_denied": total_denied,
        "approval_rate_overall": round(total_approved / total_cases, 3) if total_cases else None,
        "criteria": criteria_rows,
        "load_bearing_criteria": [c["code"] for c in load_bearing],
        "note": note,
    }
