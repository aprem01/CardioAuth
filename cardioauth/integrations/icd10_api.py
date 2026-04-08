"""ICD-10 code lookup via NLM Clinical Tables API (free, no key required).

API docs: https://clinicaltables.nlm.nih.gov/apidoc/icd10cm/v3/doc.html

Example response for ?sf=code,name&terms=I25.10:
  [1, ["I25.10"], null, [["I25.10", "Atherosclerotic heart disease of native coronary artery without angina pectoris"]]]

Response format: [total_count, [codes], null, [[code, description], ...]]
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"

# Timeout for all API calls (seconds)
_TIMEOUT = 5

# ---------------------------------------------------------------------------
# Cardiology-specific fallback cache — used when the API is unreachable
# ---------------------------------------------------------------------------
_FALLBACK_CACHE: dict[str, str] = {
    "I25.10": "Atherosclerotic heart disease of native coronary artery without angina pectoris",
    "I25.110": "Atherosclerotic heart disease of native coronary artery with unstable angina pectoris",
    "I25.118": "Atherosclerotic heart disease of native coronary artery with other forms of angina pectoris",
    "I25.119": "Atherosclerotic heart disease of native coronary artery with unspecified angina pectoris",
    "I25.5": "Ischemic cardiomyopathy",
    "I25.810": "Atherosclerosis of coronary artery bypass graft(s) without angina pectoris",
    "I50.1": "Left ventricular failure, unspecified",
    "I50.20": "Unspecified systolic (congestive) heart failure",
    "I50.21": "Acute systolic (congestive) heart failure",
    "I50.22": "Chronic systolic (congestive) heart failure",
    "I50.23": "Acute on chronic systolic (congestive) heart failure",
    "I50.30": "Unspecified diastolic (congestive) heart failure",
    "I50.9": "Heart failure, unspecified",
    "I48.0": "Paroxysmal atrial fibrillation",
    "I48.1": "Persistent atrial fibrillation",
    "I48.2": "Chronic atrial fibrillation",
    "I48.91": "Unspecified atrial fibrillation",
    "I42.0": "Dilated cardiomyopathy",
    "I42.1": "Obstructive hypertrophic cardiomyopathy",
    "I42.9": "Cardiomyopathy, unspecified",
    "I35.0": "Nonrheumatic aortic (valve) stenosis",
    "I35.1": "Nonrheumatic aortic (valve) insufficiency",
    "I34.0": "Nonrheumatic mitral (valve) insufficiency",
    "I34.1": "Nonrheumatic mitral (valve) prolapse",
    "I21.0": "ST elevation (STEMI) myocardial infarction involving left main coronary artery",
    "I21.3": "ST elevation (STEMI) myocardial infarction of unspecified site",
    "R07.9": "Chest pain, unspecified",
    "R00.0": "Tachycardia, unspecified",
    "R00.1": "Bradycardia, unspecified",
    "R94.31": "Abnormal electrocardiogram [ECG] [EKG]",
    "I10": "Essential (primary) hypertension",
    "I11.0": "Hypertensive heart disease with heart failure",
    "I47.1": "Supraventricular tachycardia",
    "I47.2": "Ventricular tachycardia",
    "I49.01": "Ventricular fibrillation",
    "Z95.0": "Presence of cardiac pacemaker",
    "Z95.1": "Presence of aortocoronary bypass graft",
    "Z95.5": "Presence of coronary angioplasty implant and graft",
}

# Keyword-to-code suggestions for common cardiology terms
_KEYWORD_SUGGESTIONS: dict[str, list[str]] = {
    "chest pain": ["R07.9", "I25.110", "I25.119", "I20.9"],
    "heart failure": ["I50.9", "I50.20", "I50.22", "I50.23", "I50.30"],
    "atrial fibrillation": ["I48.0", "I48.1", "I48.2", "I48.91"],
    "coronary artery disease": ["I25.10", "I25.110", "I25.118", "I25.5"],
    "cardiomyopathy": ["I42.0", "I42.1", "I42.9", "I25.5"],
    "aortic stenosis": ["I35.0"],
    "mitral regurgitation": ["I34.0"],
    "hypertension": ["I10", "I11.0"],
    "tachycardia": ["R00.0", "I47.1", "I47.2"],
    "bradycardia": ["R00.1"],
    "myocardial infarction": ["I21.0", "I21.3"],
    "stent": ["Z95.5"],
    "pacemaker": ["Z95.0"],
}


def _get(params: dict[str, Any]) -> list | None:
    """Make a GET request to the NLM Clinical Tables API. Returns parsed JSON or None on failure."""
    try:
        resp = requests.get(BASE_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("ICD-10 API request failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def lookup_icd10(code: str) -> dict:
    """Look up a single ICD-10 code and return its description.

    Returns:
        {"code": "I25.10", "description": "...", "found": True}
        or {"code": "XYZ", "description": "", "found": False}

    Example (live API):
        >>> lookup_icd10("I25.10")
        {"code": "I25.10", "description": "Atherosclerotic heart disease of native coronary artery without angina pectoris", "found": True}
    """
    code = code.strip().upper()

    data = _get({"sf": "code,name", "terms": code, "maxList": 1})

    if data and isinstance(data, list) and len(data) >= 4:
        total = data[0]
        details = data[3]  # [[code, description], ...]
        if total > 0 and details:
            for entry in details:
                if entry[0].upper() == code:
                    return {"code": entry[0], "description": entry[1], "found": True}
            # If exact match not found but results exist, return first result
            return {"code": details[0][0], "description": details[0][1], "found": True}

    # Fallback to cache
    if code in _FALLBACK_CACHE:
        return {"code": code, "description": _FALLBACK_CACHE[code], "found": True}

    return {"code": code, "description": "", "found": False}


def search_icd10(query: str, max_results: int = 10) -> list[dict]:
    """Search ICD-10 codes by keyword or partial code.

    Returns:
        [{"code": "R07.9", "description": "Chest pain, unspecified"}, ...]

    Example (live API):
        >>> search_icd10("chest pain", max_results=3)
        [
            {"code": "R07.1", "description": "Chest pain on breathing"},
            {"code": "R07.89", "description": "Other chest pain"},
            {"code": "R07.9", "description": "Chest pain, unspecified"},
        ]
    """
    query = query.strip()
    if not query:
        return []

    data = _get({"sf": "code,name", "terms": query, "maxList": max_results})

    if data and isinstance(data, list) and len(data) >= 4:
        details = data[3]
        if details:
            return [{"code": entry[0], "description": entry[1]} for entry in details]

    # Fallback: search the local cache by keyword
    query_lower = query.lower()
    results = []
    for code, desc in _FALLBACK_CACHE.items():
        if query_lower in code.lower() or query_lower in desc.lower():
            results.append({"code": code, "description": desc})
            if len(results) >= max_results:
                break
    return results


def validate_codes(codes: list[str]) -> list[dict]:
    """Validate a list of ICD-10 codes, return each with description and valid flag.

    Returns:
        [{"code": "I25.10", "description": "...", "valid": True}, {"code": "FAKE", "description": "", "valid": False}]

    Example:
        >>> validate_codes(["I25.10", "R07.9", "INVALID"])
        [
            {"code": "I25.10", "description": "Atherosclerotic heart disease ...", "valid": True},
            {"code": "R07.9", "description": "Chest pain, unspecified", "valid": True},
            {"code": "INVALID", "description": "", "valid": False},
        ]
    """
    results = []
    for code in codes:
        info = lookup_icd10(code)
        results.append({
            "code": info["code"],
            "description": info["description"],
            "valid": info["found"],
        })
    return results


def suggest_codes(keyword: str, procedure_code: str = "") -> list[dict]:
    """Suggest ICD-10 codes for a given clinical keyword, optionally filtered by procedure context.

    For cardiology procedures, the function first checks a curated keyword map, then
    falls back to the NLM search API.

    Returns:
        [{"code": "I25.10", "description": "...", "relevance": "high"}, ...]

    Example:
        >>> suggest_codes("chest pain", procedure_code="93458")
        [
            {"code": "R07.9", "description": "Chest pain, unspecified", "relevance": "high"},
            {"code": "I25.110", "description": "...", "relevance": "high"},
            ...
        ]
    """
    keyword_lower = keyword.strip().lower()
    results: list[dict] = []

    # Check curated keyword map first
    for key, code_list in _KEYWORD_SUGGESTIONS.items():
        if keyword_lower in key or key in keyword_lower:
            for code in code_list:
                desc = _FALLBACK_CACHE.get(code, "")
                results.append({"code": code, "description": desc, "relevance": "high"})

    # Supplement with API search
    api_results = search_icd10(keyword, max_results=10)
    seen = {r["code"] for r in results}
    for item in api_results:
        if item["code"] not in seen:
            results.append({**item, "relevance": "medium"})
            seen.add(item["code"])

    # If a procedure code is given, boost cardiology-relevant codes
    if procedure_code:
        cardio_prefixes = ("I", "R00", "R01", "R07", "R94.3", "Z95")
        for r in results:
            if r["code"].startswith(cardio_prefixes) and r["relevance"] != "high":
                r["relevance"] = "medium-high"

    return results
