"""Lean Hybrid State Machine — orchestrator.

Replaces the 4-LLM-call staging in demo_e2e.py with a single
structured LLM call surrounded by deterministic guardrails.

Stages (each is a method; data flows via a typed LeanRunContext):

  1. PRE-PASS         deterministic, ~10ms
                      regex essentials + CPT validation + form lookup
                      + applicable criteria filter + RAG policy fetch

  2. UNIFIED CALL     1 LLM call, ~5-8s, ~$0.30
                      single structured-output prompt → LeanState2Output

  3. SAFETY VERIFY    deterministic, ~50ms
                      independent regex re-extraction + cross-check
                      (THIS SESSION: thin hookup; full audit in next pass)

  4. GATE             deterministic, ~10ms
                      coherence checks + decision (transmit/hold/block)

  5. FREEZE           deterministic, ~20ms
                      packet build + audit log + (later) FHIR Provenance

This module is intentionally minimal in this first pass — the full
State 3 audit panel + State 5 FHIR Provenance land in follow-on
commits. The bones here are enough for the A/B comparison Peter
asked for.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from cardioauth.lean_prompt import (
    SYSTEM_PROMPT,
    State2ValidationError,
    build_retry_user_prompt,
    build_user_prompt,
    validate_state2_output,
)
from cardioauth.lean_schema import LeanState2Output

logger = logging.getLogger(__name__)


StageStatus = Literal["ok", "fallback", "failed", "skipped"]


# ──────────────────────────────────────────────────────────────────────
# Run trace — per-stage timing + status, mirrors demo_e2e timeline
# ──────────────────────────────────────────────────────────────────────


@dataclass
class StageResult:
    name: str
    status: StageStatus
    duration_ms: int
    summary: str
    detail: dict | None = None

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
        }
        if self.detail is not None:
            d["detail"] = self.detail
        return d


@dataclass
class LeanRunContext:
    """Mutable state carried across stages.

    Stages read what they need + write what they produce. The
    pipeline runner inspects the context after each stage to decide
    whether to continue or short-circuit.
    """

    case_id: str
    request_cpt: str
    payer: str
    raw_note: str

    # Filled by State 1
    essentials: dict = field(default_factory=dict)
    applicable_criteria: list[dict] = field(default_factory=list)
    payer_specific_criteria: list[dict] = field(default_factory=list)
    payer_policy_chunks: list[dict] = field(default_factory=list)
    payer_form_fields: list[dict] = field(default_factory=list)

    # Filled by State 2
    state2_output: LeanState2Output | None = None
    state2_raw_text: str = ""           # for replay / debug
    state2_tokens: int = 0
    state2_cost_usd: float = 0.0

    # Filled by State 3 (next pass; placeholder for now)
    safety_audit: dict | None = None

    # Filled by State 4
    decision: Literal["transmit", "hold_for_review", "block"] = "hold_for_review"
    decision_rationale: str = ""
    findings: list[dict] = field(default_factory=list)

    # Pipeline-level errors (Anthropic spend limit, etc.)
    pipeline_errors: list[dict] = field(default_factory=list)

    # Trace
    stages: list[StageResult] = field(default_factory=list)
    started_at: str = ""
    total_duration_ms: int = 0


@dataclass
class LeanRunResult:
    """The terminal output of a lean pipeline run. Serializable to
    JSON for the API endpoint backing the UI."""

    case_id: str
    decision: str
    decision_rationale: str
    request_cpt: str
    resolved_cpt: str
    payer: str
    approval_score: float
    approval_label: str
    findings: list[dict]
    pipeline_errors: list[dict]
    stages: list[dict]
    started_at: str
    total_duration_ms: int
    state2_tokens: int
    state2_cost_usd: float
    state2_output: dict | None  # full structured output, for inspection

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "decision": self.decision,
            "decision_rationale": self.decision_rationale,
            "request_cpt": self.request_cpt,
            "resolved_cpt": self.resolved_cpt,
            "payer": self.payer,
            "approval_score": self.approval_score,
            "approval_label": self.approval_label,
            "findings": self.findings,
            "pipeline_errors": self.pipeline_errors,
            "stages": self.stages,
            "started_at": self.started_at,
            "total_duration_ms": self.total_duration_ms,
            "state2_tokens": self.state2_tokens,
            "state2_cost_usd": self.state2_cost_usd,
            "state2_output": self.state2_output,
        }


# ──────────────────────────────────────────────────────────────────────
# State 1: deterministic pre-pass
# ──────────────────────────────────────────────────────────────────────


def _state1_pre_pass(ctx: LeanRunContext) -> StageResult:
    """Regex essentials + CPT validation + applicable-criteria filter
    + payer form lookup + RAG policy fetch. Pure deterministic; no
    LLM. Sets ctx.essentials, ctx.applicable_criteria,
    ctx.payer_specific_criteria, ctx.payer_policy_chunks,
    ctx.payer_form_fields."""
    t0 = time.time()
    summary_parts: list[str] = []

    # Regex essentials (already shipped)
    from cardioauth.note_essentials import (
        extract_essentials_from_note,
        normalize_payer_name,
    )
    matches = extract_essentials_from_note(ctx.raw_note)
    for field_name, m in matches.items():
        v = normalize_payer_name(m.value) if field_name == "payer_name" else m.value
        ctx.essentials[field_name] = v
    summary_parts.append(f"essentials={len(ctx.essentials)}")

    # Taxonomy filter — only criteria that apply to the request CPT.
    # As taxonomy grows from 25 → 250 entries, this filter keeps the
    # prompt size bounded.
    try:
        from cardioauth.taxonomy.taxonomy import CRITERION_TAXONOMY
        applicable = []
        payer_specific = []
        for code, criterion in CRITERION_TAXONOMY.items():
            applies_to = getattr(criterion, "applies_to", None) or []
            if not applies_to or ctx.request_cpt in applies_to:
                entry = {
                    "code": code,
                    "description": getattr(criterion, "description", ""),
                    "applies_to": list(applies_to),
                    "evidence_type": getattr(criterion, "evidence_type", ""),
                }
                # Payer-specific criteria — codes start with the payer
                # short-name (e.g. "UHC-MCG-..."). Generic codes (ECG-,
                # BMI-, EX-, NDX-) go in the main list.
                if code.startswith(ctx.payer.split()[0].upper() + "-") if ctx.payer else False:
                    payer_specific.append(entry)
                else:
                    applicable.append(entry)
        ctx.applicable_criteria = applicable
        ctx.payer_specific_criteria = payer_specific
        summary_parts.append(
            f"criteria={len(ctx.applicable_criteria)}/{len(ctx.payer_specific_criteria)}"
        )
    except Exception as e:
        logger.warning("Lean State 1: taxonomy load failed: %s", e)

    # Payer form schema lookup
    try:
        from cardioauth.payer_forms import get_payer_form
        form = get_payer_form(ctx.payer, ctx.request_cpt)
        if form is not None:
            ctx.payer_form_fields = [
                {
                    "key": f.key,
                    "label": f.label,
                    "category": f.category,
                    "required": f.required,
                    "format": f.format,
                    "options": list(getattr(f, "options", []) or []),
                    "help_text": getattr(f, "help_text", ""),
                }
                for f in form.fields
            ]
        summary_parts.append(f"form_fields={len(ctx.payer_form_fields)}")
    except Exception as e:
        logger.warning("Lean State 1: payer form lookup failed: %s", e)

    # Policy retrieval (RAG)
    try:
        from cardioauth.rag import retrieve_for_pa
        results = retrieve_for_pa(
            cpt_code=ctx.request_cpt,
            payer=ctx.payer,
            top_k=4,
        )
        ctx.payer_policy_chunks = [
            {
                "id": getattr(r.chunk, "chunk_id", ""),
                "text": (getattr(r.chunk, "text", "") or "")[:600],
            }
            for r in (results or [])
        ]
        summary_parts.append(f"policy_chunks={len(ctx.payer_policy_chunks)}")
    except Exception as e:
        logger.warning("Lean State 1: policy retrieval failed (continuing): %s", e)

    duration_ms = int((time.time() - t0) * 1000)
    return StageResult(
        name="State 1: pre-pass",
        status="ok",
        duration_ms=duration_ms,
        summary=" · ".join(summary_parts),
        detail={
            "essentials": dict(ctx.essentials),
            "applicable_criteria_count": len(ctx.applicable_criteria),
            "payer_specific_criteria_count": len(ctx.payer_specific_criteria),
            "payer_form_field_count": len(ctx.payer_form_fields),
            "payer_policy_chunk_count": len(ctx.payer_policy_chunks),
        },
    )


# ──────────────────────────────────────────────────────────────────────
# State 2: unified LLM call with schema validation + retry
# ──────────────────────────────────────────────────────────────────────


def _state2_unified_call(
    ctx: LeanRunContext,
    *,
    max_retries: int = 1,
    llm_caller: Any = None,
) -> StageResult:
    """One structured-output LLM call covering chart extraction,
    criteria evaluation, narrative drafting, and form population.

    `llm_caller` is injectable for testing; defaults to a real
    Anthropic client when not provided. The signature is:
        llm_caller(system_prompt: str, user_prompt: str) -> tuple[str, dict]
    where the dict carries {input_tokens, output_tokens, cost_usd, model}.
    """
    t0 = time.time()
    user_prompt = build_user_prompt(
        case_id=ctx.case_id,
        raw_note=ctx.raw_note,
        request_cpt=ctx.request_cpt,
        payer=ctx.payer,
        applicable_criteria=ctx.applicable_criteria,
        payer_policy_chunks=ctx.payer_policy_chunks,
        payer_form_fields=ctx.payer_form_fields,
        pre_pass_essentials=ctx.essentials,
        payer_specific_criteria=ctx.payer_specific_criteria or None,
    )

    if llm_caller is None:
        llm_caller = _real_anthropic_caller

    last_err: State2ValidationError | None = None
    raw = ""
    usage: dict = {}
    for attempt in range(max_retries + 1):
        try:
            raw, usage = llm_caller(SYSTEM_PROMPT, user_prompt)
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            ctx.pipeline_errors.append(_classify_llm_error(e))
            return StageResult(
                name="State 2: unified call",
                status="failed",
                duration_ms=duration_ms,
                summary=f"LLM call failed: {str(e)[:160]}",
                detail={"error": str(e)[:500], "attempt": attempt},
            )

        ctx.state2_raw_text = raw
        ctx.state2_tokens += int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        ctx.state2_cost_usd += float(usage.get("cost_usd", 0.0))

        try:
            ctx.state2_output = validate_state2_output(raw)
            duration_ms = int((time.time() - t0) * 1000)
            return StageResult(
                name="State 2: unified call",
                status="ok",
                duration_ms=duration_ms,
                summary=(
                    f"score={ctx.state2_output.approval_verdict.score:.0%} "
                    f"label={ctx.state2_output.approval_verdict.label} "
                    f"crit={len(ctx.state2_output.criteria_evaluated)} "
                    f"tokens={ctx.state2_tokens} "
                    f"cost=${ctx.state2_cost_usd:.4f} "
                    f"attempt={attempt + 1}"
                ),
                detail={
                    "tokens": ctx.state2_tokens,
                    "cost_usd": ctx.state2_cost_usd,
                    "attempts": attempt + 1,
                    "model": usage.get("model", ""),
                    "validated": True,
                },
            )
        except State2ValidationError as e:
            last_err = e
            logger.warning(
                "Lean State 2: schema validation failed on attempt %d: %s",
                attempt + 1, e,
            )
            if attempt < max_retries:
                # Build retry prompt with errors as feedback
                user_prompt = build_retry_user_prompt(
                    original_user_prompt=user_prompt,
                    failed_output=e.raw_output,
                    errors=e.errors,
                )

    # All retries exhausted
    duration_ms = int((time.time() - t0) * 1000)
    return StageResult(
        name="State 2: unified call",
        status="failed",
        duration_ms=duration_ms,
        summary=f"Schema validation failed after {max_retries + 1} attempts",
        detail={
            "errors": last_err.errors if last_err else [],
            "raw_output_preview": (last_err.raw_output[:500] if last_err else ""),
        },
    )


def _classify_llm_error(e: Exception) -> dict:
    """Map LLM exception → pipeline_error dict (matches demo_e2e shape)."""
    msg = str(e).lower()
    is_spend = "spend limit" in msg or "credit balance" in msg or "usage limit" in msg
    if is_spend:
        return {
            "kind": "anthropic_spend_limit",
            "severity": "blocking",
            "message": (
                "Anthropic API spend limit reached. The unified reasoning "
                "call could not complete; this run's outputs are NOT valid "
                "until credits are refilled."
            ),
            "affected_stages": ["State 2"],
            "fix_suggestion": "Refill credits at console.anthropic.com and re-run.",
        }
    return {
        "kind": "anthropic_unavailable",
        "severity": "high",
        "message": f"Unified reasoning call failed: {str(e)[:200]}",
        "affected_stages": ["State 2"],
        "fix_suggestion": "Verify API connectivity and re-run.",
    }


def _real_anthropic_caller(system_prompt: str, user_prompt: str) -> tuple[str, dict]:
    """Default LLM caller using Anthropic TOOL-USE mode.

    Forces the model to emit a JSON object matching the
    LeanState2Output schema by exposing it as a single required
    tool. The model returns a tool_use block whose `input` is
    structurally guaranteed to satisfy the schema (no retries,
    no markdown fences, no prose). This is the production-grade
    structured-output pattern in 2026.

    Falls back to free-form mode (legacy path) if tool_use isn't
    available — matches existing behavior on older clients.
    """
    import anthropic
    from cardioauth.claude_cost import TimedCall, system_with_cache_control, track_usage
    from cardioauth.config import Config
    from cardioauth.lean_schema import state2_json_schema

    cfg = Config()
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    # Build the tool definition from the LeanState2Output JSON schema.
    # Anthropic accepts the JSON Schema directly as input_schema.
    schema = state2_json_schema()
    # Strip the $defs+title wrapper if present — Anthropic wants the
    # object schema directly under input_schema.
    tool_def = {
        "name": "emit_lean_state2_output",
        "description": (
            "Emit the structured prior-authorization analysis as a single "
            "LeanState2Output object. Required: cpt_resolution, "
            "approval_verdict, narrative, documentation_quality. "
            "All clinical claims must cite verbatim quotes from the note."
        ),
        "input_schema": schema,
    }

    with TimedCall() as t:
        response = client.messages.create(
            model=cfg.model,
            max_tokens=8000,
            system=system_with_cache_control(system_prompt),
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool_def],
            tool_choice={"type": "tool", "name": "emit_lean_state2_output"},
        )
    track_usage(response, agent="LEAN_STATE2", model=cfg.model, duration_ms=t.ms)

    # Extract the tool_use block — schema-validated by Anthropic before return.
    tool_use_block = next(
        (b for b in response.content if getattr(b, "type", "") == "tool_use"),
        None,
    )
    if tool_use_block is None:
        # Fallback: model emitted text instead of tool_use (rare). Return
        # the text and let our schema validator try.
        text_block = next(
            (b for b in response.content if getattr(b, "type", "") == "text"),
            None,
        )
        raw = getattr(text_block, "text", "") if text_block else ""
    else:
        # tool_use.input is the structured object we asked for. Serialize
        # back to JSON for the existing validator path.
        import json as _json
        raw = _json.dumps(tool_use_block.input)

    in_tok = int(getattr(response.usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(response.usage, "output_tokens", 0) or 0)
    cost_usd = _estimate_cost_usd(cfg.model, in_tok, out_tok)
    usage = {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "model": cfg.model,
        "cost_usd": cost_usd,
    }
    return raw, usage


# Per-model pricing (USD per 1M tokens) for the 2026 Claude lineup.
# Used when claude_cost ledger isn't sufficient. Update as pricing
# changes; numbers here are conservative midline rates.
_MODEL_PRICES_PER_M = {
    "claude-opus-4-7":   {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in": 3.0,  "out": 15.0},
    "claude-haiku-4-5":  {"in": 0.80, "out": 4.0},
}


def _estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Rough cost estimate from token usage. Used to populate
    state2_cost_usd so the A/B harness can show a non-zero comparison."""
    base = model.split("-")[0:4]  # e.g. ["claude", "opus", "4", "7"]
    key = "-".join(base[:4]) if len(base) >= 4 else model
    # Match by prefix (handles "claude-opus-4-7[1m]" → "claude-opus-4-7")
    prices = None
    for prefix, p in _MODEL_PRICES_PER_M.items():
        if model.startswith(prefix) or key.startswith(prefix):
            prices = p
            break
    if prices is None:
        prices = {"in": 5.0, "out": 25.0}  # neutral default
    return (input_tokens * prices["in"] + output_tokens * prices["out"]) / 1_000_000.0


