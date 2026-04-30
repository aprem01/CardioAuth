"""Rule-based symptom extractor — backstop for when Claude misses.

Peter Apr 24 feedback: "symptoms are consistently recognized by the
reasoner but not being structured into the ChartData or mapped into the
payer form." Even with a strong prompt, Claude sometimes drops symptoms
into additional_notes or comorbidities instead of current_symptoms.

This module does NOT replace Claude-driven extraction. It runs after the
normalizer and fills in `current_symptoms` only when the primary bucket
came back empty. The goal: at least the headline symptoms + their change
markers reach the payer form, so "Primary symptoms" and
"new/worsening" attestations aren't blank.
"""

from __future__ import annotations

import re
from typing import Iterable

# Core cardiac symptom lexicon. Each entry is (canonical_name, pattern).
# Patterns are case-insensitive word-boundary regex. Order matters — more
# specific patterns first (e.g., "shortness of breath" before "breath").
_SYMPTOM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("dyspnea on exertion", re.compile(r"\b(dyspnea\s+on\s+exertion|DOE|exertional\s+dyspnea|SOBOE)\b", re.I)),
    ("dyspnea", re.compile(r"\b(dyspnea|shortness\s+of\s+breath|SOB)\b", re.I)),
    ("orthopnea", re.compile(r"\borthopnea\b", re.I)),
    ("paroxysmal nocturnal dyspnea", re.compile(r"\b(paroxysmal\s+nocturnal\s+dyspnea|PND)\b", re.I)),
    ("angina", re.compile(r"\b(angina|anginal)\b", re.I)),
    ("chest pain", re.compile(r"\bchest\s+(pain|discomfort|tightness|pressure|heaviness)\b", re.I)),
    ("palpitations", re.compile(r"\bpalpitation[s]?\b", re.I)),
    ("syncope", re.compile(r"\bsyncop(e|al)\b", re.I)),
    ("presyncope", re.compile(r"\b(pre[-\s]?syncope|near[-\s]?syncope|lightheaded(ness)?)\b", re.I)),
    ("fatigue", re.compile(r"\b(fatigue|fatigability|decreased\s+exercise\s+tolerance)\b", re.I)),
    ("lower extremity edema", re.compile(r"\b(lower\s+extremit(y|ies)\s+edema|pedal\s+edema|leg\s+swelling|bilateral\s+edema)\b", re.I)),
    ("claudication", re.compile(r"\bclaudicat(ion|ing)\b", re.I)),
]

# Tokens that indicate the symptom is NEW or WORSENING (primary PA driver).
_NEW_WORSENING = re.compile(
    r"\b(new[-\s]?onset|new|worsening|progressive|progressively\s+worse|"
    r"increasing|gradually\s+worse|acute|recent(ly)?|started|developed)\b",
    re.I,
)
_STABLE = re.compile(r"\b(stable|unchanged|no\s+change|chronic\s+stable)\b", re.I)

# Character cues.
_CHARACTER = {
    "exertional":  re.compile(r"\b(on\s+exertion|with\s+exertion|exertional|with\s+activity|on\s+exercise)\b", re.I),
    "at rest":     re.compile(r"\b(at\s+rest|at-rest|resting)\b", re.I),
    "typical":     re.compile(r"\btypical\b", re.I),
    "atypical":    re.compile(r"\batypical\b", re.I),
}

# Onset / duration — "x 3 weeks", "for 2 months", "since March"
_ONSET = re.compile(
    r"\b(x\s*\d+\s*(days?|weeks?|months?|yrs?|years?)|"
    r"for\s+\d+\s+(days?|weeks?|months?|years?)|"
    r"\d+\s*[-\s]?(day|week|month|year)s?\s+history|"
    r"since\s+\w+|"
    r"over\s+the\s+past\s+\w+)\b",
    re.I,
)

# ROS denials we must respect — "denies chest pain, palpitations, or syncope"
# captures everything from the denial trigger to the next sentence boundary,
# so items in a comma-list all get skipped.
_DENIAL = re.compile(
    r"\b(denies|negative\s+for|no\s+complaints?\s+of|without)\b([^.;\n]{0,200})",
    re.I,
)


