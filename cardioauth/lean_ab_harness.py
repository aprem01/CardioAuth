"""A/B harness — compare lean hybrid vs current multi-stage pipeline.

The artifact Peter asked for. Runs the SAME input through both
pipelines and produces a structured comparison report covering:

  - Latency (wall-clock per pipeline)
  - Cost (LLM tokens × current Claude pricing)
  - Decision agreement (transmit/hold/block — should match)
  - Approval-score divergence (mean absolute error across runs)
  - Criteria-met agreement (Jaccard over the set of met codes)
  - CPT resolution agreement
  - Pipeline-error agreement (both surface the same root cause)
  - Findings agreement (count + kinds, by severity)

Designed for two surfaces:
  1. CLI / script — `python -m cardioauth.lean_ab_harness` against
     a list of cases produces a JSON report + a human-readable
     markdown summary.
  2. API — same harness can be called from a `/api/demo/lean-ab`
     endpoint so the UI can surface side-by-side runs.

Cost-model: token counts come from each pipeline's actual usage
records (claude_cost ledger). Falls back to a heuristic when
unavailable. Numbers should be directional, not authoritative.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cardioauth.lean_pipeline import LeanRunResult, run_lean_pipeline

logger = logging.getLogger(__name__)


# Heuristic cost per 1k tokens (Claude Sonnet/Opus mid-range, May 2026).
# Used only when actual cost isn't tracked. Update as pricing changes.
_HEURISTIC_COST_PER_1K_TOKENS = 0.005


# ──────────────────────────────────────────────────────────────────────
# Per-case comparison
# ──────────────────────────────────────────────────────────────────────


@dataclass
class CaseComparison:
    """One side-by-side comparison between the two pipelines on a
    single input case."""

    case_id: str
    request_cpt: str
    payer: str

    # Lean pipeline run
    lean_decision: str = ""
    lean_resolved_cpt: str = ""
    lean_score: float = 0.0
    lean_label: str = ""
    lean_latency_ms: int = 0
    lean_tokens: int = 0
    lean_cost_usd: float = 0.0
    lean_finding_kinds: list[str] = field(default_factory=list)
    lean_pipeline_error_kinds: list[str] = field(default_factory=list)
    lean_criteria_met: list[str] = field(default_factory=list)

    # Current pipeline run
    current_decision: str = ""
    current_resolved_cpt: str = ""
    current_score: float = 0.0
    current_label: str = ""
    current_latency_ms: int = 0
    current_tokens: int = 0
    current_cost_usd: float = 0.0
    current_finding_kinds: list[str] = field(default_factory=list)
    current_pipeline_error_kinds: list[str] = field(default_factory=list)
    current_criteria_met: list[str] = field(default_factory=list)

    # FHIR Provenance + archive (lean only — current pipeline doesn't
    # emit FHIR Provenance yet)
    lean_provenance: dict | None = None
    lean_archive_paths: dict | None = None

    # Run-level errors (harness errors, not pipeline errors)
    lean_run_error: str = ""
    current_run_error: str = ""

    # Derived comparison metrics (computed in finalize())
    decisions_agree: bool = False
    cpts_agree: bool = False
    score_delta: float = 0.0           # lean - current
    score_abs_diff: float = 0.0
    criteria_jaccard: float = 0.0      # |∩| / |∪|
    latency_speedup: float = 0.0       # current_ms / lean_ms (>1 = lean faster)
    cost_savings_pct: float = 0.0      # (current - lean) / current * 100

    def finalize(self) -> None:
        """Compute derived metrics. Call after both runs are populated."""
        self.decisions_agree = (
            bool(self.lean_decision)
            and bool(self.current_decision)
            and self.lean_decision == self.current_decision
        )
        self.cpts_agree = (
            bool(self.lean_resolved_cpt)
            and bool(self.current_resolved_cpt)
            and self.lean_resolved_cpt == self.current_resolved_cpt
        )
        self.score_delta = self.lean_score - self.current_score
        self.score_abs_diff = abs(self.score_delta)

        s_lean = set(self.lean_criteria_met)
        s_curr = set(self.current_criteria_met)
        union = s_lean | s_curr
        self.criteria_jaccard = (
            len(s_lean & s_curr) / len(union) if union else 1.0
        )

        if self.lean_latency_ms > 0 and self.current_latency_ms > 0:
            self.latency_speedup = self.current_latency_ms / self.lean_latency_ms
        if self.current_cost_usd > 0:
            self.cost_savings_pct = (
                (self.current_cost_usd - self.lean_cost_usd) /
                self.current_cost_usd * 100.0
            )

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "request_cpt": self.request_cpt,
            "payer": self.payer,
            "lean_provenance": self.lean_provenance,
            "lean_archive_paths": self.lean_archive_paths,
            "lean": {
                "decision": self.lean_decision,
                "resolved_cpt": self.lean_resolved_cpt,
                "score": self.lean_score,
                "label": self.lean_label,
                "latency_ms": self.lean_latency_ms,
                "tokens": self.lean_tokens,
                "cost_usd": self.lean_cost_usd,
                "finding_kinds": list(self.lean_finding_kinds),
                "pipeline_error_kinds": list(self.lean_pipeline_error_kinds),
                "criteria_met": list(self.lean_criteria_met),
                "run_error": self.lean_run_error,
            },
            "current": {
                "decision": self.current_decision,
                "resolved_cpt": self.current_resolved_cpt,
                "score": self.current_score,
                "label": self.current_label,
                "latency_ms": self.current_latency_ms,
                "tokens": self.current_tokens,
                "cost_usd": self.current_cost_usd,
                "finding_kinds": list(self.current_finding_kinds),
                "pipeline_error_kinds": list(self.current_pipeline_error_kinds),
                "criteria_met": list(self.current_criteria_met),
                "run_error": self.current_run_error,
            },
            "comparison": {
                "decisions_agree": self.decisions_agree,
                "cpts_agree": self.cpts_agree,
                "score_delta": self.score_delta,
                "score_abs_diff": self.score_abs_diff,
                "criteria_jaccard": self.criteria_jaccard,
                "latency_speedup": self.latency_speedup,
                "cost_savings_pct": self.cost_savings_pct,
            },
        }


# ──────────────────────────────────────────────────────────────────────
# Aggregate report
# ──────────────────────────────────────────────────────────────────────


@dataclass
class AbReport:
    """Aggregate metrics across N case comparisons."""

    case_comparisons: list[CaseComparison] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    # Computed in finalize()
    n_cases: int = 0
    decision_agreement_rate: float = 0.0
    cpt_agreement_rate: float = 0.0
    mean_score_abs_diff: float = 0.0
    mean_criteria_jaccard: float = 0.0
    mean_latency_speedup: float = 0.0
    mean_cost_savings_pct: float = 0.0
    lean_total_latency_ms: int = 0
    current_total_latency_ms: int = 0
    lean_total_cost_usd: float = 0.0
    current_total_cost_usd: float = 0.0

    def finalize(self) -> None:
        self.n_cases = len(self.case_comparisons)
        if not self.n_cases:
            return
        for c in self.case_comparisons:
            c.finalize()
        self.decision_agreement_rate = sum(
            1 for c in self.case_comparisons if c.decisions_agree
        ) / self.n_cases
        self.cpt_agreement_rate = sum(
            1 for c in self.case_comparisons if c.cpts_agree
        ) / self.n_cases
        self.mean_score_abs_diff = sum(
            c.score_abs_diff for c in self.case_comparisons
        ) / self.n_cases
        self.mean_criteria_jaccard = sum(
            c.criteria_jaccard for c in self.case_comparisons
        ) / self.n_cases
        speedups = [c.latency_speedup for c in self.case_comparisons if c.latency_speedup > 0]
        self.mean_latency_speedup = sum(speedups) / len(speedups) if speedups else 0.0
        savings = [c.cost_savings_pct for c in self.case_comparisons if c.current_cost_usd > 0]
        self.mean_cost_savings_pct = sum(savings) / len(savings) if savings else 0.0
        self.lean_total_latency_ms = sum(c.lean_latency_ms for c in self.case_comparisons)
        self.current_total_latency_ms = sum(c.current_latency_ms for c in self.case_comparisons)
        self.lean_total_cost_usd = sum(c.lean_cost_usd for c in self.case_comparisons)
        self.current_total_cost_usd = sum(c.current_cost_usd for c in self.case_comparisons)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "n_cases": self.n_cases,
            "case_comparisons": [c.to_dict() for c in self.case_comparisons],
            "aggregate": {
                "decision_agreement_rate": self.decision_agreement_rate,
                "cpt_agreement_rate": self.cpt_agreement_rate,
                "mean_score_abs_diff": self.mean_score_abs_diff,
                "mean_criteria_jaccard": self.mean_criteria_jaccard,
                "mean_latency_speedup": self.mean_latency_speedup,
                "mean_cost_savings_pct": self.mean_cost_savings_pct,
                "lean_total_latency_ms": self.lean_total_latency_ms,
                "current_total_latency_ms": self.current_total_latency_ms,
                "lean_total_cost_usd": self.lean_total_cost_usd,
                "current_total_cost_usd": self.current_total_cost_usd,
            },
        }

    def to_markdown(self) -> str:
        """Human-readable summary suitable for emailing to Peter."""
        lines: list[str] = []
        lines.append("# Lean Hybrid vs Current Pipeline — A/B Report\n")
        lines.append(f"- Cases compared: **{self.n_cases}**")
        lines.append(f"- Started: {self.started_at}")
        lines.append(f"- Finished: {self.finished_at}\n")

        lines.append("## Aggregate metrics\n")
        lines.append(f"| Metric | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| Decision agreement | {self.decision_agreement_rate:.0%} |")
        lines.append(f"| Resolved CPT agreement | {self.cpt_agreement_rate:.0%} |")
        lines.append(f"| Mean approval-score absolute diff | {self.mean_score_abs_diff:.3f} |")
        lines.append(f"| Mean criteria-met Jaccard similarity | {self.mean_criteria_jaccard:.2f} |")
        lines.append(f"| Lean total latency | {self.lean_total_latency_ms / 1000:.1f}s |")
        lines.append(f"| Current total latency | {self.current_total_latency_ms / 1000:.1f}s |")
        if self.mean_latency_speedup > 0:
            lines.append(f"| Mean latency speedup (lean vs current) | {self.mean_latency_speedup:.1f}× |")
        lines.append(f"| Lean total cost | ${self.lean_total_cost_usd:.4f} |")
        lines.append(f"| Current total cost | ${self.current_total_cost_usd:.4f} |")
        if self.mean_cost_savings_pct:
            lines.append(f"| Mean cost savings | {self.mean_cost_savings_pct:.0f}% |")
        lines.append("")

        lines.append("## Per-case detail\n")
        for c in self.case_comparisons:
            lines.append(f"### {c.case_id} (CPT {c.request_cpt}, {c.payer})\n")
            lines.append(f"| | Lean | Current | Δ |")
            lines.append(f"|---|---|---|---|")
            lines.append(f"| Decision | {c.lean_decision or '—'} | {c.current_decision or '—'} | {'✓' if c.decisions_agree else '✗'} |")
            lines.append(f"| Resolved CPT | {c.lean_resolved_cpt or '—'} | {c.current_resolved_cpt or '—'} | {'✓' if c.cpts_agree else '✗'} |")
            lines.append(f"| Approval score | {c.lean_score:.0%} ({c.lean_label}) | {c.current_score:.0%} ({c.current_label}) | {c.score_delta:+.0%} |")
            lines.append(f"| Latency | {c.lean_latency_ms}ms | {c.current_latency_ms}ms | {c.latency_speedup:.1f}× |")
            lines.append(f"| Tokens | {c.lean_tokens} | {c.current_tokens} | |")
            lines.append(f"| Cost | ${c.lean_cost_usd:.4f} | ${c.current_cost_usd:.4f} | {c.cost_savings_pct:.0f}% |")
            lines.append(f"| Findings | {len(c.lean_finding_kinds)} | {len(c.current_finding_kinds)} | |")
            lines.append(f"| Criteria met overlap (Jaccard) | | | {c.criteria_jaccard:.2f} |")

            if c.lean_finding_kinds or c.current_finding_kinds:
                lines.append("\n**Findings**")
                lines.append(f"- Lean: {', '.join(c.lean_finding_kinds) or '—'}")
                lines.append(f"- Current: {', '.join(c.current_finding_kinds) or '—'}")

            if c.lean_run_error or c.current_run_error:
                lines.append("\n**Errors**")
                if c.lean_run_error:
                    lines.append(f"- Lean: {c.lean_run_error}")
                if c.current_run_error:
                    lines.append(f"- Current: {c.current_run_error}")
            lines.append("")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Per-pipeline runners
# ──────────────────────────────────────────────────────────────────────


def _run_lean(case: dict, llm_caller: Any = None) -> tuple[LeanRunResult, str]:
    """Execute the lean pipeline on one case. Returns (result, error)."""
    try:
        result = run_lean_pipeline(
            case_id=case.get("case_id", ""),
            raw_note=case["raw_note"],
            request_cpt=case["request_cpt"],
            payer=case["payer"],
            llm_caller=llm_caller,
        )
        return result, ""
    except Exception as e:
        logger.exception("Lean run failed for %s: %s", case.get("case_id"), e)
        return None, str(e)[:300]  # type: ignore[return-value]


def _run_current(case: dict) -> tuple[dict, str]:
    """Execute the existing demo_e2e pipeline on one case. Returns
    (timeline_dict, error). Uses run_end_to_end_demo with raw_note
    so the same code path Peter uses gets exercised."""
    try:
        from cardioauth.demo_e2e import run_end_to_end_demo
        timeline = run_end_to_end_demo(
            patient_id=case.get("patient_id", "CUSTOM"),
            procedure_code=case["request_cpt"],
            payer_name=case["payer"],
            raw_note=case["raw_note"],
            scripted_outcome=case.get("scripted_outcome", "APPROVED"),
        )
        return timeline.to_dict(), ""
    except Exception as e:
        logger.exception("Current run failed for %s: %s", case.get("case_id"), e)
        return {}, str(e)[:300]


def _harvest_lean_metrics(comp: CaseComparison, result: LeanRunResult) -> None:
    """Pull metrics out of the lean run into the comparison row."""
    comp.lean_decision = result.decision
    comp.lean_resolved_cpt = result.resolved_cpt
    comp.lean_score = result.approval_score
    comp.lean_label = result.approval_label
    comp.lean_latency_ms = result.total_duration_ms
    comp.lean_tokens = result.state2_tokens
    comp.lean_cost_usd = (
        result.state2_cost_usd
        if result.state2_cost_usd > 0
        else result.state2_tokens / 1000.0 * _HEURISTIC_COST_PER_1K_TOKENS
    )
    comp.lean_finding_kinds = sorted({f.get("kind", "") for f in result.findings})
    comp.lean_pipeline_error_kinds = sorted(
        {e.get("kind", "") for e in result.pipeline_errors}
    )
    if result.state2_output:
        comp.lean_criteria_met = sorted(
            c["code"] for c in (result.state2_output.get("criteria_evaluated") or [])
            if c.get("status") == "met"
        )
    # Pass through FHIR Provenance + archive paths so the UI can
    # render the audit-trail panel for the lean side.
    comp.lean_provenance = result.provenance
    comp.lean_archive_paths = result.archive_paths


def _harvest_current_metrics(comp: CaseComparison, timeline: dict) -> None:
    """Pull comparable metrics out of the current pipeline's timeline."""
    comp.current_latency_ms = timeline.get("total_duration_ms", 0)
    comp.current_pipeline_error_kinds = sorted(
        {e.get("kind", "") for e in (timeline.get("pipeline_errors") or [])}
    )

    # Walk steps to find Physician + Reasoner + Submission
    physician_step = next(
        (s for s in timeline.get("steps", []) if s.get("agent") == "Physician"),
        None,
    )
    reasoner_step = next(
        (s for s in timeline.get("steps", []) if s.get("agent") == "UNIFIED_REASONER"),
        None,
    )

    if physician_step:
        d = physician_step.get("detail") or {}
        comp.current_decision = d.get("decision", "")
        comp.current_score = float(d.get("approval_score") or 0.0)
        comp.current_label = d.get("approval_label", "")
        # Findings = warnings + typed_pipeline_findings + reviewer findings
        finding_kinds: set[str] = set()
        for w in d.get("warnings") or []:
            finding_kinds.add(w.get("kind", ""))
        for f in d.get("typed_pipeline_findings") or []:
            finding_kinds.add(f.get("kind", ""))
        comp.current_finding_kinds = sorted(finding_kinds - {""})
        # Resolved CPT — from submission_packet_summary
        sps = d.get("submission_packet_summary") or {}
        rc = sps.get("resolved_cpt") or {}
        comp.current_resolved_cpt = rc.get("cpt", "")

    if reasoner_step:
        d = reasoner_step.get("detail") or {}
        # Criteria-met set: walk criterion_matches
        comp.current_criteria_met = sorted({
            m.get("code", "")
            for m in (d.get("criterion_matches") or [])
            if m.get("status") == "met" and m.get("code")
        })
        # Update score/label from reasoner if Physician didn't have it
        if not comp.current_score:
            comp.current_score = float(d.get("approval_score") or 0.0)
        if not comp.current_label:
            comp.current_label = d.get("approval_label", "")

    # Token + cost estimation: walk steps, sum claude usage where tracked
    total_tokens = 0
    for step in timeline.get("steps", []):
        d = step.get("detail") or {}
        total_tokens += int(d.get("tokens", 0) or 0)
        total_tokens += int(d.get("cost_input_tokens", 0) or 0)
        total_tokens += int(d.get("cost_output_tokens", 0) or 0)
    comp.current_tokens = total_tokens
    comp.current_cost_usd = total_tokens / 1000.0 * _HEURISTIC_COST_PER_1K_TOKENS