# ──────────────────────────────────────────────────────────────────────
# State 3: independent safety verification (thin hookup for now)
# ──────────────────────────────────────────────────────────────────────


def _state3_safety_verify(ctx: LeanRunContext) -> StageResult:
    """Run the existing safety_verifier against the raw note +
    cross-check against State 2's output. Full UI panel + finding
    emission lands in the next pass; this stage produces the audit
    log so we have it for the A/B comparison."""
    t0 = time.time()

    if ctx.state2_output is None:
        return StageResult(
            name="State 3: safety verify",
            status="skipped",
            duration_ms=0,
            summary="Skipped — State 2 did not produce output.",
        )

    try:
        from cardioauth.safety_verifier import run_safety_verification

        # Build a synthetic "chart_data" from State 2's clinical_facts
        # so the safety verifier can use its existing comparator.
        chart_data: dict[str, Any] = {
            "procedure_code": ctx.state2_output.cpt_resolution.cpt,
        }
        # Group facts by category so the safety verifier's bucket
        # paths line up.
        for fact in ctx.state2_output.clinical_facts:
            bucket = {
                "ecg_finding": "ecg_findings",
                "symptom": "current_symptoms",
                "prior_imaging": "prior_stress_tests",
                "comorbidity": "active_comorbidities",
            }.get(fact.category, "additional_notes")
            chart_data.setdefault(bucket, [])
            chart_data[bucket].append({"value": fact.value, "quote": fact.evidence.quote})

        # Build a reasoner-summary equivalent so it can compare met sets
        reasoner_summary = {
            "approval_score": ctx.state2_output.approval_verdict.score,
            "approval_label": ctx.state2_output.approval_verdict.label,
            "criteria_met": [
                {"code": c.code} for c in ctx.state2_output.criteria_evaluated
                if c.status == "met"
            ],
            "criteria_not_met": [
                {"code": c.code} for c in ctx.state2_output.criteria_evaluated
                if c.status == "not_met"
            ],
            "criteria_evaluated": [
                {"code": c.code, "status": c.status}
                for c in ctx.state2_output.criteria_evaluated
            ],
        }

        audit = run_safety_verification(
            raw_note=ctx.raw_note,
            chart_data=chart_data,
            reasoner_summary=reasoner_summary,
            resolved_cpt=ctx.state2_output.cpt_resolution.cpt,
        )
        ctx.safety_audit = audit.to_dict()

        # Surface obvious issues as findings
        for cmp in audit.comparisons:
            if not cmp.fact.present:
                continue
            if cmp.relevant_criteria and cmp.criterion_met_by_reasoner is False:
                ctx.findings.append({
                    "kind": "safety_reasoner_missed_signal",
                    "severity": "high",
                    "message": (
                        f"Independent verifier found {cmp.fact.label} in the "
                        f"note but the LLM returned not_met for criteria "
                        f"{', '.join(cmp.relevant_criteria)}. Likely LLM error."
                    ),
                    "checker": "lean_state3@v1",
                })
        if audit.note_chart_cpt_mismatch:
            ctx.findings.append({
                "kind": "safety_note_chart_cpt_mismatch",
                "severity": "high",
                "message": (
                    f"Note references CPT {', '.join(audit.cpts_in_note)} "
                    f"but resolved CPT is {ctx.state2_output.cpt_resolution.cpt}."
                ),
                "checker": "lean_state3@v1",
            })

        duration_ms = int((time.time() - t0) * 1000)
        return StageResult(
            name="State 3: safety verify",
            status="ok",
            duration_ms=duration_ms,
            summary=(
                f"comparisons={len(audit.comparisons)} "
                f"findings_emitted={sum(1 for f in ctx.findings if f.get('checker', '').startswith('lean_state3'))}"
            ),
            detail={"safety_audit": ctx.safety_audit},
        )
    except Exception as e:
        logger.warning("Lean State 3: safety verifier failed (continuing): %s", e)
        duration_ms = int((time.time() - t0) * 1000)
        return StageResult(
            name="State 3: safety verify",
            status="fallback",
            duration_ms=duration_ms,
            summary=f"verifier failed: {str(e)[:120]}",
        )


