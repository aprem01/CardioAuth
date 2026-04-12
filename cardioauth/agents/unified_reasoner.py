"""UnifiedReasoner — replaces REASONING_AGENT + TAXONOMY_MATCHER.

The previous architecture split reasoning (narrative drafting) from
taxonomy scoring (criterion matrix). Each agent received a bucketed
summary of the chart, losing clinical relationships that cross
category boundaries.

This agent does it all in one Claude call with full CaseContext:
  - Raw clinical note (never summarized)
  - Structured chart data (for machine-readable access)
  - Extracted clinical relationships (pre-computed causal chains)
  - Retrieved policy criteria with citations
  - Similar past cases (precedents from Pinecone)

The prompt is modeled after Peter's GPT-5.3 prompt that produced
essentially flawless scoring — emphasizing full clinical context,
verbatim quotes, and preservation of physician intent.

Output populates:
  ctx.criterion_matches
  ctx.approval_score
  ctx.approval_label
  ctx.narrative_draft
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic

from cardioauth.case_context import CaseContext
from cardioauth.config import Config
from cardioauth.taxonomy.taxonomy import (
    TAXONOMY_VERSION,
    get_criteria_for_procedure,
)
from cardioauth.agents.json_recovery import parse_llm_json

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are UnifiedReasoner, a cardiologist-level clinical reasoning engine for CardioAuth.

Your job is to evaluate a prior authorization request using the full clinical
context of a real patient — the way a cardiologist would read a chart.

CORE PRINCIPLES

1. PHYSICIAN INTENT OVER KEYWORDS
   - Read the full clinical note carefully before judging any criterion.
   - Infer the physician's clinical reasoning, not isolated phrases.
   - Preserve causal relationships: "dyspnea + obesity → unable to exercise
     → pharmacologic imaging" is ONE clinical chain, not three separate facts.

2. CONTEXT WINDOWS MATTER
   - The RAW CLINICAL NOTE is the source of truth.
   - Structured fields and relationships are summaries for your convenience;
     the note is authoritative. If they disagree, trust the note.

3. CLINICAL CHAINS — specific patterns to recognize
   - "Unable to do TST due to dyspnea and obesity" → EX-001 is MET
     (documentation of specific functional limitation preventing exercise)
   - "Attenuation artifact" or "likely false positive" on prior SPECT
     → NDX-001 is MET (prior study effectively non-diagnostic)
   - "LBBB" on baseline ECG → ECG-001 is MET (precludes standard stress ECG)
   - "Paced rhythm" on baseline ECG → ECG-002 is MET
   - BMI ≥ 35 → BMI-001 is MET (justifies PET over SPECT)
   - "NYHA Class II/III", "CCS Class III angina" → SX-004 is MET
   - An "office note", "consultation note", "progress note", or any substantive
     clinical narrative with history/exam/assessment → DOC-001 is MET
   - Submaximal HR (<85% MPHR) on prior stress → NDX-002 is MET

4. EVIDENCE FROM THE NOTE
   - For every criterion you mark MET, include a verbatim quote (≤15 words)
     from the RAW CLINICAL NOTE.
   - For NOT_MET, briefly state what would be required.
   - NEVER fabricate evidence. If the note doesn't contain support, mark it
     NOT_MET or NOT_APPLICABLE.

5. STATUS ASSIGNMENT
   - "met"            = clinically relevant AND supported by evidence in the note
   - "not_met"        = clinically relevant BUT chart lacks documentation
   - "not_applicable" = not clinically relevant to this patient's scenario
                        (e.g., LBBB criterion when patient has no LBBB)

   Use "not_applicable" for supporting criteria that describe clinical
   scenarios the patient simply doesn't have. Don't mark them "not_met"
   and drag down the score.

6. APPROVAL SCORING
   - Required criteria drive the base score.
   - Supporting criteria should ADD to the score when met, never subtract.
   - If ALL required criteria are met, base score is ≥ 0.75.
   - If precedent cases show high approval rates for similar patients,
     adjust upward.

7. ZERO HALLUCINATION
   - If unsure, mark NOT_MET and explain.
   - Never invent labs, dates, medications, or findings that aren't in the note.

OUTPUT FORMAT — JSON ONLY, no markdown fences:

{
  "criterion_matches": [
    {
      "code": "EX-001",
      "status": "met",
      "confidence": 0.95,
      "evidence_quote": "Unable to do TST due to dyspnea and obesity",
      "reasoning": "Documented inability to exercise due to specific medical limitations (dyspnea + obesity). Directly justifies pharmacologic stress imaging.",
      "gap": "",
      "recommendation": ""
    }
  ],
  "narrative_draft": "Full PA narrative (400-600 words) with clinical rationale, evidence citations, and guideline references.",
  "approval_score": 0.85,
  "approval_label": "HIGH",
  "required_criteria_met": 8,
  "required_criteria_total": 10,
  "key_supporting_evidence": [
    "Short bullet summarizing the strongest clinical support"
  ]
}
"""


