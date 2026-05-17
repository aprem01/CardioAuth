"""Synthesize a structured chart-context header from a FHIR Bundle.

The lean pipeline's State 2 LLM populates form_field_values (patient_name,
DOB, member_id, ordering_physician, ordering_npi, primary_icd10, etc.)
by reading them out of the raw_note prose. Synthetic test cases work
because their current-encounter section inlines all that data in the
note body. But real Epic progress notes don't restate demographics —
that data lives in the structured Patient / Coverage / Practitioner /
Encounter / Condition resources. So the Epic pathway extracted facts
into the bundle but those facts never reached the form-fill prompt.

This module bridges the gap: parse the bundle's structured resources
into a canonical header block and prepend it to the raw note. The LLM
sees the same shape of context the synthetic case provides, just
sourced from FHIR rather than baked into prose.

The header is clearly delineated so the LLM treats it as authoritative
structured data, with the actual note body following.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def synthesize_chart_context(
    bundle: dict[str, Any],
    *,
    raw_note: str = "",
    corpus_documents: list | None = None,
) -> str:
    """Return an enriched raw_note that prepends a FHIR-derived context
    header to whatever clinical prose came from Epic.

    Why this matters: form_field_values gets populated by the State 2
    LLM reading the raw_note. Without this enrichment, demographics and
    coverage details extracted from FHIR resources never make it into
    the prompt — they sit unused in the bundle. After enrichment, the
    LLM sees them in the same prose-shaped header the synthetic test
    cases bake in by hand.

    Caller can also pass `corpus_documents` (the PatientCorpus.documents
    list) so the header can include an index of what other chart sections
    are available for retrieval — gives the LLM a clearer picture of
    what historical evidence exists.
    """
    resources = (bundle or {}).get("resources", {}) or {}

    lines: list[str] = []
    lines.append("=== PATIENT CONTEXT (from FHIR Bundle) ===")

    # Patient demographics
    patient = _first_resource(resources, "Patient")
    if patient:
        name = _format_name(patient.get("name") or [])
        if name:
            lines.append(f"Patient: {name}")
        dob = patient.get("birthDate") or ""
        if dob:
            lines.append(f"DOB: {dob}")
        gender = patient.get("gender") or ""
        if gender:
            lines.append(f"Sex: {gender}")
        mrn = _first_identifier_value(patient.get("identifier") or [], type_code="MR")
        if mrn:
            lines.append(f"MRN: {mrn}")

    # Coverage / insurance / member ID
    coverage = _first_resource(resources, "Coverage")
    if coverage:
        member = coverage.get("subscriberId") or ""
        payor = ""
        for p in (coverage.get("payor") or []):
            payor = p.get("display") or payor
        if payor:
            lines.append(f"Insurance Payer: {payor}")
        if member:
            lines.append(f"Insurance Member ID: {member}")

    # Encounter — date + reason + dx
    encounter = _first_resource(resources, "Encounter")
    if encounter:
        period = encounter.get("period") or {}
        date = (period.get("start") or "")[:10]
        if date:
            lines.append(f"Encounter Date: {date}")
        reasons: list[str] = []
        for r in (encounter.get("reasonReference") or []):
            if r.get("display"):
                reasons.append(r["display"])
        for r in (encounter.get("reason") or []):
            if r.get("text"):
                reasons.append(r["text"])
        if reasons:
            lines.append(f"Encounter Reason: {'; '.join(reasons[:3])}")

    # Ordering Practitioner — name + NPI
    practitioner = _first_resource(resources, "Practitioner")
    if practitioner:
        prac_name = _format_name(practitioner.get("name") or [])
        if prac_name:
            lines.append(f"Ordering Physician: {prac_name}")
        npi = _first_identifier_value(
            practitioner.get("identifier") or [],
            system="http://hl7.org/fhir/sid/us-npi",
        )
        if npi:
            lines.append(f"NPI: {npi}")

    # Ordered procedure (CPT). In synthetic cases we put this as Procedure;
    # in real Epic the original order is usually a ServiceRequest, but
    # Procedure carries the code on completed studies. Try both.
    cpt_list = _collect_cpt_codes(resources)
    if cpt_list:
        lines.append("Ordered / Recent CPT codes: " + ", ".join(cpt_list[:5]))

    # Diagnoses (Condition resources, ICD-10)
    icd_list = _collect_icd10_codes(resources)
    if icd_list:
        lines.append("Documented Diagnoses (ICD-10):")
        for code, text in icd_list[:10]:
            lines.append(f"  - {code}: {text}")

    # Available historical documents — index so the LLM knows what corpus
    # retrieval can pull from. Not a substitute for actual retrieval,
    # just a heads-up that these exist.
    if corpus_documents:
        non_current = [d for d in corpus_documents if getattr(d, "doc_type", "") != "current_note"]
        if non_current:
            lines.append("")
            lines.append(f"=== HISTORICAL DOCUMENTS AVAILABLE IN CHART ({len(non_current)} indexed) ===")
            for d in non_current[:15]:
                dt = getattr(d, "doc_type", "?")
                date = getattr(d, "date", "") or "—"
                title = getattr(d, "title", "") or "(untitled)"
                lines.append(f"  - [{dt} {date}] {title}")
            if len(non_current) > 15:
                lines.append(f"  …and {len(non_current) - 15} more")

    lines.append("")
    lines.append("=== CURRENT ENCOUNTER NOTE ===")
    note_body = (raw_note or "").strip()
    if note_body:
        lines.append(note_body)
    else:
        lines.append("(No current encounter note text supplied — see historical documents above.)")

    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────


def _first_resource(resources: dict, rtype: str) -> dict | None:
    """Return the first resource of the given type, or None."""
    bundle = resources.get(rtype) or {}
    if isinstance(bundle, dict) and "error" in bundle:
        return None
    for entry in (bundle.get("entry") or []):
        r = entry.get("resource") or {}
        if r.get("resourceType") == rtype:
            return r
    return None


def _format_name(name_list: list[dict]) -> str:
    """Best-effort name formatting from a FHIR HumanName list."""
    if not name_list:
        return ""
    n = name_list[0]
    if n.get("text"):
        return str(n["text"])
    given = " ".join(str(g) for g in (n.get("given") or []))
    family = str(n.get("family") or "")
    return (given + " " + family).strip()


def _first_identifier_value(
    identifiers: list[dict], *, system: str = "", type_code: str = "",
) -> str:
    """Pull the first identifier value matching either an Identifier.system
    URL or an Identifier.type.coding.code (e.g., 'MR' for MRN)."""
    for ident in identifiers:
        if system and ident.get("system") == system:
            return str(ident.get("value") or "")
        if type_code:
            for coding in ((ident.get("type") or {}).get("coding") or []):
                if coding.get("code") == type_code:
                    return str(ident.get("value") or "")
    # No filter or no match — return first value as fallback if no filter given
    if not system and not type_code and identifiers:
        return str(identifiers[0].get("value") or "")
    return ""


def _collect_cpt_codes(resources: dict) -> list[str]:
    """Pull CPT codes from Procedure + ServiceRequest resources."""
    out: list[str] = []
    for rtype in ("Procedure", "ServiceRequest"):
        bundle = resources.get(rtype) or {}
        if isinstance(bundle, dict) and "error" in bundle:
            continue
        for entry in (bundle.get("entry") or []):
            r = entry.get("resource") or {}
            for coding in ((r.get("code") or {}).get("coding") or []):
                if "cpt" in (coding.get("system") or "").lower() and coding.get("code"):
                    display = coding.get("display") or ""
                    label = f"{coding['code']}"
                    if display:
                        label += f" ({display[:50]})"
                    if label not in out:
                        out.append(label)
    return out


def _collect_icd10_codes(resources: dict) -> list[tuple[str, str]]:
    """Pull (code, text) tuples from Condition resources."""
    out: list[tuple[str, str]] = []
    bundle = resources.get("Condition") or {}
    if isinstance(bundle, dict) and "error" in bundle:
        return out
    for entry in (bundle.get("entry") or []):
        r = entry.get("resource") or {}
        code_obj = r.get("code") or {}
        text = code_obj.get("text") or ""
        for coding in (code_obj.get("coding") or []):
            sys_url = (coding.get("system") or "").lower()
            if "icd-10" in sys_url or "icd10" in sys_url:
                code = coding.get("code") or ""
                if code:
                    out.append((code, text or coding.get("display") or ""))
                    break
    return out
