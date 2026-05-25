"""Fill AcroForm-based payer PA PDFs with values from the lean pipeline.

Phase 1 (this module): direct mapping. ~15 demographic/coverage/CPT/ICD
fields from form_field_values go straight into their named PDF fields.
The 70+ clinical-reasoning checkboxes are left blank for staff to mark.

Phase 2 (next): clinical-reasoning checkbox inference — derive which
checkboxes to tick from criteria_evaluated + corpus_snippets +
clinical_facts. Mix of deterministic rules (ICD code → diagnosis box;
criterion-met → indication box) and a targeted LLM pass for judgment
calls. Out of scope for this commit.

Architecture:
- A FormFiller is a per-form callable that knows how to map our
  pipeline output to that PDF's specific field names.
- One registry entry per supported payer form. The route handler picks
  the filler by form_id from the routing result.
- Each filler returns the filled PDF as bytes. Caller streams it back
  with the right Content-Disposition.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Project root so relative paths in the YAML resolve correctly
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class FillResult:
    """Outcome of a fill operation. `pdf_bytes` is empty if filling failed."""
    pdf_bytes: bytes
    fields_populated: int
    fields_total: int
    missing_values: list[str]      # field names we couldn't populate
    errors: list[str]


def _get(d: dict, *keys, default: str = "") -> str:
    """Walk into nested dicts safely. Returns '' or default if any key
    is missing or value is None. Always returns a string."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return str(cur) if cur is not None else default


def _form_field_value(form_field_values: list[dict], key: str) -> str:
    """Find a form_field_values entry by key and return its value."""
    for f in form_field_values or []:
        if f.get("key") == key:
            v = f.get("value")
            if v:
                return str(v)
    return ""


