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
    approval_score: float = 0.0    # Score assigned to this past case


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

        Apr 28 (Peter Cases 3/4): the reasoner was returning 0 criteria met
        on cases with strong extraction because this composer only read the
        legacy v1 fields (`comorbidities`, `prior_treatments`) and treated
        `ecg_findings` as a string. The v2 buckets — `current_symptoms`,
        `prior_stress_tests`, `past_medical_history`, `family_history`,
        `exam_findings`, `prior_procedures`, structured `ecg_findings` —
        were never surfaced to Claude, so the reasoner reasoned over an
        empty narrative.

        Now: always compose the structured narrative AND append the raw note
        if present, so Claude has both the parsed buckets (high precision)
        and the original prose (catches anything the parser missed).
        """
        chart = self.chart_data or {}
        parts: list[str] = []

        # ── Demographics ──
        demo: list[str] = []
        if chart.get("age"):
            demo.append(f"{chart['age']}-year-old")
        if chart.get("sex"):
            demo.append("male" if chart["sex"] == "M" else "female" if chart["sex"] == "F" else chart["sex"])
        if chart.get("patient_name"):
            demo.append(chart["patient_name"])
        if demo:
            parts.append("Patient: " + " ".join(demo) + ".")

        # ── Diagnoses ──
        dx_codes = chart.get("diagnosis_codes", []) or []
        if dx_codes:
            parts.append("Active diagnoses (ICD-10): " + ", ".join(dx_codes) + ".")

        # ── Active comorbidities (v2 + legacy fallback) ──
        active_co = chart.get("active_comorbidities", []) or []
        legacy_co = chart.get("comorbidities", []) or []
        comorbidities = active_co or legacy_co
        if comorbidities:
            parts.append("Active comorbidities:\n" + "\n".join(f"  - {c}" for c in comorbidities))

        # ── Current symptoms (v2 only) ──
        symptoms = chart.get("current_symptoms", []) or []
        if symptoms:
            lines = []
            for s in symptoms:
                if not isinstance(s, dict):
                    continue
                name = s.get("name", "")
                if not name:
                    continue
                bits = [name]
                if s.get("character"):
                    bits.append(s["character"])
                if s.get("change_vs_baseline"):
                    bits.append(s["change_vs_baseline"])
                if s.get("onset"):
                    bits.append(f"onset {s['onset']}")
                if s.get("frequency"):
                    bits.append(s["frequency"])
                if s.get("severity"):
                    bits.append(s["severity"])
                lines.append("  - " + " · ".join(bits))
            if lines:
                parts.append("Current symptoms (HPI):\n" + "\n".join(lines))

        # ── Exam findings (v2) ──
        exam = chart.get("exam_findings", []) or []
        if exam:
            lines = []
            for e in exam:
                if isinstance(e, dict) and e.get("finding"):
                    lines.append(f"  - {e['finding']}" + (f" ({e['system']})" if e.get("system") else ""))
            if lines:
                parts.append("Physical exam:\n" + "\n".join(lines))

        # ── Past medical history (v2) ──
        pmh = chart.get("past_medical_history", []) or []
        if pmh:
            lines = []
            for p in pmh:
                if not isinstance(p, dict):
                    continue
                cond = p.get("condition", "")
                if not cond:
                    continue
                line = f"  - {cond}"
                if p.get("date"):
                    line += f" ({p['date']})"
                if p.get("details"):
                    line += f" — {p['details']}"
                lines.append(line)
            if lines:
                parts.append("Past medical history:\n" + "\n".join(lines))

        # ── Family history (v2) ──
        fhx = chart.get("family_history", []) or []
        if fhx:
            lines = []
            for f in fhx:
                if not isinstance(f, dict):
                    continue
                cond = f.get("condition", "")
                if not cond:
                    continue
                rel = f.get("relation", "") or "first-degree relative"
                age = f" at age {f['age_at_event']}" if f.get("age_at_event") else ""
                lines.append(f"  - {rel}: {cond}{age}")
            if lines:
                parts.append("Family history:\n" + "\n".join(lines))

        # ── Medications ──
        meds = chart.get("relevant_medications", []) or []
        if meds:
            lines = ["Current medications:"]
            for m in meds:
                if not isinstance(m, dict):
                    continue
                name = m.get("name", "")
                dose = m.get("dose", "")
                ind = m.get("indication", "")
                line = f"  - {name}"
                if dose:
                    line += f" {dose}"
                if ind:
                    line += f" ({ind})"
                lines.append(line)
            parts.append("\n".join(lines))

        # ── ECG findings (v2 list of structured dicts) ──
        ecg = chart.get("ecg_findings", []) or []
        if isinstance(ecg, list) and ecg:
            lines = []
            for e in ecg:
                if not isinstance(e, dict):
                    continue
                bits = []
                for fld in ("rhythm", "conduction", "hypertrophy_or_strain",
                            "ischemic_changes", "pacing"):
                    v = e.get(fld)
                    if v:
                        bits.append(f"{fld.replace('_', ' ')}: {v}")
                if not bits and e.get("summary"):
                    bits.append(e["summary"])
                if bits:
                    lines.append("  - " + ", ".join(bits))
            if lines:
                parts.append("Baseline ECG:\n" + "\n".join(lines))
        elif isinstance(ecg, str) and ecg:
            parts.append(f"ECG findings: {ecg}")

        # ── Prior stress tests (v2) ──
        stress = chart.get("prior_stress_tests", []) or []
        if stress:
            lines = []
            for s in stress:
                if not isinstance(s, dict):
                    continue
                modality = s.get("modality", "")
                if not modality:
                    continue
                line = f"  - {modality}"
                if s.get("date"):
                    line += f" ({s['date']})"
                if s.get("max_hr_percent"):
                    line += f", {s['max_hr_percent']} MPHR"
                if s.get("interpretation"):
                    line += f": {s['interpretation']}"
                if s.get("result_summary"):
                    line += f" — {s['result_summary']}"
                lines.append(line)
            if lines:
                parts.append("Prior stress tests:\n" + "\n".join(lines))

        # ── Prior imaging ──
        imaging = chart.get("relevant_imaging", []) or []
        if imaging:
            lines = ["Prior imaging:"]
            for i in imaging:
                if not isinstance(i, dict):
                    continue
                itype = i.get("type", "")
                idate = i.get("date", "")
                summary = i.get("result_summary", "")
                line = f"  - {itype}"
                if idate:
                    line += f" ({idate})"
                if summary:
                    line += f": {summary}"
                lines.append(line)
            parts.append("\n".join(lines))

        # ── Prior procedures (v2) ──
        procs = chart.get("prior_procedures", []) or []
        if procs:
            lines = []
            for p in procs:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "")
                if not name:
                    continue
                line = f"  - {name}"
                if p.get("date"):
                    line += f" ({p['date']})"
                if p.get("indication"):
                    line += f" for {p['indication']}"
                if p.get("outcome"):
                    line += f" — {p['outcome']}"
                lines.append(line)
            if lines:
                parts.append("Prior procedures:\n" + "\n".join(lines))

        # ── Labs ──
        labs = chart.get("relevant_labs", []) or []
        if labs:
            lines = ["Key laboratory values:"]
            for l in labs:
                if not isinstance(l, dict):
                    continue
                lines.append(
                    f"  - {l.get('name', '')}: "
                    f"{l.get('value', '')} {l.get('unit', '')}".rstrip()
                    + (f" [{l['flag']}]" if l.get("flag") else "")
                )
            parts.append("\n".join(lines))

        # ── Legacy prior_treatments (back-compat) ──
        treatments = chart.get("prior_treatments", []) or []
        if treatments and not procs:
            parts.append("Prior treatments/procedures (legacy):\n" + "\n".join(f"  - {t}" for t in treatments))

        # ── LVEF ──
        ef = chart.get("ejection_fraction", "")
        if ef:
            parts.append(f"LVEF: {ef}")

        # ── Original raw note (cached or supplied) ──
        notes = chart.get("additional_notes", "") or chart.get("office_notes", "")
        raw_supplied = self.raw_note and self.raw_note.strip()
        if raw_supplied:
            parts.append("Original clinical note (verbatim):\n" + self.raw_note.strip())
        elif notes:
            parts.append("Original clinical note (verbatim):\n" + notes)

        # ── Procedure being requested ──
        parts.append(
            f"\nRequested procedure: {self.procedure_name} "
            f"(CPT {self.procedure_code}) for {self.payer_name}."
        )

        narrative = "\n\n".join(parts)
        # Cache the composed narrative as raw_note ONLY if no raw_note was
        # passed in originally — otherwise we'd overwrite the source.
        if not raw_supplied:
            self.raw_note = narrative
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
