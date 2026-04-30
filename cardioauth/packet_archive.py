"""Phase C.1 + C.2 — versioned freezing + replay for SubmissionPacket.

Article alignment: Recommendation #4 (Confidence Scoring + Usage
Intelligence). Every verdict is reproducible from persisted state
alone, with the exact taxonomy / form schema / model version that
produced it stamped on the row.

Today's freeze content:
  packet.to_dict()                full typed packet JSON
  payer / resolved_cpt            indexed for query speed
  decision                        transmit | hold_for_review | block
  reviewer_recommendation         transmit | hold | block | ""
  finding_count                   total deterministic + reviewer findings
  highest_finding_severity        info | low | medium | high | blocking
  taxonomy_version, form_schema_version, model_version

Replay:
  load_packet(case_id) → SubmissionPacket
    Reconstructs the typed packet from persisted JSON. EvidenceGraph
    rebuilt from spans; references resolve identically (idempotent
    given same input).
"""

from __future__ import annotations

import logging
from typing import Any

from cardioauth.evidence import (
    EMPTY_REFERENCE,
    EvidenceGraph,
    EvidenceReference,
    EvidenceSpan,
)
from cardioauth.submission_packet import (
    Finding,
    FormFieldEntry,
    NarrativeAttestation,
    ResolvedCPT,
    ReviewerVerdict,
    SubmissionPacket,
    severity_rank,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Freeze
# ──────────────────────────────────────────────────────────────────────


def _highest_severity_label(packet: SubmissionPacket) -> str:
    """Return the highest severity across deterministic + reviewer
    findings, or '' when there are none."""
    sev = packet.highest_severity()
    return sev or ""


def _total_finding_count(packet: SubmissionPacket) -> int:
    n = len(packet.deterministic_findings)
    if packet.reviewer_verdict:
        for findings in packet.reviewer_verdict.findings_per_question.values():
            n += len(findings)
    return n


def freeze_packet(packet: SubmissionPacket) -> dict:
    """Persist `packet` to the submission_packets table.

    Idempotent on case_id — re-freezing replaces the prior row.
    Returns a small summary dict for telemetry/UI.
    """
    from cardioauth.persistence import get_store

    store = get_store()
    payload = packet.to_dict()
    reviewer_rec = (
        packet.reviewer_verdict.overall_recommendation
        if packet.reviewer_verdict else ""
    )
    finding_count = _total_finding_count(packet)
    highest = _highest_severity_label(packet)

    store.save_packet(
        case_id=packet.case_id,
        packet_json=payload,
        payer=packet.payer or "",
        resolved_cpt=packet.resolved_cpt.cpt or "",
        decision=packet.decision or "",
        reviewer_recommendation=reviewer_rec,
        finding_count=finding_count,
        highest_finding_severity=highest,
        taxonomy_version=packet.taxonomy_version or "",
        form_schema_version=packet.form_schema_version or "",
        model_version=packet.model_version or "",
    )
    try:
        store.append_audit(
            actor="packet_archive",
            action="packet_frozen",
            subject_id=packet.case_id,
            detail=(
                f"decision={packet.decision} "
                f"reviewer={reviewer_rec} "
                f"findings={finding_count} "
                f"highest={highest}"
            ),
        )
    except Exception:
        pass

    return {
        "case_id": packet.case_id,
        "decision": packet.decision,
        "reviewer_recommendation": reviewer_rec,
        "finding_count": finding_count,
        "highest_finding_severity": highest,
        "taxonomy_version": packet.taxonomy_version,
        "model_version": packet.model_version,
    }


# ──────────────────────────────────────────────────────────────────────
# Replay (Phase C.2 — also used by the API endpoint)
# ──────────────────────────────────────────────────────────────────────


def _restore_finding(d: dict) -> Finding:
    return Finding(
        kind=str(d.get("kind", "")),
        severity=d.get("severity", "low"),    # type: ignore[arg-type]
        message=str(d.get("message", "")),
        related_field_keys=tuple(str(k) for k in (d.get("related_field_keys") or [])),
        auto_fixable=bool(d.get("auto_fixable", False)),
        fix_suggestion=str(d.get("fix_suggestion", "")),
        checker=str(d.get("checker", "")),
    )


def _restore_evidence_reference(d: dict | None) -> EvidenceReference:
    if not d:
        return EMPTY_REFERENCE
    return EvidenceReference(
        span_ids=tuple(d.get("span_ids") or []),
        rationale=str(d.get("rationale", "")),
        derivation=str(d.get("derivation", "direct")),
    )


def _restore_form_fields(items: list[dict]) -> list[FormFieldEntry]:
    out: list[FormFieldEntry] = []
    for d in items:
        out.append(FormFieldEntry(
            key=str(d.get("key", "")),
            label=str(d.get("label", "")),
            category=str(d.get("category", "")),
            required=bool(d.get("required", False)),
            format=str(d.get("format", "text")),
            options=list(d.get("options") or []),
            value=str(d.get("value", "")),
            evidence_text=str(d.get("evidence_text", "")),
            evidence=_restore_evidence_reference(d.get("evidence")),
            status=d.get("status", "missing"),    # type: ignore[arg-type]
            missing_reason=str(d.get("missing_reason", "")),
            help_text=str(d.get("help_text", "")),
        ))
    return out


def _restore_resolved_cpt(d: dict | None) -> ResolvedCPT:
    d = d or {}
    return ResolvedCPT(
        cpt=str(d.get("cpt", "")),
        procedure=str(d.get("procedure", "")),
        source=d.get("source", "request"),    # type: ignore[arg-type]
        evidence=_restore_evidence_reference(d.get("evidence")),
        rationale=str(d.get("rationale", "")),
    )


def _restore_narrative(d: dict | None) -> NarrativeAttestation:
    d = d or {}
    return NarrativeAttestation(
        text=str(d.get("text", "")),
        cpt_referenced=str(d.get("cpt_referenced", "")),
        procedure_referenced=str(d.get("procedure_referenced", "")),
        evidence=_restore_evidence_reference(d.get("evidence")),
    )


def _restore_reviewer_verdict(d: dict | None) -> ReviewerVerdict | None:
    if not d:
        return None
    fpq: dict[str, list[Finding]] = {}
    for q, findings in (d.get("findings_per_question") or {}).items():
        fpq[q] = [_restore_finding(f) for f in (findings or [])]
    return ReviewerVerdict(
        passed=bool(d.get("passed", False)),
        overall_recommendation=d.get("overall_recommendation", "hold"),    # type: ignore[arg-type]
        rationale=str(d.get("rationale", "")),
        findings_per_question=fpq,
        model=str(d.get("model", "")),
        cost_input_tokens=int(d.get("cost_input_tokens", 0) or 0),
        cost_output_tokens=int(d.get("cost_output_tokens", 0) or 0),
        duration_ms=int(d.get("duration_ms", 0) or 0),
        reviewed_at=str(d.get("reviewed_at", "")),
    )


def packet_from_dict(payload: dict) -> SubmissionPacket:
    """Reconstruct a SubmissionPacket from its serialized form.

    Bypasses the .build() validation because it operates on already-
    persisted data; if the stored row was valid at write time, the
    references resolve identically on read.
    """
    graph = EvidenceGraph.from_dict(payload.get("evidence_graph") or {})
    packet = SubmissionPacket(
        case_id=str(payload.get("case_id", "")),
        created_at=str(payload.get("created_at", "")),
        raw_note=str(payload.get("raw_note", "")),
        chart_data=dict(payload.get("chart_data") or {}),
        policy_data=payload.get("policy_data"),
        resolved_cpt=_restore_resolved_cpt(payload.get("resolved_cpt")),
        payer=str(payload.get("payer", "")),
        form_fields=_restore_form_fields(payload.get("form_fields") or []),
        narrative=_restore_narrative(payload.get("narrative")),
        evidence_graph=graph,
        reasoner_summary=dict(payload.get("reasoner_summary") or {}),
        taxonomy_version=str(payload.get("taxonomy_version", "")),
        form_schema_version=str(payload.get("form_schema_version", "")),
        model_version=str(payload.get("model_version", "")),
        deterministic_findings=[
            _restore_finding(f) for f in (payload.get("deterministic_findings") or [])
        ],
        reviewer_verdict=_restore_reviewer_verdict(payload.get("reviewer_verdict")),
        decision=payload.get("decision", "hold_for_review"),    # type: ignore[arg-type]
        decision_rationale=str(payload.get("decision_rationale", "")),
    )
    return packet


def load_packet(case_id: str) -> SubmissionPacket | None:
    """Replay (Phase C.2): rebuild a typed packet from persisted state."""
    from cardioauth.persistence import get_store
    row = get_store().get_packet(case_id)
    if not row or not row.get("packet"):
        return None
    return packet_from_dict(row["packet"])


def list_archived_packets(
    *, payer: str = "", resolved_cpt: str = "",
    decision: str = "", limit: int = 50,
) -> list[dict]:
    """Index view — payer, cpt, decision, severity, finding_count.
    For replay, the caller does load_packet(case_id) per row."""
    from cardioauth.persistence import get_store
    return get_store().list_packets(
        payer=payer, resolved_cpt=resolved_cpt, decision=decision, limit=limit,
    )
