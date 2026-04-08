"""RxNorm medication lookup via NLM RxNav API (free, no key required).

API docs: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html

Example responses:

Search by name (GET /REST/drugs.json?name=metoprolol):
    {"drugGroup": {"name": "metoprolol", "conceptGroup": [
        {"tty": "SBD", "conceptProperties": [
            {"rxcui": "866924", "name": "metoprolol succinate 25 MG Extended Release Oral Tablet", ...}
        ]}
    ]}}

Get NDCs (GET /REST/rxcui/866924/ndcs.json):
    {"ndcGroup": {"ndcList": {"ndc": ["0093-7385-56", ...]}}}

Interaction check (GET /REST/interaction/list.json?rxcuis=866924+29046):
    {"fullInteractionTypeGroup": [{"fullInteractionType": [
        {"interactionPair": [{"description": "...", "severity": "high"}]}
    ]}]}
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://rxnav.nlm.nih.gov/REST"

_TIMEOUT = 5

# ---------------------------------------------------------------------------
# Cardiology-specific fallback cache
# ---------------------------------------------------------------------------
_FALLBACK_MEDICATIONS: dict[str, dict] = {
    "metoprolol": {
        "rxcui": "6918",
        "name": "metoprolol",
        "full_name": "metoprolol tartrate",
        "drug_class": "Beta-blocker",
    },
    "lisinopril": {
        "rxcui": "29046",
        "name": "lisinopril",
        "full_name": "lisinopril",
        "drug_class": "ACE inhibitor",
    },
    "atorvastatin": {
        "rxcui": "83367",
        "name": "atorvastatin",
        "full_name": "atorvastatin calcium",
        "drug_class": "Statin",
    },
    "warfarin": {
        "rxcui": "11289",
        "name": "warfarin",
        "full_name": "warfarin sodium",
        "drug_class": "Anticoagulant",
    },
    "apixaban": {
        "rxcui": "1364430",
        "name": "apixaban",
        "full_name": "apixaban (Eliquis)",
        "drug_class": "DOAC Anticoagulant",
    },
    "clopidogrel": {
        "rxcui": "32968",
        "name": "clopidogrel",
        "full_name": "clopidogrel bisulfate (Plavix)",
        "drug_class": "Antiplatelet",
    },
    "aspirin": {
        "rxcui": "1191",
        "name": "aspirin",
        "full_name": "aspirin",
        "drug_class": "Antiplatelet / NSAID",
    },
    "amiodarone": {
        "rxcui": "703",
        "name": "amiodarone",
        "full_name": "amiodarone hydrochloride",
        "drug_class": "Antiarrhythmic",
    },
    "furosemide": {
        "rxcui": "4603",
        "name": "furosemide",
        "full_name": "furosemide (Lasix)",
        "drug_class": "Loop diuretic",
    },
    "amlodipine": {
        "rxcui": "17767",
        "name": "amlodipine",
        "full_name": "amlodipine besylate",
        "drug_class": "Calcium channel blocker",
    },
    "digoxin": {
        "rxcui": "3407",
        "name": "digoxin",
        "full_name": "digoxin",
        "drug_class": "Cardiac glycoside",
    },
    "heparin": {
        "rxcui": "5224",
        "name": "heparin",
        "full_name": "heparin sodium",
        "drug_class": "Anticoagulant",
    },
    "nitroglycerin": {
        "rxcui": "4917",
        "name": "nitroglycerin",
        "full_name": "nitroglycerin",
        "drug_class": "Nitrate vasodilator",
    },
    "losartan": {
        "rxcui": "52175",
        "name": "losartan",
        "full_name": "losartan potassium",
        "drug_class": "ARB",
    },
    "spironolactone": {
        "rxcui": "9997",
        "name": "spironolactone",
        "full_name": "spironolactone",
        "drug_class": "Aldosterone antagonist",
    },
}

_FALLBACK_INTERACTIONS: list[dict] = [
    {
        "drugs": ["warfarin", "aspirin"],
        "description": "Increased risk of bleeding when warfarin is combined with aspirin.",
        "severity": "high",
    },
    {
        "drugs": ["amiodarone", "warfarin"],
        "description": "Amiodarone may increase the anticoagulant effect of warfarin, increasing bleeding risk.",
        "severity": "high",
    },
    {
        "drugs": ["amiodarone", "digoxin"],
        "description": "Amiodarone may increase digoxin serum concentration, risking toxicity.",
        "severity": "high",
    },
    {
        "drugs": ["metoprolol", "amiodarone"],
        "description": "Concomitant use may cause additive bradycardia and AV block.",
        "severity": "moderate",
    },
    {
        "drugs": ["lisinopril", "spironolactone"],
        "description": "Combined use may increase risk of hyperkalemia.",
        "severity": "moderate",
    },
    {
        "drugs": ["clopidogrel", "aspirin"],
        "description": "Dual antiplatelet therapy increases bleeding risk; common but requires monitoring.",
        "severity": "moderate",
    },
]


def _get_json(path: str, params: dict[str, Any] | None = None) -> dict | None:
    """GET a JSON endpoint from RxNav. Returns parsed dict or None on failure."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("RxNorm API request failed (%s): %s", path, exc)
        return None


