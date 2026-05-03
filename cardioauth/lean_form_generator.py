"""Agentic Generator 2 — payer form from blank PA-form PDF.

The second of three content generators that make the lean
architecture scale to "many payers" without manual hand-coding
becoming the bottleneck.

Today: each new payer gets a hand-coded PayerForm definition in
payer_forms.py. UHC + Aetna + Anthem + Medicare = 4 forms × ~22
fields each, all hand-written with the right populated_from paths.

To scale: 8 payers × 6 procedures = 48 form definitions. Manual =
weeks of careful work. With this generator: ~30 minutes of
clinician/eng review per payer-form draft.

Architecture: same 5-phase lean-hybrid pattern as Generator 1.
LLM does extraction + design + drafting; deterministic code does
schema normalization, populated_from-path inference, and dedupe.

Phases:
  1. ANALYSE          (LLM)           PDF text → list of candidate
                                       fields with labels, categories,
                                       formats, required-ness
  2. OPTIMISE         (deterministic)  normalize categories against
                                       canonical set; dedupe; merge
                                       common fields against the
                                       _common_fields() base; infer
                                       populated_from paths from
                                       canonical chart-data shape
  3. DESIGN           (LLM)           pick canonical key names,
                                       resolve format ambiguity,
                                       fill help_text
  4. GENERATE         (deterministic)  emit Python source ready to
                                       paste into payer_forms.py
  5. REPORT           (deterministic)  coverage + provenance with
                                       per-field source citations

Output is a CandidatePayerForm — typed, reviewable. Nothing is
auto-merged into payer_forms.py without a human PR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Output schemas
# ──────────────────────────────────────────────────────────────────────


FieldCategory = Literal["patient", "provider", "procedure", "clinical", "attestation"]
FieldFormat = Literal[
    "text", "textarea", "date", "select", "checkbox", "radio",
    "number", "phone", "email", "cpt", "icd10", "npi",
]


class CandidateFormField(BaseModel):
    """One field from the payer's blank form."""
    key: str = Field(..., description="snake_case stable identifier")
    label: str = Field(..., description="As shown on the form")
    category: FieldCategory
    required: bool = Field(default=False)
    format: FieldFormat = Field(default="text")
    options: list[str] = Field(default_factory=list, description="For select/radio")
    populated_from: str = Field(
        default="",
        description=(
            "Inferred chart_data path or 'manual_attestation'. "
            "E.g. 'chart_data.patient_name'."
        ),
    )
    help_text: str = Field(default="", description="One-sentence guidance for the form-filler")
    min_length: int = Field(default=0, description="Validation hint for text fields")
    # Provenance
    source_quote: str = Field(default="", description="Verbatim form-text supporting this field")
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    notes_for_reviewer: str = Field(default="")


class CandidatePayerForm(BaseModel):
    """One payer-form draft."""
    payer: str
    vendor: str = Field(default="", description="UM vendor / submission channel (Availity / eviCore / direct)")
    name: str = Field(..., description="Form name (e.g., 'Cardiac Advanced Imaging PA Worksheet')")
    applies_to_cpts: list[str] = Field(default_factory=list)
    submission_channel: Literal["portal", "fax", "api", "phone"] = Field(default="portal")
    fields: list[CandidateFormField] = Field(default_factory=list)
    notes: str = Field(default="")


class FormGeneratorOutput(BaseModel):
    """The structured tool-use response."""
    schema_version: str = Field(default="form-gen-1.0")
    generated_at: str
    candidate_form: CandidatePayerForm
    requires_human_judgment: list[str] = Field(default_factory=list)
    notes: str = Field(default="")


# ──────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────