def _split_name(full: str) -> tuple[str, str]:
    """Split 'Eleanor R. Whitford' → ('Eleanor R.', 'Whitford'). The MA
    form has a single 'Patient Name First Last' text box, but other
    forms split. Keep both forms available."""
    parts = (full or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


# ──────────────────────────────────────────────────────────────────────
# UHC MA Cardiac Imaging Prior Authorization — 153 AcroForm fields
# ──────────────────────────────────────────────────────────────────────


def fill_uhc_ma_cardiac_imaging(
    *,
    lean_result: dict,
    chart_context: dict | None = None,
    pdf_path: str | Path | None = None,
) -> FillResult:
    """Fill the UHC Massachusetts Cardiac Imaging PA form with values
    extracted by the lean pipeline.

    Phase 1: demographics + coverage + ordering provider + CPT + ICD +
    date of service. Checkboxes left for staff. Path 2 (clinical-
    reasoning checkbox inference) is a separate next-phase commit.
    """
    pdf_path = Path(pdf_path) if pdf_path else _PROJECT_ROOT / "tests/fixtures/payer_forms/pdfs/uhc-ma-cardiac-imaging.pdf"
    if not pdf_path.exists():
        return FillResult(
            pdf_bytes=b"", fields_populated=0, fields_total=0,
            missing_values=[], errors=[f"PDF template not found at {pdf_path}"],
        )

    s2 = (lean_result or {}).get("state2_output") or {}
    fields = s2.get("form_field_values") or []
    cpt_res = s2.get("cpt_resolution") or {}
    cs = chart_context or {}

    # Build the value map — UHC PDF field name → string value
    patient_name = _form_field_value(fields, "patient_name")
    patient_dob = _form_field_value(fields, "patient_dob")
    member_id = _form_field_value(fields, "member_id")
    payer_name = _form_field_value(fields, "payer_name") or (lean_result.get("payer") or "")
    ordering_physician = _form_field_value(fields, "ordering_physician")
    ordering_npi = _form_field_value(fields, "ordering_npi")
    cpt_code = _form_field_value(fields, "cpt_code") or cpt_res.get("cpt", "") or lean_result.get("request_cpt", "")
    procedure_name = _form_field_value(fields, "procedure_name") or cpt_res.get("procedure_name", "")
    primary_icd10 = _form_field_value(fields, "primary_icd10")

    # Diagnosis description — try secondary or build from primary if needed
    icd_description = _form_field_value(fields, "primary_diagnosis_description")

    # Date of service — prefer chart-summary's encounter date, fall back to today
    encounter_date = cs.get("encounter_date") or cs.get("latest_doc_date") or ""
    if not encounter_date:
        from datetime import date
        encounter_date = date.today().isoformat()

    direct_map: dict[str, str] = {
        # SECTION 1 — Member demographics
        "Patient Name First Last": patient_name,
        "DOB": patient_dob,
        "Health Plan": payer_name,
        "Member ID": member_id,

        # SECTION 2 — Ordering provider
        "Physician Name First Last": ordering_physician,
        "NPI": ordering_npi,

        # SECTION 3 — Facility (most blank; staff fills facility detail)
        "Date of Service": encounter_date,

        # SECTION 4 — Exam request
        "CPT Codes": cpt_code,
        "Description": procedure_name,
        "ICD Diagnosis Codes": primary_icd10,
        "Description_2": icd_description,
    }

    # Strip empty values — leave the PDF blank rather than write '' which
    # can confuse some PDF viewers
    final_map = {k: v for k, v in direct_map.items() if v}

    # Render
    return _fill_pdf(pdf_path, final_map, total_fields=153)


# ──────────────────────────────────────────────────────────────────────
# Generic PDF AcroForm fill
# ──────────────────────────────────────────────────────────────────────


def _fill_pdf(template_path: Path, values: dict[str, str], *, total_fields: int = 0) -> FillResult:
    """Apply a {field_name: value} dict to a PDF AcroForm and return the
    filled bytes. Uses pypdf — works for any standard AcroForm.
    """
    import pypdf
    from pypdf import PdfReader, PdfWriter

    errors: list[str] = []
    missing: list[str] = []
    try:
        reader = PdfReader(str(template_path))
        writer = PdfWriter(clone_from=reader)

        # pypdf's update_page_form_field_values fills text fields well.
        # For checkboxes we'd need to set "/V": "/Yes" — Phase 2.
        populated = 0
        existing_fields = reader.get_fields() or {}
        for page in writer.pages:
            writer.update_page_form_field_values(page, values)

        # Verify how many of the requested values actually landed on a
        # known field name (helps spot typos against the template).
        for k in values:
            if k in existing_fields:
                populated += 1
            else:
                missing.append(k)

        # Ensure form data shows in viewers (preserve form's own settings).
        # pypdf doesn't expose NeedAppearances toggle directly via writer —
        # this is intentionally a no-op for now; most modern viewers
        # render filled text fields regardless.

        buf = io.BytesIO()
        writer.write(buf)
        return FillResult(
            pdf_bytes=buf.getvalue(),
            fields_populated=populated,
            fields_total=total_fields or len(existing_fields),
            missing_values=missing,
            errors=errors,
        )
    except Exception as e:
        logger.exception("PDF fill failed for %s", template_path)
        return FillResult(
            pdf_bytes=b"", fields_populated=0, fields_total=0,
            missing_values=[], errors=[f"{type(e).__name__}: {e}"],
        )


# ──────────────────────────────────────────────────────────────────────
# Registry — form_id → filler
# ──────────────────────────────────────────────────────────────────────


_FILLERS: dict[str, Callable[..., FillResult]] = {
    "uhc-ma-cardiac-imaging": fill_uhc_ma_cardiac_imaging,
}


def fill_form(form_id: str, *, lean_result: dict, chart_context: dict | None = None) -> FillResult:
    """Dispatch entry point. Raises KeyError if the form_id has no filler."""
    filler = _FILLERS.get(form_id)
    if filler is None:
        return FillResult(
            pdf_bytes=b"", fields_populated=0, fields_total=0,
            missing_values=[],
            errors=[f"No filler registered for form_id '{form_id}'. "
                    f"Supported: {sorted(_FILLERS.keys())}"],
        )
    return filler(lean_result=lean_result, chart_context=chart_context)


def supported_form_ids() -> list[str]:
    return sorted(_FILLERS.keys())