def _build_user_message(ctx: CaseContext, applicable_criteria: list) -> str:
    """Build the user message with full clinical context."""

    # Format the taxonomy as a structured list
    criteria_json = json.dumps([
        {
            "code": c.code,
            "category": c.category,
            "short_name": c.short_name,
            "definition": c.definition,
            "evidence_type": c.evidence_type,
            "severity": c.severity,
        }
        for c in applicable_criteria
    ], indent=2)

    # Pre-extracted clinical relationships (hints for the reasoner)
    relationships_text = ""
    if ctx.relationships:
        relationships_text = "\n\nPRE-EXTRACTED CLINICAL RELATIONSHIPS (for your reference — verify against the note):\n"
        for r in ctx.relationships:
            relationships_text += (
                f"  - [{r.supports_criterion}] {r.conclusion}\n"
                f"    Quote: \"{r.evidence_quote}\"\n"
            )

    # Precedents (if any)
    precedents_text = ""
    if ctx.precedents:
        precedents_text = "\n\nSIMILAR PAST CASES (outcomes for reference):\n"
        for p in ctx.precedents[:5]:
            precedents_text += (
                f"  - {p.cpt_code} / {p.payer} / {p.outcome} "
                f"(similarity {p.similarity:.2f}): {p.summary[:120]}\n"
            )

    # Policy citations
    policy_text = ""
    if ctx.policy_data:
        pol = ctx.policy_data
        policy_text = f"\n\nPAYER POLICY ({pol.get('payer', ctx.payer_name)}):\n"
        for crit in pol.get("clinical_criteria", [])[:10]:
            if isinstance(crit, dict):
                policy_text += f"  - {crit.get('criterion', '')}\n"

    # Build message
    return (
        f"PRIOR AUTHORIZATION REQUEST\n"
        f"Procedure: {ctx.procedure_name} (CPT {ctx.procedure_code})\n"
        f"Payer: {ctx.payer_name}\n"
        f"Taxonomy version: {TAXONOMY_VERSION}\n\n"
        f"══════════════ RAW CLINICAL NOTE (SOURCE OF TRUTH) ══════════════\n"
        f"{ctx.raw_note}\n"
        f"═══════════════════════════════════════════════════════════════\n"
        f"{relationships_text}"
        f"{precedents_text}"
        f"{policy_text}\n\n"
        f"CRITERIA TO EVALUATE (evaluate each against the raw note above):\n"
        f"{criteria_json}\n\n"
        f"Produce the JSON output per the system prompt. Only include "
        f"criterion_matches for criteria in the list above. For each MET "
        f"criterion, include a verbatim evidence_quote from the raw note."
    )