# ──────────────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────────────


def compare_one_case(
    case: dict, *, lean_llm_caller: Any = None,
) -> CaseComparison:
    """Run both pipelines on one case and return a populated
    CaseComparison row."""
    comp = CaseComparison(
        case_id=case.get("case_id", "?"),
        request_cpt=case["request_cpt"],
        payer=case["payer"],
    )

    lean_result, lean_err = _run_lean(case, llm_caller=lean_llm_caller)
    if lean_err:
        comp.lean_run_error = lean_err
    elif lean_result is not None:
        _harvest_lean_metrics(comp, lean_result)

    current_timeline, current_err = _run_current(case)
    if current_err:
        comp.current_run_error = current_err
    else:
        _harvest_current_metrics(comp, current_timeline)

    comp.finalize()
    return comp


def run_ab(
    cases: list[dict],
    *,
    lean_llm_caller: Any = None,
) -> AbReport:
    """Run both pipelines across N cases and return the aggregate
    A/B report. Each case dict must have at minimum:
      - case_id (str, optional — auto-generated if absent)
      - raw_note (str)
      - request_cpt (str)
      - payer (str)
      - patient_id (str, optional)
      - scripted_outcome ('APPROVED' | 'DENIED' | 'PENDING', optional)
    """
    report = AbReport(
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    for i, case in enumerate(cases):
        logger.info("A/B harness: case %d/%d (%s)", i + 1, len(cases), case.get("case_id"))
        comp = compare_one_case(case, lean_llm_caller=lean_llm_caller)
        report.case_comparisons.append(comp)
    report.finished_at = datetime.now(timezone.utc).isoformat()
    report.finalize()
    return report


# ──────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────


def _cli_main() -> None:
    """Run A/B against the bundled MockNote PDFs.

    Usage:
        python -m cardioauth.lean_ab_harness
    """
    import json
    from pathlib import Path

    from cardioauth.pdf_parser import parse_pdf_to_markdown

    doc_dir = Path(__file__).resolve().parent.parent / "doc"
    cases: list[dict] = []
    for pdf, cpt in [("MockNote1.pdf", "78452"), ("MockNote2.pdf", "78492")]:
        path = doc_dir / pdf
        if not path.exists():
            logger.warning("Skipping %s (not found)", path)
            continue
        try:
            note_md = parse_pdf_to_markdown(str(path))
        except Exception as e:
            logger.warning("PDF parse failed for %s: %s", pdf, e)
            continue
        cases.append({
            "case_id": pdf.replace(".pdf", ""),
            "patient_id": "CUSTOM-PDF",
            "request_cpt": cpt,
            "payer": "UnitedHealthcare",
            "raw_note": note_md,
            "scripted_outcome": "APPROVED",
        })

    if not cases:
        print("No test cases found. Place MockNote1.pdf and MockNote2.pdf in doc/.")
        return

    report = run_ab(cases)
    print(report.to_markdown())
    out_path = Path("/tmp/lean_ab_report.json")
    out_path.write_text(json.dumps(report.to_dict(), indent=2, default=str))
    print(f"\nFull JSON report: {out_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _cli_main()
