"""Epic FHIR R4 client with automatic JWT-based token management."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import jwt
import requests

from cardioauth.config import Config

logger = logging.getLogger(__name__)

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
    def __init__(self, config: Config) -> None:
        self.config = config
        self.base_url = config.epic_base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/fhir+json"})
        self._token: str | None = None
        self._token_expires: float = 0

    def _get_token(self) -> str:
        """Get a bearer token using Epic's Backend System JWT flow."""
        if self._token and time.time() < self._token_expires - 30:
            return self._token

        now = int(time.time())
        private_key = self.config.get_private_key()
        if not private_key:
            raise RuntimeError("No Epic private key configured")

        # Build the JWT assertion
        claims = {
            "iss": self.config.epic_client_id,
            "sub": self.config.epic_client_id,
            "aud": self.config.epic_token_url,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 300,  # 5 min max per Epic spec
        }

        assertion = jwt.encode(
            claims,
            private_key,
            algorithm="RS384",
            headers={"kid": "cardioauth-1"},
        )

        # Exchange JWT for access token
        resp = requests.post(
            self.config.epic_token_url,
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        token_data = resp.json()

        self._token = token_data["access_token"]
        self._token_expires = now + token_data.get("expires_in", 300)

        logger.info("FHIR: obtained access token (expires in %ds)", token_data.get("expires_in", 300))
        return self._token

    def _get(self, resource_type: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        token = self._get_token()
        self.session.headers["Authorization"] = f"Bearer {token}"
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
