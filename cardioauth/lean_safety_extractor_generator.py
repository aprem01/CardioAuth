"""Agentic Generator 3 — safety_verifier extractors from criterion taxonomy.

The third (and final) of the content generators. Closes the
scaling loop: as the taxonomy grows from 17 → 250 criteria, the
safety_verifier must keep up with regex extractors that cover
every criterion's clinical signature INDEPENDENTLY of the LLM.
That's what gives us the "second witness" for hallucination
detection.

Today: safety_verifier.py has 8 hand-coded extractors (LBBB,
RBBB, paced rhythm, inability to exercise, etc.). Each takes ~30
minutes to write + denial-guard tune + test. 250 criteria × 30
min = 125 hours of careful regex work. With this generator:
~5 minutes of engineer review per criterion.

Architecture: same 5-phase lean-hybrid pattern. LLM proposes
patterns; deterministic code validates them against a small
corpus of clinical-language samples to catch obvious false-
positives BEFORE merging.

Phases:
  1. ANALYSE          (LLM)          criterion definition + sample
                                      notes → candidate regex
                                      patterns + denial guards
  2. OPTIMISE         (deterministic) compile each pattern; reject
                                      uncompilable; dedupe; ensure
                                      patterns use word boundaries
                                      where appropriate
  3. DESIGN           (LLM)          consolidate near-duplicate
                                      patterns; pick canonical
                                      function name + label
  4. GENERATE         (deterministic) emit Python check_xxx()
                                      function + denial-guard regex
                                      list ready to paste into
                                      safety_verifier.py
  5. REPORT           (deterministic) per-pattern false-positive
                                      rate against the supplied
                                      corpus + per-pattern hit rate
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Output schemas
# ──────────────────────────────────────────────────────────────────────


class CandidatePattern(BaseModel):
    """One proposed regex with optional context."""
    pattern: str = Field(..., description="Python regex (use \\b word boundaries)")
    rationale: str = Field(default="", description="Why this matches the clinical concept")
    sample_match: str = Field(default="", description="Example phrase the pattern catches")


class CandidateExtractor(BaseModel):
    """One safety-verifier extractor draft.

    Mirrors safety_verifier.check_lbbb() / check_rbbb() etc.
    """
    fact_id: str = Field(..., description="snake_case identifier (e.g. 'lbbb_present')")
    label: str = Field(..., description="Human-readable label (e.g. 'Left bundle branch block (LBBB)')")
    function_name: str = Field(..., description="check_xxx — Python function name")
    criterion_codes: list[str] = Field(
        default_factory=list,
        description="CRITERION_TAXONOMY codes this extractor maps to",
    )
    patterns: list[CandidatePattern] = Field(
        default_factory=list,
        description="Positive patterns — ANY match triggers present=True",
    )
    denial_patterns: list[CandidatePattern] = Field(
        default_factory=list,
        description=(
            "Patterns that reverse a positive match (e.g., 'denies LBBB'). "
            "ANY match overrides the positive patterns and sets present=False."
        ),
    )
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    notes_for_reviewer: str = Field(default="")


class SafetyExtractorGeneratorOutput(BaseModel):
    """The structured tool-use response."""
    schema_version: str = Field(default="safety-ext-gen-1.0")
    generated_at: str
    candidate_extractor: CandidateExtractor
    requires_human_judgment: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────


@dataclass
class SafetyExtGenStage:
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
class SafetyExtGenResult:
    criterion_codes: list[str]
    candidate_extractor: CandidateExtractor | None = None
    requires_human_judgment: list[str] = field(default_factory=list)
    stages: list[SafetyExtGenStage] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    total_duration_ms: int = 0
    llm_tokens: int = 0
    llm_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "criterion_codes": list(self.criterion_codes),
            "candidate_extractor": (
                self.candidate_extractor.model_dump()
                if self.candidate_extractor else None
            ),
            "requires_human_judgment": list(self.requires_human_judgment),
            "stages": [s.to_dict() for s in self.stages],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": self.total_duration_ms,
            "llm_tokens": self.llm_tokens,
            "llm_cost_usd": self.llm_cost_usd,
        }

    def to_python_source(self) -> str:
        """Emit Python source for safety_verifier.py."""
        if not self.candidate_extractor:
            return "# (no extractor drafted)"
        e = self.candidate_extractor
        positive_pats = ", ".join(repr(p.pattern) for p in e.patterns) or ""
        denial_pats = ", ".join(repr(p.pattern) for p in e.denial_patterns) or ""
        denial_block = (
            f",\n        denial_patterns=[\n            {denial_pats}\n        ],"
            if denial_pats else ""
        )
        return (
            f"# AUTO-GENERATED by lean_safety_extractor_generator — review before merging.\n"
            f"# Criterion codes: {', '.join(e.criterion_codes)}\n"
            f"# Generated: {self.finished_at}\n"
            f"\n"
            f"def {e.function_name}(note: str) -> FactCheck:\n"
            f"    return _check_pattern(\n"
            f"        note,\n"
            f"        fact_id={e.fact_id!r},\n"
            f"        label={e.label!r},\n"
            f"        patterns=[\n            {positive_pats}\n        ]"
            f"{denial_block}\n"
            f"    )\n"
            f"# confidence={e.confidence:.2f}"
            + (f" — {e.notes_for_reviewer}" if e.notes_for_reviewer else "")
        )


# ──────────────────────────────────────────────────────────────────────
# System prompt + tool definition
# ──────────────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are CardioAuth's clinical regex engineer. You read criterion
definitions and a small corpus of sample clinical-note phrasings,
then draft a safety_verifier extractor — a check_xxx(note) → FactCheck
function.

Your output is REVIEWED BY A HUMAN before merging.

# Operating principles

1. **Independent of the LLM.** This extractor is the second witness;
   it must work without ANY LLM. Use only regex + simple Python.

2. **Word boundaries matter.** \\bLBBB\\b — never plain LBBB. Without
   boundaries, "BLBBBLE" matches.

3. **Denial guards.** If the note says "denies LBBB", a naive pattern
   will match. List denial patterns that, when matched, REVERSE
   the positive judgment.

4. **Multiple positive patterns are fine.** "LBBB" + "left bundle
   branch block" + "left BBB" — all match the same fact. Provide
   each as a separate pattern.

5. **Case-insensitive.** Patterns will run with re.IGNORECASE.

6. **Each pattern carries its rationale + sample.** Provide a
   sample phrasing the pattern catches. Reviewers spot-check.

# Output

Emit a single SafetyExtractorGeneratorOutput object via the
emit_safety_extractor tool. JSON only.
"""


