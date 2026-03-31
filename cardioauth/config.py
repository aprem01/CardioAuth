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
    epic_base_url: str = field(default_factory=lambda: os.environ.get("EPIC_FHIR_BASE_URL", ""))
    epic_bearer_token: str = field(default_factory=lambda: os.environ.get("EPIC_BEARER_TOKEN", ""))

    # Vector store (Pinecone)
    pinecone_api_key: str = field(default_factory=lambda: os.environ.get("PINECONE_API_KEY", ""))
    pinecone_index: str = field(default_factory=lambda: os.environ.get("PINECONE_INDEX", "cardioauth-policies"))

    # Thresholds
    chart_confidence_threshold: float = 0.8
    approval_likelihood_threshold: float = 0.6

    def validate(self) -> list[str]:
        """Return list of missing required config values."""
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.epic_base_url:
            missing.append("EPIC_FHIR_BASE_URL")
        if not self.epic_bearer_token:
            missing.append("EPIC_BEARER_TOKEN")
        return missing