def _find_rxcui(name: str) -> str | None:
    """Resolve a medication name to an RxCUI via the /rxcui endpoint."""
    data = _get_json("rxcui.json", {"name": name, "search": 2})
    if data:
        id_group = data.get("idGroup", {})
        rxnorm_ids = id_group.get("rxnormId")
        if rxnorm_ids:
            return rxnorm_ids[0]
    return None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def lookup_medication(name: str) -> dict:
    """Look up medication by name, return RxCUI, full name, and available forms.

    Returns:
        {
            "name": "metoprolol",
            "rxcui": "6918",
            "found": True,
            "forms": [{"rxcui": "866924", "name": "metoprolol succinate 25 MG ..."}],
        }

    Example (live API):
        >>> lookup_medication("metoprolol")
        {"name": "metoprolol", "rxcui": "6918", "found": True, "forms": [...]}
    """
    name_lower = name.strip().lower()

    data = _get_json("drugs.json", {"name": name_lower})

    if data:
        drug_group = data.get("drugGroup", {})
        concept_groups = drug_group.get("conceptGroup") or []
        forms: list[dict] = []
        for group in concept_groups:
            for prop in group.get("conceptProperties", []):
                forms.append({"rxcui": prop.get("rxcui", ""), "name": prop.get("name", "")})

        if forms:
            # Try to find the base ingredient RxCUI
            rxcui = _find_rxcui(name_lower) or forms[0]["rxcui"]
            return {
                "name": name_lower,
                "rxcui": rxcui,
                "found": True,
                "forms": forms[:20],  # cap at 20 forms
            }

    # Fallback
    if name_lower in _FALLBACK_MEDICATIONS:
        fb = _FALLBACK_MEDICATIONS[name_lower]
        return {
            "name": name_lower,
            "rxcui": fb["rxcui"],
            "found": True,
            "forms": [{"rxcui": fb["rxcui"], "name": fb["full_name"]}],
            "source": "fallback_cache",
        }

    return {"name": name_lower, "rxcui": None, "found": False, "forms": []}


def get_ndc_codes(medication_name: str) -> list[str]:
    """Get NDC codes for a medication.

    Returns:
        ["0093-7385-56", "0093-7385-01", ...]

    Example (live API):
        >>> get_ndc_codes("metoprolol")
        ["0093-7385-56", ...]
    """
    med = lookup_medication(medication_name)
    if not med["found"] or not med.get("rxcui"):
        return []

    # Try getting NDCs for the base RxCUI first
    rxcui = med["rxcui"]
    data = _get_json(f"rxcui/{rxcui}/ndcs.json")

    if data:
        ndc_group = data.get("ndcGroup", {})
        ndc_list = ndc_group.get("ndcList")
        if ndc_list and isinstance(ndc_list, dict):
            ndcs = ndc_list.get("ndc", [])
            if ndcs:
                return ndcs[:50]  # cap at 50

    # Try the first form's RxCUI if base didn't have NDCs
    for form in med.get("forms", []):
        form_rxcui = form.get("rxcui")
        if form_rxcui and form_rxcui != rxcui:
            data = _get_json(f"rxcui/{form_rxcui}/ndcs.json")
            if data:
                ndc_group = data.get("ndcGroup", {})
                ndc_list = ndc_group.get("ndcList")
                if ndc_list and isinstance(ndc_list, dict):
                    ndcs = ndc_list.get("ndc", [])
                    if ndcs:
                        return ndcs[:50]
            break  # only try one form

    return []


