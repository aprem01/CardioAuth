"""Lean Hybrid State Machine — State 2 output schema.

This module defines the structured-output contract that the unified LLM
call (State 2) must satisfy. It replaces the loose dict assembly across
CHART_AGENT → POLICY_AGENT → UnifiedReasoner → Reviewer with one
typed Pydantic schema validated at the LLM output boundary.

Design principles (per Peter's "scaling concern" + 2026 production
best-practice research):

  1. No procedure-specific fields. Everything keyed by code drawn
     from CRITERION_TAXONOMY at runtime. Adding TAVR or PCI is
     pure data work; the schema doesn't change.

  2. Explicit ambiguity. Criterion status is met / not_met /
     not_evaluated / ambiguous, with confidence 0.0–1.0 and a
     requires_human_review flag. Real cases are not always binary.

  3. List-valued evidence per criterion. A single criterion can be
     satisfied by ANY of several evidence pieces (LBBB OR pacemaker
     OR inability-to-exercise → ECG-001 met). Schema supports
     OR-paths natively.

  4. Documentation variability. extraction_confidence per chart
     field. note_format_quality flag at the top level. Missing
     fields are explicit, not silent.

  5. Payer-specific routing. payer_specific_criteria_evaluated is
     separate from generic. Same fact can map differently per payer.

  6. Forward-compatibility. New criterion kinds, new evidence types,
     new payer-specific rules are added in the taxonomy / payer-form
     modules — not by changing this schema.

Schema versioning: bumped on incompatible changes so frozen packets
can be replayed against the schema they were produced under.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


SCHEMA_VERSION = "lean-1.0"


def _normalize_enum(
    value, allowed: tuple[str, ...], *, preserve_case: str = "lower",
):
    """Normalize an LLM-emitted string to one of the allowed enum
    values, tolerating common casing/spacing variants.

    Strategy: try exact match → canonicalize separators (- _ space →
    _) and case-fold → also match with separators removed. Returns
    the canonical allowed value when matched, else the canonicalized
    input so Pydantic surfaces the standard literal-error.
    """
    if not isinstance(value, str):
        return value
    if value in allowed:
        return value

    s = value.strip().replace(" ", "_").replace("-", "_")
    target = s.upper() if preserve_case == "upper" else s.lower()
    target_no_sep = target.replace("_", "")

    for candidate in allowed:
        canonical = (
            candidate.upper() if preserve_case == "upper" else candidate.lower()
        )
        if target == canonical:
            return candidate
        if target_no_sep == canonical.replace("_", ""):
            return candidate
    # No match — return the canonicalized form so Pydantic's error
    # message is clean (the user sees their input, lowercased + with
    # underscores, not a CamelCase mangled mess).
    return target


# ──────────────────────────────────────────────────────────────────────
# Atomic structures
# ──────────────────────────────────────────────────────────────────────


class EvidenceQuote(BaseModel):
    """A verbatim snippet from the source note that supports a claim.

    Char offsets are best-effort — Pydantic schema-via-LLM doesn't
    guarantee exact offsets, but the verbatim quote MUST appear in the
    raw note (we verify this deterministically in State 3).
    """

    quote: str = Field(..., description="Verbatim text from the note. Must appear in the source.")
    char_start: int = Field(default=0, ge=0, description="Best-effort start offset.")
    char_end: int = Field(default=0, ge=0, description="Best-effort end offset.")
    source_section: str = Field(
        default="",
        description="Best-effort section name (HPI, Assessment, ECG, etc.) — informational only.",
    )


CriterionStatus = Literal["met", "not_met", "not_evaluated", "ambiguous"]


class CriterionEvaluation(BaseModel):
    """Per-criterion structured judgment.

    `code` is a stable identifier from CRITERION_TAXONOMY (e.g.
    'ECG-001', 'BMI-001'). The schema does NOT enumerate codes — they
    come from the runtime taxonomy injected into the prompt, so adding
    procedures is pure data work.

    Multiple evidence pieces are supported (an OR-path criterion may
    have several quotes that each independently satisfy it).
    """

    code: str = Field(..., description="Criterion code from CRITERION_TAXONOMY (e.g. 'ECG-001').")
    status: CriterionStatus
    evidence: list[EvidenceQuote] = Field(
        default_factory=list,
        description="Verbatim quotes supporting the judgment. Empty for not_evaluated.",
    )

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, v):
        """Tolerate common LLM emit variants: 'Met' → 'met',
        'NOT MET' → 'not_met', 'NotEvaluated' → 'not_evaluated'.
        Anthropic's tool-use sometimes returns the literal-cased
        Python identifier or a human-readable label instead of the
        snake_case enum value."""
        return _normalize_enum(v, ("met", "not_met", "not_evaluated", "ambiguous"))
    rationale: str = Field(
        default="",
        description="One-sentence reasoning. Required for ambiguous and not_met.",
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Reasoner's confidence in THIS criterion's judgment.",
    )
    requires_human_review: bool = Field(
        default=False,
        description="True when the LLM is genuinely uncertain or sees contradictions.",
    )

    @model_validator(mode="after")
    def _backfill_missing_rationale(self) -> "CriterionEvaluation":
        """When status is not_met / ambiguous and rationale is empty,
        fill a placeholder rationale rather than reject the whole
        output. Anthropic's tool-use enforces the JSON shape but
        doesn't see cross-field invariants, so the LLM occasionally
        emits not_met without rationale. Filling a placeholder lets
        the run complete; the State 4 gate emits a low-severity
        finding so the reviewer still sees the gap.

        Hard rejection here was the source of "lean unified call
        failing schema validation" — fixed by softening to a
        placeholder."""
        if self.status in ("ambiguous", "not_met") and not self.rationale.strip():
            object.__setattr__(
                self, "rationale",
                f"[Auto-filled: LLM emitted status={self.status} without explicit rationale.]",
            )
        return self


# ──────────────────────────────────────────────────────────────────────
# Chart data — generic, taxonomy-agnostic, scales to N procedures
# ──────────────────────────────────────────────────────────────────────


class ClinicalFact(BaseModel):
    """A single extracted clinical fact, anchored to a verbatim quote.

    Generic by design. The fact's *category* is one of a small fixed
    set (symptom, ecg_finding, prior_test, lab, medication, etc.); the
    *value* is free-text (clinical language). Mapping facts → criteria
    happens in State 4 via the taxonomy, NOT in this schema. This is
    what lets us add procedures without changing the schema.
    """

    category: Literal[
        "symptom", "ecg_finding", "prior_imaging", "prior_procedure",
        "lab", "medication", "comorbidity", "exam_finding",
        "physical_constraint", "social_history", "other",
    ]
    value: str = Field(..., description="Clinical-language description.")
    evidence: EvidenceQuote = Field(...)
    extraction_confidence: float = Field(default=0.85, ge=0.0, le=1.0)


# ──────────────────────────────────────────────────────────────────────
# CPT resolution
# ──────────────────────────────────────────────────────────────────────


CptResolutionSource = Literal[
    "request",                  # the requested CPT matches the note
    "note_extracted",           # the note clearly orders a different CPT
    "ambiguous_human_decide",   # signals are mixed; human must decide
]


class CptResolution(BaseModel):
    cpt: str = Field(..., description="The canonical CPT for this case.")
    procedure_name: str = Field(..., description="Human-readable procedure name.")
    source: CptResolutionSource
    request_cpt: str = Field(..., description="Echoes the input request CPT.")

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, v):
        return _normalize_enum(v, (
            "request", "note_extracted", "ambiguous_human_decide",
        ))
    rationale: str = Field(
        default="",
        description="Required when source is not 'request'.",
    )
    note_evidence: list[EvidenceQuote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _backfill_missing_rationale(self) -> "CptResolution":
        """Same softening as CriterionEvaluation: fill a placeholder
        rather than reject when the LLM omits rationale on a non-
        request CPT resolution."""
        if self.source != "request" and not self.rationale.strip():
            object.__setattr__(
                self, "rationale",
                f"[Auto-filled: LLM emitted source={self.source} without explicit rationale.]",
            )
        return self


# ──────────────────────────────────────────────────────────────────────
# Form fields — generic, payer-agnostic mapping
# ──────────────────────────────────────────────────────────────────────


FormFieldStatus = Literal["populated", "missing", "needs_verify", "incomplete"]


class FormFieldValue(BaseModel):
    """One filled form field. Schema is payer-agnostic; the actual
    field set comes from the runtime PayerForm injected into the
    prompt. Adding payers is pure data work."""

    key: str = Field(..., description="Stable field key from the PayerForm schema.")
    value: str = Field(default="", description="The value to populate; empty if missing.")
    status: FormFieldStatus
    evidence: list[EvidenceQuote] = Field(default_factory=list)
    missing_reason: str = Field(default="")


# ──────────────────────────────────────────────────────────────────────
# Approval verdict
# ──────────────────────────────────────────────────────────────────────


ApprovalLabel = Literal["HIGH", "MEDIUM", "LOW", "DO_NOT_SUBMIT", "INSUFFICIENT"]


class ApprovalVerdict(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    label: ApprovalLabel
    headline_summary: list[str] = Field(
        default_factory=list,
        description="3-5 bullet points summarizing the case for the physician.",
    )

    @field_validator("label", mode="before")
    @classmethod
    def _normalize_label(cls, v):
        """Tolerate variant casings: 'High' → 'HIGH', 'do not submit' →
        'DO_NOT_SUBMIT'. Anthropic's tool-use occasionally emits the
        natural-language form instead of the canonical enum value."""
        normalized = _normalize_enum(v, (
            "HIGH", "MEDIUM", "LOW", "DO_NOT_SUBMIT", "INSUFFICIENT",
        ), preserve_case="upper")
        return normalized


# ──────────────────────────────────────────────────────────────────────
# Narrative
# ──────────────────────────────────────────────────────────────────────


class NarrativeDraft(BaseModel):
    text: str = Field(default="", description="Medical-necessity attestation prose.")
    cpt_referenced: str = Field(default="", description="The CPT the narrative explicitly mentions.")
    procedure_referenced: str = Field(default="", description="The procedure name the narrative mentions.")


# ──────────────────────────────────────────────────────────────────────
# Documentation-quality signals (for handling Peter's "documentation
# variability" concern explicitly)
# ──────────────────────────────────────────────────────────────────────


NoteFormatQuality = Literal["structured", "semi_structured", "unstructured", "fragmented"]


class DocumentationQuality(BaseModel):
    """Surface signals about the note's parsability so downstream
    layers can adjust trust accordingly."""

    note_format_quality: NoteFormatQuality = Field(default="semi_structured")
    missing_essential_fields: list[str] = Field(
        default_factory=list,
        description="Names of payer-required fields the LLM couldn't find.",
    )
    extraction_warnings: list[str] = Field(
        default_factory=list,
        description="Any signals the LLM is uncertain about (e.g. 'BMI mentioned twice with conflicting values').",
    )
    overall_extraction_confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    @field_validator("note_format_quality", mode="before")
    @classmethod
    def _normalize_quality(cls, v):
        if not isinstance(v, str):
            return v
        s = v.strip().lower().replace(" ", "_").replace("-", "_")
        # Common synonyms
        if s in ("structured_note", "well_structured", "complete"):
            return "structured"
        if s in ("partial", "partially_structured"):
            return "semi_structured"
        return s


# ──────────────────────────────────────────────────────────────────────
# State 2 Output — the unified LLM call's return contract
# ──────────────────────────────────────────────────────────────────────


class LeanState2Output(BaseModel):
    """The single structured output of the unified State 2 LLM call.

    This object is the source of truth that downstream states (3, 4, 5)
    consume. State 3 (safety verifier) cross-checks `clinical_facts`
    and `criteria_evaluated` against an INDEPENDENT regex re-extraction
    of the raw note. State 4 (deterministic gate) reads `decision_
    inputs` and applies the gate logic. State 5 freezes the packet.

    Schema is taxonomy-agnostic: criterion codes come from the runtime
    taxonomy injected into the prompt. Adding procedures is pure data
    work; this schema does not change.
    """

    schema_version: str = Field(default=SCHEMA_VERSION)

    # Identity (echoed from input — used to verify call integrity)
    case_id: str
    request_cpt: str
    payer: str

    # Resolution
    cpt_resolution: CptResolution

    # Extraction
    clinical_facts: list[ClinicalFact] = Field(
        default_factory=list,
        description="All extracted facts. Each anchored to a verbatim quote.",
    )

    # Identification (the 6 essentials — these are special enough to
    # be top-level even though they could live in clinical_facts)
    patient_name: str = Field(default="")
    date_of_birth: str = Field(default="")
    insurance_id: str = Field(default="")
    payer_name: str = Field(default="")
    attending_physician: str = Field(default="")
    attending_npi: str = Field(default="")

    # Reasoning — supports multiple criterion families AND payer-
    # specific routing as we scale.
    criteria_evaluated: list[CriterionEvaluation] = Field(
        default_factory=list,
        description="Generic criteria from CRITERION_TAXONOMY.",
    )
    payer_specific_criteria_evaluated: list[CriterionEvaluation] = Field(
        default_factory=list,
        description="Payer-specific overrides or additions (e.g. UHC's MCG-specific bundle).",
    )

    # Verdict — default to MEDIUM/0.5 with a "verdict-omitted" headline
    # if the LLM truncates before emitting it. State 4 gate sees the
    # absent label and routes to hold_for_review automatically.
    approval_verdict: ApprovalVerdict = Field(
        default_factory=lambda: ApprovalVerdict(
            score=0.5, label="MEDIUM",
            headline_summary=["[LLM did not emit approval verdict — physician review.]"],
        ),
    )

    # Form
    form_field_values: list[FormFieldValue] = Field(default_factory=list)

    # Narrative — default to empty NarrativeDraft if the LLM truncates
    # or omits. State 4's coherence check still runs; the State 4 gate
    # treats empty narrative.text as a finding rather than a hard
    # failure.
    narrative: NarrativeDraft = Field(default_factory=NarrativeDraft)

    # Quality signals — default if omitted
    documentation_quality: DocumentationQuality = Field(default_factory=DocumentationQuality)

    # Alternative-modality suggestion (advisory; State 4 surfaces it)
    alternative_modality_suggestion: str | None = Field(
        default=None,
        description="If a different procedure family fits better (e.g. PET when BMI≥35).",
    )

    # Self-reported flags for State 4 gate
    physician_review_recommended: bool = Field(
        default=False,
        description="True when the LLM thinks a clinician should look before submission.",
    )
    review_reasons: list[str] = Field(
        default_factory=list,
        description="Why physician_review_recommended was set.",
    )


# ──────────────────────────────────────────────────────────────────────
# Public schema dump — for the prompt + Anthropic JSON mode
# ──────────────────────────────────────────────────────────────────────


def state2_json_schema() -> dict:
    """Return the JSON schema for the unified State 2 output.

    Used in two places:
      - The system prompt: shown to the LLM as the contract it must
        emit.
      - JSON validation at State 2's output boundary: the actual
        Pydantic validation that catches drift.
    """
    return LeanState2Output.model_json_schema()


def state2_json_schema_flat() -> dict:
    """Same as state2_json_schema(), but with all $defs inlined and all
    $refs resolved.

    Anthropic's tool-use input_schema validator chokes on schemas with
    $ref / $defs (verified in production: live endpoint hung on the
    referenced shape). This flattened variant inlines every reference
    so the schema is fully self-contained — exactly what tool-use
    mode wants.

    Pure function. Safe to call repeatedly.
    """
    return _inline_refs(LeanState2Output.model_json_schema())


def _inline_refs(schema: dict) -> dict:
    """Walk a JSON Schema and inline every $ref against the top-level
    $defs. Recursive but bounded by reference depth (Pydantic schemas
    don't have cycles in practice — a future cycle would blow the
    stack, but that's not how our data model works).

    Returns a NEW schema dict; does not mutate the input.
    """
    if not isinstance(schema, dict):
        return schema

    # Lift the $defs once at the top
    defs = schema.get("$defs", {}) or schema.get("definitions", {}) or {}

    def _resolve(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                key = ref[len("#/$defs/"):]
                target = defs.get(key)
                if target is None:
                    # Unknown ref — leave as-is so the caller can decide
                    return node
                # Recursively inline the target's own refs
                inlined = _resolve(target)
                # Preserve any sibling fields from the original $ref node
                # (description, title, default, etc.) — JSON Schema 2020-12
                # allows mixing $ref + sibling keywords; some validators
                # respect them.
                merged = dict(inlined)
                for k, v in node.items():
                    if k != "$ref":
                        merged[k] = v
                return merged
            if isinstance(ref, str) and ref.startswith("#/definitions/"):
                key = ref[len("#/definitions/"):]
                target = defs.get(key)
                if target is None:
                    return node
                inlined = _resolve(target)
                merged = dict(inlined)
                for k, v in node.items():
                    if k != "$ref":
                        merged[k] = v
                return merged
            return {k: _resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    flattened = _resolve(schema)
    # Drop the $defs / definitions tables now that everything's inlined
    if isinstance(flattened, dict):
        flattened.pop("$defs", None)
        flattened.pop("definitions", None)
    return flattened