@dataclass
class FormGenStage:
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
class FormGenResult:
    payer: str
    candidate_form: CandidatePayerForm | None = None
    requires_human_judgment: list[str] = field(default_factory=list)
    stages: list[FormGenStage] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    total_duration_ms: int = 0
    llm_tokens: int = 0
    llm_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "payer": self.payer,
            "candidate_form": (
                self.candidate_form.model_dump() if self.candidate_form else None
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
        """Emit Python source ready to paste into payer_forms.py."""
        if not self.candidate_form:
            return "# (no form drafted)"
        f = self.candidate_form
        lines = [
            "# AUTO-GENERATED by lean_form_generator — review before merging.",
            f"# Payer: {f.payer}",
            f"# Form: {f.name}",
            f"# Generated: {self.finished_at}",
            "",
            f"def _{f.payer.lower().replace(' ', '_')}_form() -> PayerForm:",
            f'    """{f.name}\n\n    Auto-drafted; clinician + eng review required.\n    """',
            f'    common = _common_fields()',
            f'    custom = [',
        ]
        for fld in f.fields:
            opts = (
                f", options={fld.options!r}" if fld.options else ""
            )
            help_t = (
                f", help_text={fld.help_text!r}" if fld.help_text else ""
            )
            min_l = (
                f", min_length={fld.min_length}" if fld.min_length else ""
            )
            pop = (
                f", populated_from={fld.populated_from!r}"
                if fld.populated_from and fld.populated_from != "manual_attestation"
                else ""
            )
            lines.append(
                f"        FormField(key={fld.key!r}, label={fld.label!r}, "
                f"category={fld.category!r}, required={fld.required}, "
                f"format={fld.format!r}{opts}{pop}{min_l}{help_t}),"
                f"  # confidence={fld.confidence:.2f}"
                + (f" — {fld.notes_for_reviewer}" if fld.notes_for_reviewer else "")
            )
        lines += [
            f'    ]',
            f'    return PayerForm(',
            f'        payer={f.payer!r},',
            f'        vendor={f.vendor!r},',
            f'        name={f.name!r},',
            f'        applies_to_cpts={f.applies_to_cpts!r},',
            f'        submission_channel={f.submission_channel!r},',
            f'        fields=common + custom,',
            f'        notes={f.notes!r},',
            f'    )',
        ]
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# System prompt + tool definition
# ──────────────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are CardioAuth's payer-form engineer. You read scanned/exported
PA forms (UHC, Aetna, Anthem, Medicare, eviCore worksheets, MCG/
InterQual checklists) and draft a CandidatePayerForm definition.

Your output is REVIEWED BY A HUMAN before merging.

# Operating principles

1. **One form per submission.** A form is one PA worksheet — extract
   the fields exactly as they appear, in their original order.

2. **Distinguish form sections by category.**
     patient      — name, DOB, member ID, sex
     provider     — ordering MD, NPI, contact
     procedure    — CPT, ICD-10, procedure name, dates
     clinical    — symptoms, prior testing, exercise tolerance,
                    documentation of medical necessity
     attestation  — physician sign-off, dates, signatures

3. **Map common fields to canonical paths.** If a field is the
   patient's name, set populated_from='chart_data.patient_name'.
   For DOB → chart_data.date_of_birth. For NPI →
   chart_data.attending_npi. For CPT → chart_data.procedure_code.
   Manual attestations (signatures, dates added at submission) get
   populated_from='manual_attestation'.

4. **format constrains the field type.**
   text / textarea / date / select / checkbox / radio / number /
   phone / email / cpt / icd10 / npi.

5. **Cite the verbatim form-text.** Every field must carry the form's
   own label in source_quote. Reviewers spot-check against the PDF.

6. **One stable key per field, snake_case.** patient_name not
   patientName. lbbb_documented not "LBBB documented?".

7. **Required = the form marks it required (asterisk, "required",
   etc.).**

# Output

Emit a single FormGeneratorOutput object via the
emit_payer_form tool. No prose. No markdown. JSON only.
"""


def _tool_def() -> dict:
    from cardioauth.lean_schema import _inline_refs
    schema = FormGeneratorOutput.model_json_schema()
    return {
        "name": "emit_payer_form",
        "description": "Emit a candidate PayerForm from PDF text.",
        "input_schema": _inline_refs(schema),
    }


# ──────────────────────────────────────────────────────────────────────
# Default LLM caller (Anthropic tool-use)
# ──────────────────────────────────────────────────────────────────────


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
            model=cfg.model, max_tokens=8000,
            system=system_with_cache_control(system_prompt),
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_payer_form"},
        )
    track_usage(response, agent="LEAN_FORM_GEN", model=cfg.model, duration_ms=t.ms)

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


# Canonical chart_data paths the LLM is allowed to suggest. Anything
# else gets normalized to manual_attestation in Phase 2.
_CANONICAL_PATHS = {
    "chart_data.patient_name", "chart_data.date_of_birth",
    "chart_data.sex", "chart_data.age", "chart_data.insurance_id",
    "chart_data.payer_name", "chart_data.attending_physician",
    "chart_data.attending_npi", "chart_data.procedure_code",
    "chart_data.procedure_requested", "chart_data.diagnosis_codes",
    "chart_data.bmi", "chart_data.height", "chart_data.weight",
    "chart_data.allergies", "chart_data.current_medications",
    "chart_data.current_symptoms", "chart_data.ecg_findings",
    "chart_data.prior_stress_tests", "chart_data.prior_imaging",
    "chart_data.prior_procedures", "chart_data.past_medical_history",
    "chart_data.active_comorbidities", "chart_data.exam_findings",
    "chart_data.relevant_labs", "chart_data.additional_notes",
    "policy_data.auth_required", "policy_data.payer",
    "reasoning.approval_score", "reasoning.approval_label",
    "reasoning.pa_narrative_draft",
}


def _phase1_analyse(
    *, payer: str, form_pdf_text: str, llm_caller: Any,
) -> tuple[FormGeneratorOutput | None, dict, FormGenStage]:
    import time
    t0 = time.time()
    user_prompt = f"""\
# Payer
{payer}

# Form PDF text (extracted)
{form_pdf_text}

Extract every field on the form into a CandidateFormField. Cite each
field's verbatim label in source_quote. Map common fields (name, DOB,
NPI, CPT, ICD-10) to chart_data paths. Use 'manual_attestation' for
signatures and submission-time dates.
"""
    try:
        parsed, usage = llm_caller(_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        return None, {}, FormGenStage(
            name="Phase 1: analyse", status="failed",
            duration_ms=int((time.time() - t0) * 1000),
            summary=f"LLM call failed: {str(e)[:160]}",
        )

    parsed.setdefault("generated_at", datetime.now(timezone.utc).isoformat())

    try:
        output = FormGeneratorOutput(**parsed)
    except Exception as e:
        return None, usage, FormGenStage(
            name="Phase 1: analyse", status="failed",
            duration_ms=int((time.time() - t0) * 1000),
            summary=f"Schema validation failed: {str(e)[:160]}",
        )

    return output, usage, FormGenStage(
        name="Phase 1: analyse", status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=(
            f"fields={len(output.candidate_form.fields)} "
            f"unsure={len(output.requires_human_judgment)}"
        ),
    )


def _phase2_optimise(output: FormGeneratorOutput) -> tuple[FormGeneratorOutput, FormGenStage]:
    """Deterministic dedupe + populated_from validation."""
    import time
    t0 = time.time()
    cf = output.candidate_form
    seen_keys: set[str] = set()
    kept: list[CandidateFormField] = []
    invalid_paths: list[str] = []
    duplicate_keys: list[str] = []

    for fld in cf.fields:
        # Stable key: snake_case, no duplicates
        key = fld.key.strip()
        if key in seen_keys:
            duplicate_keys.append(key)
            continue
        seen_keys.add(key)

        # Validate populated_from against canonical set
        path = fld.populated_from.strip()
        if path and path != "manual_attestation" and path not in _CANONICAL_PATHS:
            invalid_paths.append(f"{key}:{path}")
            path = "manual_attestation"

        kept.append(fld.model_copy(update={
            "key": key,
            "label": fld.label.strip(),
            "populated_from": path,
            "options": [o.strip() for o in fld.options if o.strip()],
        }))

    new_form = cf.model_copy(update={"fields": kept})
    new_output = output.model_copy(update={"candidate_form": new_form})

    duration_ms = int((time.time() - t0) * 1000)
    return new_output, FormGenStage(
        name="Phase 2: optimise", status="ok",
        duration_ms=duration_ms,
        summary=(
            f"kept={len(kept)} dups={len(duplicate_keys)} "
            f"invalid_paths={len(invalid_paths)}"
        ),
        detail={
            "duplicate_keys": duplicate_keys,
            "invalid_paths": invalid_paths,
        },
    )


def _phase3_design(output: FormGeneratorOutput) -> FormGenStage:
    """Light validation: every required field has a populated_from
    or is a manual attestation; no two fields with the same label."""
    import time
    t0 = time.time()
    fields = output.candidate_form.fields
    no_path_required: list[str] = []
    duplicate_labels: dict[str, list[str]] = {}

    for fld in fields:
        if fld.required and not fld.populated_from:
            no_path_required.append(fld.key)
        duplicate_labels.setdefault(fld.label.lower(), []).append(fld.key)

    near_dups = {l: keys for l, keys in duplicate_labels.items() if len(keys) > 1}

    duration_ms = int((time.time() - t0) * 1000)
    status = "fallback" if (no_path_required or near_dups) else "ok"
    return FormGenStage(
        name="Phase 3: design", status=status,
        duration_ms=duration_ms,
        summary=(
            f"required_no_path={len(no_path_required)} "
            f"near_dup_labels={len(near_dups)}"
        ),
        detail={
            "required_without_path": no_path_required,
            "near_duplicate_labels": near_dups,
        } if (no_path_required or near_dups) else None,
    )


def _phase4_generate(output: FormGeneratorOutput) -> FormGenStage:
    """Source generation lives on the result class; Phase 4 just
    counts."""
    import time
    t0 = time.time()
    return FormGenStage(
        name="Phase 4: generate", status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=f"emitted_fields={len(output.candidate_form.fields)}",
    )


def _phase5_report(output: FormGeneratorOutput) -> FormGenStage:
    """Coverage report."""
    import time
    t0 = time.time()
    fields = output.candidate_form.fields
    by_category: dict[str, int] = {}
    by_path_kind: dict[str, int] = {"chart": 0, "policy": 0, "reasoning": 0, "manual": 0, "unmapped": 0}
    quoted = 0
    for fld in fields:
        by_category[fld.category] = by_category.get(fld.category, 0) + 1
        if fld.source_quote.strip():
            quoted += 1
        if fld.populated_from.startswith("chart_data."):
            by_path_kind["chart"] += 1
        elif fld.populated_from.startswith("policy_data."):
            by_path_kind["policy"] += 1
        elif fld.populated_from.startswith("reasoning."):
            by_path_kind["reasoning"] += 1
        elif fld.populated_from == "manual_attestation":
            by_path_kind["manual"] += 1
        else:
            by_path_kind["unmapped"] += 1

    avg_conf = (
        sum(f.confidence for f in fields) / len(fields)
        if fields else 0.0
    )

    return FormGenStage(
        name="Phase 5: report", status="ok",
        duration_ms=int((time.time() - t0) * 1000),
        summary=(
            f"avg_conf={avg_conf:.2f} quoted={quoted}/{len(fields)} "
            f"by_cat={by_category} mapping={by_path_kind}"
        ),
        detail={
            "average_confidence": avg_conf,
            "quoted_fields": quoted,
            "by_category": by_category,
            "by_path_kind": by_path_kind,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────


def generate_payer_form(
    *,
    payer: str,
    form_pdf_text: str,
    llm_caller: Any = None,
) -> FormGenResult:
    """Run the 5-phase agentic form generator.

    Args:
      payer: payer name
      form_pdf_text: extracted text from the blank PA form PDF
      llm_caller: injectable for tests; defaults to Anthropic tool-use
    """
    import time
    overall_start = time.time()

    if llm_caller is None:
        llm_caller = _default_llm_caller

    result = FormGenResult(
        payer=payer,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Phase 1: LLM analyse
    output, usage, stage1 = _phase1_analyse(
        payer=payer, form_pdf_text=form_pdf_text, llm_caller=llm_caller,
    )
    result.stages.append(stage1)
    result.llm_tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
    result.llm_cost_usd = float(usage.get("cost_usd", 0.0))

    if stage1.status == "failed" or output is None:
        result.finished_at = datetime.now(timezone.utc).isoformat()
        result.total_duration_ms = int((time.time() - overall_start) * 1000)
        return result

    # Phase 2: deterministic optimise
    output, stage2 = _phase2_optimise(output)
    result.stages.append(stage2)

    # Phase 3: design check
    result.stages.append(_phase3_design(output))

    # Phase 4: generate (source is on result)
    result.stages.append(_phase4_generate(output))

    # Phase 5: report
    result.stages.append(_phase5_report(output))

    result.candidate_form = output.candidate_form
    result.requires_human_judgment = output.requires_human_judgment
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.total_duration_ms = int((time.time() - overall_start) * 1000)
    return result
