"""Data models for SUBMISSION_AGENT output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    action: str
    timestamp: str
    actor: str


class SubmissionResult(BaseModel):
    submission_id: str
    payer: str
    procedure: str
    patient_id: str
    submission_channel: str
    submission_timestamp: str
    confirmation_number: str = ""
    expected_decision_date: str = ""
    follow_up_scheduled: str = ""
    status: Literal["submitted", "pending_approval", "error"] = "submitted"
    audit_trail: list[AuditEntry] = []


class LearningPayload(BaseModel):
    payer: str
    procedure: str
    criteria_met_at_submission: list[str] = []
    outcome: str
    denial_reason: str = ""
    appeal_outcome: str = ""


class OutcomeResult(BaseModel):
    submission_id: str
    outcome: Literal["APPROVED", "DENIED", "PENDING", "INFO_REQUESTED"]
    denial_reason: str = ""
    appeal_recommended: bool = False
    appeal_draft: str = ""
    appeal_deadline: str = ""
    outcome_timestamp: str
    learning_payload: LearningPayload | None = None
