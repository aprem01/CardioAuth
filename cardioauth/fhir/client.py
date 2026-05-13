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
    "Encounter",
    "DocumentReference",
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

        # Exchange JWT for access token. Request the SMART v2 system-level
        # scopes for every resource we read — Epic issues a no-permission
        # token if scope isn't asked for, so reads come back 403. The set
        # below matches the Incoming APIs selected on the vendor portal.
        scopes = " ".join([
            "system/Patient.read",
            "system/Condition.read",
            "system/Observation.read",
            "system/MedicationRequest.read",
            "system/DiagnosticReport.read",
            "system/Procedure.read",
            "system/Coverage.read",
            "system/Encounter.read",
            "system/DocumentReference.read",
            "system/Binary.read",
        ])
        resp = requests.post(
            self.config.epic_token_url,
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
                "scope": scopes,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if not resp.ok:
            # OAuth error responses come as JSON {error, error_description}
            # — surface that explicitly instead of the bare HTTP code so
            # diagnostic output tells us WHY Epic rejected the assertion
            # (invalid_client = JWKS unfetched/key unknown; invalid_grant
            # = signature can't be verified; invalid_request = malformed).
            err_body = (resp.text or "")[:400]
            raise requests.HTTPError(
                f"Epic token exchange failed: HTTP {resp.status_code} body={err_body}",
                response=resp,
            )
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
                logger.warning("FHIR: failed to fetch %s for patient %s: %s", resource_type, patient_id[:4] + "***", e)
                bundle["resources"][resource_type] = {"error": str(e)}

        return bundle

    def fetch_binary(self, binary_url: str) -> tuple[str, bytes]:
        """Fetch a Binary resource (typically a DocumentReference attachment).

        Epic returns Binary resources either as JSON wrapping base64 data or
        as a raw bytestream depending on the Accept header. We negotiate JSON
        because that gives us mime-type metadata too.

        Returns (content_type, raw_bytes).
        """
        import base64

        token = self._get_token()
        self.session.headers["Authorization"] = f"Bearer {token}"
        # Allow either FHIR-wrapped JSON or raw octet-stream
        headers = {"Accept": "application/fhir+json, application/octet-stream;q=0.8"}
        # Binary may come as an absolute URL on the FHIR server or a relative
        # reference like "Binary/abc-123" — normalize either way.
        if binary_url.startswith("http://") or binary_url.startswith("https://"):
            url = binary_url
        else:
            url = f"{self.base_url}/{binary_url.lstrip('/')}"
        resp = self.session.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype.lower():
            body = resp.json()
            data_b64 = body.get("data", "")
            return body.get("contentType", "application/octet-stream"), base64.b64decode(data_b64) if data_b64 else b""
        return ctype, resp.content
