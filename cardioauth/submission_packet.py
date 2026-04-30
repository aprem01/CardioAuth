"""First-class submission packet — typed, traceable, validated.

Replaces the loose runtime dict assembly in demo_e2e.py. The packet is
built once, validated for referential integrity at construction, and
flows through the verification + reviewer pipeline as a typed object.

Design (article alignment: "Ontology-Driven Domain Modelling" + "Confidence
Scoring and Usage Intelligence"; Peter alignment: "one final layer sees
all of them together"):

  ResolvedCPT          — the canonical CPT for this case (may differ from
                         the request CPT when the note clearly orders
                         something else)
  FormFieldEntry       — typed form row with normalized value + evidence
                         reference + lineage
  NarrativeAttestation — the medical-necessity attestation, with explicit
                         CPT/procedure references the reviewer can check
  Finding              — a verification check's output (deterministic or
                         from the LLM reviewer)
  ReviewerVerdict      — the LLM reviewer's structured answer to the
                         9-question checklist
  SubmissionPacket     — the assembled packet; referential integrity
                         validated at construction
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from cardioauth.evidence import (
    EMPTY_REFERENCE,
    EvidenceGraph,
    EvidenceReference,
)


# ──────────────────────────────────────────────────────────────────────
# ResolvedCPT
# ──────────────────────────────────────────────────────────────────────


CptResolutionSource = Literal["request", "note_extracted", "reasoner_recommended", "manual_override"]


@dataclass(frozen=True)
class ResolvedCPT:
    """The system's resolution of which CPT this case is actually about.

    The packet has exactly one ResolvedCPT. Form fields, narrative
    attestation, and submission payload must all reference it. The
    coherence reviewer's job is to verify they do.
    """

    cpt: str
    procedure: str
    source: CptResolutionSource
    evidence: EvidenceReference = EMPTY_REFERENCE
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "cpt": self.cpt,
            "procedure": self.procedure,
            "source": self.source,
            "evidence": self.evidence.to_dict(),
            "rationale": self.rationale,
        }


# ──────────────────────────────────────────────────────────────────────
# FormFieldEntry
# ──────────────────────────────────────────────────────────────────────


FieldStatus = Literal[
    "populated", "missing", "incomplete", "needs_verify", "optional_empty",
]


@dataclass
class FormFieldEntry:
    """A single populated form field, with normalized value + lineage.

    `value` is what the form sends to the payer (snapped to the field's
    allowed options when it's a select). `evidence_text` is the rich
    pre-normalized string the mapper produced. `evidence` is the
    structured pointer back into the EvidenceGraph.
    """

    key: str
    label: str
    category: str
    required: bool
    format: str
    options: list[str] = field(default_factory=list)
    value: str = ""
    evidence_text: str = ""
    evidence: EvidenceReference = EMPTY_REFERENCE
    status: FieldStatus = "missing"
    missing_reason: str = ""
    help_text: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "required": self.required,
            "format": self.format,
            "options": list(self.options),
            "value": self.value,
            "evidence_text": self.evidence_text,
            "evidence": self.evidence.to_dict(),
            "status": self.status,
            "missing_reason": self.missing_reason,
            "help_text": self.help_text,
        }


# ──────────────────────────────────────────────────────────────────────
# NarrativeAttestation
# ──────────────────────────────────────────────────────────────────────


@dataclass
class NarrativeAttestation:
    """The medical-necessity attestation that goes to the payer.

    The reviewer checks that `cpt_referenced` matches the packet's
    ResolvedCPT and that `procedure_referenced` is consistent.
    """

    text: str
    cpt_referenced: str = ""           # the CPT the narrative explicitly mentions
    procedure_referenced: str = ""     # procedure name the narrative mentions
    evidence: EvidenceReference = EMPTY_REFERENCE

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "cpt_referenced": self.cpt_referenced,
            "procedure_referenced": self.procedure_referenced,
            "evidence": self.evidence.to_dict(),
        }


# ──────────────────────────────────────────────────────────────────────
# Finding
# ──────────────────────────────────────────────────────────────────────


FindingSeverity = Literal["info", "low", "medium", "high", "blocking"]


@dataclass(frozen=True)
class Finding:
    """A verification check's output. Composable; stacked on the packet.

    `auto_fixable` and `fix_suggestion` describe a rewrite that would
    resolve the finding. The deterministic checks may flag fixable
    issues; the LLM reviewer may apply them in autonomous-fixer mode
    later (today: read-only).
    """

    kind: str                            # "cpt_attestation_mismatch", etc.
    severity: FindingSeverity
    message: str
    related_field_keys: tuple[str, ...] = tuple()
    auto_fixable: bool = False
    fix_suggestion: str = ""
    checker: str = "unknown"             # which checker produced this finding

    def __post_init__(self):
        if isinstance(self.related_field_keys, list):
            object.__setattr__(self, "related_field_keys", tuple(self.related_field_keys))

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "related_field_keys": list(self.related_field_keys),
            "auto_fixable": self.auto_fixable,
            "fix_suggestion": self.fix_suggestion,
            "checker": self.checker,
        }


def severity_rank(sev: FindingSeverity) -> int:
    return {"info": 0, "low": 1, "medium": 2, "high": 3, "blocking": 4}[sev]


# ──────────────────────────────────────────────────────────────────────
# ReviewerVerdict
# ──────────────────────────────────────────────────────────────────────


ReviewerRecommendation = Literal["transmit", "hold", "block"]


@dataclass
class ReviewerVerdict:
    """The LLM reviewer's structured answer to the 9-question checklist.

    findings_per_question is keyed by short question id ("q1" through
    "q9"); the message describes what the reviewer found.
    """

    passed: bool
    overall_recommendation: ReviewerRecommendation
    rationale: str
    findings_per_question: dict[str, list[Finding]] = field(default_factory=dict)
    model: str = ""
    cost_input_tokens: int = 0
    cost_output_tokens: int = 0
    duration_ms: int = 0
    reviewed_at: str = ""

    @classmethod
    def empty(cls) -> "ReviewerVerdict":
        return cls(
            passed=True,
            overall_recommendation="transmit",
            rationale="",
            findings_per_question={},
        )

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "overall_recommendation": self.overall_recommendation,
            "rationale": self.rationale,
            "findings_per_question": {
                q: [f.to_dict() for f in findings]
                for q, findings in self.findings_per_question.items()
            },
            "model": self.model,
            "cost_input_tokens": self.cost_input_tokens,
            "cost_output_tokens": self.cost_output_tokens,
            "duration_ms": self.duration_ms,
            "reviewed_at": self.reviewed_at,
        }


# ──────────────────────────────────────────────────────────────────────
# SubmissionPacket
# ──────────────────────────────────────────────────────────────────────


PacketDecision = Literal["transmit", "hold_for_review", "block"]


class SubmissionPacketIntegrityError(Exception):
    """Raised when a SubmissionPacket fails referential-integrity validation."""


@dataclass
class SubmissionPacket:
    """First-class submission packet.

    Built ONCE per case. Referential integrity validated at construction:
      - resolved_cpt.evidence references must resolve in the graph
      - every form field's evidence references must resolve
      - narrative.evidence references must resolve

    Findings and reviewer_verdict can be appended after construction
    by the verification pipeline; the rest is immutable.
    """

    # Identity
    case_id: str
    created_at: str

    # Source artifacts
    raw_note: str
    chart_data: dict                            # ChartData.model_dump() for now
    policy_data: dict | None                    # PolicyData.model_dump() | None

    # Canonical resolution
    resolved_cpt: ResolvedCPT
    payer: str

    # Built artifacts
    form_fields: list[FormFieldEntry]
    narrative: NarrativeAttestation

    # Lineage
    evidence_graph: EvidenceGraph

    # Reasoner snapshot: score, label, agreement, alternative-modality,
    # capped-from / capped-to fields. Populated by the packet builder
    # so checkers don't need a live reasoning object.
    reasoner_summary: dict[str, Any] = field(default_factory=dict)

    # Versioning (so we can replay verdicts later)
    taxonomy_version: str = ""
    form_schema_version: str = ""
    model_version: str = ""

    # Verification (set by the pipeline after construction)
    deterministic_findings: list[Finding] = field(default_factory=list)
    reviewer_verdict: ReviewerVerdict | None = None
    decision: PacketDecision = "hold_for_review"
    decision_rationale: str = ""

    # ── Construction ──

    @classmethod
    def build(
        cls,
        *,
        case_id: str,
        raw_note: str,
        chart_data: dict,
        policy_data: dict | None,
        resolved_cpt: ResolvedCPT,
        payer: str,
        form_fields: list[FormFieldEntry],
        narrative: NarrativeAttestation,
        evidence_graph: EvidenceGraph,
        reasoner_summary: dict | None = None,
        taxonomy_version: str = "",
        form_schema_version: str = "",
        model_version: str = "",
    ) -> "SubmissionPacket":
        """Build and validate a SubmissionPacket."""
        packet = cls(
            case_id=case_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            raw_note=raw_note,
            chart_data=chart_data,
            policy_data=policy_data,
            resolved_cpt=resolved_cpt,
            payer=payer,
            form_fields=form_fields,
            narrative=narrative,
            reasoner_summary=reasoner_summary or {},
            evidence_graph=evidence_graph,
            taxonomy_version=taxonomy_version,
            form_schema_version=form_schema_version,
            model_version=model_version,
        )
        packet._validate_integrity()
        return packet

    def _validate_integrity(self) -> None:
        """Referential integrity check. Raises on broken references.

        We're strict at construction so downstream code can rely on the
        graph resolving every span_id without defensive .get() calls.
        """
        problems: list[str] = []

        if not self.evidence_graph.references_resolve(self.resolved_cpt.evidence):
            unresolved = self.evidence_graph.unresolved_span_ids(self.resolved_cpt.evidence)
            problems.append(f"resolved_cpt.evidence references unknown spans: {unresolved}")

        for f in self.form_fields:
            if f.evidence.is_empty():
                continue
            if not self.evidence_graph.references_resolve(f.evidence):
                unresolved = self.evidence_graph.unresolved_span_ids(f.evidence)
                problems.append(
                    f"form field {f.key!r} evidence references unknown spans: {unresolved}"
                )

        if not self.narrative.evidence.is_empty():
            if not self.evidence_graph.references_resolve(self.narrative.evidence):
                unresolved = self.evidence_graph.unresolved_span_ids(self.narrative.evidence)
                problems.append(f"narrative.evidence references unknown spans: {unresolved}")

        if problems:
            raise SubmissionPacketIntegrityError(
                "SubmissionPacket failed integrity validation:\n  - "
                + "\n  - ".join(problems)
            )

    # ── Pipeline mutators (verification stage) ──

    def add_findings(self, findings: list[Finding]) -> None:
        self.deterministic_findings.extend(findings)

    def set_reviewer_verdict(self, verdict: ReviewerVerdict) -> None:
        self.reviewer_verdict = verdict

    def set_decision(self, decision: PacketDecision, rationale: str = "") -> None:
        self.decision = decision
        self.decision_rationale = rationale

    # ── Queries used by the pipeline ──

    def highest_severity(self) -> FindingSeverity | None:
        all_findings = list(self.deterministic_findings)
        if self.reviewer_verdict:
            for findings in self.reviewer_verdict.findings_per_question.values():
                all_findings.extend(findings)
        if not all_findings:
            return None
        return max(all_findings, key=lambda f: severity_rank(f.severity)).severity

    def has_blocking_findings(self) -> bool:
        return any(f.severity == "blocking" for f in self.deterministic_findings)

    # ── Serialization ──

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "created_at": self.created_at,
            "raw_note": self.raw_note,
            "chart_data": self.chart_data,
            "policy_data": self.policy_data,
            "resolved_cpt": self.resolved_cpt.to_dict(),
            "payer": self.payer,
            "form_fields": [f.to_dict() for f in self.form_fields],
            "narrative": self.narrative.to_dict(),
            "reasoner_summary": self.reasoner_summary,
            "evidence_graph": self.evidence_graph.to_dict(),
            "taxonomy_version": self.taxonomy_version,
            "form_schema_version": self.form_schema_version,
            "model_version": self.model_version,
            "deterministic_findings": [f.to_dict() for f in self.deterministic_findings],
            "reviewer_verdict": (
                self.reviewer_verdict.to_dict() if self.reviewer_verdict else None
            ),
            "decision": self.decision,
            "decision_rationale": self.decision_rationale,
        }
