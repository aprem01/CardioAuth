"""Payer form schemas + field-level population.

Peter's Apr 22 feedback: "the critical step for the extraction workflow is
making sure we can consistently and accurately populate the different
types of payer forms." This module defines realistic form schemas per
payer + vendor, walks them against our ChartData v2 + PolicyData +
ReasoningResult, and returns a per-field population report.

Field status is one of:
  - populated   — value found, looks acceptable
  - incomplete  — value found but doesn't meet format/length expectations
  - missing     — required field with no value
  - n/a         — non-required field that doesn't apply to this case

Medicare is a special case: for most outpatient imaging, PA isn't
required — instead, a Coverage Determination Checklist (NCD/LCD
compliance + audit-ready documentation) is the right shape. See
get_medicare_coverage_form() below.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)


FieldCategory = Literal[
    "patient", "provider", "procedure", "clinical", "attestation"
]
FieldFormat = Literal[
    "text", "select", "date", "checkbox", "textarea", "icd10", "cpt"
]


# ────────────────────────────────────────────────────────────────────────
# Schema dataclasses
# ────────────────────────────────────────────────────────────────────────


@dataclass
class FormField:
    key: str
    label: str
    category: FieldCategory
    required: bool = False
    format: FieldFormat = "text"
    options: list[str] = field(default_factory=list)
    # Where to look in our data. "chart_data.X" / "policy_data.X" / "reasoning.X"
    # or a callable for complex lookups.
    populated_from: str | Callable | None = None
    # Minimum characters for "incomplete" detection on text fields
    min_length: int = 0
    help_text: str = ""


@dataclass
class PayerForm:
    payer: str
    vendor: str
    name: str
    applies_to_cpts: list[str]  # empty = all CPTs
    fields: list[FormField]
    submission_channel: str = "portal"
    notes: str = ""


# ────────────────────────────────────────────────────────────────────────
# Form definitions
# ────────────────────────────────────────────────────────────────────────


def _common_fields() -> list[FormField]:
    """Fields every PA form has — patient, provider, procedure basics."""
    return [
        # Patient block
        FormField(key="patient_name", label="Patient name", category="patient", required=True,
                  populated_from="chart_data.patient_name", min_length=2),
        FormField(key="patient_dob", label="Date of birth", category="patient", required=True,
                  format="date", populated_from="chart_data.date_of_birth"),
        FormField(key="patient_sex", label="Sex", category="patient", required=True,
                  format="select", options=["M", "F", "Other"],
                  populated_from="chart_data.sex"),
        FormField(key="member_id", label="Insurance member ID", category="patient", required=True,
                  populated_from="chart_data.insurance_id", min_length=4),
        # Provider block
        FormField(key="ordering_physician", label="Ordering physician", category="provider", required=True,
                  populated_from="chart_data.attending_physician", min_length=2),
        FormField(key="ordering_npi", label="Ordering NPI", category="provider", required=True,
                  populated_from="chart_data.attending_npi", min_length=10,
                  help_text="10-digit National Provider Identifier"),
        # Procedure block
        FormField(key="cpt_code", label="CPT code", category="procedure", required=True,
                  format="cpt", populated_from="chart_data.procedure_code"),
        FormField(key="procedure_name", label="Procedure", category="procedure", required=True,
                  populated_from="chart_data.procedure_requested"),
        FormField(key="primary_icd10", label="Primary ICD-10", category="procedure", required=True,
                  format="icd10",
                  populated_from=lambda chart, policy, reasoning: (
                      chart.diagnosis_codes[0] if chart.diagnosis_codes else ""
                  )),
        FormField(key="secondary_icd10", label="Secondary ICD-10(s)", category="procedure",
                  populated_from=lambda chart, policy, reasoning: (
                      ", ".join(chart.diagnosis_codes[1:5]) if chart.diagnosis_codes else ""
                  )),
    ]


def _uhc_cardiac_imaging_form() -> PayerForm:
    """UHC PA worksheet for cardiac advanced imaging (78492/78452 etc.).

    In real life this is submitted via Availity or via eviCore's adapter.
    Field shape modeled after UHC's published PA checklists.
    """
    clinical_fields = [
        FormField(key="chief_complaint_symptoms", label="Primary symptoms", category="clinical", required=True,
                  format="textarea", min_length=10,
                  populated_from=lambda chart, policy, reasoning: _fmt_symptoms(chart),
                  help_text="Chest pain / dyspnea / syncope / etc. with onset, frequency, character"),
        FormField(key="symptom_change_vs_baseline", label="New or worsening since prior imaging?",
                  category="clinical", required=True, format="select",
                  options=["Yes — new", "Yes — worsening", "No — stable", "No prior imaging"],
                  populated_from=lambda chart, policy, reasoning: _fmt_symptom_change(chart)),
        FormField(key="prior_stress_test", label="Prior stress test result", category="clinical", required=True,
                  format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_prior_stress(chart),
                  help_text="Modality, date, %MPHR, interpretation (non-diagnostic, equivocal, positive, etc.)"),
        FormField(key="ecg_findings", label="Baseline ECG findings", category="clinical",
                  format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_ecg(chart),
                  help_text="Rhythm, LBBB/paced/WPW/LVH+strain"),
        FormField(key="bmi", label="BMI (if ≥35, supports PET over SPECT)", category="clinical",
                  populated_from=lambda chart, policy, reasoning: _fmt_bmi(chart)),
        FormField(key="exercise_capacity", label="Can patient exercise adequately?", category="clinical",
                  required=True, format="select",
                  options=["Yes", "No — see functional limitation", "Unknown"],
                  populated_from=lambda chart, policy, reasoning: _fmt_exercise_capacity(chart)),
        FormField(key="exercise_limitation", label="If No, specific functional limitation",
                  category="clinical", format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_exercise_limitation(chart),
                  help_text="Specific diagnosis (COPD, severe OA, etc.) + explicit inability to perform TST"),
        FormField(key="medical_therapy", label="Medical therapy trial (duration + agents)",
                  category="clinical", format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_meds(chart),
                  help_text="GDMT agents at max tolerated dose for ≥ 6 weeks"),
        FormField(key="relevant_prior_imaging", label="Other relevant prior imaging",
                  category="clinical", format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_imaging(chart)),
        # Attestation block
        FormField(key="medical_necessity_statement", label="Medical necessity attestation",
                  category="attestation", required=True, format="textarea", min_length=40,
                  populated_from=lambda chart, policy, reasoning: (
                      (reasoning.pa_narrative_draft or "")[:600] if reasoning else ""
                  )),
        FormField(key="no_duplicate_imaging_12mo",
                  label="No similar imaging within last 12 months (or new symptoms)",
                  category="attestation", required=True, format="checkbox",
                  populated_from=lambda chart, policy, reasoning: _check_freq_compliance(chart, reasoning)),
        FormField(key="in_network_attestation", label="Ordering physician is in-network",
                  category="attestation", required=True, format="checkbox",
                  populated_from="flagged_requires_verify"),
    ]
    return PayerForm(
        payer="UnitedHealthcare",
        vendor="Availity",
        name="UHC Advanced Cardiac Imaging — PA Worksheet",
        applies_to_cpts=["78492", "78491", "78452", "78451", "75557", "75574"],
        fields=_common_fields() + clinical_fields,
        submission_channel="availity",
        notes="Submitted via Availity portal. eviCore handles some UHC plans — verify routing.",
    )


def _aetna_evicore_form() -> PayerForm:
    """Aetna advanced imaging — routed through eviCore."""
    clinical_fields = [
        FormField(key="chief_complaint_symptoms", label="Chief complaint / symptoms",
                  category="clinical", required=True, format="textarea", min_length=10,
                  populated_from=lambda chart, policy, reasoning: _fmt_symptoms(chart)),
        FormField(key="symptom_timeline", label="Symptom timeline (onset/freq/progression)",
                  category="clinical", required=True, format="textarea", min_length=10,
                  populated_from=lambda chart, policy, reasoning: _fmt_symptom_timeline(chart)),
        FormField(key="prior_testing", label="Prior diagnostic testing",
                  category="clinical", required=True, format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_prior_stress(chart) + "\n" + _fmt_imaging(chart)),
        FormField(key="ecg_findings", label="Baseline ECG", category="clinical", format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_ecg(chart)),
        FormField(key="functional_class", label="Functional class (NYHA / CCS)",
                  category="clinical",
                  populated_from=lambda chart, policy, reasoning: _fmt_functional_class(chart)),
        FormField(key="risk_factors", label="CV risk factor profile",
                  category="clinical", format="textarea",
                  populated_from=lambda chart, policy, reasoning: _fmt_risk_factors(chart)),
        FormField(key="clinical_rationale", label="Clinical rationale for requested study",
                  category="attestation", required=True, format="textarea", min_length=40,
                  populated_from=lambda chart, policy, reasoning: (
                      (reasoning.pa_narrative_draft or "")[:600] if reasoning else ""
                  )),
    ]
    return PayerForm(
        payer="Aetna",
        vendor="eviCore",
        name="Aetna Advanced Imaging (eviCore) — Clinical Worksheet",
        applies_to_cpts=["78492", "78491", "78452", "78451", "75557", "75574"],
        fields=_common_fields() + clinical_fields,
        submission_channel="evicore",
        notes="Submit through eviCore portal (not Aetna direct).",
    )


def _medicare_coverage_checklist() -> PayerForm:
    """Medicare is different — for most outpatient imaging PA isn't required.

    What matters: NCD/LCD compliance + audit-ready documentation. This form
    is a coverage determination checklist, not a PA worksheet.
    """
    fields = _common_fields() + [
        FormField(key="ncd_lcd_applicable", label="Applicable NCD/LCD",
                  category="procedure", required=True,
                  populated_from="policy_data.policy_source",
                  help_text="e.g. NCD 220.6 (Cardiac PET)"),
        FormField(key="ncd_lcd_criteria_met", label="NCD/LCD coverage criteria satisfied",
                  category="clinical", required=True, format="checkbox",
                  populated_from=lambda chart, policy, reasoning: _check_ncd_criteria(reasoning)),
        FormField(key="mac_jurisdiction", label="MAC jurisdiction",
                  category="provider", required=True,
                  populated_from="chart_data.mac_jurisdiction",
                  help_text="Verify facility falls under correct Medicare Administrative Contractor"),
        FormField(key="secondary_payer_present", label="Secondary commercial payer on file?",
                  category="patient", format="checkbox",
                  populated_from="chart_data.secondary_payer",
                  help_text="If YES, traditional PA may still apply to the secondary payer"),
        FormField(key="abn_signed", label="ABN signed if service may not meet coverage",
                  category="attestation", format="checkbox",
                  populated_from="chart_data.abn_signed"),
        # Clinical documentation for audit
        FormField(key="symptoms_documented", label="Symptoms documented in note",
                  category="clinical", required=True, format="checkbox",
                  populated_from=lambda chart, policy, reasoning: _check_has_symptoms(chart)),
        FormField(key="prior_testing_documented", label="Prior noninvasive testing documented",
                  category="clinical", required=True, format="checkbox",
                  populated_from=lambda chart, policy, reasoning: _check_has_prior_testing(chart)),
        FormField(key="ecg_documented", label="Baseline ECG documented",
                  category="clinical", format="checkbox",
                  populated_from=lambda chart, policy, reasoning: bool(chart.ecg_findings)),
        FormField(key="medical_necessity_narrative", label="Medical necessity narrative (audit-ready)",
                  category="attestation", required=True, format="textarea", min_length=60,
                  populated_from=lambda chart, policy, reasoning: (
                      (reasoning.pa_narrative_draft or "")[:800] if reasoning else ""
                  )),
    ]
    return PayerForm(
        payer="Medicare",
        vendor="CMS",
        name="Medicare Coverage Documentation Checklist",
        applies_to_cpts=[],  # all outpatient imaging
        fields=fields,
        submission_channel="documentation_only",
        notes=(
            "PA typically NOT required for outpatient imaging. Documentation must "
            "support medical necessity per the applicable NCD/LCD and be audit-ready. "
            "If a secondary commercial payer is on file, that payer's PA workflow still applies."
        ),
    )


_FORMS_REGISTRY: list[PayerForm] = [
    _uhc_cardiac_imaging_form(),
    _aetna_evicore_form(),
    _medicare_coverage_checklist(),
]


def get_payer_form(payer: str, cpt_code: str) -> PayerForm | None:
    """Find the best-matching form template."""
    payer_lower = (payer or "").lower()

    def _matches_payer(form: PayerForm) -> bool:
        p = form.payer.lower()
        if p == payer_lower:
            return True
        aliases = {
            "uhc": "unitedhealthcare",
            "united": "unitedhealthcare",
            "unitedhealthcare": "unitedhealthcare",
            "aetna": "aetna",
            "medicare": "medicare",
            "cms": "medicare",
        }
        return aliases.get(payer_lower) == aliases.get(p)

    candidates = [f for f in _FORMS_REGISTRY if _matches_payer(f)]
    if not candidates:
        return None

    # Prefer CPT-specific over generic
    for form in candidates:
        if not form.applies_to_cpts or cpt_code in form.applies_to_cpts:
            return form
    return candidates[0]


# ────────────────────────────────────────────────────────────────────────
# Population engine
# ────────────────────────────────────────────────────────────────────────


# Sentinel for fields that can't be auto-populated and need manual attestation.
class _NeedsVerifySentinel:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "<NEEDS_VERIFY>"


NEEDS_VERIFY = _NeedsVerifySentinel()


def populate_payer_form(
    form: PayerForm,
    *,
    chart_data: Any,
    policy_data: Any,
    reasoning: Any,
) -> dict[str, Any]:
    """Walk the form schema and return per-field population status.

    Returns:
      {
        "form_name": "...",
        "payer": "...", "vendor": "...",
        "fields": [{key, label, category, required, value, status,
                    missing_reason, help_text}],
        "counts": {"populated": N, "missing_required": N,
                   "incomplete": N, "optional_empty": N,
                   "needs_verify": N, "total": N},
        "ready_to_submit": bool,
      }
    """
    populated_rows: list[dict] = []
    counts = {
        "populated": 0, "missing_required": 0, "incomplete": 0,
        "optional_empty": 0, "needs_verify": 0,
    }

    for f in form.fields:
        value = _resolve_value(f, chart_data, policy_data, reasoning)
        str_value = _to_string(value)
        status, reason = _classify_field(f, value, str_value)

        populated_rows.append({
            "key": f.key,
            "label": f.label,
            "category": f.category,
            "required": f.required,
            "format": f.format,
            "options": f.options,
            "value": str_value,
            "status": status,
            "missing_reason": reason,
            "help_text": f.help_text,
        })
        if status == "populated":
            counts["populated"] += 1
        elif status == "needs_verify":
            counts["needs_verify"] += 1
        elif status == "missing":
            if f.required:
                counts["missing_required"] += 1
            else:
                counts["optional_empty"] += 1
        elif status == "incomplete":
            counts["incomplete"] += 1

    counts["total"] = len(form.fields)
    # needs_verify doesn't block ready_to_submit — the user just has to attest.
    ready = counts["missing_required"] == 0 and counts["incomplete"] == 0

    return {
        "form_name": form.name,
        "payer": form.payer,
        "vendor": form.vendor,
        "submission_channel": form.submission_channel,
        "notes": form.notes,
        "fields": populated_rows,
        "counts": counts,
        "ready_to_submit": ready,
    }


def _resolve_value(f: FormField, chart_data, policy_data, reasoning) -> Any:
    src = f.populated_from
    if src is None:
        return ""
    if callable(src):
        try:
            return src(chart_data, policy_data, reasoning)
        except Exception as e:
            logger.debug("payer_forms: callable for %s failed: %s", f.key, e)
            return ""
    if src == "flagged_requires_verify":
        return NEEDS_VERIFY
    if isinstance(src, str) and "." in src:
        root_name, _, path = src.partition(".")
        root = {"chart_data": chart_data, "policy_data": policy_data, "reasoning": reasoning}.get(root_name)
        if root is None:
            return ""
        try:
            val = getattr(root, path, None)
            if val is None and isinstance(root, dict):
                val = root.get(path)
            return val
        except Exception:
            return ""
    return ""


def _to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, _NeedsVerifySentinel):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)
    return str(value)


def _classify_field(f: FormField, value: Any, str_value: str) -> tuple[str, str]:
    if isinstance(value, _NeedsVerifySentinel):
        return "needs_verify", "Manual attestation — confirm before submitting"
    empty = (value is None) or (str_value.strip() == "")
    if empty:
        if f.required:
            return "missing", f"Required {f.category} field not populated"
        return "missing", ""
    if f.min_length and len(str_value.strip()) < f.min_length:
        return "incomplete", f"Value shorter than required minimum ({f.min_length} chars)"
    if f.format == "select" and f.options and str_value not in f.options:
        return "incomplete", f"Value '{str_value}' not in allowed options {f.options}"
    return "populated", ""


# ────────────────────────────────────────────────────────────────────────
# Clinical formatters (pull from ChartData v2)
# ────────────────────────────────────────────────────────────────────────


def _fmt_symptoms(chart) -> str:
    syms = getattr(chart, "current_symptoms", []) or []
    if not syms:
        return ""
    return "; ".join(
        " · ".join(filter(None, [
            s.name,
            s.character,
            f"onset {s.onset}" if s.onset else "",
            s.frequency,
            s.severity,
        ]))
        for s in syms
    )


def _fmt_symptom_timeline(chart) -> str:
    syms = getattr(chart, "current_symptoms", []) or []
    bits = []
    for s in syms:
        parts = [s.name]
        if s.onset:
            parts.append("onset " + s.onset)
        if s.frequency:
            parts.append(s.frequency)
        if s.change_vs_baseline:
            parts.append(s.change_vs_baseline)
        bits.append(" / ".join(parts))
    return "; ".join(bits)


def _fmt_symptom_change(chart) -> str:
    syms = getattr(chart, "current_symptoms", []) or []
    changes = [s.change_vs_baseline for s in syms if s.change_vs_baseline]
    if not changes:
        return ""
    c = changes[0].lower()
    if "new" in c:
        return "Yes — new"
    if "wors" in c or "progress" in c:
        return "Yes — worsening"
    if "stable" in c or "no change" in c:
        return "No — stable"
    return changes[0]


def _fmt_prior_stress(chart) -> str:
    tests = getattr(chart, "prior_stress_tests", []) or []
    if not tests:
        return ""
    return "; ".join(
        " / ".join(filter(None, [
            t.modality,
            t.date,
            f"{t.max_hr_percent} MPHR" if t.max_hr_percent else "",
            t.interpretation,
            t.result_summary,
        ]))
        for t in tests
    )


def _fmt_ecg(chart) -> str:
    ecg = getattr(chart, "ecg_findings", []) or []
    if not ecg:
        return ""
    bits = []
    for e in ecg:
        parts = list(filter(None, [
            f"rhythm: {e.rhythm}" if e.rhythm else "",
            f"conduction: {e.conduction}" if e.conduction else "",
            e.hypertrophy_or_strain,
            e.ischemic_changes,
            f"pacing: {e.pacing}" if e.pacing else "",
            e.summary,
        ]))
        bits.append(" · ".join(parts))
    return "; ".join(bits)


def _fmt_bmi(chart) -> str:
    import re
    # Check comorbidities + additional notes for BMI mention
    sources = list(getattr(chart, "active_comorbidities", []) or [])
    sources.extend(getattr(chart, "comorbidities", []) or [])
    sources.append(getattr(chart, "additional_notes", "") or "")
    for src in sources:
        m = re.search(r"bmi\s*(?:of)?[\s:]*(\d{1,2}(?:\.\d)?)", str(src).lower())
        if m:
            return f"BMI {m.group(1)}"
    return ""


def _fmt_exercise_capacity(chart) -> str:
    syms = getattr(chart, "current_symptoms", []) or []
    for s in syms:
        text = (s.name + " " + (s.character or "")).lower()
        if "unable" in text or "cannot" in text or "limit" in text:
            return "No — see functional limitation"
    notes = (getattr(chart, "additional_notes", "") or "").lower()
    if "unable to exercise" in notes or "cannot exercise" in notes:
        return "No — see functional limitation"
    if "exercise" in notes or "ambulat" in notes:
        return "Unknown"
    return ""


def _fmt_exercise_limitation(chart) -> str:
    notes = getattr(chart, "additional_notes", "") or ""
    import re
    m = re.search(r"(unable to[^.]+\.|cannot[^.]+\.|exercise limit[^.]+\.)", notes, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _fmt_meds(chart) -> str:
    meds = getattr(chart, "relevant_medications", []) or []
    if not meds:
        return ""
    return "; ".join(
        " ".join(filter(None, [m.name, m.dose, f"(since {m.start_date})" if m.start_date else ""]))
        for m in meds
    )


def _fmt_imaging(chart) -> str:
    img = getattr(chart, "relevant_imaging", []) or []
    if not img:
        return ""
    return "; ".join(
        f"{i.type} ({i.date}): {i.result_summary[:120]}" if i.result_summary else f"{i.type} ({i.date})"
        for i in img
    )


def _fmt_functional_class(chart) -> str:
    import re
    syms = getattr(chart, "current_symptoms", []) or []
    for s in syms:
        if s.severity and re.search(r"(nyha|ccs|ehra)", (s.severity or "").lower()):
            return s.severity
    notes = getattr(chart, "additional_notes", "") or ""
    m = re.search(r"(nyha|ccs|ehra)\s*(?:class)?\s*(i{1,4}v?|[1-4])", notes.lower())
    if m:
        return m.group(0).upper()
    return ""


def _fmt_risk_factors(chart) -> str:
    comorbid = getattr(chart, "active_comorbidities", []) or []
    comorbid.extend(getattr(chart, "comorbidities", []) or [])
    risk_keywords = ["diabetes", "htn", "hypertension", "dm", "dyslipidemia",
                     "hyperlipidemia", "smoker", "smoking", "cad", "ckd"]
    found = [c for c in comorbid if any(k in c.lower() for k in risk_keywords)]
    return "; ".join(found)


def _check_freq_compliance(chart, reasoning) -> str:
    """Returns 'yes' when FREQ-001 is met or not_applicable. 'no' otherwise."""
    if reasoning is None:
        return ""
    matches = getattr(reasoning, "__dict__", {}).get("_unified_matches", [])
    for m in matches:
        if m.get("code") == "FREQ-001":
            return "yes" if m.get("status") == "met" else "no"
    return ""


def _check_ncd_criteria(reasoning) -> str:
    """For Medicare — check if coded criteria are substantially met."""
    if reasoning is None:
        return ""
    try:
        score = reasoning.approval_likelihood_score
        if score is None:
            return ""
        return "yes" if score >= 0.7 else "no"
    except Exception:
        return ""


def _check_has_symptoms(chart) -> str:
    syms = getattr(chart, "current_symptoms", []) or []
    return "yes" if syms else "no"


def _check_has_prior_testing(chart) -> str:
    tests = getattr(chart, "prior_stress_tests", []) or []
    imaging = getattr(chart, "relevant_imaging", []) or []
    return "yes" if (tests or imaging) else "no"
