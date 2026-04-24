"""Configuration for CardioAuth."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # Anthropic
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    model: str = "claude-sonnet-4-6"

    # Epic FHIR
    epic_base_url: str = field(default_factory=lambda: os.environ.get(
        "EPIC_FHIR_BASE_URL",
        "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
    ))
    epic_client_id: str = field(default_factory=lambda: os.environ.get("EPIC_CLIENT_ID", ""))
    epic_private_key_path: str = field(default_factory=lambda: os.environ.get("EPIC_PRIVATE_KEY_PATH", "private.pem"))
    epic_private_key: str = field(default_factory=lambda: os.environ.get("EPIC_PRIVATE_KEY", ""))
    epic_token_url: str = field(default_factory=lambda: os.environ.get(
        "EPIC_TOKEN_URL",
        "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
    ))

    # Vector store (Pinecone)
    pinecone_api_key: str = field(default_factory=lambda: os.environ.get("PINECONE_API_KEY", ""))
    pinecone_index: str = field(default_factory=lambda: os.environ.get("PINECONE_INDEX", "cardioauth-policies"))

    # AWS Comprehend Medical (optional preprocessing)
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    use_comprehend_medical: bool = field(default_factory=lambda: os.environ.get("USE_COMPREHEND_MEDICAL", "").lower() in ("1", "true", "yes"))

    # PDF parser for chart + policy ingest. "llamaparse" | "disabled".
    # Real PHI requires a BAA with the provider — until signed, UI enforces
    # deidentified-only uploads and server accepts them as demo input.
    llamaparse_api_key: str = field(default_factory=lambda: os.environ.get("LLAMAPARSE_API_KEY", ""))
    pdf_parser: str = field(default_factory=lambda: os.environ.get(
        "PDF_PARSER",
        "llamaparse" if os.environ.get("LLAMAPARSE_API_KEY") else "disabled",
    ))

    # Thresholds
    chart_confidence_threshold: float = 0.8
    approval_likelihood_threshold: float = 0.6

    # Self-consistency ensemble — run the reasoner N times and majority-vote.
    # N=1: single run (fastest + cheapest, pre-production default).
    # N=3: real clinical use — catches model noise, produces agreement scores
    # the physician can interpret. Costs ~3x the API calls per request.
    reasoning_ensemble_n: int = field(default_factory=lambda: int(os.environ.get("REASONING_ENSEMBLE_N", "1")))
    reasoning_ensemble_temperature: float = field(default_factory=lambda: float(os.environ.get("REASONING_ENSEMBLE_TEMPERATURE", "0.3")))
    # Any criterion where fewer than this fraction of runs agree on the
    # final status gets flagged to the cardiologist for review.
    reasoning_agreement_flag_threshold: float = field(default_factory=lambda: float(os.environ.get("REASONING_AGREEMENT_FLAG_THRESHOLD", "0.67")))

    def get_private_key(self) -> str:
        """Return private key contents from env var or file."""
        if self.epic_private_key:
            return self.epic_private_key
        if os.path.exists(self.epic_private_key_path):
            with open(self.epic_private_key_path) as f:
                return f.read()
        return ""

    def validate(self) -> list[str]:
        """Return list of missing required config values."""
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.epic_client_id:
            missing.append("EPIC_CLIENT_ID")
        if not self.get_private_key():
            missing.append("EPIC_PRIVATE_KEY or EPIC_PRIVATE_KEY_PATH")
        return missing
