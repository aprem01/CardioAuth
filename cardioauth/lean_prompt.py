"""Lean Hybrid State Machine — State 2 unified prompt.

The single load-bearing prompt that replaces CHART_AGENT + POLICY_AGENT
+ UnifiedReasoner + Reviewer with one structured-output call. The
prompt is built dynamically per case from:

  - The raw note (raw text or LlamaParse markdown)
  - The applicable taxonomy subset (only criteria with applies_to
    matching the request CPT — keeps prompt small as taxonomy grows)
  - The payer-specific policy chunks (RAG, scoped to (payer, CPT))
  - The PayerForm schema (field set the LLM needs to populate)
  - The deterministic pre-pass output (essentials, request CPT,
    payer name) so the LLM doesn't redo work the regex did

Scaling design:
  • Prompt size grows LINEARLY with applicable criteria, not total
    taxonomy size. ECG/exercise/BMI criteria are reused across PET +
    SPECT + CT, so prompt overhead amortizes.
  • Payer policy chunks are RAG-retrieved per (payer, CPT). New
    payers = new RAG entries; prompt template doesn't change.
  • Form schemas are payer-agnostic in the prompt (just a list of
    field keys + categories + help text). New forms = new schemas
    fed in here; prompt template doesn't change.
  • Cache control: the SYSTEM portion (instruction + schema +
    taxonomy doc) is cached across cases; only the per-case USER
    portion (note + RAG + form) changes. Drives cost down at scale.
"""

from __future__ import annotations

import json
from typing import Any

from cardioauth.lean_schema import LeanState2Output, state2_json_schema


# ──────────────────────────────────────────────────────────────────────
# System prompt — cached across cases, contains the contract
# ──────────────────────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
You are CardioAuth's clinical reasoning engine for prior authorization.

Your job: read a deidentified clinical note, evaluate the requested
procedure against the supplied criteria + payer policy, populate the
payer's PA form fields, and return a single structured JSON object
matching the schema below. You are the only LLM call in this pipeline.
Independent deterministic verifiers will re-read the note after you
and cross-check your output. Be honest about ambiguity — when a
criterion is genuinely unclear, say "ambiguous" and explain why.

# Operating principles

1. **Cite verbatim quotes from the note.** Every claim about a clinical
   fact and every "met" judgment must be backed by an exact quote.
   Quotes will be verified against the source text — invented quotes
   will be flagged.

2. **Do not silently fix CPT mismatches.** If the request CPT differs
   from what the note clearly orders, set cpt_resolution.source to
   "note_extracted" and explain why. The physician decides whether to
   correct the request — do not pre-decide for them.

3. **Use the supplied taxonomy.** Criterion codes (e.g. ECG-001,
   BMI-001) come from the runtime taxonomy in the user message.
   Do not invent codes. If a criterion is irrelevant to the case,
   mark it not_evaluated rather than fabricating evidence.

4. **Distinguish met / not_met / ambiguous / not_evaluated.**
   - met: clear evidence in the note; provide verbatim quotes
   - not_met: actively absent or contradicted; provide one-sentence rationale
   - ambiguous: signals are mixed; provide rationale and set
     requires_human_review=true
   - not_evaluated: criterion doesn't apply to this case

5. **Documentation quality matters.** Set
   documentation_quality.note_format_quality honestly:
   - structured: full sectioned note (HPI, Assessment, ECG, etc.)
   - semi_structured: sections present but terse
   - unstructured: prose without clear sections
   - fragmented: bullet lists or note dumps with missing context

6. **Be honest about uncertainty.** If you can't determine a fact, say
   so via documentation_quality.extraction_warnings or
   physician_review_recommended=true. Do not pad output with guesses.

# Output contract

You MUST emit valid JSON matching the LeanState2Output schema below.
No prose before or after the JSON. No markdown fences. Just the JSON
object. The schema (in JSON Schema format):

```json
%(json_schema)s
```

# Failure modes to avoid

- Inventing CPT codes that don't appear in the note or request
- Marking criteria "met" without a verbatim quote from the note
- Claiming the chart has a fact when the note doesn't (chart and
  note may diverge — use the NOTE as source of truth here)
- Generating a narrative that references a different CPT than
  cpt_resolution.cpt
- Skipping criteria_evaluated when the input taxonomy lists them
  (mark them not_evaluated explicitly)

