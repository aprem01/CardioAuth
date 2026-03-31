"""Data models for POLICY_AGENT output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ClinicalCriterion(BaseModel):
    criterion: str = Field(description="What the payer requires")
    required: bool
    evidence_type: str = Field(description="lab, imaging, clinical note, etc")
    acceptable_values: str = ""


class PolicyData(BaseModel):
    payer: str
    procedure: str
    cpt_code: str
    auth_required: bool | None = Field(
        default=None,
        description="True/False/None(unknown - escalate to human)",
    )
    clinical_criteria: list[ClinicalCriterion] = []
    documentation_required: list[str] = []
    submission_format: Literal["portal", "fax", "clearinghouse", "api", "unknown"] = "unknown"
    typical_turnaround_days: int = 0
    common_denial_reasons: list[str] = []
    appeal_success_factors: list[str] = []
    policy_source: str = ""
    policy_last_updated: str = ""
