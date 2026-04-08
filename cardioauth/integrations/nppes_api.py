"""NPPES NPI Registry API integration.

Free, no API key required.
Docs: https://npiregistry.cms.hhs.gov/api-page
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://npiregistry.cms.hhs.gov/api/"
DEFAULT_VERSION = "2.1"
REQUEST_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_request(params: dict[str, Any]) -> dict[str, Any]:
    """Send a GET request to the NPPES API and return the JSON response."""
    params.setdefault("version", DEFAULT_VERSION)
    try:
        resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error("NPPES API request failed: %s", exc)
        return {"error": str(exc), "result_count": 0, "results": []}


def _parse_provider(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract useful fields from a single NPPES result entry into a clean dict."""
    basic = raw.get("basic", {})
    taxonomies = raw.get("taxonomies", [])
    addresses = raw.get("addresses", [])
    identifiers = raw.get("identifiers", [])

    # Name — handle individual vs. organization
    entity_type = basic.get("enumeration_type", "")
    if entity_type == "NPI-2":
        name = basic.get("organization_name", "")
    else:
        first = basic.get("first_name", "")
        last = basic.get("last_name", "")
        credential = basic.get("credential", "")
        name = f"Dr. {first} {last}".strip()
        if credential:
            name = f"{name}, {credential}"

    # Primary taxonomy
    primary_tax = next(
        (t for t in taxonomies if t.get("primary", False)),
        taxonomies[0] if taxonomies else {},
    )
    specialty = primary_tax.get("desc", "")
    taxonomy_code = primary_tax.get("code", "")

    # Practice location address (type 2) preferred, else mailing (type 1)
    practice_addr = next(
        (a for a in addresses if a.get("address_purpose") == "LOCATION"),
        addresses[0] if addresses else {},
    )
    addr_parts = [
        practice_addr.get("address_1", ""),
        practice_addr.get("address_2", ""),
    ]
    city = practice_addr.get("city", "")
    state = practice_addr.get("state", "")
    postal = practice_addr.get("postal_code", "")[:5] if practice_addr.get("postal_code") else ""
    street = ", ".join(p for p in addr_parts if p)
    full_address = f"{street}, {city}, {state} {postal}".strip(", ")

    phone = practice_addr.get("telephone_number", "")

    # Status
    status = basic.get("status", "A")
    status_label = "active" if status == "A" else "inactive"

    return {
        "npi": str(raw.get("number", "")),
        "name": name,
        "credential": basic.get("credential", ""),
        "entity_type": entity_type,
        "specialty": specialty,
        "taxonomy_code": taxonomy_code,
        "address": full_address,
        "phone": phone,
        "enumeration_date": basic.get("enumeration_date", ""),
        "last_updated": basic.get("last_updated", ""),
        "status": status_label,
        "identifiers": [
            {"code": i.get("code", ""), "desc": i.get("desc", ""), "identifier": i.get("identifier", "")}
            for i in identifiers
        ],
        "all_taxonomies": [
            {"code": t.get("code", ""), "desc": t.get("desc", ""), "primary": t.get("primary", False)}
            for t in taxonomies
        ],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup_npi(npi: str) -> dict[str, Any]:
    """Look up a provider by NPI number.

    Returns a clean dict with name, credentials, taxonomy, address, phone.
    """
    data = _make_request({"number": npi})
    results = data.get("results", [])
    if not results:
        return {"error": "NPI not found", "npi": npi}
    return _parse_provider(results[0])


def search_providers(
    last_name: str = "",
    first_name: str = "",
    state: str = "",
    specialty: str = "cardiology",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search for providers by name and specialty.

    Returns a list of clean provider dicts.
    """
    params: dict[str, Any] = {"limit": min(limit, 200)}
    if last_name:
        params["last_name"] = last_name
    if first_name:
        params["first_name"] = first_name
    if state:
        params["state"] = state
    if specialty:
        params["taxonomy_description"] = specialty

    data = _make_request(params)
    results = data.get("results", [])
    return [_parse_provider(r) for r in results]


def validate_npi(npi: str) -> dict[str, Any]:
    """Validate an NPI number exists and return provider info.

    Returns a dict with ``valid`` boolean and provider details if found.
    """
    provider = lookup_npi(npi)
    if "error" in provider:
        return {"valid": False, "npi": npi, "reason": provider["error"]}
    return {
        "valid": True,
        "npi": npi,
        "name": provider["name"],
        "credential": provider["credential"],
        "specialty": provider["specialty"],
        "status": provider["status"],
    }


def get_provider_details(npi: str) -> dict[str, Any]:
    """Get full provider details including taxonomy codes, addresses, identifiers.

    Returns the complete parsed provider dict with all nested data.
    """
    data = _make_request({"number": npi})
    results = data.get("results", [])
    if not results:
        return {"error": "NPI not found", "npi": npi}

    provider = _parse_provider(results[0])
    # Also include the raw addresses for maximum detail
    raw = results[0]
    provider["addresses_full"] = [
        {
            "purpose": a.get("address_purpose", ""),
            "address_1": a.get("address_1", ""),
            "address_2": a.get("address_2", ""),
            "city": a.get("city", ""),
            "state": a.get("state", ""),
            "postal_code": a.get("postal_code", ""),
            "phone": a.get("telephone_number", ""),
            "fax": a.get("fax_number", ""),
        }
        for a in raw.get("addresses", [])
    ]
    return provider
