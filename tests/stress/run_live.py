"""Drive the live deployed lean pipeline with the stress corpus.

Hits the production /api/demo/end-to-end-lean endpoint with each
case in tests/stress/cases.py, captures the full response, and
prints a per-case scorecard plus an aggregate summary.

Usage:
    python tests/stress/run_live.py
    python tests/stress/run_live.py --case STRESS-03-cpt-divergence
    python tests/stress/run_live.py --base-url http://localhost:8080
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error

# Make the stress module importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tests.stress.cases import CASES, StressCase, case_by_id, all_case_ids


DEFAULT_BASE = "https://cardioauth2-production.up.railway.app"
LEAN_ENDPOINT = "/api/demo/end-to-end-lean"


def post_lean(base_url: str, case: StressCase, timeout: float = 280) -> tuple[int, dict | None, float]:
    """POST a case to the lean endpoint. Returns (http_status, json_body, duration_s)."""
    payload = {
        "patient_id": "CUSTOM",
        "procedure_code": case.request_cpt,
        "payer_name": case.payer,
        "scripted_outcome": "APPROVED",
        "approver_name": "Stress-Test Runner",
        "raw_note": case.note,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=base_url + LEAN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, json.loads(text), time.time() - t0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
        except Exception:
            j = {"_raw_error": body[:1000]}
        return e.code, j, time.time() - t0
    except Exception as e:
        return 0, {"_runtime_error": str(e)[:500]}, time.time() - t0


def score_case(case: StressCase, response: dict) -> dict[str, Any]:
    """Score a single case response against the case's expectation.
    Honest scoring — no ground truth, just observable signals."""
    out = {
        "case_id": case.case_id,
        "request_cpt": case.request_cpt,
        "expectation": case.expectation[:80],
    }

    if "_runtime_error" in response or "_raw_error" in response or "detail" in response:
        out["status"] = "RUNTIME_ERROR"
        out["error"] = (
            response.get("_runtime_error")
            or response.get("_raw_error")
            or response.get("detail")
        )
        return out

    out["case_id_returned"] = response.get("case_id", "")
    out["decision"] = response.get("decision", "")
    out["resolved_cpt"] = response.get("resolved_cpt", "")
    out["score"] = response.get("approval_score", 0.0)
    out["label"] = response.get("approval_label", "")
    out["duration_s"] = response.get("total_duration_ms", 0) / 1000
    out["tokens"] = response.get("state2_tokens", 0)
    out["cost_usd"] = response.get("state2_cost_usd", 0.0)
    out["pipeline_errors"] = len(response.get("pipeline_errors", []))
    out["findings"] = len(response.get("findings", []))
    out["finding_kinds"] = sorted({
        f.get("kind", "") for f in response.get("findings", [])
    })

    # State 2 specifics
    stages = response.get("stages", [])
    s2 = next((s for s in stages if "State 2" in s.get("name", "")), None)
    if s2:
        out["state2_status"] = s2.get("status", "")
        det = s2.get("detail") or {}
        out["state2_attempts"] = det.get("attempts")
        if det.get("errors"):
            out["state2_schema_errors"] = [
                f"{e.get('loc')}: {e.get('msg')}"
                for e in det["errors"][:3]
            ]

    out2 = response.get("state2_output") or {}
    if out2:
        out["criteria_met_count"] = sum(
            1 for c in out2.get("criteria_evaluated", [])
            if c.get("status") == "met"
        )
        out["criteria_total"] = len(out2.get("criteria_evaluated", []))
        out["narrative_cpt"] = (out2.get("narrative") or {}).get("cpt_referenced", "")
        out["doc_quality"] = (
            (out2.get("documentation_quality") or {}).get("note_format_quality", "")
        )
        out["cpt_resolution_source"] = (
            (out2.get("cpt_resolution") or {}).get("source", "")
        )

    # Sanity-check failure modes. CPT divergence with a recorded
    # source (note_extracted / ambiguous_human_decide) is correct
    # behavior — only flag when the source is "request" but the cpt
    # mysteriously differs.
    flags = []
    if out.get("case_id_returned") and case.request_cpt not in out["case_id_returned"]:
        flags.append("case_id_does_not_contain_request_cpt")
    cpt_source = out.get("cpt_resolution_source", "")
    if (out.get("resolved_cpt")
            and out["resolved_cpt"] != case.request_cpt
            and cpt_source == "request"):
        flags.append(f"resolved_cpt_differs_without_source_change:{out['resolved_cpt']}")
    if out.get("narrative_cpt") and out.get("resolved_cpt") and \
       out["narrative_cpt"] != out["resolved_cpt"]:
        flags.append("narrative_cpt_diverges_from_resolved")
    if response.get("pipeline_errors"):
        flags.append("has_pipeline_errors")
    if out.get("state2_status") == "failed":
        flags.append("state2_failed")
    if out.get("http_status") and out["http_status"] >= 500:
        flags.append(f"http_{out['http_status']}")
    out["red_flags"] = flags

    out["status"] = "OK" if not flags else "FLAGS"
    return out


def print_scorecard(scores: list[dict]) -> None:
    print()
    print("=" * 100)
    print(f"{'CASE':<35} {'STATUS':<10} {'DECISION':<18} {'CPT':<8} {'SCORE':<8} {'CRIT':<7} {'TIME':<7} {'FLAGS'}")
    print("-" * 100)
    for s in scores:
        flags = ",".join(s.get("red_flags", [])) or "—"
        decision = s.get("decision", "?")
        cpt = s.get("resolved_cpt", "?")
        score = (
            f"{s.get('score', 0)*100:.0f}%" if isinstance(s.get("score"), (int, float))
            else "?"
        )
        crit = (
            f"{s.get('criteria_met_count', 0)}/{s.get('criteria_total', 0)}"
            if "criteria_total" in s else "—"
        )
        dur = f"{s.get('duration_s', 0):.0f}s" if "duration_s" in s else "—"
        status = s.get("status", "?")
        case_id = s["case_id"][:34]
        print(f"{case_id:<35} {status:<10} {decision:<18} {cpt:<8} {score:<8} {crit:<7} {dur:<7} {flags[:40]}")
    print("=" * 100)
    n_total = len(scores)
    n_ok = sum(1 for s in scores if s.get("status") == "OK")
    n_flags = sum(1 for s in scores if s.get("status") == "FLAGS")
    n_err = sum(1 for s in scores if s.get("status") == "RUNTIME_ERROR")
    total_cost = sum(s.get("cost_usd", 0) for s in scores)
    total_time = sum(s.get("duration_s", 0) for s in scores)
    print(f"\n{n_ok}/{n_total} clean, {n_flags} flagged, {n_err} runtime error · "
          f"total ${total_cost:.4f}, {total_time:.0f}s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--case", help="Run a single case by id")
    parser.add_argument("--out", default="/tmp/stress_results.json", help="Where to dump full responses")
    parser.add_argument("--summary-out", default="/tmp/stress_scorecard.json")
    args = parser.parse_args()

    cases_to_run = (
        [case_by_id(args.case)] if args.case else CASES
    )

    scores: list[dict] = []
    full_responses: dict[str, dict] = {}
    for case in cases_to_run:
        print(f"→ {case.case_id} ({case.request_cpt}, {case.payer})...", flush=True)
        status, response, duration = post_lean(args.base_url, case)
        if response is not None:
            full_responses[case.case_id] = response
        score = score_case(case, response or {})
        score["http_status"] = status
        score["wall_clock_s"] = duration
        scores.append(score)
        print(f"  HTTP {status}, wall {duration:.1f}s, "
              f"decision={score.get('decision', '?')}, "
              f"cpt={score.get('resolved_cpt', '?')}, "
              f"score={score.get('score', 0)*100:.0f}%, "
              f"flags={','.join(score.get('red_flags', [])) or 'none'}")

    print_scorecard(scores)

    Path(args.out).write_text(json.dumps(full_responses, indent=2, default=str))
    Path(args.summary_out).write_text(json.dumps(scores, indent=2, default=str))
    print(f"\nFull responses: {args.out}")
    print(f"Scorecard:      {args.summary_out}")


if __name__ == "__main__":
    main()