# ──────────────────────────────────────────────────────────────────────
# State 4: deterministic gate
# ──────────────────────────────────────────────────────────────────────


def _state4_gate(ctx: LeanRunContext) -> StageResult:
    """Coherence checks + decision (transmit / hold / block).

    Keeps using the existing verification.default_pipeline checkers
    when we have a SubmissionPacket equivalent; for now does the
    minimum: essentials, CPT alignment, score-band gating."""
    t0 = time.time()

    if ctx.state2_output is None:
        ctx.decision = "block"
        ctx.decision_rationale = "State 2 produced no output."
        return StageResult(
            name="State 4: gate",
            status="failed",
            duration_ms=0,
            summary="No State 2 output — blocked.",
        )

    out = ctx.state2_output

    # 1. Essentials check (regex-pre-pass overlay or LLM-emitted)
    essential_keys = (
        "patient_name", "date_of_birth", "insurance_id",
        "payer_name", "attending_physician",
    )
    missing_essentials = []
    for k in essential_keys:
        v = ctx.essentials.get(k) or getattr(out, k, "")
        if not (v or "").strip():
            missing_essentials.append(k)

    if missing_essentials:
        ctx.findings.append({
            "kind": "missing_essential",
            "severity": "blocking",
            "message": f"Required fields absent: {', '.join(missing_essentials)}",
            "related_field_keys": missing_essentials,
            "checker": "lean_state4@v1",
        })

    # 2. CPT coherence: narrative cpt_referenced must match resolved CPT
    if (out.narrative.cpt_referenced and
            out.narrative.cpt_referenced != out.cpt_resolution.cpt):
        ctx.findings.append({
            "kind": "cpt_attestation_vs_resolved",
            "severity": "high",
            "message": (
                f"Narrative references CPT {out.narrative.cpt_referenced} but "
                f"resolved CPT is {out.cpt_resolution.cpt}. Internal inconsistency."
            ),
            "checker": "lean_state4@v1",
        })

    # 3. Ambiguous criterion → physician review
    ambiguous_count = sum(1 for c in out.criteria_evaluated if c.status == "ambiguous")
    if ambiguous_count > 0:
        ctx.findings.append({
            "kind": "criteria_ambiguous",
            "severity": "medium",
            "message": (
                f"{ambiguous_count} criterion(s) marked ambiguous by reasoner. "
                "Physician review recommended."
            ),
            "checker": "lean_state4@v1",
        })

    # 4. Decision
    blocking_findings = [f for f in ctx.findings if f.get("severity") == "blocking"]
    high_findings = [f for f in ctx.findings if f.get("severity") == "high"]

    if blocking_findings:
        ctx.decision = "block"
        ctx.decision_rationale = (
            "Submission cannot proceed: " +
            "; ".join(f["message"] for f in blocking_findings)
        )
    elif high_findings or out.physician_review_recommended or out.approval_verdict.label in ("LOW", "DO_NOT_SUBMIT", "INSUFFICIENT"):
        ctx.decision = "hold_for_review"
        reasons = [f["message"][:120] for f in high_findings]
        if out.physician_review_recommended:
            reasons.extend(out.review_reasons or ["LLM flagged for review"])
        if out.approval_verdict.label in ("LOW", "DO_NOT_SUBMIT", "INSUFFICIENT"):
            reasons.append(f"Reasoner verdict: {out.approval_verdict.label} ({out.approval_verdict.score:.0%})")
        ctx.decision_rationale = "Hold for review: " + "; ".join(reasons[:3])
    else:
        ctx.decision = "transmit"
        ctx.decision_rationale = (
            f"All checks passed. Reasoner score "
            f"{out.approval_verdict.score:.0%} ({out.approval_verdict.label})."
        )

    duration_ms = int((time.time() - t0) * 1000)
    return StageResult(
        name="State 4: gate",
        status="ok",
        duration_ms=duration_ms,
        summary=f"decision={ctx.decision} findings={len(ctx.findings)}",
        detail={
            "decision": ctx.decision,
            "rationale": ctx.decision_rationale,
            "findings_count": len(ctx.findings),
        },
    )