If you would have failed any of these checks, fix it before emitting.
""" % {"json_schema": json.dumps(state2_json_schema(), indent=2)}


# ──────────────────────────────────────────────────────────────────────
# Per-case user prompt builder
# ──────────────────────────────────────────────────────────────────────


def build_user_prompt(
    *,
    case_id: str,
    raw_note: str,
    request_cpt: str,
    payer: str,
    applicable_criteria: list[dict],
    payer_policy_chunks: list[dict],
    payer_form_fields: list[dict],
    pre_pass_essentials: dict,
    payer_specific_criteria: list[dict] | None = None,
    similar_precedents: list[dict] | None = None,
) -> str:
    """Build the per-case user message. The system prompt is cached;
    this changes per call.

    Inputs are minimal-shaped dicts (not Pydantic) so this builder is
    decoupled from the upstream type system. The state machine layer
    is responsible for marshaling.

    `applicable_criteria` is the taxonomy slice already filtered to
    the request CPT — the LLM should evaluate ALL of them and emit
    a CriterionEvaluation entry for each (met / not_met / ambiguous /
    not_evaluated).

    `payer_policy_chunks` are RAG hits scoped to (payer, CPT) — the
    LLM uses them as the canonical policy context.

    `payer_form_fields` is the list of fields the form expects;
    the LLM populates them where the note supports it.

    `pre_pass_essentials` is the regex-extracted identity dict —
    the LLM should ECHO these into the output (don't re-extract).
    """
    sections: list[str] = []

    sections.append(f"# Case\ncase_id: {case_id}\nrequest_cpt: {request_cpt}\npayer: {payer}\n")

    # Echo the deterministic pre-pass output so the LLM passes it
    # through rather than re-extracting (avoids drift).
    if pre_pass_essentials:
        sections.append(
            "# Pre-pass essentials (echo these into the output)\n"
            + json.dumps(pre_pass_essentials, indent=2)
        )

    sections.append(
        f"# Applicable criteria for CPT {request_cpt}\n"
        "Evaluate EVERY criterion below. Emit one CriterionEvaluation "
        "entry per code. Use 'not_evaluated' when irrelevant.\n"
        + json.dumps(applicable_criteria, indent=2)
    )

    if payer_specific_criteria:
        sections.append(
            f"# Payer-specific criteria for {payer}\n"
            "Evaluate these too — emit them in payer_specific_criteria_evaluated.\n"
            + json.dumps(payer_specific_criteria, indent=2)
        )

    if payer_policy_chunks:
        sections.append(
            f"# Payer policy excerpts for {payer} / CPT {request_cpt}\n"
            "Use these as the canonical policy context. Cite chunk IDs "
            "in rationale where relevant.\n"
            + json.dumps(payer_policy_chunks, indent=2)
        )

    sections.append(
        "# Form fields to populate\n"
        "Emit a FormFieldValue entry per key. Status: 'populated' if "
        "you can fill from the note, 'missing' if absent, "
        "'needs_verify' if it's a manual attestation, 'incomplete' if "
        "partial.\n"
        + json.dumps(payer_form_fields, indent=2)
    )

    if similar_precedents:
        sections.append(
            "# Similar prior cases (informational only — do not copy verdicts)\n"
            + json.dumps(similar_precedents, indent=2)
        )

    sections.append(
        "# Clinical note\n"
        "BEGIN NOTE\n"
        + raw_note.strip()
        + "\nEND NOTE\n"
    )

    sections.append(
        "# Output\n"
        "Emit a single JSON object matching LeanState2Output. No prose. "
        "No markdown. Just the JSON."
    )

    return "\n\n".join(sections)


# ──────────────────────────────────────────────────────────────────────
# Validation helper for the LLM output
# ──────────────────────────────────────────────────────────────────────


class State2ValidationError(Exception):
    """Raised when the LLM's output fails the LeanState2Output schema."""

    def __init__(self, raw_output: str, errors: list[dict[str, Any]]):
        self.raw_output = raw_output
        self.errors = errors
        super().__init__(self._format_errors(errors))

    @staticmethod
    def _format_errors(errors: list[dict[str, Any]]) -> str:
        lines = ["LLM output failed schema validation:"]
        for e in errors:
            loc = ".".join(str(p) for p in e.get("loc", []))
            lines.append(f"  - {loc}: {e.get('msg', e)}")
        return "\n".join(lines)


def validate_state2_output(raw_text: str) -> LeanState2Output:
    """Parse the LLM's raw text into a validated LeanState2Output.

    Raises State2ValidationError on parse failure or schema violation,
    carrying both the raw output (so a retry can include it as feedback)
    and a structured error list (so a programmatic fixer can target
    specific fields).
    """
    from cardioauth.agents.json_recovery import parse_llm_json
    from pydantic import ValidationError

    parsed = parse_llm_json(raw_text, fallback=None)
    if parsed is None:
        raise State2ValidationError(
            raw_output=raw_text,
            errors=[{"loc": ["__root__"], "msg": "JSON parse failed"}],
        )

    try:
        return LeanState2Output(**parsed)
    except ValidationError as e:
        raise State2ValidationError(raw_output=raw_text, errors=e.errors()) from e


def build_retry_user_prompt(
    *,
    original_user_prompt: str,
    failed_output: str,
    errors: list[dict[str, Any]],
) -> str:
    """Build a retry message that includes the failed output + specific
    schema errors. Standard self-correction pattern: the LLM tends to
    fix what you point at."""
    error_lines = []
    for e in errors:
        loc = ".".join(str(p) for p in e.get("loc", []))
        error_lines.append(f"  - {loc}: {e.get('msg', e)}")
    error_block = "\n".join(error_lines)

    return f"""\
Your previous output failed schema validation. Here are the errors:

{error_block}

Your previous output (truncated):

```
{failed_output[:2000]}
```

Re-emit a corrected JSON object that satisfies the LeanState2Output
schema. Address every error above. Output JSON only.

Original request:

{original_user_prompt}
"""
