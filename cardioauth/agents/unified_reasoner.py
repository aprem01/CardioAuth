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


FEW_SHOT_EXAMPLES = """\
═════════════════════════ FEW-SHOT EXAMPLES ═════════════════════════

These are CORRECT evaluations from validated cases. Match this reasoning style.

─── EXAMPLE 1 ───
Note excerpt: "67M with CAD. Unable to do TST due to dyspnea and obesity (BMI 38).
Prior exercise treadmill test non-diagnostic at 68% MPHR. CCS Class III exertional
angina despite 6 weeks optimal medical therapy. Cardiology office note attached."

Correct evaluations:
  EX-001 = met — "Unable to do TST due to dyspnea and obesity"
  BMI-001 = met — "BMI 38" (≥35 threshold)
  NDX-002 = met — "68% MPHR" (submaximal, <85%)
  SX-003 = met — "CCS Class III exertional angina"
  SX-004 = met — "CCS Class III" (validated functional class)
  DOC-001 = met — "Cardiology office note attached"
  MED-001 = met — "6 weeks optimal medical therapy" (documented trial)
  ECG-001 = not_applicable — no LBBB mentioned
  LVEF-002 = not_applicable — LVEF not reduced

Approval score: 0.91 (HIGH) — all required criteria met, 4 supporting met.

─── EXAMPLE 2 ───
Note excerpt: "72F with CAD s/p PCI. Prior SPECT showed attenuation artifact,
likely false positive. CCS Class II angina. BMI 36. Office visit note included."

Correct evaluations:
  NDX-001 = met — "Prior SPECT showed attenuation artifact, likely false positive"
  BMI-001 = met — "BMI 36" (≥35)
  DOC-001 = met — "Office visit note included"
  SX-003 = met — "CCS Class II angina"
  SX-004 = met — "CCS Class II" (validated functional class)
  EX-001 = not_applicable — no exercise limitation noted
  ECG-001 = not_applicable — no LBBB

Approval score: 0.85 (HIGH).

─── EXAMPLE 3 ───
Note excerpt: "62M ischemic cardiomyopathy, LVEF 35%, LBBB on baseline ECG.
NYHA Class III despite maximal GDMT x 6 months. Cardiac stress PET for viability."

Correct evaluations:
  LVEF-002 = met — "LVEF 35%" (≤40 threshold)
  ECG-001 = met — "LBBB on baseline ECG"
  SX-004 = met — "NYHA Class III"
  MED-001 = met — "maximal GDMT x 6 months" (documented maximal therapy)
  DOC-001 = met — clinical narrative with H&P provided
  BMI-001 = not_applicable — BMI not specified or not ≥35

Approval score: 0.89 (HIGH).

═══════════════════════════════════════════════════════════════════

"""


