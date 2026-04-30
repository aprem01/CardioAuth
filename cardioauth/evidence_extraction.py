"""Evidence-emitting helpers used by extractors.

Phase A.3: extractors that produce ChartData fields also emit EvidenceSpans
into a shared EvidenceGraph. Downstream artifacts (form fields, narrative
attestation, criterion matches) reference those spans.

Design points:
- Every value extracted from a known position in the raw note carries
  precise char offsets (start, end). The regex symptom backstop is the
  cleanest example.
- Values produced by Claude (which reformats input) carry char offsets
  ONLY when we can locate them by substring search. When we can't, the
  span is still recorded with offsets (0, 0) so the chain isn't broken.
- The structured ChartExtractionResult wraps (ChartData, EvidenceGraph)
  so callers receive both atomically — no in-band sidecars on Pydantic
  models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cardioauth.evidence import EvidenceGraph, EvidenceSpan


# ──────────────────────────────────────────────────────────────────────
# Locator helpers
# ──────────────────────────────────────────────────────────────────────


def locate_in_source(
    source_text: str,
    needle: str,
    *,
    context_chars: int = 30,
) -> tuple[int, int, str]:
    """Find `needle` inside `source_text` (case-insensitive).

    Returns (char_start, char_end, snippet_with_context).
    Returns (0, 0, "") when needle is empty or not found.

    For multi-word needles, also tries the first significant token
    (length >= 4) as a fuzzy fallback so e.g. "BMI 38" still locates
    when the source says "BMI of 38".
    """
    if not source_text or not needle:
        return (0, 0, "")

    needle_clean = needle.strip()
    src_lower = source_text.lower()
    idx = src_lower.find(needle_clean.lower())
    if idx < 0:
        # Fuzzy fallback: longest token >= 4 chars
        candidates = sorted(
            (t for t in needle_clean.split() if len(t) >= 4),
            key=len, reverse=True,
        )
        for tok in candidates:
            idx = src_lower.find(tok.lower())
            if idx >= 0:
                end = idx + len(tok)
                start_ctx = max(0, idx - context_chars)
                end_ctx = min(len(source_text), end + context_chars)
                return (idx, end, source_text[start_ctx:end_ctx])
        return (0, 0, "")

    end = idx + len(needle_clean)
    start_ctx = max(0, idx - context_chars)
    end_ctx = min(len(source_text), end + context_chars)
    return (idx, end, source_text[start_ctx:end_ctx])


def make_span(
    *,
    source_text: str,
    extracted_value: str,
    field_path: str,
    extractor: str,
    extractor_version: str = "v1",
    confidence: float = 0.85,
    source_id: str = "raw_note",
    source_type: str = "raw_note",
    explicit_offsets: tuple[int, int] | None = None,
) -> EvidenceSpan:
    """Build an EvidenceSpan, locating the value in source_text by
    substring search unless explicit offsets are supplied.

    Use `explicit_offsets` from regex `.span()` results — they're
    precise. Fall back to substring search for Claude-extracted values.
    """
    if explicit_offsets is not None:
        char_start, char_end = explicit_offsets
        if 0 <= char_start <= char_end <= len(source_text):
            ctx_start = max(0, char_start - 30)
            ctx_end = min(len(source_text), char_end + 30)
            quote = source_text[ctx_start:ctx_end]
        else:
            quote = extracted_value
            char_start = char_end = 0
    else:
        char_start, char_end, quote = locate_in_source(source_text, extracted_value)
        if not quote:
            quote = extracted_value

    return EvidenceSpan.new(
        source_id=source_id,
        source_type=source_type,
        char_start=char_start,
        char_end=char_end,
        quote=quote,
        extracted_value=extracted_value,
        field_path=field_path,
        extractor=extractor,
        extractor_version=extractor_version,
        confidence=confidence,
    )


# ──────────────────────────────────────────────────────────────────────
# ChartExtractionResult — typed return shape for extractors
# ──────────────────────────────────────────────────────────────────────


@dataclass
class ChartExtractionResult:
    """Atomic return shape: ChartData + the spans that produced it.

    Replaces the prior pattern of returning bare ChartData and stashing
    an evidence sidecar on `__dict__`. Callers that need both unpack
    explicitly: `result.chart`, `result.evidence_graph`.
    """

    chart: Any                          # cardioauth.models.chart.ChartData
    evidence_graph: EvidenceGraph

    def to_dict(self) -> dict:
        return {
            "chart": self.chart.model_dump(mode="json") if hasattr(self.chart, "model_dump") else self.chart,
            "evidence_graph": self.evidence_graph.to_dict(),
        }


# ──────────────────────────────────────────────────────────────────────
# Walking a parsed chart dict to emit spans for every populated field
# ──────────────────────────────────────────────────────────────────────


# Top-level demographic / identity fields whose values come from Claude
# parsing the note. Spans get located by substring search.
_DEMOGRAPHIC_FIELDS: tuple[str, ...] = (
    "patient_name", "date_of_birth", "age", "sex",
    "attending_physician", "attending_npi",
    "insurance_id", "payer_name",
    "procedure_code", "procedure_requested",
    "mac_jurisdiction", "secondary_payer",
)


# v2 list buckets and the dict keys to try (in order) for the label
# that names the extracted concept. The first non-empty value wins.
# Items with no recognizable label are skipped.
_LIST_FIELD_NAME_KEYS: dict[str, tuple[str, ...]] = {
    "current_symptoms": ("name",),
    "exam_findings": ("finding",),
    "past_medical_history": ("condition",),
    "family_history": ("condition", "relation"),
    "prior_procedures": ("name",),
    "prior_stress_tests": ("modality",),
    # ECG has multiple structured sub-fields — any one populated suffices
    "ecg_findings": ("summary", "conduction", "rhythm",
                     "hypertrophy_or_strain", "ischemic_changes", "pacing"),
    "relevant_imaging": ("type",),
    "relevant_medications": ("name",),
    "relevant_labs": ("name",),
}


def emit_spans_for_chart_dict(
    *,
    chart_dict: dict,
    raw_note: str,
    graph: EvidenceGraph,
    extractor: str = "claude_chart_extraction",
    extractor_version: str = "v1",
    base_confidence: float = 0.85,
) -> EvidenceGraph:
    """Walk a parsed chart dict and emit one EvidenceSpan per populated
    field, attaching them to `graph`. Returns the same graph for chaining.

    Top-level scalars produce spans with field_path "chart.<key>".
    List items produce spans with field_path "chart.<bucket>[<i>]".
    """
    if not isinstance(chart_dict, dict):
        return graph

    # Demographics & top-level identity scalars
    for key in _DEMOGRAPHIC_FIELDS:
        v = chart_dict.get(key)
        if v in (None, "", []):
            continue
        graph.add(make_span(
            source_text=raw_note,
            extracted_value=str(v),
            field_path=f"chart.{key}",
            extractor=extractor,
            extractor_version=extractor_version,
            confidence=base_confidence,
        ))

    # Diagnosis codes
    for i, code in enumerate(chart_dict.get("diagnosis_codes", []) or []):
        graph.add(make_span(
            source_text=raw_note,
            extracted_value=str(code),
            field_path=f"chart.diagnosis_codes[{i}]",
            extractor=extractor,
            extractor_version=extractor_version,
            confidence=base_confidence,
        ))

    # active_comorbidities (list of strings)
    for i, item in enumerate(chart_dict.get("active_comorbidities", []) or []):
        if not item:
            continue
        graph.add(make_span(
            source_text=raw_note,
            extracted_value=str(item),
            field_path=f"chart.active_comorbidities[{i}]",
            extractor=extractor,
            extractor_version=extractor_version,
            confidence=base_confidence,
        ))

    # Structured list buckets
    for bucket, name_keys in _LIST_FIELD_NAME_KEYS.items():
        items = chart_dict.get(bucket) or []
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            # Pick the first non-empty label across the candidate keys,
            # then fall back to generic description/text fields.
            label = ""
            for key in name_keys:
                v = item.get(key)
                if v:
                    label = str(v)
                    break
            if not label:
                label = item.get("description") or item.get("text") or ""
            if not label:
                continue
            graph.add(make_span(
                source_text=raw_note,
                extracted_value=str(label),
                field_path=f"chart.{bucket}[{i}]",
                extractor=extractor,
                extractor_version=extractor_version,
                confidence=base_confidence,
            ))

    return graph
