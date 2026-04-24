"""Normalize raw Claude JSON extractions into ChartData-compatible dicts.

Real-world Claude responses don't always match the schema shape exactly:

  * nested blocks — `{"patient": {"name": ..., "dob": ..., "sex": ...}}`
  * bare-string list items — `"current_symptoms": ["dyspnea", "angina"]`
    where ChartData expects `[{"name": "dyspnea"}, ...]`
  * value variance — "67 y/o", "sixty-seven", "Male", "01/15/1958",
    "NPI: 1234567890"

Without normalization, Pydantic validation errors drop the entire extraction
back to the skeletal fallback. This module closes that gap so real clinical
notes produce usable ChartData even when Claude hedges on shape.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── Demographic normalizers ──────────────────────────────────────────────

_AGE_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}


def normalize_age(val: Any) -> int | None:
    """Extract integer age from many human forms.

    Handles: 67, "67", "67 y/o", "67 yo", "67 years old", "sixty-seven",
    "age 67", "67F" (sex suffix — we pull the number).
    """
    if val is None or val == "":
        return None
    if isinstance(val, int):
        return val if 0 < val < 130 else None
    if isinstance(val, float):
        return int(val) if 0 < val < 130 else None
    if not isinstance(val, str):
        return None

    s = val.strip().lower()
    # Grab the first digit run — "67", "67yo", "age 67", "67 y/o" all work.
    # Word boundaries don't help here ("67yo" has no \b between 7 and y).
    m = re.search(r"(\d{1,3})", s)
    if m:
        n = int(m.group(1))
        return n if 0 < n < 130 else None

    parts = re.split(r"[-\s]+", s)
    total = 0
    found = False
    for p in parts:
        if p in _AGE_WORDS:
            total += _AGE_WORDS[p]
            found = True
    return total if found and 0 < total < 130 else None


def normalize_sex(val: Any) -> str:
    """Normalize sex to 'M' / 'F' / 'Other' / ''."""
    if not val:
        return ""
    s = str(val).strip().lower()
    if s in ("m", "male", "man"):
        return "M"
    if s in ("f", "female", "woman"):
        return "F"
    if s in ("o", "other", "nonbinary", "non-binary", "nb", "x"):
        return "Other"
    return ""


_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
    "%Y/%m/%d",
)


def normalize_date(val: Any) -> str:
    """Return ISO 8601 (YYYY-MM-DD) or the original string if unrecognized."""
    if not val:
        return ""
    if not isinstance(val, str):
        return str(val)
    s = val.strip()
    if not s:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s


def normalize_npi(val: Any) -> str:
    """Extract a 10-digit NPI from strings like 'NPI: 1234567890' or '1234567890'."""
    if not val:
        return ""
    s = str(val).strip()
    digits = re.sub(r"\D", "", s)
    return digits if len(digits) == 10 else s


# ── List-item coercion ──────────────────────────────────────────────────

def _coerce_symptom(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item if "name" in item else None
    if isinstance(item, str) and item.strip():
        return {"name": item.strip()}
    return None


def _coerce_exam_finding(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item if "finding" in item else None
    if isinstance(item, str) and item.strip():
        return {"finding": item.strip()}
    return None


def _coerce_pmh(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item if "condition" in item else None
    if isinstance(item, str) and item.strip():
        return {"condition": item.strip()}
    return None


def _coerce_family_hx(item: Any) -> dict | None:
    if isinstance(item, dict):
        if "condition" in item:
            item.setdefault("relation", "")
            return item
        return None
    if isinstance(item, str) and item.strip():
        return {"relation": "", "condition": item.strip()}
    return None


def _coerce_procedure(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item if "name" in item else None
    if isinstance(item, str) and item.strip():
        return {"name": item.strip()}
    return None


def _coerce_stress_test(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item if "modality" in item else None
    if isinstance(item, str) and item.strip():
        return {"modality": item.strip()}
    return None


def _coerce_ecg(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item
    if isinstance(item, str) and item.strip():
        return {"summary": item.strip()}
    return None


def _coerce_imaging(item: Any) -> dict | None:
    if isinstance(item, dict):
        return item if "type" in item else None
    if isinstance(item, str) and item.strip():
        return {"type": item.strip(), "date": "", "result_summary": ""}
    return None


def _coerce_medication(item: Any) -> dict | None:
    if isinstance(item, dict):
        if "name" in item:
            item.setdefault("dose", "")
            item.setdefault("start_date", "")
            return item
        return None
    if isinstance(item, str) and item.strip():
        return {"name": item.strip(), "dose": "", "start_date": ""}
    return None


def _coerce_lab(item: Any) -> dict | None:
    if isinstance(item, dict):
        if "name" in item and "value" in item:
            item.setdefault("date", "")
            item.setdefault("unit", "")
            return item
        return None
    return None  # Labs as bare strings are ambiguous — drop rather than fabricate.


_LIST_COERCERS = {
    "current_symptoms": _coerce_symptom,
    "exam_findings": _coerce_exam_finding,
    "past_medical_history": _coerce_pmh,
    "family_history": _coerce_family_hx,
    "prior_procedures": _coerce_procedure,
    "prior_stress_tests": _coerce_stress_test,
    "ecg_findings": _coerce_ecg,
    "relevant_imaging": _coerce_imaging,
    "relevant_medications": _coerce_medication,
    "relevant_labs": _coerce_lab,
}


def _coerce_list(key: str, items: Any) -> list:
    if not isinstance(items, list):
        if items:
            items = [items]
        else:
            return []
    coercer = _LIST_COERCERS.get(key)
    if not coercer:
        return items
    out = []
    for it in items:
        coerced = coercer(it)
        if coerced is not None:
            out.append(coerced)
    return out


# ── Flattening ──────────────────────────────────────────────────────────

_NESTED_MAPS = {
    # Claude likes to group demographics under 'patient'
    "patient": {
        "name": "patient_name",
        "full_name": "patient_name",
        "dob": "date_of_birth",
        "date_of_birth": "date_of_birth",
        "age": "age",
        "sex": "sex",
        "gender": "sex",
    },
    # Ordering physician / attending / provider
    "provider": {
        "name": "attending_physician",
        "physician": "attending_physician",
        "attending": "attending_physician",
        "npi": "attending_npi",
    },
    "attending": {
        "name": "attending_physician",
        "npi": "attending_npi",
    },
    "ordering_physician": {
        "name": "attending_physician",
        "npi": "attending_npi",
    },
    # Insurance block
    "insurance": {
        "id": "insurance_id",
        "member_id": "insurance_id",
        "payer": "payer_name",
        "payer_name": "payer_name",
    },
}


def _flatten_nested(data: dict) -> dict:
    """Fold nested demographic/provider/insurance blocks into top-level keys."""
    for src_key, field_map in _NESTED_MAPS.items():
        nested = data.get(src_key)
        if not isinstance(nested, dict):
            continue
        for nk, target in field_map.items():
            v = nested.get(nk)
            if v in (None, ""):
                continue
            # Don't clobber existing top-level value
            if data.get(target):
                continue
            data[target] = v
        # Leave the original nested key — ChartData will ignore it during filtering.
    return data


# ── Top-level entrypoint ────────────────────────────────────────────────

def normalize_claude_extraction(data: Any) -> dict:
    """Reshape raw Claude JSON output into something ChartData(**data) accepts.

    Safe to pass garbage in — returns {} on non-dict input so the caller
    can fall back to the skeletal chart.
    """
    if not isinstance(data, dict):
        return {}

    data = _flatten_nested(data)

    if "age" in data:
        age = normalize_age(data["age"])
        if age is not None:
            data["age"] = age
        else:
            data.pop("age", None)

    if "sex" in data:
        data["sex"] = normalize_sex(data["sex"])

    if "date_of_birth" in data:
        data["date_of_birth"] = normalize_date(data["date_of_birth"])

    if "attending_npi" in data:
        data["attending_npi"] = normalize_npi(data["attending_npi"])

    if "patient_name" in data and isinstance(data["patient_name"], str):
        data["patient_name"] = data["patient_name"].strip()

    if "attending_physician" in data and isinstance(data["attending_physician"], str):
        data["attending_physician"] = data["attending_physician"].strip()

    ac = data.get("active_comorbidities")
    if isinstance(ac, list):
        flat = []
        for item in ac:
            if isinstance(item, str) and item.strip():
                flat.append(item.strip())
            elif isinstance(item, dict):
                v = item.get("name") or item.get("condition") or ""
                if v:
                    flat.append(str(v).strip())
        data["active_comorbidities"] = flat

    dc = data.get("diagnosis_codes")
    if isinstance(dc, list):
        cleaned = []
        for it in dc:
            if not it:
                continue
            s = str(it).strip()
            s = re.sub(r"^ICD-?10:?\s*", "", s, flags=re.IGNORECASE)
            if s:
                cleaned.append(s)
        data["diagnosis_codes"] = cleaned

    for list_key in _LIST_COERCERS:
        if list_key in data:
            data[list_key] = _coerce_list(list_key, data[list_key])

    return data
