"""Data models for CHART_AGENT output."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LabResult(BaseModel):
    name: str
    value: str
    date: str = Field(description="ISO 8601 date")
    unit: str
    flag: str = ""


class ImagingResult(BaseModel):
    type: str
    date: str = Field(description="ISO 8601 date")
    result_summary: str
    ordering_provider: str = ""


class Medication(BaseModel):
    name: str
    dose: str
    start_date: str = Field(description="ISO 8601 date")
    indication: str = ""


class ChartData(BaseModel):
    patient_id: str
    procedure_requested: str
    procedure_code: str = Field(description="CPT code")
    diagnosis_codes: list[str] = Field(description="ICD-10 codes, primary first")
    relevant_labs: list[LabResult] = []
    relevant_imaging: list[ImagingResult] = []
    relevant_medications: list[Medication] = []
    prior_treatments: list[str] = []
    comorbidities: list[str] = []
    attending_physician: str = ""
    insurance_id: str = ""
    payer_name: str = ""
    confidence_score: float = Field(ge=0.0, le=1.0, description="Data completeness 0-1")
    missing_fields: list[str] = []