class UnifiedReasoner:
    """Single agent that reasons + scores taxonomy in one Claude call."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def reason(self, ctx: CaseContext) -> None:
        """Run unified reasoning over the full CaseContext.

        Populates:
          ctx.criterion_matches
          ctx.approval_score
          ctx.approval_label
          ctx.narrative_draft
        """
        start = time.time()

        # Ensure we have a narrative to reason over
        ctx.build_clinical_narrative()

        # Get applicable criteria for this procedure + payer
        applicable = get_criteria_for_procedure(ctx.procedure_code, ctx.payer_name)
        if not applicable:
            logger.warning("UnifiedReasoner: no taxonomy criteria for CPT %s", ctx.procedure_code)
            ctx.warn("warning", "UnifiedReasoner",
                     f"No taxonomy criteria defined for CPT {ctx.procedure_code}", "no_criteria")
            return

        user_msg = _build_user_message(ctx, applicable)

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text
            tokens = getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0) if hasattr(response, "usage") else 0

            data = parse_llm_json(raw, fallback={
                "criterion_matches": [],
                "narrative_draft": "",
                "approval_score": 0.0,
                "approval_label": "INSUFFICIENT",
            })

            # Populate context
            ctx.criterion_matches = data.get("criterion_matches", [])
            ctx.narrative_draft = data.get("narrative_draft", "")

            # Use returned score, or compute from matches
            returned_score = data.get("approval_score")
            if isinstance(returned_score, (int, float)):
                ctx.approval_score = float(returned_score)
            else:
                ctx.approval_score = _compute_approval_score(
                    ctx.criterion_matches, applicable
                )

            ctx.approval_label = data.get("approval_label") or _label_from_score(ctx.approval_score)

            elapsed = int((time.time() - start) * 1000)
            ctx.trace(
                agent_name="UnifiedReasoner",
                action=f"scored {len(ctx.criterion_matches)} criteria, approval={ctx.approval_score:.2f}",
                summary=f"{ctx.approval_label} — {data.get('required_criteria_met', '?')}/{data.get('required_criteria_total', '?')} required met",
                tokens=tokens,
                ms=elapsed,
            )

        except anthropic.BadRequestError as e:
            msg = str(e)
            kind = "spend_limit" if ("usage limit" in msg.lower() or "spend limit" in msg.lower()) else "bad_request"
            ctx.warn("critical", "UnifiedReasoner", msg[:200], kind)
            logger.warning("UnifiedReasoner BadRequest: %s", msg[:200])
        except anthropic.RateLimitError as e:
            ctx.warn("warning", "UnifiedReasoner", "Claude rate limit hit", "rate_limit")
            logger.warning("UnifiedReasoner rate limit: %s", e)
        except Exception as e:
            ctx.warn("warning", "UnifiedReasoner", f"Reasoning failed: {str(e)[:200]}", "error")
            logger.exception("UnifiedReasoner failed")


# ────────────────────────────────────────────────────────────────────────
# Recalibrated approval scoring
# ────────────────────────────────────────────────────────────────────────


def _compute_approval_score(matches: list[dict], applicable: list) -> float:
    """Fallback scoring — used if the reasoner doesn't return a score.

    Calibration rules (per Peter's validation):
      - Only count REQUIRED criteria in the base
      - Supporting criteria only boost, never penalize
      - If all required criteria are met → minimum 75%
    """
    crit_by_code = {c.code: c for c in applicable}

    req_total = 0
    req_met = 0
    sup_met = 0
    sup_evaluated = 0

    for m in matches:
        code = m.get("code", "")
        status = m.get("status", "not_applicable")
        c = crit_by_code.get(code)
        if not c or status == "not_applicable":
            continue
        if c.severity == "required":
            req_total += 1
            if status == "met":
                req_met += 1
        else:
            sup_evaluated += 1
            if status == "met":
                sup_met += 1

    base = (req_met / req_total) if req_total > 0 else 1.0

    # If all required criteria met, floor at 0.75
    if req_total > 0 and req_met == req_total:
        base = max(base, 0.75)

    # Supporting boost up to +20% (tuned higher because in practice these
    # almost always lift approval probability)
    boost = 0.20 * (sup_met / sup_evaluated) if sup_evaluated > 0 else 0.0

    return round(min(1.0, base + boost), 2)


def _label_from_score(score: float) -> str:
    """Map approval score to label."""
    if score >= 0.80:
        return "HIGH"
    if score >= 0.60:
        return "MEDIUM"
    if score >= 0.40:
        return "LOW"
    return "INSUFFICIENT"


def reason_with_unified_agent(ctx: CaseContext, config: Config) -> None:
    """Public entry point — run unified reasoning on the context."""
    UnifiedReasoner(config).reason(ctx)