def _find_window(text: str, match: re.Match, radius: int = 80) -> str:
    """Return ~radius chars on either side of a match for context probing."""
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return text[start:end]


def _is_denied(text: str, symptom_pattern: re.Pattern) -> bool:
    """Cheap check: is this symptom explicitly denied in the note?"""
    for denial in _DENIAL.finditer(text):
        chunk = denial.group(0)
        if symptom_pattern.search(chunk):
            return True
    return False


def extract_symptoms_from_text(text: str) -> list[dict]:
    """Return a list of Symptom-dict entries built from free-text.

    Dict shape matches the Symptom pydantic model so the caller can
    feed it into ChartData(current_symptoms=[...]).

    Only returns symptoms we're reasonably sure of — matches that are
    explicitly denied in the note are skipped.
    """
    return [s for s, _ in extract_symptoms_with_spans(text)]


def extract_symptoms_with_spans(text: str) -> list[tuple[dict, tuple[int, int]]]:
    """Phase A.3: same as extract_symptoms_from_text but returns the
    char-span where each symptom was detected so the caller can emit
    EvidenceSpans with precise offsets.
    """
    if not text or not text.strip():
        return []

    seen: set[str] = set()
    out: list[tuple[dict, tuple[int, int]]] = []
    for name, pat in _SYMPTOM_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if _is_denied(text, pat):
            continue
        if name in seen:
            continue
        seen.add(name)

        window = _find_window(text, m)
        entry: dict = {"name": name}

        if _NEW_WORSENING.search(window):
            entry["change_vs_baseline"] = "new or worsening"
        elif _STABLE.search(window):
            entry["change_vs_baseline"] = "stable"

        for char_name, char_pat in _CHARACTER.items():
            if char_pat.search(window):
                entry["character"] = char_name
                break

        onset_m = _ONSET.search(window)
        if onset_m:
            entry["onset"] = onset_m.group(0).strip()

        out.append((entry, m.span()))

    # Dedupe: if we extracted both "dyspnea on exertion" AND "dyspnea",
    # keep only the more specific one.
    if any(s["name"] == "dyspnea on exertion" for s, _ in out):
        out = [(s, sp) for s, sp in out if s["name"] != "dyspnea"]
    return out


def backfill_symptoms_if_missing(
    chart_dict: dict,
    raw_note: str,
    evidence_graph: "EvidenceGraph | None" = None,  # type: ignore[name-defined]
) -> dict:
    """Augment a normalized chart dict with rule-based symptoms.

    Only fills in when `current_symptoms` is empty or contains no name —
    Claude's extraction is authoritative when it produces anything.
    The augmented entry is flagged via missing_fields so the physician
    sees that the symptom came from a heuristic, not the primary
    extraction.

    Phase A.3: when `evidence_graph` is provided, emit one EvidenceSpan
    per backfilled symptom with precise char offsets from the regex
    match — so downstream coherence checks can verify each symptom is
    grounded in a real source span.
    """
    existing = chart_dict.get("current_symptoms") or []
    has_named = any(
        isinstance(s, dict) and s.get("name") for s in existing
    )
    if has_named:
        return chart_dict

    probes: list[str] = []
    if raw_note:
        probes.append(raw_note)
    if chart_dict.get("additional_notes"):
        probes.append(str(chart_dict["additional_notes"]))
    text = "\n".join(probes)

    pairs = extract_symptoms_with_spans(text)
    if not pairs:
        return chart_dict

    symptoms = [s for s, _ in pairs]
    chart_dict["current_symptoms"] = symptoms
    mf = list(chart_dict.get("missing_fields", []) or [])
    mf.append(
        f"Symptoms backfilled from note text (Claude extraction left bucket empty): "
        f"{', '.join(s['name'] for s in symptoms)}"
    )
    chart_dict["missing_fields"] = mf

    if evidence_graph is not None:
        from cardioauth.evidence_extraction import make_span
        for i, (sym, span_offsets) in enumerate(pairs):
            evidence_graph.add(make_span(
                source_text=text,
                extracted_value=sym["name"],
                field_path=f"chart.current_symptoms[{i}]",
                extractor="symptom_fallback",
                extractor_version="v1",
                confidence=0.7,  # rule-based, lower than Claude
                explicit_offsets=span_offsets,
            ))

    return chart_dict
