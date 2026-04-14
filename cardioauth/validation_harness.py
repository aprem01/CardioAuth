"""Batch validation harness for clinical-grade evaluation.

Takes a list of labeled cases (with physician-adjudicated gold labels for
both per-criterion status and overall approval outcome), runs each through
the reasoning pipeline, and emits a calibration report with the metrics
a clinical validation study needs:

  - Per-criterion accuracy, precision, recall (how often we agree with
    the physician on each taxonomy code)
  - Overall approval prediction sensitivity / specificity / PPV / NPV
  - Score calibration: does the mean approval score at each bucket match
    the actual approval rate?
  - Silent-drop incidence (criteria in gold but missing from reasoner)

This is what replaces "it looked good on C1–C5" with a real falsifiable
performance number.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Iterable

from cardioauth.case_context import CaseContext
from cardioauth.config import Config
from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure

logger = logging.getLogger(__name__)


@dataclass
class LabeledCase:
    """One validation case with physician-adjudicated ground truth."""
    case_id: str
    procedure_code: str
    procedure_name: str
    payer_name: str
    raw_note: str
    gold_outcome: str                           # "approved" | "denied"
    gold_criterion_labels: dict[str, str] = field(default_factory=dict)
    # per-criterion gold: code -> "met" | "not_met" | "not_applicable"

    # Optional chart_data for reasoners that want structure
    chart_data: dict = field(default_factory=dict)


@dataclass
class CaseResult:
    case_id: str
    predicted_approval: bool
    predicted_score: float
    gold_outcome: str
    gold_approval: bool
    criterion_agreements: int = 0
    criterion_disagreements: int = 0
    silently_dropped_gold: list[str] = field(default_factory=list)
    hallucinated: list[str] = field(default_factory=list)
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CalibrationReport:
    n_cases: int
    overall_approval: dict   # sens/spec/ppv/npv/accuracy
    criterion_agreement: dict   # accuracy per criterion + macro
    calibration_curve: list[dict]   # score bucket -> actual approval rate
    silent_drop_rate: float
    hallucination_rate: float
    avg_duration_ms: float
    per_case: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


# ────────────────────────────────────────────────────────────────────────
# Core harness
# ────────────────────────────────────────────────────────────────────────


def _run_one(case: LabeledCase, config: Config) -> CaseResult:
    from cardioauth.agents.relationship_extractor import extract_relationships
    from cardioauth.agents.unified_reasoner import reason_with_unified_agent

    t0 = time.time()
    ctx = CaseContext(
        case_id=case.case_id,
        procedure_code=case.procedure_code,
        procedure_name=case.procedure_name,
        payer_name=case.payer_name,
        user_id="validation",
        raw_note=case.raw_note,
        chart_data=case.chart_data,
    )
    ctx.build_clinical_narrative()

    try:
        extract_relationships(ctx, config)
    except Exception as e:
        logger.debug("validation: relationship extract failed for %s: %s", case.case_id, e)

    try:
        reason_with_unified_agent(ctx, config)
    except Exception as e:
        return CaseResult(
            case_id=case.case_id,
            predicted_approval=False,
            predicted_score=0.0,
            gold_outcome=case.gold_outcome,
            gold_approval=(case.gold_outcome.lower() == "approved"),
            error=f"reasoner_failed: {e}",
            duration_ms=int((time.time() - t0) * 1000),
        )

    applicable = {c.code for c in get_criteria_for_procedure(case.procedure_code, case.payer_name)}
    predicted_by_code = {m.get("code"): m.get("status", "not_met") for m in ctx.criterion_matches}
    gold = case.gold_criterion_labels or {}

    agreements = 0
    disagreements = 0
    silently_dropped: list[str] = []
    hallucinated: list[str] = []

    # Per-criterion comparison — only count codes that are applicable (CPT gate)
    for code in applicable:
        gold_label = gold.get(code)
        if not gold_label:
            continue  # no gold label, skip
        predicted = predicted_by_code.get(code)
        if predicted is None:
            silently_dropped.append(code)
            disagreements += 1
            continue
        # Collapse "not_applicable" → "not_met" for fair comparison
        pred_norm = "not_met" if predicted == "not_applicable" else predicted
        gold_norm = "not_met" if gold_label == "not_applicable" else gold_label
        if pred_norm == gold_norm:
            agreements += 1
        else:
            disagreements += 1

    # Codes returned by the reasoner that aren't applicable to this CPT
    for code in predicted_by_code:
        if code and code not in applicable:
            hallucinated.append(code)

    predicted_approval = ctx.approval_score >= 0.6
    gold_approval = case.gold_outcome.lower() == "approved"

    return CaseResult(
        case_id=case.case_id,
        predicted_approval=predicted_approval,
        predicted_score=ctx.approval_score,
        gold_outcome=case.gold_outcome,
        gold_approval=gold_approval,
        criterion_agreements=agreements,
        criterion_disagreements=disagreements,
        silently_dropped_gold=silently_dropped,
        hallucinated=hallucinated,
        duration_ms=int((time.time() - t0) * 1000),
    )


def _confusion(results: list[CaseResult]) -> dict:
    """Binary classification metrics for the approved/denied prediction."""
    tp = sum(1 for r in results if r.predicted_approval and r.gold_approval)
    fp = sum(1 for r in results if r.predicted_approval and not r.gold_approval)
    tn = sum(1 for r in results if not r.predicted_approval and not r.gold_approval)
    fn = sum(1 for r in results if not r.predicted_approval and r.gold_approval)

    def _safe(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "sensitivity_recall": _safe(tp, tp + fn),
        "specificity": _safe(tn, tn + fp),
        "ppv_precision": _safe(tp, tp + fp),
        "npv": _safe(tn, tn + fn),
        "accuracy": _safe(tp + tn, tp + fp + tn + fn),
    }


def _calibration_curve(results: list[CaseResult], buckets: int = 5) -> list[dict]:
    """Group predicted scores into equal-width buckets and compute actual
    approval rate in each bucket. A well-calibrated system has
    mean(score) ≈ actual_approval_rate in each bucket.
    """
    edges = [i / buckets for i in range(buckets + 1)]
    out = []
    for i in range(buckets):
        lo, hi = edges[i], edges[i + 1]
        in_bucket = [r for r in results if lo <= r.predicted_score < hi or (i == buckets - 1 and r.predicted_score == 1.0)]
        if not in_bucket:
            out.append({"range": f"{lo:.2f}-{hi:.2f}", "n": 0,
                        "mean_predicted_score": None, "actual_approval_rate": None,
                        "calibration_gap": None})
            continue
        mean_score = sum(r.predicted_score for r in in_bucket) / len(in_bucket)
        actual_rate = sum(1 for r in in_bucket if r.gold_approval) / len(in_bucket)
        out.append({
            "range": f"{lo:.2f}-{hi:.2f}",
            "n": len(in_bucket),
            "mean_predicted_score": round(mean_score, 3),
            "actual_approval_rate": round(actual_rate, 3),
            "calibration_gap": round(mean_score - actual_rate, 3),
        })
    return out


def run_validation_batch(cases: Iterable[LabeledCase], config: Config | None = None) -> CalibrationReport:
    """Run every case through the reasoner and produce a calibration report."""
    config = config or Config()
    cases_list = list(cases)
    results: list[CaseResult] = [_run_one(c, config) for c in cases_list]

    # Criterion agreement (macro across cases)
    total_agree = sum(r.criterion_agreements for r in results)
    total_disagree = sum(r.criterion_disagreements for r in results)
    total_compared = total_agree + total_disagree
    criterion_accuracy = round(total_agree / total_compared, 3) if total_compared else None

    overall = _confusion(results)
    curve = _calibration_curve(results)

    total_silent_drops = sum(len(r.silently_dropped_gold) for r in results)
    total_hallucinated = sum(len(r.hallucinated) for r in results)
    durations = [r.duration_ms for r in results if r.duration_ms]
    avg_duration = round(sum(durations) / len(durations), 1) if durations else 0.0

    return CalibrationReport(
        n_cases=len(results),
        overall_approval=overall,
        criterion_agreement={
            "total_agreements": total_agree,
            "total_disagreements": total_disagree,
            "accuracy": criterion_accuracy,
        },
        calibration_curve=curve,
        silent_drop_rate=round(total_silent_drops / max(total_compared, 1), 3),
        hallucination_rate=round(total_hallucinated / max(len(results), 1), 3),
        avg_duration_ms=avg_duration,
        per_case=[r.to_dict() for r in results],
    )


# ────────────────────────────────────────────────────────────────────────
# JSONL loader for labeled case batches
# ────────────────────────────────────────────────────────────────────────


def load_cases_jsonl(path: str) -> list[LabeledCase]:
    """Load labeled cases from a JSONL file.

    Each line must be a JSON object with at least:
      case_id, procedure_code, procedure_name, payer_name, raw_note,
      gold_outcome, gold_criterion_labels.
    """
    cases: list[LabeledCase] = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
                cases.append(LabeledCase(
                    case_id=obj["case_id"],
                    procedure_code=obj["procedure_code"],
                    procedure_name=obj.get("procedure_name", obj["procedure_code"]),
                    payer_name=obj["payer_name"],
                    raw_note=obj["raw_note"],
                    gold_outcome=obj["gold_outcome"],
                    gold_criterion_labels=obj.get("gold_criterion_labels", {}),
                    chart_data=obj.get("chart_data", {}),
                ))
            except Exception as e:
                logger.warning("load_cases_jsonl: line %d failed: %s", i, e)
    return cases