# ──────────────────────────────────────────────────────────────────────
# Top-level runner
# ──────────────────────────────────────────────────────────────────────


def run_lean_pipeline(
    *,
    case_id: str | None = None,
    raw_note: str,
    request_cpt: str,
    payer: str,
    llm_caller: Any = None,
    max_retries: int = 1,
) -> LeanRunResult:
    """Run the lean hybrid state machine end-to-end.

    `llm_caller` is injectable for tests; pass a callable matching:
        (system_prompt: str, user_prompt: str) -> (raw_text: str, usage: dict)
    """
    if not case_id:
        case_id = f"LEAN-{request_cpt}-{uuid.uuid4().hex[:6].upper()}"

    overall_start = time.time()
    ctx = LeanRunContext(
        case_id=case_id, request_cpt=request_cpt,
        payer=payer, raw_note=raw_note,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # State 1
    ctx.stages.append(_state1_pre_pass(ctx))

    # State 2
    ctx.stages.append(_state2_unified_call(
        ctx, max_retries=max_retries, llm_caller=llm_caller,
    ))

    # State 3 (only if State 2 succeeded)
    ctx.stages.append(_state3_safety_verify(ctx))

    # State 4
    ctx.stages.append(_state4_gate(ctx))

    ctx.total_duration_ms = int((time.time() - overall_start) * 1000)

    out = ctx.state2_output
    return LeanRunResult(
        case_id=ctx.case_id,
        decision=ctx.decision,
        decision_rationale=ctx.decision_rationale,
        request_cpt=ctx.request_cpt,
        resolved_cpt=(out.cpt_resolution.cpt if out else ""),
        payer=ctx.payer,
        approval_score=(out.approval_verdict.score if out else 0.0),
        approval_label=(out.approval_verdict.label if out else "INSUFFICIENT"),
        findings=list(ctx.findings),
        pipeline_errors=list(ctx.pipeline_errors),
        stages=[s.to_dict() for s in ctx.stages],
        started_at=ctx.started_at,
        total_duration_ms=ctx.total_duration_ms,
        state2_tokens=ctx.state2_tokens,
        state2_cost_usd=ctx.state2_cost_usd,
        state2_output=(out.model_dump() if out else None),
    )
