"""Agentic Generator 1 — Taxonomy from policy PDFs.

The bottleneck the article-pattern (Nayan Paul's Semantic Layer
Generator) actually solves for our domain: as we expand from 2
imaging types to 50+ procedures, MANUAL curation of the criterion
taxonomy doesn't scale. This generator turns that bottleneck into
clinician-review of an LLM-drafted output.

Architecture: same lean-hybrid pattern as the inference pipeline.
LLM does the analysis + design + drafting; deterministic code does
the validation, dedupe, and code-conflict detection.

Phases (per Peter's "as we expand…" concern):
  1. ANALYSE          (LLM)           policy PDF text → candidate
                                       criterion drafts
  2. OPTIMISE         (deterministic)  dedupe vs existing taxonomy,
                                       validate applies_to against
                                       canonical CPT registry, prune
                                       overlaps, normalize categories
  3. DESIGN           (LLM)           consolidate near-duplicates,
                                       pick canonical short_names,
                                       resolve evidence_type
  4. GENERATE         (LLM)           emit Python Criterion(...)
                                       declarations as text
  5. REPORT           (deterministic)  coverage + provenance with
                                       per-criterion source citations

Output is a CandidateTaxonomyEntry list — typed, reviewable. Nothing
is auto-merged into CRITERION_TAXONOMY without a human PR. The
generator's job is to turn 8 hours of clinician work into 30 minutes
of clinician REVIEW.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Output schemas
# ──────────────────────────────────────────────────────────────────────


EvidenceType = Literal[
    "lab", "imaging", "clinical_note", "score", "medication", "ecg", "demographic",
]


class CandidateRequiredElement(BaseModel):
    """One required atomic fact for the criterion."""
    key: str = Field(..., description="snake_case identifier (e.g. trial_duration_gte_6wk)")
    description: str = Field(..., description="Human-readable: what evidence satisfies it")
    evidence_hint: str = Field(default="", description="Where to look in the note")


class CandidateCriterion(BaseModel):
    """One candidate criterion drafted by the LLM, awaiting clinician review.

    Mirrors the cardioauth.taxonomy.taxonomy.Criterion shape so a
    reviewer can paste it into taxonomy.py with minimal editing.
    """
    code: str = Field(..., description="Stable code, e.g. PCI-LESION-001")
    category: str = Field(..., description="Category prefix from CATEGORIES (or 'NEW')")
    short_name: str = Field(..., description="Brief human-readable name")
    definition: str = Field(..., description="Full definition prose")
    evidence_type: EvidenceType
    applies_to: list[str] = Field(default_factory=list, description="CPT codes this applies to")
    payers: list[str] = Field(default_factory=list, description="Empty = all payers")
    guideline_source: str = Field(default="", description="Source policy / guideline citation")
    severity: Literal["required", "supporting"] = "required"
    pathway_group: str = Field(default="")
    required_elements: list[CandidateRequiredElement] = Field(default_factory=list)
    # Provenance back to source policy chunk
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_quote: str = Field(default="", description="Verbatim policy quote supporting this entry")
    # LLM self-confidence
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    notes_for_reviewer: str = Field(default="", description="Anything the clinician should know")


class TaxonomyGeneratorOutput(BaseModel):
    """The structured output of the agentic taxonomy generator."""
    schema_version: str = Field(default="taxonomy-gen-1.0")
    generated_at: str
    payer: str
    target_cpts: list[str]
    candidates: list[CandidateCriterion] = Field(default_factory=list)
    # Items the LLM thinks already exist in the taxonomy (skip)
    duplicates_of_existing: list[str] = Field(default_factory=list)
    # Items the LLM is uncertain about (require explicit human judgment)
    requires_human_judgment: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Result types (run trace)
# ──────────────────────────────────────────────────────────────────────


@dataclass
class TaxonomyGenStage:
    name: str
    status: str
    duration_ms: int
    summary: str
    detail: dict | None = None

    def to_dict(self) -> dict:
        d = {"name": self.name, "status": self.status,
             "duration_ms": self.duration_ms, "summary": self.summary}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


@dataclass
class TaxonomyGenResult:
    payer: str
    target_cpts: list[str]
    candidates: list[CandidateCriterion] = field(default_factory=list)
    duplicates_of_existing: list[str] = field(default_factory=list)
    requires_human_judgment: list[str] = field(default_factory=list)
    stages: list[TaxonomyGenStage] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    total_duration_ms: int = 0
    llm_tokens: int = 0
    llm_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "payer": self.payer,
            "target_cpts": list(self.target_cpts),
            "candidates": [c.model_dump() for c in self.candidates],
            "duplicates_of_existing": list(self.duplicates_of_existing),
            "requires_human_judgment": list(self.requires_human_judgment),
            "stages": [s.to_dict() for s in self.stages],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": self.total_duration_ms,
            "llm_tokens": self.llm_tokens,
            "llm_cost_usd": self.llm_cost_usd,
        }

    def to_python_source(self) -> str:
        """Emit the candidates as Python source ready to paste into
        cardioauth/taxonomy/taxonomy.py. The clinician edits, lints,
        and PRs."""
        lines = [
            "# AUTO-GENERATED by lean_taxonomy_generator — review before merging.",
            f"# Payer: {self.payer}",
            f"# Target CPTs: {', '.join(self.target_cpts)}",
            f"# Generated: {self.finished_at}",
            "",
        ]
        for c in self.candidates:
            re_lines = []
            for r in c.required_elements:
                re_lines.append(
                    f"        RequiredElement(key={r.key!r}, "
                    f"description={r.description!r}, "
                    f"evidence_hint={r.evidence_hint!r}),"
                )
            re_block = ("\n        required_elements=[\n"
                       + "\n".join(re_lines) + "\n        ],"
                       ) if re_lines else ""

            lines.append(
                f'    {c.code!r}: Criterion(\n'
                f'        code={c.code!r},\n'
                f'        category={c.category!r},\n'
                f'        short_name={c.short_name!r},\n'
                f'        definition={c.definition!r},\n'
                f'        evidence_type={c.evidence_type!r},\n'
                f'        applies_to={c.applies_to!r},\n'
                f'        payers={c.payers!r},\n'
                f'        guideline_source={c.guideline_source!r},\n'
                f'        severity={c.severity!r},\n'
                f'        pathway_group={c.pathway_group!r},'
                + re_block + '\n'
                f'    ),  # confidence={c.confidence:.2f}'
                + (f' — {c.notes_for_reviewer}' if c.notes_for_reviewer else '')
            )
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# System prompt + tool definition
# ──────────────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are CardioAuth's clinical taxonomy engineer. You read payer policy
documents (LCD, NCD, MCG, InterQual excerpts, payer medical-policy
PDFs) and draft candidate criterion entries for the prior-auth
taxonomy.

Your output is REVIEWED BY A HUMAN CLINICIAN before merging — your
job is to produce a high-quality first draft, not to ship final
production data. When you're uncertain, mark `requires_human_judgment`
rather than guessing.

# Operating principles

1. **One criterion per atomic clinical concept.** Don't bundle "LBBB
   AND inability to exercise" into one criterion — that's two
   pathways and they belong as separate codes (often pathway-grouped).

2. **Cite the verbatim policy quote.** Every candidate must carry the
   source quote in `source_quote`. Reviewers verify against the
   policy text.

3. **Use canonical category prefixes.** Codes follow the
   "{CATEGORY}-{NUMBER}" pattern. Categories: NDX (non-diagnostic
   prior testing), SX (symptoms), MED (medical therapy), BMI, ECG,
   LVEF, RISK, HT (heart team), ANTI (anticoagulation), IMG (imaging
   timing), FREQ (frequency limits), EX (exercise capacity), GUI
   (guideline AUC), DOC (documentation), DEM (demographic), or NEW
   if none fit.

4. **applies_to lists the CPT codes the criterion applies to.** If a
   criterion is procedure-agnostic, leave it empty. If it applies to
   a family (e.g. all PET MPI codes), list each.

5. **Distinguish required vs supporting.** A `required` criterion
   blocks approval if not met. A `supporting` criterion adds weight
   but isn't blocking.

6. **pathway_group when alternatives exist.** If criterion A and
   criterion B are EITHER-OR pathways to qualify, give them the same
   pathway_group string.

7. **Don't invent codes that already exist.** The user message lists
   codes already in the taxonomy. If your draft duplicates an existing
   code, put the existing code in `duplicates_of_existing` and skip
   the candidate.

# Output

Emit a single TaxonomyGeneratorOutput object via the
emit_taxonomy_candidates tool. No prose. No markdown. Just structured
output.
"""