def _tool_def() -> dict:
    from cardioauth.lean_schema import _inline_refs
    schema = SafetyExtractorGeneratorOutput.model_json_schema()
    return {
        "name": "emit_safety_extractor",
        "description": "Emit a candidate safety_verifier extractor with patterns + denial guards.",
        "input_schema": _inline_refs(schema),
    }


def _default_llm_caller(system_prompt: str, user_prompt: str) -> tuple[dict, dict]:
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
            model=cfg.model, max_tokens=4000,
            system=system_with_cache_control(system_prompt),
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_safety_extractor"},
        )
    track_usage(response, agent="LEAN_SAFETY_EXT_GEN", model=cfg.model, duration_ms=t.ms)

    block = next(
        (b for b in response.content if getattr(b, "type", "") == "tool_use"),
        None,
    )
    parsed = block.input if block is not None else {}

    in_tok = int(getattr(response.usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(response.usage, "output_tokens", 0) or 0)
    return parsed, {
        "input_tokens": in_tok, "output_tokens": out_tok,
        "model": cfg.model,
        "cost_usd": _estimate_cost_usd(cfg.model, in_tok, out_tok),
    }


# ──────────────────────────────────────────────────────────────────────
# Phases
# ──────────────────────────────────────────────────────────────────────


def _phase1_analyse(
    *,
    criterion_codes: list[str],
    criterion_definition: str,
    positive_samples: list[str],
    negative_samples: list[str],
    llm_caller: Any,
) -> tuple[SafetyExtractorGeneratorOutput | None, dict, SafetyExtGenStage]:
    import time
    t0 = time.time()
    user_prompt = f"""\
# Criterion codes
{', '.join(criterion_codes)}

# Criterion definition
{criterion_definition}

# Positive samples (your patterns SHOULD match these)
{chr(10).join('- ' + s for s in positive_samples)}

# Negative samples (your patterns should NOT match these — denials,
# unrelated mentions, etc.)
{chr(10).join('- ' + s for s in negative_samples)}

Draft a safety_verifier extractor. Cover the positive samples;
add denial_patterns to handle the negative samples. Cite each
pattern's sample_match. Emit via the emit_safety_extractor tool.
"""
    try:
        parsed, usage = llm_caller(_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        return None, {}, SafetyExtGenStage(
            name="Phase 1: analyse", status="failed",
            duration_ms=int((time.time() - t0) * 1000),
            summary=f"LLM call failed: {str(e)[:160]}",
        )

    parsed.setdefault("generated_at", datetime.now(timezone.utc).isoformat())

    try:
        output = SafetyExtractorGeneratorOutput(**parsed)
    except Exception as e:
        return None, usage, SafetyExtGenStage(
            name="Phase 1: analyse", status="failed",
            duration_ms=int((time.time() - t0) * 1000),
            summary=f"Schema validation failed: {str(e)[:160]}",
        )

    n_pos = len(output.candidate_extractor.patterns)
    n_neg = len(output.candidate_extractor.denial_patterns)
    return output, usage, SafetyExtGenStage(
        name="Phase 1: analyse", status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=f"positive={n_pos} denial={n_neg}",
    )


def _phase2_optimise(
    output: SafetyExtractorGeneratorOutput,
) -> tuple[SafetyExtractorGeneratorOutput, SafetyExtGenStage]:
    """Compile every pattern; drop uncompilable; dedupe."""
    import time
    t0 = time.time()
    e = output.candidate_extractor

    def _filter_compile(pats: list[CandidatePattern]) -> tuple[list[CandidatePattern], list[str]]:
        kept: list[CandidatePattern] = []
        seen_strs: set[str] = set()
        rejected: list[str] = []
        for p in pats:
            try:
                re.compile(p.pattern)
            except re.error as err:
                rejected.append(f"{p.pattern} ({err})")
                continue
            if p.pattern in seen_strs:
                rejected.append(f"{p.pattern} (duplicate)")
                continue
            seen_strs.add(p.pattern)
            kept.append(p)
        return kept, rejected

    kept_pos, rejected_pos = _filter_compile(e.patterns)
    kept_neg, rejected_neg = _filter_compile(e.denial_patterns)

    # Warn (not reject) when positive patterns lack word boundaries —
    # common false-positive risk
    no_boundary_warnings = [
        p.pattern for p in kept_pos
        if not (r"\b" in p.pattern or r"^" in p.pattern or r"$" in p.pattern)
    ]

    new_e = e.model_copy(update={
        "patterns": kept_pos,
        "denial_patterns": kept_neg,
    })
    new_output = output.model_copy(update={"candidate_extractor": new_e})

    duration_ms = int((time.time() - t0) * 1000)
    return new_output, SafetyExtGenStage(
        name="Phase 2: optimise", status="ok",
        duration_ms=duration_ms,
        summary=(
            f"kept_pos={len(kept_pos)} kept_neg={len(kept_neg)} "
            f"rejected={len(rejected_pos) + len(rejected_neg)} "
            f"no_boundary={len(no_boundary_warnings)}"
        ),
        detail={
            "rejected_positive": rejected_pos,
            "rejected_denial": rejected_neg,
            "missing_word_boundaries": no_boundary_warnings,
        },
    )


def _phase3_design(output: SafetyExtractorGeneratorOutput) -> SafetyExtGenStage:
    """Sanity check: extractor has at least one positive pattern;
    function_name follows snake_case + check_ prefix convention."""
    import time
    t0 = time.time()
    e = output.candidate_extractor
    issues = []
    if not e.patterns:
        issues.append("No positive patterns survived Phase 2")
    if not e.function_name.startswith("check_"):
        issues.append(f"function_name {e.function_name!r} should start with 'check_'")
    if not re.match(r"^[a-z_][a-z0-9_]*$", e.function_name):
        issues.append(f"function_name {e.function_name!r} not snake_case")

    duration_ms = int((time.time() - t0) * 1000)
    return SafetyExtGenStage(
        name="Phase 3: design",
        status="fallback" if issues else "ok",
        duration_ms=duration_ms,
        summary=("issues=" + str(len(issues))) if issues else "design clean",
        detail={"issues": issues} if issues else None,
    )


def _phase4_generate(output: SafetyExtractorGeneratorOutput) -> SafetyExtGenStage:
    """Source generation lives on the result; this stage counts."""
    import time
    t0 = time.time()
    e = output.candidate_extractor
    return SafetyExtGenStage(
        name="Phase 4: generate", status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=f"emitted patterns={len(e.patterns)} denials={len(e.denial_patterns)}",
    )


def _phase5_report(
    output: SafetyExtractorGeneratorOutput,
    *,
    positive_samples: list[str],
    negative_samples: list[str],
) -> SafetyExtGenStage:
    """Run the candidate against positive + negative samples and
    report hit rates."""
    import time
    t0 = time.time()
    e = output.candidate_extractor

    def _matches(text: str) -> bool:
        for p in e.denial_patterns:
            if re.search(p.pattern, text, re.IGNORECASE):
                return False
        for p in e.patterns:
            if re.search(p.pattern, text, re.IGNORECASE):
                return True
        return False

    pos_hit = sum(1 for s in positive_samples if _matches(s))
    neg_false = sum(1 for s in negative_samples if _matches(s))

    pos_rate = (pos_hit / len(positive_samples)) if positive_samples else 0.0
    neg_fp_rate = (neg_false / len(negative_samples)) if negative_samples else 0.0

    return SafetyExtGenStage(
        name="Phase 5: report",
        status="ok" if (pos_rate >= 0.8 and neg_fp_rate <= 0.2) else "fallback",
        duration_ms=int((time.time() - t0) * 1000),
        summary=(
            f"pos_recall={pos_rate:.0%} neg_fp_rate={neg_fp_rate:.0%} "
            f"({pos_hit}/{len(positive_samples)} pos · "
            f"{neg_false}/{len(negative_samples)} false-positives)"
        ),
        detail={
            "positive_recall": pos_rate,
            "negative_false_positive_rate": neg_fp_rate,
            "positive_hits": pos_hit,
            "negative_false_positives": neg_false,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


def generate_safety_extractor(
    *,
    criterion_codes: list[str],
    criterion_definition: str,
    positive_samples: list[str],
    negative_samples: list[str] | None = None,
    llm_caller: Any = None,
) -> SafetyExtGenResult:
    """Run the 5-phase agentic safety-extractor generator.

    Args:
      criterion_codes: codes from CRITERION_TAXONOMY this extractor maps to
      criterion_definition: prose describing what the extractor should detect
      positive_samples: phrases that SHOULD match
      negative_samples: phrases that should NOT match (denials, unrelated)
      llm_caller: injectable for tests; defaults to Anthropic tool-use
    """
    import time
    overall_start = time.time()

    if negative_samples is None:
        negative_samples = []

    if llm_caller is None:
        llm_caller = _default_llm_caller

    result = SafetyExtGenResult(
        criterion_codes=list(criterion_codes),
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Phase 1
    output, usage, stage1 = _phase1_analyse(
        criterion_codes=criterion_codes,
        criterion_definition=criterion_definition,
        positive_samples=positive_samples,
        negative_samples=negative_samples,
        llm_caller=llm_caller,
    )
    result.stages.append(stage1)
    result.llm_tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
    result.llm_cost_usd = float(usage.get("cost_usd", 0.0))

    if stage1.status == "failed" or output is None:
        result.finished_at = datetime.now(timezone.utc).isoformat()
        result.total_duration_ms = int((time.time() - overall_start) * 1000)
        return result

    # Phase 2
    output, stage2 = _phase2_optimise(output)
    result.stages.append(stage2)

    # Phase 3
    result.stages.append(_phase3_design(output))

    # Phase 4
    result.stages.append(_phase4_generate(output))

    # Phase 5: regression test against samples
    result.stages.append(_phase5_report(
        output, positive_samples=positive_samples, negative_samples=negative_samples,
    ))

    result.candidate_extractor = output.candidate_extractor
    result.requires_human_judgment = output.requires_human_judgment
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.total_duration_ms = int((time.time() - overall_start) * 1000)
    return result
