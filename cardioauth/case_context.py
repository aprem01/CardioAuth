"""CaseContext — shared working memory that travels through every agent.

The key insight from Peter's validation: the old pipeline lost clinical
relationships across agent boundaries because each agent received a
lossy summary of what came before. CaseContext fixes this by making
the raw clinical narrative AND all structured extractions available
to every agent.

Flow:
  1. CHART_AGENT populates .chart_data and preserves .raw_note
  2. RelationshipExtractor reads raw_note → populates .relationships
  3. POLICY_AGENT populates .policy_data + .retrieved_chunks
  4. PrecedentRetriever queries Pinecone → populates .precedents
  5. UnifiedReasoner sees the whole context → populates .criterion_matches
  6. After the request, the context is serialized to Postgres for audit
     and embedded into Pinecone for future precedent retrieval.

Design principles:
  - Raw note is NEVER dropped — every agent can access it
  - Each agent appends to reasoning_trace so we have an audit log
  - Bucketing becomes a derived VIEW, not a replacement
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ClinicalRelationship:
    """A causal/logical chain extracted from the clinical narrative.

    Examples:
      - "dyspnea + obesity" → "unable to exercise" → "pharmacologic imaging indicated"
      - "prior SPECT attenuation artifact" → "non-diagnostic prior study" → "PET justified"
      - "LBBB on baseline ECG" → "stress ECG uninterpretable" → "nuclear imaging indicated"
    """
    premises: list[str]            # Observations from the note
    conclusion: str                # What they clinically imply
    supports_criterion: str = ""   # Which taxonomy code this relationship supports
    evidence_quote: str = ""       # Verbatim quote from the note
    confidence: float = 0.0


@dataclass
class PrecedentCase:
    """A similar past case retrieved from long-term memory."""
    case_id: str
    summary: str                   # Short description of the case
    cpt_code: str
    payer: str
    outcome: str                   # approved / denied / pending
    similarity: float              # 0-1, cosine similarity
    narrative_excerpt: str = ""    # Part of the winning narrative


@dataclass
class AgentTrace:
    """One entry in the audit trail — what each agent concluded."""
    agent_name: str
    timestamp: str
    action: str
    output_summary: str
    tokens_used: int = 0
    duration_ms: int = 0


@dataclass
class CaseContext:
    """Shared working memory for a single PA request."""

    # ── Identity ──
    case_id: str
    procedure_code: str
    procedure_name: str
    payer_name: str
    user_id: str = "demo"

    # ── Raw narrative (NEVER dropped) ──
    raw_note: str = ""

    # ── Structured extractions ──
    chart_data: dict = field(default_factory=dict)
    policy_data: dict = field(default_factory=dict)

    # ── Clinical relationships (from raw note) ──
    relationships: list[ClinicalRelationship] = field(default_factory=list)

    # ── Long-term memory ──
    precedents: list[PrecedentCase] = field(default_factory=list)

    # ── Reasoning outputs ──
    criterion_matches: list[dict] = field(default_factory=list)
    approval_score: float = 0.0
    approval_label: str = ""
    narrative_draft: str = ""

    # ── Audit trail ──
    reasoning_trace: list[AgentTrace] = field(default_factory=list)
    system_warnings: list[dict] = field(default_factory=list)

    # ── Metadata ──
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def trace(self, agent_name: str, action: str, summary: str, tokens: int = 0, ms: int = 0) -> None:
        """Append an entry to the reasoning trace — every agent should call this."""
        self.reasoning_trace.append(AgentTrace(
            agent_name=agent_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            output_summary=summary[:500],
            tokens_used=tokens,
            duration_ms=ms,
        ))

    def warn(self, level: str, agent: str, message: str, kind: str = "") -> None:
        """Record a system warning (e.g., agent fallback, partial failure)."""
        self.system_warnings.append({
            "level": level,
            "agent": agent,
            "message": message,
            "kind": kind,
        })

    def build_clinical_narrative(self) -> str:
        """Build a unified clinical narrative from structured chart_data.

        Used when the raw_note wasn't provided (e.g., custom form submissions).
        Composes all structured fields into a readable clinical note so
        downstream agents can reason over the full clinical picture
        rather than isolated fields.
        """
        if self.raw_note:
            return self.raw_note

        chart = self.chart_data or {}
        parts = []

        # Demographics
        demo = []
        if chart.get("age"):
            demo.append(f"{chart['age']}-year-old")
        if chart.get("sex"):
            demo.append("male" if chart["sex"] == "M" else "female")
        if chart.get("patient_name"):
            demo.append(chart["patient_name"])
        if demo:
            parts.append("Patient: " + " ".join(demo) + ".")

        # Diagnoses
        dx_codes = chart.get("diagnosis_codes", []) or []
        if dx_codes:
            parts.append("Active diagnoses (ICD-10): " + ", ".join(dx_codes) + ".")

        # Comorbidities
        comorbidities = chart.get("comorbidities", []) or []
        if comorbidities:
            parts.append("Comorbidities:\n" + "\n".join(f"  - {c}" for c in comorbidities))

        # Medications
        meds = chart.get("relevant_medications", []) or []
        if meds:
            parts.append("Current medications:")
            for m in meds:
                name = m.get("name", "")
                dose = m.get("dose", "")
                ind = m.get("indication", "")
                line = f"  - {name}"
                if dose:
                    line += f" {dose}"
                if ind:
                    line += f" ({ind})"
                parts.append(line)

        # Imaging / prior studies
        imaging = chart.get("relevant_imaging", []) or []
        if imaging:
            parts.append("Prior imaging and studies:")
            for i in imaging:
                itype = i.get("type", "")
                idate = i.get("date", "")
                summary = i.get("result_summary", "")
                line = f"  - {itype}"
                if idate:
                    line += f" ({idate})"
                if summary:
                    line += f": {summary}"
                parts.append(line)

        # Labs
        labs = chart.get("relevant_labs", []) or []
        if labs:
            parts.append("Key laboratory values:")
            for l in labs:
                parts.append(
                    f"  - {l.get('name', '')}: "
                    f"{l.get('value', '')} {l.get('unit', '')}".rstrip()
                )

        # Prior treatments
        treatments = chart.get("prior_treatments", []) or []
        if treatments:
            parts.append("Prior treatments/procedures:\n" + "\n".join(f"  - {t}" for t in treatments))

        # ECG findings (if standalone)
        ecg = chart.get("ecg_findings", "")
        if ecg:
            parts.append(f"ECG findings: {ecg}")

        # LVEF (if standalone)
        ef = chart.get("ejection_fraction", "")
        if ef:
            parts.append(f"LVEF: {ef}")

        # Additional notes — THIS IS THE OFFICE NOTE for DOC-001
        notes = chart.get("additional_notes", "") or chart.get("office_notes", "")
        if notes:
            parts.append("Clinical notes / office consultation note:")
            parts.append(notes)

        # Procedure being requested
        parts.append(
            f"\nRequested procedure: {self.procedure_name} "
            f"(CPT {self.procedure_code}) for {self.payer_name}."
        )

        narrative = "\n\n".join(parts)
        self.raw_note = narrative  # Cache it
        return narrative

    def to_dict(self) -> dict:
        """Serialize for database storage and API responses."""
        return {
            "case_id": self.case_id,
            "procedure_code": self.procedure_code,
            "procedure_name": self.procedure_name,
            "payer_name": self.payer_name,
            "user_id": self.user_id,
            "raw_note": self.raw_note,
            "chart_data": self.chart_data,
            "policy_data": self.policy_data,
            "relationships": [
                {
                    "premises": r.premises,
                    "conclusion": r.conclusion,
                    "supports_criterion": r.supports_criterion,
                    "evidence_quote": r.evidence_quote,
                    "confidence": r.confidence,
                }
                for r in self.relationships
            ],
            "precedents": [
                {
                    "case_id": p.case_id,
                    "summary": p.summary,
                    "cpt_code": p.cpt_code,
                    "payer": p.payer,
                    "outcome": p.outcome,
                    "similarity": p.similarity,
                    "narrative_excerpt": p.narrative_excerpt,
                }
                for p in self.precedents
            ],
            "criterion_matches": self.criterion_matches,
            "approval_score": self.approval_score,
            "approval_label": self.approval_label,
            "narrative_draft": self.narrative_draft,
            "reasoning_trace": [
                {
                    "agent_name": t.agent_name,
                    "timestamp": t.timestamp,
                    "action": t.action,
                    "output_summary": t.output_summary,
                    "tokens_used": t.tokens_used,
                    "duration_ms": t.duration_ms,
                }
                for t in self.reasoning_trace
            ],
            "system_warnings": self.system_warnings,
            "created_at": self.created_at,
        }
