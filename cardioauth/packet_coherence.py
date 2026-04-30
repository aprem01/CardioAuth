"""Submission-packet coherence check (Peter Apr 30).

Peter's architectural observation: many remaining failure modes aren't
extraction or reasoning errors — they're coherence failures across the
final packet. The chart can be right, the form can be right, the
reasoner can be right, but the narrative attestation can still
reference a different CPT, or the form value can be semantically
correct but not match the payer's allowed option list.

This module is a deterministic reviewer: it walks the assembled
artifacts, checks they agree, and emits severity-tagged warnings
where they don't. It does not call any LLM. It does not change any
artifact — it only flags. The gate consumes the warnings.

Usage:
    warnings = check_packet_coherence(
        chart=chart_data,
        reasoning=reasoning_result,
        raw_note=raw_note,
    )

Each warning is a dict shaped like the rest of the gate's warnings:
    {"kind": str, "severity": str, "message": str}
"""

from __future__ import annotations

import re
from typing import Any


_CPT_PATTERN = re.compile(r"\b(CPT\s*[#:]?\s*)?(\d{5})\b")


def _extract_cpt_codes(text: str) -> set[str]:
    """Return the set of 5-digit codes appearing in `text`.

    We restrict to codes that are explicitly tagged 'CPT' OR that fall
    inside the cardiology-relevant ranges to reduce false positives
    (e.g., a phone number starting with the digits 78492 would otherwise
    get flagged).
    """
    if not text:
        return set()
    found: set[str] = set()
    for m in _CPT_PATTERN.finditer(text):
        is_cpt = bool(m.group(1))   # "CPT 78492" prefix matched
        code = m.group(2)
        if is_cpt or _is_cardiology_cpt(code):
            found.add(code)
    return found


def _is_cardiology_cpt(code: str) -> bool:
    """Restrict the bareword digit match to cardiology-relevant ranges."""
    if not code or len(code) != 5:
        return False
    # 33xxx (cardiac surgery, TAVR, ablation, devices)
    # 75xxx (vascular imaging, CCTA, MRI)
    # 78xxx (nuclear cardiology, PET, SPECT)
    # 92xxx-93xxx (cardiac diagnostic + procedures)
    n = code[:2]
    return n in ("33", "75", "78", "92", "93")


def check_packet_coherence(
    *,
    chart: Any,
    reasoning: Any = None,
    raw_note: str = "",
) -> list[dict]:
    """Walk the assembled packet artifacts and emit coherence warnings.

    Today: CPT consistency across (chart.procedure_code, raw_note,
    pa_narrative_draft). Returns [] when no mismatches are found.
    """
    warnings: list[dict] = []
    requested_cpt = (getattr(chart, "procedure_code", "") or "").strip()
    requested_proc = (getattr(chart, "procedure_requested", "") or "").strip()
    narrative = ""
    if reasoning is not None:
        narrative = (getattr(reasoning, "pa_narrative_draft", "") or "")

    # ── CPT mismatch: note vs chart ──
    if raw_note and requested_cpt:
        note_cpts = _extract_cpt_codes(raw_note)
        # Only warn when the note has at least one CPT and none of them
        # match the requested CPT. Notes that don't mention any CPT are
        # not coherence problems.
        if note_cpts and requested_cpt not in note_cpts:
            warnings.append({
                "kind": "cpt_note_mismatch",
                "severity": "high",
                "message": (
                    f"Requested CPT {requested_cpt} but the clinical note "
                    f"references CPT {', '.join(sorted(note_cpts))}. "
                    "Resolve the intended CPT before submission so the form, "
                    "attestation, and payload all reference the same code."
                ),
            })

    # ── CPT mismatch: narrative vs chart ──
    if narrative and requested_cpt:
        narr_cpts = _extract_cpt_codes(narrative)
        # Filter narrative_cpts to those clearly used as 'the procedure'
        # (very lightweight — just check if the requested code appears).
        if narr_cpts and requested_cpt not in narr_cpts:
            warnings.append({
                "kind": "cpt_attestation_mismatch",
                "severity": "high",
                "message": (
                    f"Form is built for CPT {requested_cpt} ({requested_proc}) "
                    f"but the medical-necessity attestation references CPT "
                    f"{', '.join(sorted(narr_cpts))}. The submission packet "
                    "would be internally inconsistent — fix the attestation "
                    "or resolve the CPT before transmitting."
                ),
            })

    # ── Procedure-name vs narrative spot-check ──
    # If the narrative names a DIFFERENT procedure modality than
    # procedure_requested, flag it. Only fire when the narrative is
    # substantive (>= 80 chars) AND mentions at least one modality
    # keyword — short or generic narratives don't count as mismatches.
    if narrative and requested_proc and len(narrative) >= 80:
        narr_lower = narrative.lower()
        req_lower = requested_proc.lower()
        modality_keywords = (
            "pet", "spect", "ett", "treadmill", "cath", "mri", "ct ",
            "cta", "echo", "tavr", "ablation",
        )
        narr_modalities = {kw for kw in modality_keywords if kw in narr_lower}
        req_modalities = {kw for kw in modality_keywords if kw in req_lower}
        # Both sides must mention SOMETHING for a "drift" claim to be meaningful.
        if (narr_modalities and req_modalities
                and not (req_modalities & narr_modalities)):
            warnings.append({
                "kind": "procedure_name_drift",
                "severity": "medium",
                "message": (
                    f"Requested procedure '{requested_proc}' but narrative "
                    f"mentions a different modality ({', '.join(sorted(narr_modalities))}). "
                    "Verify the attestation is about the right study."
                ),
            })

    return warnings
