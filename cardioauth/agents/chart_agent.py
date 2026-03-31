"""CHART_AGENT - Clinical data extraction from Epic FHIR."""

from __future__ import annotations

import json
import logging

import anthropic

from cardioauth.config import Config
from cardioauth.fhir.client import FHIRClient
from cardioauth.models.chart import ChartData

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are CHART_AGENT, a clinical data extraction specialist for CardioAuth.

You receive raw FHIR resources for a patient and must extract all clinically
relevant data for a prior authorization request. Return a structured JSON object.

Rules:
- Only extract data that exists in the provided FHIR resources. Never infer or fill gaps.
- Flag any missing field that a payer is likely to require.
- If confidence score is below 0.8, list what is missing and why.
- All dates in ISO 8601 format.
- Do not include PHI in logs or debug output.

Return ONLY valid JSON matching this schema:
{
  "patient_id": "",
  "procedure_requested": "",
  "procedure_code": "",
  "diagnosis_codes": [],
  "relevant_labs": [{"name": "", "value": "", "date": "", "unit": "", "flag": ""}],
  "relevant_imaging": [{"type": "", "date": "", "result_summary": "", "ordering_provider": ""}],
  "relevant_medications": [{"name": "", "dose": "", "start_date": "", "indication": ""}],
  "prior_treatments": [],
  "comorbidities": [],
  "attending_physician": "",
  "insurance_id": "",
  "payer_name": "",
  "confidence_score": 0.0,
  "missing_fields": []
}
"""


class ChartAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.fhir = FHIRClient(config.epic_base_url, config.epic_bearer_token)

    def run(self, patient_id: str, procedure_code: str, payer_id: str) -> ChartData:
        logger.info("CHART_AGENT: extracting data for patient=%s procedure=%s", patient_id, procedure_code)

        fhir_bundle = self.fhir.get_patient_bundle(patient_id, procedure_code)

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Extract clinical data for prior authorization.\n"
                    f"Patient ID: {patient_id}\n"
                    f"Procedure code (CPT): {procedure_code}\n"
                    f"Payer ID: {payer_id}\n\n"
                    f"FHIR Resources:\n{json.dumps(fhir_bundle, indent=2, default=str)}"
                ),
            }],
        )

        raw = response.content[0].text
        data = json.loads(raw)
        chart = ChartData(**data)

        if chart.confidence_score < self.config.chart_confidence_threshold:
            logger.warning(
                "CHART_AGENT: low confidence %.2f — missing: %s",
                chart.confidence_score,
                chart.missing_fields,
            )

        return chart
