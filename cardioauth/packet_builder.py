"""Phase A.5 + B.1 — typed SubmissionPacket builder.

Single entry point that takes the artifacts the existing pipeline
already produces (chart, raw note, evidence graph, policy data,
reasoning result, payer form) and assembles them into a validated
SubmissionPacket.

Phase B.1 adds the proper CPT reconciler — see cpt_resolver.py.
Disagreements between request, note, and reasoner-recommended CPTs
emit Findings on the packet rather than being recorded only in
ResolvedCPT.rationale.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cardioauth.evidence import (
    EMPTY_REFERENCE,
    EvidenceGraph,
    EvidenceReference,
    EvidenceSpan,
)
from cardioauth.evidence_extraction import locate_in_source, make_span
from cardioauth.ontology import SubmissionPacketOntology, get_default_ontology
from cardioauth.payer_forms import PayerForm, populate_payer_form_entries
from cardioauth.submission_packet import (
    NarrativeAttestation,
    ResolvedCPT,
    SubmissionPacket,
)

logger = logging.getLogger(__name__)


_CPT_REFERENCE_PATTERN = re.compile(r"\bCPT\s*[#:]?\s*(\d{5})\b")
_CARDIOLOGY_BAREWORD_CPT = re.compile(r"\b(33|75|78|92|93)\d{3}\b")


def _extract_cpt_from_text(text: str) -> str:
    """First explicit CPT reference in the text, else the first
    cardiology-range bareword 5-digit code, else empty."""
    if not text:
        return ""
    m = _CPT_REFERENCE_PATTERN.search(text)
    if m:
        return m.group(1)
    m = _CARDIOLOGY_BAREWORD_CPT.search(text)
    if m:
        return m.group(0)
    return ""


def build_resolved_cpt(
    *,
    chart_procedure_code: str,
    chart_procedure_requested: str,
    raw_note: str = "",
    reasoning: Any = None,
    evidence_graph: EvidenceGraph | None = None,
):
    """Phase B.1: delegates to the proper CPT resolver.

    Returns (ResolvedCPT, findings). Callers that don't need the
    findings list can ignore it; build_submission_packet stacks them
    onto the packet so the reviewer + UI surface them.
    """
    from cardioauth.cpt_resolver import resolve_cpt
    return resolve_cpt(
        chart_procedure_code=chart_procedure_code,
        chart_procedure_requested=chart_procedure_requested,
        raw_note=raw_note,
        reasoning=reasoning,
        evidence_graph=evidence_graph,
    )


def build_narrative_attestation(
    *,
    reasoning: Any,
    raw_note: str = "",
    evidence_graph: EvidenceGraph | None = None,
) -> NarrativeAttestation:
    """Build the typed NarrativeAttestation from a reasoning result.

    Extracts cpt_referenced + procedure_referenced from the narrative
    text so the reviewer can compare them against ResolvedCPT.
    """
    text = ""
    if reasoning is not None:
        text = (getattr(reasoning, "pa_narrative_draft", "") or "")

    cpt_in_narrative = _extract_cpt_from_text(text)
    proc_in_narrative = _extract_procedure_modality(text)

    # Evidence reference: the narrative is itself an artifact, not
    # extracted from the chart. We seed an empty ref today; Phase B.3
    # (reviewer) attaches the spans the narrative is supposed to cite.
    ref = EMPTY_REFERENCE
    return NarrativeAttestation(
        text=text,
        cpt_referenced=cpt_in_narrative,
        procedure_referenced=proc_in_narrative,
        evidence=ref,
    )


def _extract_procedure_modality(text: str) -> str:
    """Best-effort: name the modality the narrative is written about."""
    if not text:
        return ""
    lc = text.lower()
    for label, kw in (
        ("Cardiac PET", "cardiac pet"),
        ("Cardiac SPECT", "cardiac spect"),
        ("Exercise SPECT", "exercise spect"),
        ("Stress Echocardiogram", "stress echo"),
        ("ETT (treadmill stress test)", "treadmill"),
        ("Left Heart Catheterization", "left heart cath"),
        ("Cardiac MRI", "cardiac mri"),
        ("Coronary CT Angiography", "ccta"),
        ("TAVR", "tavr"),
        ("Catheter Ablation", "ablation"),
    ):
        if kw in lc:
            return label
    return ""


def build_submission_packet(
    *,
    case_id: str,
    raw_note: str,
    chart: Any,                          # ChartData instance
    policy_data: Any,                    # PolicyData | None
    reasoning: Any,
    evidence_graph: EvidenceGraph,
    payer_form: PayerForm | None,
    payer: str,
    ontology: SubmissionPacketOntology | None = None,
    taxonomy_version: str = "",
    form_schema_version: str = "",
    model_version: str = "",
) -> SubmissionPacket:
    """Phase A.5 — assemble a validated SubmissionPacket from artifacts.

    Replaces the loose dict-and-mutation runtime in demo_e2e with a
    typed, integrity-validated object that subsequent phases attach
    findings + reviewer verdicts to.
    """
    if ontology is None:
        ontology = get_default_ontology()

    # Resolved CPT — request canonical, reconciled against note + reasoner
    resolved, cpt_findings = build_resolved_cpt(
        chart_procedure_code=getattr(chart, "procedure_code", ""),
        chart_procedure_requested=getattr(chart, "procedure_requested", ""),
        raw_note=raw_note,
        reasoning=reasoning,
        evidence_graph=evidence_graph,
    )

    # Reasoner summary snapshot for the verification pipeline
    reasoner_summary: dict = {}
    if reasoning is not None:
        reasoner_summary = {
            "approval_score": getattr(reasoning, "approval_likelihood_score", None),
            "approval_label": getattr(reasoning, "approval_likelihood_label", ""),
            "approval_score_raw": getattr(reasoning, "_approval_score_raw", None),
            "score_capped_by_extraction": getattr(reasoning, "_score_capped_by_extraction", False),
            "chart_confidence": getattr(chart, "confidence_score", None),
            "alternative_modality": getattr(reasoning, "_alternative_modality", None),
            "criteria_met_count": len(getattr(reasoning, "criteria_met", []) or []),
            "criteria_not_met_count": len(getattr(reasoning, "criteria_not_met", []) or []),
        }

    # Narrative attestation
    narrative = build_narrative_attestation(
        reasoning=reasoning, raw_note=raw_note,
        evidence_graph=evidence_graph,
    )

    # Form fields (typed)
    if payer_form is not None:
        form_entries = populate_payer_form_entries(
            payer_form,
            chart_data=chart,
            policy_data=policy_data,
            reasoning=reasoning,
            evidence_graph=evidence_graph,
            ontology=ontology,
        )
    else:
        form_entries = []

    # Versioning
    if not taxonomy_version:
        try:
            from cardioauth.taxonomy.taxonomy import TAXONOMY_VERSION
            taxonomy_version = TAXONOMY_VERSION
        except Exception:
            taxonomy_version = "unknown"

    packet = SubmissionPacket.build(
        case_id=case_id,
        raw_note=raw_note,
        chart_data=chart.model_dump(mode="json") if hasattr(chart, "model_dump") else dict(chart),
        policy_data=(policy_data.model_dump(mode="json") if policy_data is not None and hasattr(policy_data, "model_dump") else None),
        resolved_cpt=resolved,
        payer=payer,
        form_fields=form_entries,
        narrative=narrative,
        evidence_graph=evidence_graph,
        reasoner_summary=reasoner_summary,
        taxonomy_version=taxonomy_version,
        form_schema_version=form_schema_version,
        model_version=model_version,
    )
    # Phase B.1: stack the CPT-reconciler findings onto the packet so
    # the gate, reviewer, and UI all surface them.
    if cpt_findings:
        packet.add_findings(cpt_findings)
    return packet
