"""Epic FHIR R4 client for extracting patient clinical data."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# FHIR resource types relevant to cardiology prior auth
RESOURCE_TYPES = [
    "Patient",
    "Condition",
    "Observation",
    "MedicationRequest",
    "DiagnosticReport",
    "Procedure",
    "Coverage",
]


class FHIRClient:
    def __init__(self, base_url: str, bearer_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/fhir+json",
        })

    def _get(self, resource_type: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{resource_type}"
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_patient_bundle(self, patient_id: str, procedure_code: str) -> dict[str, Any]:
        """Fetch all clinically relevant FHIR resources for a patient."""
        bundle: dict[str, Any] = {"patient_id": patient_id, "resources": {}}

        for resource_type in RESOURCE_TYPES:
            try:
                if resource_type == "Patient":
                    data = self._get(resource_type, {"_id": patient_id})
                else:
                    data = self._get(resource_type, {"patient": patient_id})
                bundle["resources"][resource_type] = data
            except requests.RequestException as e:
                logger.warning("FHIR: failed to fetch %s for patient %s: %s", resource_type, patient_id, e)
                bundle["resources"][resource_type] = {"error": str(e)}

        return bundle
