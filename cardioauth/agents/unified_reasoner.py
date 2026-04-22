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

GAP CLASSIFICATION — PATHWAY GROUPS (Peter C10-C13 Apr 14)

Some criteria are ALTERNATIVE pathways to qualify, not all required
together. When you receive the criteria list you will see each criterion
annotated with an optional `pathway_group`. If criteria share a
pathway_group, the case qualifies if ANY ONE is met — the others are
not "gaps", they are simply pathways not used.

When you produce your narrative and reasoning:

  - Do NOT list unmet pathway_group members as blocking deficiencies
    if another member of that group is met.
  - The system post-processor will reclassify them automatically, but
    your narrative should not claim the case is weak on the basis of
    alternative pathways not used.

Example: For CPT 78492 the pharmacologic-stress-justification pathway
group contains EX-001, ECG-001, ECG-002, ECG-003, ECG-004. If the chart
documents an inability to exercise (EX-001 met), then the case has its
pharmacologic justification — the absence of LBBB, paced rhythm, WPW,
and LVH is NOT a gap. It's simply "alternatives not used."

NARRATIVE STAY-IN-TAXONOMY RULE (Peter C10-C13 #2)

Your narrative MUST only argue medical necessity using the scored
criteria. Do NOT invoke clinical reasoning that isn't backed by a
coded criterion (e.g. citing RBBB/LAFB as if they were formal PET
pathways when the taxonomy does not include them). If you genuinely
believe a case merits a clinical argument beyond the taxonomy, place
it in a separate `supplemental_clinical_argument` field with the
explicit disclaimer "not policy-backed — for physician judgment only".

HEADLINE SUMMARY (Peter C10-C13 #6)

Return a `headline_summary` with the top 1–3 real reasons the case is
strong or weak. This is what the physician reads first. Examples:
  - "Strong: prior SPECT non-diagnostic + LBBB on ECG + BMI 38"
  - "Weak: no qualifying pathway for pharmacologic stress (patient
     exercises without limitation; no ECG abnormality)"
  - "Weak: BMI not documented; no prior non-diagnostic testing"

Keep each reason to ≤12 words. Do NOT include pathway alternatives
not used as reasons.

COMPLETENESS + CPT GATING — CRITICAL RULES

The list of criteria you receive has ALREADY BEEN FILTERED to those that
apply to the requested CPT code. You are NOT allowed to mark any of them
"not_applicable" — by definition, every criterion in your list IS applicable
to this procedure.

Rules for the final classification of each criterion in the list:

  - "met" — clearly supported by verbatim evidence in the note AND every
            required_element (if any) has a verbatim quote satisfying it
  - "not_met" — applicable but the note lacks sufficient documentation,
                OR one or more required_elements lack evidence
                (this is the DEFAULT when evidence is absent or ambiguous)

You MUST return exactly one criterion_matches entry for each criterion in
the provided CRITERIA TO EVALUATE list. Do not skip any. Do not use
"not_applicable" — the CPT filter already handled applicability upstream.

If you're not sure whether a criterion is met, default to "not_met" and
state what additional documentation would be required.

═══════════════════════════════════════════════════════════════════════════
DEFINITIONAL COMPLETENESS — NEW STRICT RULE (Apr 13, 2026)
═══════════════════════════════════════════════════════════════════════════

Each criterion may include `required_elements`. These are atomic facts
that must ALL be documented for the criterion to qualify as "met".
Presence of ONE element does NOT satisfy the criterion — you must find
evidence for EVERY required_element.

For each criterion with required_elements, you MUST return per-element
findings in `elements_satisfied`:

  {
    "key": "<the element key>",
    "found": true | false,
    "evidence_quote": "<verbatim <=15-word quote from note if found, else empty>"
  }

Enforcement rule (the system will also deterministically enforce this
after you respond — but you should apply it yourself first):

  If ANY required_element has found=false, the criterion's status MUST be
  "not_met". Do not mark met unless every element is found with a quote.

Failure patterns to avoid (Peter's Apr 13 cases):

  ✗ MED-002: medication list shown, no start date or duration documented
    → INCORRECT to mark met. "start_date_or_duration" element is NOT found.
    → Correct: not_met, gap="No start date or duration ≥6 weeks documented"

  ✗ SX-001 (repeat imaging): symptoms described but no comparison to
    baseline, or explicit "no new complaints"
    → INCORRECT to mark met. "change_vs_baseline" element is NOT found.
    → Correct: not_met, gap="No explicit new/worsening change vs baseline"

  ✗ SX-002: symptoms mentioned, no onset date / frequency / progression
    → INCORRECT to mark met. Any missing element = not_met.

  ✗ EX-001: "dyspnea on exertion" noted, no explicit statement linking it
    to inability to perform stress testing
    → INCORRECT to mark met. "explicit_causal_link_to_exercise" NOT found.
    → Correct: not_met, gap="Dyspnea documented but no explicit link to
       inability to exercise / perform TST"

Rule of thumb: if you would have to INFER one of the required_elements
rather than quote it, the element is NOT found.

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
      "recommendation": "",
      "elements_satisfied": [
        {"key": "specific_limiting_condition", "found": true, "evidence_quote": "dyspnea and obesity"},
        {"key": "explicit_causal_link_to_exercise", "found": true, "evidence_quote": "Unable to do TST due to"}
      ]
    }
  ],
  "narrative_draft": "Full PA narrative (400-600 words) with clinical rationale, evidence citations, and guideline references.",
  "approval_score": 0.85,
  "approval_label": "HIGH",
  "required_criteria_met": 8,
  "required_criteria_total": 10,
  "key_supporting_evidence": [
    "Short bullet summarizing the strongest clinical support"
  ],
  "headline_summary": [
    "Top 1-3 reasons case is strong or weak, each <=12 words",
    "Do not list alternative pathways not used as reasons"
  ],
  "supplemental_clinical_argument": ""
}
"""


def _build_user_message(ctx: CaseContext, applicable_criteria: list) -> str:
    """Build the user message with full clinical context."""

    # Format the taxonomy as a structured list, including required_elements so
    # the reasoner must verify each element independently before marking met.
    # This addresses Peter's Apr 13 feedback: presence of a feature was being
    # treated as satisfaction of the full definition.
    criteria_json = json.dumps([
        {
            "code": c.code,
            "category": c.category,
            "short_name": c.short_name,
            "definition": c.definition,
            "evidence_type": c.evidence_type,
            "severity": c.severity,
            # Peter C10-C13: pathway_group tells the reasoner that criteria
            # sharing the same group value are ALTERNATIVES. Case qualifies if
            # any one is met — unmet others are not gaps, just alternatives.
            "pathway_group": c.pathway_group or None,
            "required_elements": [
                {
                    "key": e.key,
                    "description": e.description,
                    "evidence_hint": e.evidence_hint,
                }
                for e in (c.required_elements or [])
            ],
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

    # ── Calibration layer — P2 stats + payer-global rules + freshness ──
    # Historical approval rates anchor the score. Denial patterns are things
    # the narrative should address preemptively. Global rules are cross-CPT
    # gotchas (in-network ordering, eligibility re-verification, etc.).
    calibration_text = ""
    from cardioauth.stats import check_policy_freshness, get_global_rules, get_payer_stats
    stats = get_payer_stats(ctx.payer_name, ctx.procedure_code)
    if stats:
        calibration_text += (
            f"\n\nHISTORICAL PAYER STATISTICS ({ctx.payer_name} / {ctx.procedure_code}, "
            f"n={stats.sample_size}, vintage={stats.data_vintage}):\n"
            f"  First-pass approval rate: {stats.approval_rate:.0%}\n"
        )
        if stats.p2p_success_rate is not None:
            calibration_text += f"  Peer-to-peer overturn rate: {stats.p2p_success_rate:.0%}\n"
        if stats.appeal_win_rate is not None:
            calibration_text += f"  Appeal win rate: {stats.appeal_win_rate:.0%}\n"
        if stats.top_denial_reasons:
            calibration_text += "  Top historical denial reasons (address these in the narrative):\n"
            for reason in stats.top_denial_reasons[:5]:
                calibration_text += f"    - {reason}\n"
        calibration_text += (
            f"\nCalibration hint: when documentation aligns with what this payer\n"
            f"historically approves for this CPT, use {stats.approval_rate:.0%} as an\n"
            f"anchor; adjust up if the case is strong on all required criteria, adjust\n"
            f"down if any historical denial reason is present.\n"
        )

    global_rules = get_global_rules(ctx.payer_name)
    if global_rules:
        calibration_text += f"\nPAYER-GLOBAL RULES ({ctx.payer_name} — apply across every CPT):\n"
        for rule in global_rules:
            marker = "⚠" if rule.denial_if_missed else "•"
            calibration_text += f"  {marker} [{rule.rule_id} / {rule.kind}] {rule.description}\n"
        calibration_text += (
            "\nIf any global rule is not addressable from the chart, add it to\n"
            "cardiologist_review_flags so the physician can verify before submission.\n"
        )

    if ctx.policy_data:
        freshness = check_policy_freshness(ctx.policy_data.get("policy_last_updated"))
        if freshness.level == "stale_critical":
            calibration_text += f"\n⚠ POLICY FRESHNESS: {freshness.message} Flag this for human review.\n"
        elif freshness.level == "stale_warning":
            calibration_text += f"\nPOLICY FRESHNESS: {freshness.message}\n"
        elif freshness.level == "unknown":
            calibration_text += f"\nPOLICY FRESHNESS: {freshness.message}\n"

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
        f"{calibration_text}"
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
    """Reasons + scores taxonomy via one or more Claude calls.

    Self-consistency ensemble (config.reasoning_ensemble_n > 1):
      Runs the reasoner N times with mild temperature, then merges by
      majority vote per criterion. Produces an agreement_score per
      criterion so physicians can tell confident decisions from noisy
      ones. Standard technique from Wang et al. self-consistency CoT.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def reason(self, ctx: CaseContext) -> None:
        """Run unified reasoning (ensemble or single). Populates ctx."""
        start = time.time()
        ctx.build_clinical_narrative()

        applicable = get_criteria_for_procedure(ctx.procedure_code, ctx.payer_name)
        if not applicable:
            logger.warning("UnifiedReasoner: no taxonomy criteria for CPT %s", ctx.procedure_code)
            ctx.warn("warning", "UnifiedReasoner",
                     f"No taxonomy criteria defined for CPT {ctx.procedure_code}", "no_criteria")
            return

        user_msg = _build_user_message(ctx, applicable)
        n_runs = max(1, int(self.config.reasoning_ensemble_n))

        # Use temperature 0 for single runs (deterministic), the configured
        # temperature for ensembles (diversity across samples).
        temperature = 0.0 if n_runs == 1 else self.config.reasoning_ensemble_temperature

        run_outputs: list[dict] = []
        tokens_total = 0
        for i in range(n_runs):
            result = self._invoke_once(ctx, user_msg, temperature, run_index=i)
            if result is None:
                continue
            run_outputs.append(result["data"])
            tokens_total += result["tokens"]

        if not run_outputs:
            # Every run failed — warnings already recorded in _invoke_once
            return

        # Merge per-criterion across runs (majority vote + agreement scoring)
        if len(run_outputs) == 1:
            merged_matches, per_criterion_agreement = self._merge_single(
                run_outputs[0].get("criterion_matches", []), applicable,
            )
        else:
            merged_matches, per_criterion_agreement = self._merge_ensemble(
                [r.get("criterion_matches", []) for r in run_outputs],
                applicable,
            )

        ctx.criterion_matches = merged_matches
        ctx.narrative_draft = run_outputs[0].get("narrative_draft", "")

        # Stash headline summary + supplemental argument from the first run
        # (these don't make sense to majority-vote — keep first-run version)
        first = run_outputs[0]
        ctx.__dict__["_headline_summary"] = first.get("headline_summary") or []
        ctx.__dict__["_supplemental_clinical_argument"] = first.get("supplemental_clinical_argument", "")

        # Classify gaps into blocking / alternative_not_used / supporting_unmet.
        # Peter C10-C13 #1: the UI and narrative should treat these differently.
        try:
            from cardioauth.taxonomy.taxonomy import classify_gaps
            gap_classes = classify_gaps(applicable, merged_matches)
            ctx.__dict__["_gap_classification"] = {
                "blocking": gap_classes["blocking"],
                "alternative_not_used": gap_classes["alternative_not_used"],
                "supporting_unmet": gap_classes["supporting_unmet"],
            }
            # Apply the _gap_class tag back onto the merged_matches so the
            # audit trail and UI can render per-criterion classification.
            by_code = {m.get("code"): m for m in merged_matches}
            for bucket_name in ("blocking", "alternative_not_used", "supporting_unmet"):
                for m in gap_classes[bucket_name]:
                    target = by_code.get(m.get("code"))
                    if target is not None:
                        target["_gap_class"] = bucket_name
                        if m.get("_pathway_group"):
                            target["_pathway_group"] = m["_pathway_group"]
        except Exception as e:
            logger.warning("classify_gaps failed: %s", e)
            ctx.__dict__["_gap_classification"] = None

        # Score: mean across runs, falling back to computed score if LLM didn't return one
        scores = [r.get("approval_score") for r in run_outputs if isinstance(r.get("approval_score"), (int, float))]
        if scores:
            reasoner_score = float(sum(scores)) / len(scores)
        else:
            reasoner_score = _compute_approval_score(ctx.criterion_matches, applicable)

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

        # Ensemble-level agreement summary
        if per_criterion_agreement:
            agreements = [a for a in per_criterion_agreement.values() if a is not None]
            case_agreement = round(sum(agreements) / len(agreements), 2) if agreements else 1.0
            ctx.__dict__["_ensemble_agreement"] = {
                "n_runs": len(run_outputs),
                "case_agreement_score": case_agreement,
                "per_criterion": per_criterion_agreement,
            }

            # Flag low-agreement criteria to the cardiologist
            threshold = self.config.reasoning_agreement_flag_threshold
            low_agreement = [
                code for code, agree in per_criterion_agreement.items()
                if agree is not None and agree < threshold
            ]
            if low_agreement:
                ctx.warn(
                    "warning", "UnifiedReasoner",
                    f"Low agreement across {len(run_outputs)} reasoner runs on: "
                    f"{', '.join(sorted(low_agreement))} — cardiologist should verify.",
                    "low_agreement",
                )
        else:
            ctx.__dict__["_ensemble_agreement"] = {
                "n_runs": len(run_outputs),
                "case_agreement_score": 1.0,
                "per_criterion": {},
            }

        ctx.approval_label = run_outputs[0].get("approval_label") or _label_from_score(ctx.approval_score)

        elapsed = int((time.time() - start) * 1000)
        agreement_str = ""
        if len(run_outputs) > 1:
            agreement_str = f", agreement={ctx.__dict__['_ensemble_agreement']['case_agreement_score']:.2f}"
        ctx.trace(
            agent_name="UnifiedReasoner",
            action=f"scored {len(ctx.criterion_matches)} criteria across {len(run_outputs)} run(s), approval={ctx.approval_score:.2f}{agreement_str}",
            summary=f"{ctx.approval_label}",
            tokens=tokens_total,
            ms=elapsed,
        )

    # ── Ensemble plumbing ─────────────────────────────────────────────

    def _invoke_once(
        self,
        ctx: CaseContext,
        user_msg: str,
        temperature: float,
        run_index: int,
    ) -> dict | None:
        """One LLM call. Returns {data, tokens} or None on failure."""
        from cardioauth.claude_cost import TimedCall, system_with_cache_control, track_usage
        try:
            kwargs = dict(
                model=self.config.model,
                max_tokens=8000,
                # Cache the large stable system prompt (~6KB) so ensemble
                # runs 2 and 3 read it from cache instead of re-tokenizing.
                system=system_with_cache_control(SYSTEM_PROMPT),
                messages=[{"role": "user", "content": user_msg}],
            )
            if temperature > 0:
                kwargs["temperature"] = temperature
            with TimedCall() as _t:
                response = self.client.messages.create(**kwargs)
            track_usage(
                response,
                agent="UNIFIED_REASONER",
                model=self.config.model,
                duration_ms=_t.ms,
                case_id=ctx.case_id,
            )

            raw = response.content[0].text
            tokens = 0
            if hasattr(response, "usage"):
                tokens = getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0)

            data = parse_llm_json(raw, fallback={
                "criterion_matches": [],
                "narrative_draft": "",
                "approval_score": 0.0,
                "approval_label": "INSUFFICIENT",
            })
            return {"data": data, "tokens": tokens}

        except anthropic.BadRequestError as e:
            msg = str(e)
            kind = "spend_limit" if ("usage limit" in msg.lower() or "spend limit" in msg.lower()) else "bad_request"
            ctx.warn("critical", "UnifiedReasoner", f"[run {run_index}] {msg[:200]}", kind)
            logger.warning("UnifiedReasoner BadRequest (run %d): %s", run_index, msg[:200])
        except anthropic.RateLimitError:
            ctx.warn("warning", "UnifiedReasoner", f"[run {run_index}] Claude rate limit hit", "rate_limit")
            logger.warning("UnifiedReasoner rate limit (run %d)", run_index)
        except Exception as e:
            ctx.warn("warning", "UnifiedReasoner", f"[run {run_index}] Reasoning failed: {str(e)[:200]}", "error")
            logger.exception("UnifiedReasoner failed (run %d)", run_index)
        return None

    @staticmethod
    def _merge_single(
        raw_matches: list[dict],
        applicable: list,
    ) -> tuple[list[dict], dict[str, float]]:
        """Single-run merge = just enforce CPT gating and record agreement=1.0."""
        enforced = _enforce_cpt_gating(raw_matches, applicable)
        agreement = {e["code"]: 1.0 for e in enforced if "code" in e}
        return enforced, agreement

    @staticmethod
    def _merge_ensemble(
        per_run_matches: list[list[dict]],
        applicable: list,
    ) -> tuple[list[dict], dict[str, float]]:
        """Majority-vote status per criterion across runs; record agreement.

        Agreement score for a criterion = (# runs that agreed with majority) / N.
        Tie goes to 'not_met' (safer default — we'd rather the physician
        verify a borderline case than auto-approve).
        """
        # Enforce each run individually first — so missing criteria get filled
        # before voting.
        enforced_runs = [_enforce_cpt_gating(m, applicable) for m in per_run_matches]
        n_runs = len(enforced_runs)
        codes = [c.code for c in applicable]

        merged: list[dict] = []
        agreement: dict[str, float] = {}

        for code in sorted(codes):
            runs_for_code = [next((e for e in run if e.get("code") == code), None) for run in enforced_runs]
            runs_for_code = [r for r in runs_for_code if r]
            if not runs_for_code:
                continue

            statuses = [r.get("status", "not_met") for r in runs_for_code]
            met_count = sum(1 for s in statuses if s == "met")
            not_met_count = sum(1 for s in statuses if s == "not_met")

            # Majority — tie → not_met (conservative)
            if met_count > not_met_count:
                majority_status = "met"
                agreeing = met_count
            else:
                majority_status = "not_met"
                agreeing = not_met_count

            # Representative entry: take the first run matching majority status
            representative = next(
                (r for r in runs_for_code if r.get("status") == majority_status),
                runs_for_code[0],
            )

            merged_entry = dict(representative)
            merged_entry["status"] = majority_status
            merged_entry["_ensemble_n_runs"] = n_runs
            merged_entry["_ensemble_agreement"] = round(agreeing / n_runs, 2)
            merged_entry["_ensemble_statuses"] = statuses
            if agreeing < n_runs:
                merged_entry["_ensemble_dissent"] = (
                    f"{agreeing}/{n_runs} runs said {majority_status}; "
                    f"{n_runs - agreeing} dissenting"
                )
            merged.append(merged_entry)
            agreement[code] = round(agreeing / n_runs, 2)

        return merged, agreement


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