def check_interactions(medication_names: list[str]) -> list[dict]:
    """Check for drug-drug interactions between a list of medications.

    Returns:
        [
            {
                "drug_pair": ["warfarin", "aspirin"],
                "description": "Increased risk of bleeding ...",
                "severity": "high",
            },
            ...
        ]

    Example (live API):
        >>> check_interactions(["warfarin", "aspirin", "metoprolol"])
        [{"drug_pair": ["warfarin", "aspirin"], "description": "...", "severity": "high"}, ...]
    """
    if len(medication_names) < 2:
        return []

    # Resolve each name to RxCUI
    rxcui_map: dict[str, str] = {}
    for name in medication_names:
        med = lookup_medication(name)
        if med["found"] and med.get("rxcui"):
            rxcui_map[name.strip().lower()] = med["rxcui"]

    if len(rxcui_map) < 2:
        # Not enough resolved — fall back to local cache
        return _fallback_interactions(medication_names)

    rxcui_str = "+".join(rxcui_map.values())
    # Must pass rxcuis with literal '+' separators — use the full URL to avoid
    # requests URL-encoding the '+' as %2B (which the API rejects).
    data = _get_json(f"interaction/list.json?rxcuis={rxcui_str}")

    interactions: list[dict] = []

    if data:
        for group in data.get("fullInteractionTypeGroup", []):
            for interaction_type in group.get("fullInteractionType", []):
                # Extract drug names from the interaction
                min_concepts = interaction_type.get("minConceptItem", {})
                pair_names: list[str] = []
                if isinstance(min_concepts, dict):
                    pair_names.append(min_concepts.get("name", ""))
                elif isinstance(min_concepts, list):
                    pair_names = [m.get("name", "") for m in min_concepts]

                for pair in interaction_type.get("interactionPair", []):
                    desc = pair.get("description", "")
                    severity = pair.get("severity", "N/A")
                    interactions.append({
                        "drug_pair": pair_names if len(pair_names) >= 2 else medication_names[:2],
                        "description": desc,
                        "severity": severity,
                    })

    if not interactions:
        return _fallback_interactions(medication_names)

    return interactions


def _fallback_interactions(medication_names: list[str]) -> list[dict]:
    """Check the local interaction cache for known pairs."""
    names_lower = {n.strip().lower() for n in medication_names}
    results = []
    for entry in _FALLBACK_INTERACTIONS:
        pair_set = {d.lower() for d in entry["drugs"]}
        if pair_set.issubset(names_lower):
            results.append({
                "drug_pair": entry["drugs"],
                "description": entry["description"],
                "severity": entry["severity"],
                "source": "fallback_cache",
            })
    return results


def normalize_medication(name: str) -> dict:
    """Normalize a medication name to standard RxNorm terminology.

    Returns:
        {
            "input": "metoprolol",
            "normalized_name": "metoprolol tartrate",
            "rxcui": "6918",
            "found": True,
        }

    Example (live API):
        >>> normalize_medication("lopressor")
        {"input": "lopressor", "normalized_name": "metoprolol tartrate", "rxcui": "6918", "found": True}
    """
    name_stripped = name.strip()

    # Try approximate match endpoint
    data = _get_json("approximateTerm.json", {"term": name_stripped, "maxEntries": 1})
    if data:
        candidates = data.get("approximateGroup", {}).get("candidate", [])
        if candidates:
            rxcui = candidates[0].get("rxcui", "")
            if rxcui:
                # Get the standard name for this RxCUI
                props = _get_json(f"rxcui/{rxcui}/properties.json")
                if props:
                    prop_data = props.get("properties", {})
                    return {
                        "input": name_stripped,
                        "normalized_name": prop_data.get("name", name_stripped),
                        "rxcui": rxcui,
                        "found": True,
                    }

    # Fallback
    name_lower = name_stripped.lower()
    if name_lower in _FALLBACK_MEDICATIONS:
        fb = _FALLBACK_MEDICATIONS[name_lower]
        return {
            "input": name_stripped,
            "normalized_name": fb["full_name"],
            "rxcui": fb["rxcui"],
            "found": True,
            "source": "fallback_cache",
        }

    return {
        "input": name_stripped,
        "normalized_name": name_stripped,
        "rxcui": None,
        "found": False,
    }
