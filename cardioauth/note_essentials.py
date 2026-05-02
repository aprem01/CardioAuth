"""Deterministic regex extraction for the six payer-required essentials.

Peter's May rerun feedback exposed this gap: when the Claude chart-
extraction call fails (spend limit, rate limit, network), the demo
fell back to a skeletal chart with empty patient_name / DOB / member
ID / ordering physician — and EssentialsChecker dutifully flagged
all of them as "missing required fields." From the physician's POV
that's the system lying: the fields are sitting in the note in
plain text. The LLM just couldn't be reached.

This module re-extracts the six essentials with deterministic regex
patterns. It runs BEFORE the Claude call (cheap, no IO) and its
output is overlaid onto whatever Claude returned (Claude wins when
both agree; regex fills the blanks). When Claude fails entirely the
regex output is the chart's only source of truth for these fields,
and the essentials checker stops emitting misleading false-positives.

The regex patterns target standard clinical-note headers:
  Patient: Jane Smith
  DOB: 01/15/1958
  Member ID: UHC123456
  Ordering: Dr. John Doe (NPI 1234567890)

Patterns are intentionally narrow — false-positives are worse than
false-negatives because the LLM is the primary path. Each pattern
reports both the value and the verbatim character span so the
evidence graph can carry it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EssentialMatch:
    """One regex match with its verbatim source span."""

    field: str          # e.g. "patient_name"
    value: str          # extracted value (cleaned)
    char_start: int
    char_end: int
    rule: str           # which pattern fired (for audit)


_DOB_VALUE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})"

_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "patient_name": [
        # Label is case-insensitive via (?i:...); value capture is case-
        # sensitive so "patient: presents with chest pain" can't match.
        ("patient_label",
         r"(?:^|\n)\s*(?i:Patient(?:\s+Name)?|Pt|Name)\s*[:#-]\s*"
         r"([A-Z][A-Za-z'\-]+(?:[ \t]+[A-Z][A-Za-z'\-]+){1,3})"),
        ("re_label",
         r"(?:^|\n)\s*(?i:Re)\s*[:#-]\s*"
         r"([A-Z][A-Za-z'\-]+(?:[ \t]+[A-Z][A-Za-z'\-]+){1,3})"),
    ],
    "date_of_birth": [
        ("dob_label",
         r"\b(?:DOB|D\.?O\.?B\.?|Date\s+of\s+Birth|Birth\s*date)\s*[:#-]?\s*"
         + _DOB_VALUE),
        ("born_label",
         r"\bborn\s+(?:on\s+)?" + _DOB_VALUE),
    ],
    "insurance_id": [
        ("member_id_label",
         r"\b(?:Member\s*(?:ID|#)|Insurance\s*(?:ID|#)|Subscriber\s*(?:ID|#)|"
         r"Policy\s*(?:#|Number)|Insurance\s*Member\s*ID)\s*[:#-]?\s*"
         r"([A-Z0-9\-]{4,20})"),
        ("plan_id",
         r"\b(?:UHC|Aetna|Anthem|BCBS|Cigna|Humana|Medicare)\s*"
         r"(?:Member|Plan|ID|#)?\s*[:#-]?\s*([A-Z0-9\-]{6,20})"),
    ],
    "payer_name": [
        ("payer_label",
         r"\b(?i:Payer|Insurance|Plan)\s*[:#-]\s*"
         r"((?:United\s*Healthcare|UnitedHealthcare|UHC|Aetna|Anthem|"
         r"Blue\s*Cross\s*Blue\s*Shield|BCBS|Cigna|Humana|"
         r"Medicare|Medicaid|Tricare)[A-Za-z \t]{0,40})"),
    ],
    "attending_physician": [
        ("ordering_label",
         r"\b(?i:Ordering(?:\s+(?:MD|Provider|Physician))?|"
         r"Attending(?:\s+Physician)?|Provider|Referring\s+(?:MD|Physician))\s*"
         r"[:#-]\s*((?:Dr\.?\s+)?[A-Z][A-Za-z'\-\.]+(?:[ \t]+[A-Z][A-Za-z'\-\.]+){1,3})"),
        ("signed_by",
         r"\b(?i:Signed|Electronically\s+signed)\s+(?i:by)\s*[:#-]?\s*"
         r"((?:Dr\.?\s+)?[A-Z][A-Za-z'\-\.]+(?:[ \t]+[A-Z][A-Za-z'\-\.]+){1,3})"),
    ],
    "attending_npi": [
        ("npi_label",
         r"\bNPI\s*(?:#|number)?\s*[:#-]?\s*(\d{10})\b"),
    ],
}


def _clean(value: str) -> str:
    """Trim trailing punctuation/whitespace so 'UnitedHealthcare,' → 'UnitedHealthcare'."""
    return value.strip().rstrip(",;:.")


def extract_essentials_from_note(raw_note: str) -> dict[str, EssentialMatch]:
    """Return a dict of field → EssentialMatch for every essential we
    can find in the note. Only the FIRST match per field is kept;
    later mentions are ignored (the header is canonical).

    For fields where the captured VALUE must be capitalized (names,
    payer brands) the patterns themselves use `(?i:LABEL)` so the
    label matches case-insensitively while the value capture stays
    case-sensitive — that way "patient: presents with chest pain"
    doesn't pass for a name.

    Pure function — no IO, no shared state. Safe to call in any path.
    """
    if not raw_note:
        return {}

    # Fields whose captured value must remain case-sensitive (names,
    # payer brands). The label parts of their patterns use (?i:...)
    # so labels still match regardless of casing.
    case_sensitive_value_fields = {
        "patient_name", "attending_physician", "payer_name",
    }

    out: dict[str, EssentialMatch] = {}
    for field, patterns in _PATTERNS.items():
        flags = re.MULTILINE
        if field not in case_sensitive_value_fields:
            flags |= re.IGNORECASE
        for rule_name, pattern in patterns:
            m = re.search(pattern, raw_note, flags)
            if m and m.lastindex:
                value = _clean(m.group(m.lastindex))
                if not value:
                    continue
                out[field] = EssentialMatch(
                    field=field,
                    value=value,
                    char_start=m.start(m.lastindex),
                    char_end=m.end(m.lastindex),
                    rule=rule_name,
                )
                break  # first hit per field wins
    return out


# Names we accept on the regex pass for payer normalization. Keeps
# the ChartData payer_name consistent with what the rest of the
# pipeline expects.
_PAYER_CANONICAL = {
    "uhc": "UnitedHealthcare",
    "united healthcare": "UnitedHealthcare",
    "unitedhealthcare": "UnitedHealthcare",
    "aetna": "Aetna",
    "anthem": "Anthem",
    "bcbs": "Blue Cross Blue Shield",
    "blue cross blue shield": "Blue Cross Blue Shield",
    "cigna": "Cigna",
    "humana": "Humana",
    "medicare": "Medicare",
    "medicaid": "Medicaid",
    "tricare": "Tricare",
}


def normalize_payer_name(value: str) -> str:
    """Map a regex-matched payer string to the canonical name. Unknown
    inputs are returned unchanged."""
    key = value.strip().lower()
    return _PAYER_CANONICAL.get(key, value.strip())


def overlay_essentials(
    chart_dict: dict,
    essentials: dict[str, EssentialMatch],
) -> dict:
    """Fill empty essential slots in `chart_dict` from regex matches.

    Claude wins when it produced a non-empty value; regex only fills
    blanks. Returns a new dict (does not mutate input).

    For payer_name the regex match goes through canonical normalization
    so "UHC" upgrades to "UnitedHealthcare".
    """
    out = dict(chart_dict)
    for field, match in essentials.items():
        existing = out.get(field, "")
        if isinstance(existing, str) and existing.strip():
            continue  # Claude already had it
        value = match.value
        if field == "payer_name":
            value = normalize_payer_name(value)
        out[field] = value
    return out
