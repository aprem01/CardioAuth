"""Phase B.1 — ResolvedCPT reconciler.

The pipeline has up to four CPT signals per case:
  1. Request CPT      — chart.procedure_code (what the order asks for)
  2. Note CPT         — explicitly mentioned in raw_note
  3. Reasoner CPT     — alternative modality the reasoner suggested
  4. Narrative CPT    — referenced in pa_narrative_draft

Today's policy: the request CPT is canonical. Disagreements don't
silently change the order — they surface as Findings the reviewer +
back office act on. Peter Apr 30: "if there is a CPT or modality
mismatch, is it clearly flagged before submission rather than
silently changed?"

This module is deterministic. The LLM reviewer (B.3) consumes its
output as additional context and may add its own narrative-level
findings.
"""

from __future__ import annotations

import re
from typing import Any

from cardioauth.evidence import (
    EMPTY_REFERENCE,
    EvidenceGraph,
    EvidenceReference,
)
from cardioauth.submission_packet import Finding, ResolvedCPT


_CPT_REFERENCE_PATTERN = re.compile(r"\bCPT\s*[#:]?\s*(\d{5})\b")
_CARDIOLOGY_BAREWORD_CPT = re.compile(r"\b(?:33|75|78|92|93)\d{3}\b")


# Procedure-family roots — used by `same_family` to decide whether a
# request/note CPT divergence is a minor variant (PET single vs
# multiple, both 78491/78492) or a family-level mismatch (PET vs SPECT).
# Each family is a frozenset so membership is O(1).
_CPT_FAMILIES: tuple[frozenset[str], ...] = (
    # Cardiac PET (78491 single, 78492 multiple)
    frozenset({"78491", "78492"}),
    # Cardiac SPECT (78451 single, 78452 multiple)
    frozenset({"78451", "78452"}),
    # Stress echo
    frozenset({"93350", "93351"}),
    # Cardiac MRI/CT family
    frozenset({"75557", "75561", "75565", "75571", "75572", "75573", "75574"}),
    # TAVR family
    frozenset({"33361", "33362", "33363", "33364", "33365"}),
    # LHC family
    frozenset({"93454", "93458", "93459", "93460"}),
)


def same_family(a: str, b: str) -> bool:
    """True iff a and b are siblings within the same CPT family."""
    if not a or not b or a == b:
        return a == b
    for fam in _CPT_FAMILIES:
        if a in fam and b in fam:
            return True
    return False


def extract_cpt_from_text(text: str) -> str:
    """First explicit 'CPT NNNNN' tag, else first cardiology-range
    bareword 5-digit code, else empty string."""
    if not text:
        return ""
    m = _CPT_REFERENCE_PATTERN.search(text)
    if m:
        return m.group(1)
    m = _CARDIOLOGY_BAREWORD_CPT.search(text)
    if m:
        return m.group(0)
    return ""


def _reasoner_alternative_cpt(reasoning: Any) -> str:
    """Pull the alternative-modality CPT off a reasoning result, if any."""
    if reasoning is None:
        return ""
    alt = getattr(reasoning, "_alternative_modality", None)
    if isinstance(alt, dict):
        return (alt.get("cpt") or "").strip()
    return ""


def _build_evidence_reference(
    evidence_graph: EvidenceGraph | None,
) -> EvidenceReference:
    if evidence_graph is None:
        return EMPTY_REFERENCE
    spans = evidence_graph.spans_for_field_path("chart.procedure_code")
    if not spans:
        return EMPTY_REFERENCE
    return EvidenceReference.from_spans(
        spans, rationale="request CPT", derivation="direct",
    )


def resolve_cpt(
    *,
    chart_procedure_code: str,
    chart_procedure_requested: str,
    raw_note: str = "",
    reasoning: Any = None,
    evidence_graph: EvidenceGraph | None = None,
) -> tuple[ResolvedCPT, list[Finding]]:
    """Reconcile every CPT signal and return (canonical CPT, findings).

    Resolution policy:
      - The request CPT is canonical. The system never autonomously
        rewrites the order.
      - When the note explicitly orders a DIFFERENT CPT in a different
        procedure family → high-severity finding (Peter's Case 5).
      - When the note CPT is a same-family minor variant → low finding.
      - When the reasoner suggests an alternative modality whose CPT
        differs from the request → medium finding ("alternative modality").
      - Narrative CPT divergence is flagged separately by the
        packet_coherence module already; we don't duplicate it here.
    """
    request_cpt = (chart_procedure_code or "").strip()
    request_proc = (chart_procedure_requested or "").strip()
    note_cpt = extract_cpt_from_text(raw_note)
    reasoner_cpt = _reasoner_alternative_cpt(reasoning)

    findings: list[Finding] = []
    rationale_bits: list[str] = [f"resolved={request_cpt}", "source=request"]

    # Note-vs-request reconciliation
    if note_cpt and request_cpt and note_cpt != request_cpt:
        if same_family(request_cpt, note_cpt):
            findings.append(Finding(
                kind="cpt_minor_variant",
                severity="low",
                message=(
                    f"Request CPT {request_cpt} and note CPT {note_cpt} are "
                    "in the same procedure family — likely a single vs "
                    "multiple-study or coding-detail variant. Verify "
                    "the intended code before submission."
                ),
                checker="cpt_resolver_v1",
            ))
            rationale_bits.append(f"note variant {note_cpt}")
        else:
            findings.append(Finding(
                kind="cpt_family_mismatch",
                severity="high",
                message=(
                    f"Request CPT {request_cpt} is for "
                    f"{request_proc or 'the requested procedure'}, but the "
                    f"clinical note explicitly orders CPT {note_cpt}. "
                    "These are different procedure families. Resolve before "
                    "submission so the form, attestation, and payload are "
                    "internally consistent."
                ),
                checker="cpt_resolver_v1",
                auto_fixable=False,
            ))
            rationale_bits.append(f"note orders {note_cpt}")
    elif note_cpt and note_cpt == request_cpt:
        rationale_bits.append("note agrees")

    # Reasoner-alternative reconciliation
    if reasoner_cpt and request_cpt and reasoner_cpt != request_cpt:
        # If reasoner agrees with the note, the divergence is one signal
        # (not two). Phrase the message accordingly.
        agrees_with_note = reasoner_cpt == note_cpt
        if agrees_with_note:
            findings.append(Finding(
                kind="cpt_reasoner_supports_note",
                severity="medium",
                message=(
                    f"Reasoner suggests CPT {reasoner_cpt} as the more "
                    f"appropriate modality (matching the note). Request "
                    f"remains {request_cpt}; surface the alternative on "
                    "the holding queue."
                ),
                checker="cpt_resolver_v1",
            ))
        else:
            findings.append(Finding(
                kind="cpt_alternative_modality",
                severity="medium",
                message=(
                    f"Reasoner suggested CPT {reasoner_cpt} as an "
                    f"alternative to the request CPT {request_cpt}. "
                    "Treat as advisory; the request remains canonical."
                ),
                checker="cpt_resolver_v1",
            ))
        rationale_bits.append(f"reasoner alt {reasoner_cpt}")

    resolved = ResolvedCPT(
        cpt=request_cpt,
        procedure=request_proc,
        source="request",
        evidence=_build_evidence_reference(evidence_graph),
        rationale="; ".join(rationale_bits),
    )
    return resolved, findings
