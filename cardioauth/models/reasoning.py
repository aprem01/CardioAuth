"""Data models for REASONING_AGENT output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CriterionEvaluation(BaseModel):
    criterion: str
    met: bool
    evidence: str = Field(description="Exact chart data that satisfies this criterion")
    confidence: float = Field(ge=0.0, le=1.0)


class CriterionGap(BaseModel):
    criterion: str
    gap: str = Field(description="What is missing")
    recommendation: str = Field(description="What the cardiologist should add")


class ReasoningResult(BaseModel):
    criteria_met: list[CriterionEvaluation] = []
    criteria_not_met: list[CriterionGap] = []
    approval_likelihood_score: float = Field(ge=0.0, le=1.0)
    approval_likelihood_label: Literal["HIGH", "MEDIUM", "LOW", "DO NOT SUBMIT"] = "LOW"
    missing_documentation: list[str] = []
    pa_narrative_draft: str = Field(description="Full narrative for submission, max 400 words")
    narrative_tone: Literal["clinical"] = "clinical"
    guideline_citations: list[str] = []
    cardiologist_review_flags: list[str] = []