def _enforce_element_completeness(entry: dict, crit) -> dict:
    """Enforce that all required_elements are found with evidence.

    If the criterion has required_elements and any is missing, force
    status=not_met regardless of what the LLM decided. Backs Peter's
    Apr 13 feedback: the LLM was marking criteria met when only part
    of the definition was documented.
    """
    required = getattr(crit, "required_elements", None) or []
    if not required:
        return entry

    reported = {e.get("key"): e for e in (entry.get("elements_satisfied") or [])}
    missing_keys: list[str] = []
    missing_descriptions: list[str] = []

    for re in required:
        elem = reported.get(re.key)
        if not elem or not elem.get("found"):
            missing_keys.append(re.key)
            missing_descriptions.append(f"{re.key}: {re.description}")

    if missing_keys and entry.get("status") == "met":
        # Force not_met — presence of one element is not satisfaction
        entry["status"] = "not_met"
        prev_enforced = entry.get("_enforced", "")
        entry["_enforced"] = (prev_enforced + ";" if prev_enforced else "") + "element_incomplete"
        existing_gap = entry.get("gap", "") or ""
        extra_gap = (
            f"Definitional completeness not met — missing elements: "
            f"{', '.join(missing_keys)}. Need: {' | '.join(missing_descriptions)}"
        )
        entry["gap"] = (existing_gap + " " + extra_gap).strip() if existing_gap else extra_gap
        if not entry.get("recommendation"):
            entry["recommendation"] = (
                f"Document the missing elements explicitly in the note: {', '.join(missing_keys)}"
            )

    # Surface the element-level verdict for the audit trail
    entry["_missing_elements"] = missing_keys
    return entry


def _enforce_cpt_gating(raw_matches: list[dict], applicable: list) -> list[dict]:
    """Enforce Peter's rules deterministically:

    1. Every applicable criterion MUST have an entry (no silent skipping)
    2. Applicable criteria cannot be marked "not_applicable" — coerce to "not_met"
    3. If the LLM didn't return a criterion, synthesize a "not_met" entry
    4. Normalize status values (accept "met"/"MET"/"Met" → "met")
    5. Definitional completeness: if any required_element lacks evidence,
       force status=not_met (Apr 13 fix)
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
                "_missing_elements": [e.key for e in (crit.required_elements or [])],
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
            enforced_entry = _enforce_element_completeness(enforced_entry, crit)
            enforced.append(enforced_entry)
            continue

        # Accepted status — normalize back, then enforce element completeness
        enforced_entry = dict(llm_entry)
        enforced_entry["status"] = status
        enforced_entry = _enforce_element_completeness(enforced_entry, crit)
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