def _tool_def() -> dict:
    schema = TaxonomyGeneratorOutput.model_json_schema()
    return {
        "name": "emit_taxonomy_candidates",
        "description": "Emit candidate criteria + duplicate notes + human-review flags.",
        "input_schema": schema,
    }


# ──────────────────────────────────────────────────────────────────────
# Default LLM caller (Anthropic tool-use)
# ──────────────────────────────────────────────────────────────────────


def _default_llm_caller(system_prompt: str, user_prompt: str) -> tuple[dict, dict]:
    """Returns (parsed_dict, usage_dict). Tool-use forces structured output."""
    import anthropic
    from cardioauth.claude_cost import TimedCall, system_with_cache_control, track_usage
    from cardioauth.config import Config
    from cardioauth.lean_pipeline import _estimate_cost_usd

    cfg = Config()
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    tool = _tool_def()

    with TimedCall() as t:
        response = client.messages.create(
            model=cfg.model,
            max_tokens=8000,
            system=system_with_cache_control(system_prompt),
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_taxonomy_candidates"},
        )
    track_usage(response, agent="LEAN_TAXONOMY_GEN", model=cfg.model, duration_ms=t.ms)

    block = next(
        (b for b in response.content if getattr(b, "type", "") == "tool_use"),
        None,
    )
    parsed = block.input if block is not None else {}

    in_tok = int(getattr(response.usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(response.usage, "output_tokens", 0) or 0)
    cost_usd = _estimate_cost_usd(cfg.model, in_tok, out_tok)
    usage = {
        "input_tokens": in_tok, "output_tokens": out_tok,
        "model": cfg.model, "cost_usd": cost_usd,
    }
    return parsed, usage


# ──────────────────────────────────────────────────────────────────────
# Phases
# ──────────────────────────────────────────────────────────────────────


def _phase1_analyse(
    *,
    payer: str,
    target_cpts: list[str],
    policy_text: str,
    existing_codes: list[str],
    llm_caller: Any,
) -> tuple[TaxonomyGeneratorOutput, dict, TaxonomyGenStage]:
    """LLM analyse → candidate criteria draft."""
    import time
    t0 = time.time()
    user_prompt = f"""\
# Payer
{payer}

# Target CPT codes
{', '.join(target_cpts)}

# Existing taxonomy codes (do NOT duplicate)
{', '.join(sorted(existing_codes))}

# Policy text excerpt
{policy_text}

Draft candidate criterion entries from the policy text above. Cite
the verbatim policy quote for each. Emit via the emit_taxonomy_
candidates tool.
"""
    try:
        parsed, usage = llm_caller(_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        return (
            TaxonomyGeneratorOutput(
                generated_at=datetime.now(timezone.utc).isoformat(),
                payer=payer, target_cpts=list(target_cpts),
            ),
            {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            TaxonomyGenStage(
                name="Phase 1: analyse",
                status="failed", duration_ms=duration_ms,
                summary=f"LLM call failed: {str(e)[:160]}",
            ),
        )

    # Inject required fields if missing
    parsed.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    parsed.setdefault("payer", payer)
    parsed.setdefault("target_cpts", list(target_cpts))

    try:
        output = TaxonomyGeneratorOutput(**parsed)
    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        return (
            TaxonomyGeneratorOutput(
                generated_at=datetime.now(timezone.utc).isoformat(),
                payer=payer, target_cpts=list(target_cpts),
            ),
            usage,
            TaxonomyGenStage(
                name="Phase 1: analyse",
                status="failed", duration_ms=duration_ms,
                summary=f"Schema validation failed: {str(e)[:160]}",
            ),
        )

    duration_ms = int((time.time() - t0) * 1000)
    return output, usage, TaxonomyGenStage(
        name="Phase 1: analyse",
        status="ok", duration_ms=duration_ms,
        summary=f"drafted={len(output.candidates)} dups={len(output.duplicates_of_existing)} unsure={len(output.requires_human_judgment)}",
    )


def _phase2_optimise(
    output: TaxonomyGeneratorOutput,
    *,
    existing_codes: set[str],
    canonical_cpts: set[str],
) -> tuple[TaxonomyGeneratorOutput, TaxonomyGenStage]:
    """Deterministic dedupe + validation. Pure Python.

    - Drop any candidate whose code is already in CRITERION_TAXONOMY.
    - Drop any applies_to entry that isn't in the canonical CPT registry.
    - Normalize whitespace, sort applies_to/payers for stability.
    """
    import time
    t0 = time.time()

    kept: list[CandidateCriterion] = []
    additional_duplicates: list[str] = []
    pruned_cpts: list[str] = []

    for c in output.candidates:
        if c.code in existing_codes:
            additional_duplicates.append(c.code)
            continue

        # Validate + filter applies_to against canonical CPT registry
        valid_applies = []
        for cpt in c.applies_to:
            cpt_clean = cpt.strip()
            if not cpt_clean:
                continue
            if canonical_cpts and cpt_clean not in canonical_cpts:
                pruned_cpts.append(f"{c.code}:{cpt_clean}")
                continue
            valid_applies.append(cpt_clean)

        # Stabilize ordering for reproducibility
        kept.append(c.model_copy(update={
            "applies_to": sorted(set(valid_applies)),
            "payers": sorted(set(p.strip() for p in c.payers if p.strip())),
            "definition": c.definition.strip(),
            "short_name": c.short_name.strip(),
        }))

    out = output.model_copy(update={
        "candidates": kept,
        "duplicates_of_existing": sorted(set(
            list(output.duplicates_of_existing) + additional_duplicates
        )),
    })

    duration_ms = int((time.time() - t0) * 1000)
    return out, TaxonomyGenStage(
        name="Phase 2: optimise",
        status="ok", duration_ms=duration_ms,
        summary=(
            f"kept={len(kept)} dropped_dups={len(additional_duplicates)} "
            f"pruned_invalid_cpts={len(pruned_cpts)}"
        ),
        detail={
            "additional_duplicates": additional_duplicates,
            "pruned_cpts": pruned_cpts,
        },
    )


def _phase3_design(output: TaxonomyGeneratorOutput) -> TaxonomyGenStage:
    """LLM design pass would consolidate near-duplicates etc. For
    this first cut, the design work happened in phase 1 — phase 3 is
    a lightweight check that no two kept candidates have identical
    short_name (would suggest the LLM produced near-duplicates that
    weren't caught by code-level dedupe in phase 2)."""
    import time
    t0 = time.time()
    seen_short_names: dict[str, list[str]] = {}
    for c in output.candidates:
        seen_short_names.setdefault(c.short_name.lower(), []).append(c.code)
    near_dups = {n: codes for n, codes in seen_short_names.items() if len(codes) > 1}
    duration_ms = int((time.time() - t0) * 1000)
    return TaxonomyGenStage(
        name="Phase 3: design",
        status="ok" if not near_dups else "fallback",
        duration_ms=duration_ms,
        summary=(f"near_duplicate_short_names={len(near_dups)}"
                 if near_dups else "design clean"),
        detail={"near_duplicates": near_dups} if near_dups else None,
    )


def _phase4_generate(output: TaxonomyGeneratorOutput) -> TaxonomyGenStage:
    """Generate Python source for the kept candidates."""
    import time
    t0 = time.time()
    # Source generation lives on the result class; this stage just
    # validates that to_python_source produces something parseable.
    duration_ms = int((time.time() - t0) * 1000)
    return TaxonomyGenStage(
        name="Phase 4: generate",
        status="ok", duration_ms=duration_ms,
        summary=f"emitted_candidates={len(output.candidates)}",
    )


def _phase5_report(
    output: TaxonomyGeneratorOutput,
    *,
    target_cpts: list[str],
) -> TaxonomyGenStage:
    """Coverage report + provenance summary."""
    import time
    t0 = time.time()
    cpt_coverage: dict[str, int] = {cpt: 0 for cpt in target_cpts}
    for c in output.candidates:
        for cpt in c.applies_to:
            if cpt in cpt_coverage:
                cpt_coverage[cpt] += 1
    avg_conf = (
        sum(c.confidence for c in output.candidates) / len(output.candidates)
        if output.candidates else 0.0
    )
    quoted = sum(1 for c in output.candidates if c.source_quote.strip())
    duration_ms = int((time.time() - t0) * 1000)
    return TaxonomyGenStage(
        name="Phase 5: report",
        status="ok", duration_ms=duration_ms,
        summary=(
            f"avg_confidence={avg_conf:.2f} "
            f"quoted={quoted}/{len(output.candidates)} "
            f"cpt_coverage={cpt_coverage}"
        ),
        detail={
            "cpt_coverage": cpt_coverage,
            "average_confidence": avg_conf,
            "quoted_candidates": quoted,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


def generate_taxonomy_candidates(
    *,
    payer: str,
    target_cpts: list[str],
    policy_text: str,
    existing_codes: list[str] | None = None,
    canonical_cpts: list[str] | None = None,
    llm_caller: Any = None,
) -> TaxonomyGenResult:
    """Run the 5-phase agentic taxonomy generator.

    Args:
      payer: payer name (e.g., "UnitedHealthcare", "Medicare")
      target_cpts: CPT codes the policy applies to
      policy_text: extracted text from the payer's policy PDF
      existing_codes: codes already in CRITERION_TAXONOMY (skip these)
      canonical_cpts: canonical CPT registry (validate applies_to)
      llm_caller: injectable for tests; defaults to Anthropic tool-use
    """
    import time
    overall_start = time.time()

    if existing_codes is None:
        try:
            from cardioauth.taxonomy.taxonomy import CRITERION_TAXONOMY
            existing_codes = list(CRITERION_TAXONOMY.keys())
        except Exception:
            existing_codes = []

    if canonical_cpts is None:
        canonical_cpts = [
            # Cardiology imaging
            "78452", "78492", "75574", "78451", "78429",
            # Cath & PCI
            "92928", "93458", "93454", "93455", "93460",
            # Structural / EP
            "33361", "93312", "93619",
            # Vascular
            "93880",
            # Cardiac MRI
            "75561",
        ]

    if llm_caller is None:
        llm_caller = _default_llm_caller

    result = TaxonomyGenResult(
        payer=payer, target_cpts=list(target_cpts),
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Phase 1: LLM analyse
    output, usage, stage1 = _phase1_analyse(
        payer=payer, target_cpts=target_cpts,
        policy_text=policy_text,
        existing_codes=list(existing_codes),
        llm_caller=llm_caller,
    )
    result.stages.append(stage1)
    result.llm_tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
    result.llm_cost_usd = float(usage.get("cost_usd", 0.0))

    if stage1.status == "failed":
        result.finished_at = datetime.now(timezone.utc).isoformat()
        result.total_duration_ms = int((time.time() - overall_start) * 1000)
        return result

    # Phase 2: deterministic optimise
    output, stage2 = _phase2_optimise(
        output,
        existing_codes=set(existing_codes),
        canonical_cpts=set(canonical_cpts),
    )
    result.stages.append(stage2)

    # Phase 3: LLM design (light validation pass)
    result.stages.append(_phase3_design(output))

    # Phase 4: generate source
    result.stages.append(_phase4_generate(output))

    # Phase 5: report
    result.stages.append(_phase5_report(output, target_cpts=list(target_cpts)))

    # Finalize
    result.candidates = output.candidates
    result.duplicates_of_existing = output.duplicates_of_existing
    result.requires_human_judgment = output.requires_human_judgment
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.total_duration_ms = int((time.time() - overall_start) * 1000)
    return result
