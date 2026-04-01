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

    # Thresholds
    chart_confidence_threshold: float = 0.8
    approval_likelihood_threshold: float = 0.6

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
