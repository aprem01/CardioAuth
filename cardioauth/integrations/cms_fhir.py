"""CMS FHIR (BCDA / Blue Button) client for Medicare data.

Sandbox: https://sandbox.bcda.cms.gov/api/v2/
Production: https://bcda.cms.gov/api/v2/

When no CMS credentials are configured the client operates in **demo mode**,
returning realistic mock Medicare data suitable for development and demos.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

SANDBOX_BASE_URL = "https://sandbox.bcda.cms.gov/api/v2/"
PRODUCTION_BASE_URL = "https://bcda.cms.gov/api/v2/"
REQUEST_TIMEOUT = 15  # seconds


class CMSFHIRClient:
    """Client for the CMS Beneficiary Claims Data API (BCDA).

    Operates in demo mode when credentials are not supplied.
    """

    def __init__(
        self,
        base_url: str = "",
        client_id: str = "",
        client_secret: str = "",
    ) -> None:
        """Initialize CMS FHIR client.

        Parameters
        ----------
        base_url:
            API base URL.  Defaults to the CMS sandbox.
        client_id / client_secret:
            OAuth2 client credentials obtained from CMS.
            If empty the client falls back to demo mode.
        """
        self.base_url = (base_url or SANDBOX_BASE_URL).rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: str = ""
        self._token_expiry: datetime | None = None
        self.demo_mode = not (client_id and client_secret)

        if self.demo_mode:
            logger.info("CMSFHIRClient initialized in DEMO mode (no credentials)")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        """Obtain or refresh an OAuth2 access token from CMS."""
        if self.demo_mode:
            return
        if self._access_token and self._token_expiry and datetime.utcnow() < self._token_expiry:
            return

        token_url = self.base_url.replace("/api/v2", "") + "/auth/token"
        try:
            resp = requests.post(
                token_url,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self._token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 60)
        except requests.RequestException as exc:
            logger.error("CMS token request failed: %s", exc)
            raise

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Authenticated GET against the BCDA API."""
        self._ensure_token()
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/fhir+json",
        }
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Demo data
    # ------------------------------------------------------------------

    @staticmethod
    def _demo_eligibility(medicare_id: str) -> dict[str, Any]:
        """Return realistic mock eligibility data."""
        # Simulate a few scenarios based on suffix
        inactive_ids = {"9999999999", "0000000000"}
        active = medicare_id not in inactive_ids

        return {
            "medicare_id": medicare_id,
            "eligible": active,
            "status": "active" if active else "inactive",
            "beneficiary_name": "Demo Patient",
            "date_of_birth": "1948-06-15",
            "gender": "male",
            "part_a": {
                "enrolled": active,
                "effective_date": "2013-07-01",
                "termination_date": None,
            },
            "part_b": {
                "enrolled": active,
                "effective_date": "2013-07-01",
                "termination_date": None,
            },
            "part_d": {
                "enrolled": active,
                "plan_name": "SilverScript Choice (PDP)" if active else None,
                "effective_date": "2020-01-01" if active else None,
            },
            "medicare_advantage": {
                "enrolled": False,
                "plan_name": None,
            },
            "dual_eligible": False,
            "esrd": False,
            "last_verified": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    @staticmethod
    def _demo_coverage(medicare_id: str) -> dict[str, Any]:
        """Return realistic mock coverage details."""
        return {
            "medicare_id": medicare_id,
            "coverage_type": "Medicare Fee-For-Service",
            "part_a": {
                "status": "active",
                "benefit_period_start": "2026-01-01",
                "deductible_met": True,
                "deductible_amount": 1632.0,
                "inpatient_days_used": 3,
                "inpatient_days_remaining": 57,
                "snf_days_used": 0,
                "snf_days_remaining": 100,
            },
            "part_b": {
                "status": "active",
                "deductible_met": True,
                "deductible_amount": 240.0,
                "coinsurance_rate": 0.20,
                "annual_out_of_pocket": 1845.50,
            },
            "part_d": {
                "status": "active",
                "plan_name": "SilverScript Choice (PDP)",
                "plan_id": "S5601-076-0",
                "phase": "initial_coverage",
                "total_drug_cost_ytd": 2340.00,
                "true_out_of_pocket_ytd": 680.00,
                "catastrophic_threshold": 8000.0,
            },
            "medigap": {
                "has_supplement": True,
                "plan_type": "Plan G",
                "carrier": "AARP/UnitedHealthcare",
            },
            "primary_care_provider": {
                "npi": "1234567890",
                "name": "Dr. Sarah Johnson",
            },
            "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    @staticmethod
    def _demo_claims(medicare_id: str, procedure_code: str = "") -> list[dict[str, Any]]:
        """Return realistic mock cardiology claims history."""
        all_claims = [
            {
                "claim_id": "CLM-2026-00481",
                "date_of_service": "2026-03-10",
                "provider_npi": "1234567890",
                "provider_name": "Dr. Peter Ramirez",
                "facility": "Metro Heart Center",
                "procedure_code": "93306",
                "procedure_name": "Transthoracic echocardiogram (TTE)",
                "diagnosis_codes": ["I50.22", "I25.10"],
                "billed_amount": 1250.00,
                "medicare_paid": 845.00,
                "patient_responsibility": 169.00,
                "status": "paid",
            },
            {
                "claim_id": "CLM-2026-00327",
                "date_of_service": "2026-02-05",
                "provider_npi": "1234567890",
                "provider_name": "Dr. Peter Ramirez",
                "facility": "Metro Heart Center",
                "procedure_code": "93000",
                "procedure_name": "Electrocardiogram (ECG) 12-lead",
                "diagnosis_codes": ["I48.91", "I25.10"],
                "billed_amount": 320.00,
                "medicare_paid": 215.00,
                "patient_responsibility": 43.00,
                "status": "paid",
            },
            {
                "claim_id": "CLM-2025-04892",
                "date_of_service": "2025-12-18",
                "provider_npi": "9876543210",
                "provider_name": "Dr. Lisa Chen",
                "facility": "University Cardiology Associates",
                "procedure_code": "93458",
                "procedure_name": "Left heart catheterization",
                "diagnosis_codes": ["I25.10", "I50.22"],
                "billed_amount": 5800.00,
                "medicare_paid": 3920.00,
                "patient_responsibility": 784.00,
                "status": "paid",
            },
            {
                "claim_id": "CLM-2025-04501",
                "date_of_service": "2025-11-20",
                "provider_npi": "1234567890",
                "provider_name": "Dr. Peter Ramirez",
                "facility": "Metro Heart Center",
                "procedure_code": "93350",
                "procedure_name": "Stress echocardiogram",
                "diagnosis_codes": ["I20.9", "I25.10"],
                "billed_amount": 1680.00,
                "medicare_paid": 1135.00,
                "patient_responsibility": 227.00,
                "status": "paid",
            },
            {
                "claim_id": "CLM-2025-03100",
                "date_of_service": "2025-09-14",
                "provider_npi": "1234567890",
                "provider_name": "Dr. Peter Ramirez",
                "facility": "Metro Heart Center",
                "procedure_code": "93228",
                "procedure_name": "Remote cardiac telemetry (30 days)",
                "diagnosis_codes": ["I48.91"],
                "billed_amount": 890.00,
                "medicare_paid": 602.00,
                "patient_responsibility": 120.40,
                "status": "paid",
            },
        ]

        if procedure_code:
            return [c for c in all_claims if c["procedure_code"] == procedure_code]
        return all_claims

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_medicare_eligibility(self, medicare_id: str) -> dict[str, Any]:
        """Check if a Medicare beneficiary has active coverage.

        In demo mode returns realistic mock data.
        """
        if self.demo_mode:
            return self._demo_eligibility(medicare_id)

        try:
            data = self._get(f"Patient/{medicare_id}/$eligibility")
            # Parse FHIR Bundle response
            entries = data.get("entry", [])
            if not entries:
                return {"medicare_id": medicare_id, "eligible": False, "status": "not_found"}
            resource = entries[0].get("resource", {})
            return {
                "medicare_id": medicare_id,
                "eligible": resource.get("active", False),
                "status": "active" if resource.get("active") else "inactive",
                "raw": resource,
            }
        except requests.RequestException as exc:
            logger.error("CMS eligibility check failed: %s", exc)
            return {"medicare_id": medicare_id, "eligible": False, "error": str(exc)}

    def get_coverage_details(self, medicare_id: str) -> dict[str, Any]:
        """Get Medicare coverage details including Part A/B/D status.

        In demo mode returns realistic mock data.
        """
        if self.demo_mode:
            return self._demo_coverage(medicare_id)

        try:
            data = self._get("Coverage", params={"beneficiary": medicare_id})
            entries = data.get("entry", [])
            coverages = [e.get("resource", {}) for e in entries]
            return {
                "medicare_id": medicare_id,
                "coverages": coverages,
            }
        except requests.RequestException as exc:
            logger.error("CMS coverage lookup failed: %s", exc)
            return {"medicare_id": medicare_id, "error": str(exc)}

    def get_claims_history(
        self,
        medicare_id: str,
        procedure_code: str = "",
    ) -> list[dict[str, Any]]:
        """Get prior claims for a beneficiary, optionally filtered by procedure.

        In demo mode returns realistic mock cardiology claims.
        """
        if self.demo_mode:
            return self._demo_claims(medicare_id, procedure_code)

        try:
            params: dict[str, Any] = {"patient": medicare_id}
            if procedure_code:
                params["service-code"] = procedure_code
            data = self._get("ExplanationOfBenefit", params=params)
            entries = data.get("entry", [])
            return [e.get("resource", {}) for e in entries]
        except requests.RequestException as exc:
            logger.error("CMS claims history failed: %s", exc)
            return []
