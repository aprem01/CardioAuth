"""Evidence-span lineage layer.

Every claim the system makes — an extracted symptom, a populated form
field, a criterion match, a narrative attestation — must be traceable
back to the verbatim source span that supports it. This module defines
the typed contracts used across the pipeline.

Design (article alignment: "Confidence Scoring and Usage Intelligence"
+ Peter's "evidence preserved separately from normalized payer answer"):

  EvidenceSpan       — a verbatim pointer into a source document. Created
                       at extraction time. Immutable.
  EvidenceReference  — a downstream artifact's pointer to one or more
                       spans that justify it. Created at the point a
                       claim is made.
  EvidenceGraph      — per-case container of all spans + reverse lookups.
                       Today: dict-backed. Future: graph store / NetworkX.

Spans are immutable once created. References are immutable once attached
to an artifact. The graph is mutable only during pipeline assembly; once
the SubmissionPacket is built, it's frozen.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable


# ──────────────────────────────────────────────────────────────────────
# EvidenceSpan
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceSpan:
    """A verbatim pointer to a piece of source content that supports a claim.

    Immutable. Carries everything a reviewer needs to verify the claim
    by going back to the source.
    """

    # Identity
    span_id: str

    # Where this span lives
    source_id: str           # "raw_note" | "fhir_obs:abc-123" | "policy_chunk:UHC-PET-001"
    source_type: str         # "raw_note" | "fhir_resource" | "policy_chunk" | "structured"

    # Where in the source (offsets into raw_note / chunk text; 0 if N/A for structured sources)
    char_start: int
    char_end: int
    quote: str               # verbatim snippet, truncated to ~240 chars for storage

    # What was extracted from this span
    extracted_value: str     # the parsed value (e.g., "BMI 38", "LBBB", "1958-01-15")
    field_path: str          # canonical path to the artifact this evidences (e.g., "chart.active_comorbidities[2]")

    # Provenance
    extractor: str           # "claude_chart_extraction" | "regex_symptom_v1" | "fhir_observation"
    extractor_version: str   # version of the extractor that produced this
    confidence: float        # 0.0-1.0 from the extractor
    extracted_at: str        # ISO timestamp

    @classmethod
    def new(
        cls,
        *,
        source_id: str,
        source_type: str,
        char_start: int = 0,
        char_end: int = 0,
        quote: str = "",
        extracted_value: str = "",
        field_path: str = "",
        extractor: str = "unknown",
        extractor_version: str = "v1",
        confidence: float = 1.0,
    ) -> "EvidenceSpan":
        """Create a span with an auto-generated id and timestamp."""
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence out of range: {confidence}")
        return cls(
            span_id=f"span_{uuid.uuid4().hex[:12]}",
            source_id=source_id,
            source_type=source_type,
            char_start=int(char_start),
            char_end=int(char_end),
            quote=(quote or "")[:240],
            extracted_value=str(extracted_value),
            field_path=field_path,
            extractor=extractor,
            extractor_version=extractor_version,
            confidence=float(confidence),
            extracted_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────
# EvidenceReference
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceReference:
    """A downstream claim's pointer to the spans that justify it.

    Used by FormFieldEntry, CriterionMatch, NarrativeAttestation, etc.
    A reference may point at multiple spans (e.g., a form field
    populated from both a chart bucket and a note phrase).
    """

    span_ids: tuple[str, ...]
    rationale: str = ""             # optional human-readable justification
    derivation: str = "direct"      # "direct" | "inferred" | "rule" | "llm_extraction"

    def __post_init__(self):
        # Defensive normalization for callers that pass a list
        if isinstance(self.span_ids, list):
            object.__setattr__(self, "span_ids", tuple(self.span_ids))

    @classmethod
    def from_spans(
        cls,
        spans: Iterable[EvidenceSpan],
        *,
        rationale: str = "",
        derivation: str = "direct",
    ) -> "EvidenceReference":
        return cls(
            span_ids=tuple(s.span_id for s in spans),
            rationale=rationale,
            derivation=derivation,
        )

    def to_dict(self) -> dict:
        return {
            "span_ids": list(self.span_ids),
            "rationale": self.rationale,
            "derivation": self.derivation,
        }

    def is_empty(self) -> bool:
        return not self.span_ids


EMPTY_REFERENCE = EvidenceReference(span_ids=tuple(), rationale="", derivation="direct")


# ──────────────────────────────────────────────────────────────────────
# EvidenceGraph
# ──────────────────────────────────────────────────────────────────────


@dataclass
class EvidenceGraph:
    """Container for all evidence spans in a single case.

    Today: dict-backed with reverse indexes by source and by extractor.
    Future: NetworkX or proper graph store with relationship traversal.
    The public API stays stable across that migration.
    """

    spans: dict[str, EvidenceSpan] = field(default_factory=dict)

    # ── Mutation (only during pipeline assembly) ──

    def add(self, span: EvidenceSpan) -> str:
        """Add a span; returns its id. Idempotent on span_id."""
        self.spans[span.span_id] = span
        return span.span_id

    def add_many(self, spans: Iterable[EvidenceSpan]) -> list[str]:
        return [self.add(s) for s in spans]

    # ── Lookup ──

    def get(self, span_id: str) -> EvidenceSpan | None:
        return self.spans.get(span_id)

    def get_many(self, span_ids: Iterable[str]) -> list[EvidenceSpan]:
        return [s for s in (self.get(i) for i in span_ids) if s is not None]

    def all_spans(self) -> list[EvidenceSpan]:
        return list(self.spans.values())

    def spans_for_source(self, source_id: str) -> list[EvidenceSpan]:
        return [s for s in self.spans.values() if s.source_id == source_id]

    def spans_for_extractor(self, extractor: str) -> list[EvidenceSpan]:
        return [s for s in self.spans.values() if s.extractor == extractor]

    def spans_for_field_path(self, field_path: str) -> list[EvidenceSpan]:
        return [s for s in self.spans.values() if s.field_path == field_path]

    # ── Validation ──

    def references_resolve(self, ref: EvidenceReference) -> bool:
        """True iff every span_id in the reference exists in the graph."""
        return all(sid in self.spans for sid in ref.span_ids)

    def unresolved_span_ids(self, ref: EvidenceReference) -> list[str]:
        return [sid for sid in ref.span_ids if sid not in self.spans]

    # ── Serialization ──

    def to_dict(self) -> dict:
        return {
            "spans": [s.to_dict() for s in self.spans.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceGraph":
        graph = cls()
        for s in data.get("spans", []):
            graph.spans[s["span_id"]] = EvidenceSpan(**s)
        return graph

    def __len__(self) -> int:
        return len(self.spans)