SYSTEM_PROMPT = FEW_SHOT_EXAMPLES + """\
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

COMPLETENESS + CPT GATING — CRITICAL RULES

The list of criteria you receive has ALREADY BEEN FILTERED to those that
apply to the requested CPT code. You are NOT allowed to mark any of them
"not_applicable" — by definition, every criterion in your list IS applicable
to this procedure.

Rules for the final classification of each criterion in the list:

  - "met" — clearly supported by verbatim evidence in the note
  - "not_met" — applicable but the note lacks sufficient documentation
                (this is the DEFAULT when evidence is absent or ambiguous)

You MUST return exactly one criterion_matches entry for each criterion in
the provided CRITERIA TO EVALUATE list. Do not skip any. Do not use
"not_applicable" — the CPT filter already handled applicability upstream.

If you're not sure whether a criterion is met, default to "not_met" and
state what additional documentation would be required.

Examples of WRONG behavior (do not do this):

  ✗ Skipping ECG-003 because the note doesn't mention WPW. Correct: return
    ECG-003 with status="not_met", gap="No WPW pattern documented on ECG"
  ✗ Skipping NDX-004 because there's no echo. Correct: return NDX-004 with
    status="not_met", gap="No prior technically limited echo documented"
  ✗ Marking DOC-001 "not_applicable" because the office note is short.
    Correct: evaluate the note content; if clinical narrative is present,
    mark it "met"; if not, "not_met"

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

    # Gold-standard training cases — retrieved by SEMANTIC SIMILARITY (Pathway 3)
    training_examples_text = ""
    try:
        from cardioauth.training import retrieve_similar_gold_cases, get_all_training_cases
        # Prefer similarity-based retrieval from Pinecone
        matching = retrieve_similar_gold_cases(
            case_summary=ctx.raw_note[:1500],
            procedure_code=ctx.procedure_code,
            top_k=3,
        )
        # Fallback: if Pinecone unavailable, load by CPT match from JSONL
        if not matching:
            all_cases = get_all_training_cases()
            matching = [
                tc for tc in all_cases
                if tc.get("procedure_code") == ctx.procedure_code
                and tc.get("criterion_labels")
            ][:2]

        if matching:
            training_examples_text = "\n\nGOLD-STANDARD PHYSICIAN-LABELED CASES (most similar to this case):\n"
            for tc in matching:
                sim = tc.get("_similarity")
                sim_str = f" (similarity {sim:.2f})" if sim else ""
                training_examples_text += f"\n  Case: {tc.get('title', tc.get('case_id', ''))}{sim_str}\n"
                training_examples_text += f"  Outcome: {tc.get('actual_outcome', 'unknown')}, "
                training_examples_text += f"Gold score: {tc.get('gold_approval_score', 0):.2f}\n"
                training_examples_text += f"  Note excerpt: {tc.get('raw_note', '')[:400]}...\n"
                training_examples_text += f"  Physician gold labels:\n"
                for lbl in (tc.get("criterion_labels", []) or [])[:10]:
                    training_examples_text += f"    {lbl.get('code')}: {lbl.get('gold_status')}"
                    if lbl.get('gold_evidence'):
                        training_examples_text += f" — \"{lbl['gold_evidence'][:80]}\""
                    training_examples_text += "\n"
            training_examples_text += "\nMatch this physician's reasoning style closely when evaluating the current case.\n"
    except Exception as e:
        logger.warning("Training gold retrieval for prompt failed: %s", e)

    # Past physician corrections on similar cases — "mistakes to avoid"
    corrections_text = ""
    try:
        from cardioauth.feedback import retrieve_relevant_corrections
        corrections = retrieve_relevant_corrections(
            case_summary=ctx.raw_note[:1500],
            procedure_code=ctx.procedure_code,
            payer=ctx.payer_name,
            top_k=3,
        )
        if corrections:
            corrections_text = "\n\nPAST PHYSICIAN CORRECTIONS (similar cases — avoid these mistakes):\n"
            for c in corrections:
                corrections_text += (
                    f"  - Criterion {c.get('criterion_code', '')}: "
                    f"system previously said '{c.get('system_said', '')}' "
                    f"but physician corrected to '{c.get('physician_said', '')}'. "
                    f"Reason: {c.get('reason', '')[:150]}\n"
                )
    except Exception:
        pass

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
        f"{training_examples_text}"
        f"{policy_text}"
        f"{corrections_text}\n\n"
        f"CRITERIA TO EVALUATE (evaluate each against the raw note above):\n"
        f"{criteria_json}\n\n"
        f"Produce the JSON output per the system prompt. You MUST include "
        f"a criterion_matches entry for EVERY criterion listed above "
        f"(that's {len(applicable_criteria)} criteria total — return exactly "
        f"{len(applicable_criteria)} entries). For each criterion assign "
        f"status='met' (if supported by evidence) or 'not_met' (if applicable "
        f"but not supported). Never 'not_applicable' — the CPT filter already "
        f"handled applicability. For each MET criterion, include a verbatim "
        f"evidence_quote from the raw note."
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

            # Populate context — but enforce Peter's rules deterministically
            # regardless of what the LLM returned (per-case validation feedback)
            raw_matches = data.get("criterion_matches", [])
            ctx.criterion_matches = _enforce_cpt_gating(raw_matches, applicable)
            ctx.narrative_draft = data.get("narrative_draft", "")

            # Use returned score, or compute from matches
            returned_score = data.get("approval_score")
            if isinstance(returned_score, (int, float)):
                reasoner_score = float(returned_score)
            else:
                reasoner_score = _compute_approval_score(
                    ctx.criterion_matches, applicable
                )

            # Precedent-weighted blend: 70% reasoner + 30% precedent median
            # Only apply when we have precedents of the same procedure type
            precedent_score = _precedent_median_score(ctx.precedents, ctx.procedure_code)
            if precedent_score is not None:
                ctx.approval_score = round(0.7 * reasoner_score + 0.3 * precedent_score, 2)
                ctx.trace(
                    "ApprovalScoring",
                    f"blended: reasoner={reasoner_score:.2f} × 0.7 + precedent_median={precedent_score:.2f} × 0.3",
                    f"final={ctx.approval_score:.2f}",
                )
            else:
                ctx.approval_score = round(reasoner_score, 2)

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
        status = m.get("status", "not_met")
        c = crit_by_code.get(code)
        if not c:
            continue
        # After CPT gating, every applicable criterion has status met/not_met.
        # (not_applicable should not occur here; if it does, treat as not_met.)
        if status == "not_applicable":
            status = "not_met"
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


def _enforce_cpt_gating(raw_matches: list[dict], applicable: list) -> list[dict]:
    """Enforce Peter's rules deterministically:

    1. Every applicable criterion MUST have an entry (no silent skipping)
    2. Applicable criteria cannot be marked "not_applicable" — coerce to "not_met"
    3. If the LLM didn't return a criterion, synthesize a "not_met" entry
    4. Normalize status values (accept "met"/"MET"/"Met" → "met")

    This is the post-processing fix for the "14 missing" / "sometimes n/a"
    pattern Peter documented.
    """
    applicable_codes = {c.code for c in applicable}
    crit_by_code = {c.code: c for c in applicable}

    # Index what the LLM returned
    returned_by_code: dict[str, dict] = {}
    for m in raw_matches:
        code = m.get("code", "")
        if not code:
            continue
        returned_by_code[code] = m

    enforced: list[dict] = []
    for code in sorted(applicable_codes):
        crit = crit_by_code[code]
        llm_entry = returned_by_code.get(code)

        if llm_entry is None:
            # LLM skipped this criterion — default to not_met
            enforced.append({
                "code": code,
                "status": "not_met",
                "confidence": 0.5,
                "evidence_quote": "",
                "reasoning": f"Criterion not addressed in reasoner output; defaulted to not_met per CPT gate rule.",
                "gap": crit.definition if hasattr(crit, "definition") else "Evidence required",
                "recommendation": f"Document evidence satisfying {code}: {crit.short_name}" if hasattr(crit, "short_name") else "",
                "_enforced": "missing_filled",
            })
            continue

        # Normalize status
        status = (llm_entry.get("status") or "").strip().lower()
        if status not in ("met", "not_met"):
            # Reject "not_applicable" (or empty/unknown) — coerce to not_met
            # because this criterion IS applicable to the CPT
            enforced_entry = dict(llm_entry)
            enforced_entry["status"] = "not_met"
            enforced_entry["_enforced"] = f"coerced_from_{status or 'empty'}"
            # Preserve any evidence the LLM provided
            if not enforced_entry.get("gap"):
                enforced_entry["gap"] = enforced_entry.get("reasoning", "")[:200] or "Applicable to CPT but no supporting evidence"
            enforced.append(enforced_entry)
            continue

        # Accepted status — normalize back
        enforced_entry = dict(llm_entry)
        enforced_entry["status"] = status
        enforced.append(enforced_entry)

    return enforced


def _precedent_median_score(precedents, cpt_code: str) -> float | None:
    """Return the median approval_score of precedents that share the same CPT
    AND were approved. Only consider top-3 by similarity.

    Returns None if no usable precedents.
    """
    if not precedents:
        return None
    # Filter: same CPT + approved outcome
    matching = [p for p in precedents
                if p.cpt_code == cpt_code and (p.outcome or "").lower() == "approved"]
    if not matching:
        return None
    # Use top 3 by similarity
    top = sorted(matching, key=lambda p: p.similarity, reverse=True)[:3]
    # Extract scores from metadata if available; otherwise default to 0.85 for approved cases
    scores = []
    for p in top:
        # Precedent may have approval_score stored; fall back to 0.85 for approved
        if hasattr(p, "approval_score") and p.approval_score:
            scores.append(p.approval_score)
        else:
            scores.append(0.85)  # approved-case default
    if not scores:
        return None
    # Median
    scores.sort()
    n = len(scores)
    if n % 2 == 1:
        return scores[n // 2]
    return (scores[n // 2 - 1] + scores[n // 2]) / 2


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
