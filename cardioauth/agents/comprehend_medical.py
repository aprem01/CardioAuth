"""AWS Comprehend Medical entity extraction — HIPAA-eligible preprocessing.

This module wraps AWS Comprehend Medical's DetectEntities_v2 API to extract
structured clinical entities from free text. It serves as an optional
preprocessing layer that runs BEFORE the Claude reasoning call, producing
cleaner typed entity data for the evidence bucketing pipeline.

Why this matters:
  - Comprehend Medical is purpose-built for clinical NLP and handles
    medication dosing, lab values with units, ECG patterns, and LVEF
    extraction more reliably than a general LLM on first pass.
  - It is HIPAA-eligible with a BAA — critical when connecting to
    real Epic FHIR data with real PHI.
  - Free tier: 25,000 units/month (each unit = 100 UTF-8 characters),
    roughly 50-100 clinical documents/month at no cost.

Architecture:
  Raw clinical text (FHIR notes, uploaded documents)
      → Comprehend Medical DetectEntities_v2
      → Structured entities (medications, dx, labs, procedures, anatomy)
      → Merged into ChartData fields
      → Fed into evidence_buckets.bucket_chart_evidence()
      → Claude REASONING_AGENT + TAXONOMY_MATCHER
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ComprehendEntity:
    """A single clinical entity extracted by Comprehend Medical."""
    text: str
    category: str          # MEDICATION, MEDICAL_CONDITION, TEST_TREATMENT_PROCEDURE, etc.
    entity_type: str       # DX_NAME, DOSAGE, STRENGTH, ROUTE_OR_MODE, etc.
    score: float           # 0-1 confidence
    traits: list[str] = field(default_factory=list)   # NEGATION, DIAGNOSIS, SIGN, SYMPTOM
    attributes: list[dict] = field(default_factory=list)


@dataclass
class ComprehendExtractionResult:
    """Structured output from Comprehend Medical processing."""
    diagnoses: list[dict] = field(default_factory=list)        # [{text, icd_code?, confidence}]
    medications: list[dict] = field(default_factory=list)      # [{name, dose, route, frequency}]
    lab_values: list[dict] = field(default_factory=list)       # [{name, value, unit}]
    procedures: list[dict] = field(default_factory=list)       # [{name, date?}]
    anatomy: list[dict] = field(default_factory=list)          # [{system, site}]
    symptoms: list[str] = field(default_factory=list)
    ecg_findings: list[str] = field(default_factory=list)
    lvef: dict | None = None                                   # {value, raw_text}
    bmi: dict | None = None                                    # {value}
    functional_class: str = ""                                 # NYHA/CCS/EHRA
    raw_entities: list[dict] = field(default_factory=list)     # Full entity dump for debugging
    engine: str = "aws_comprehend_medical"

    def to_dict(self) -> dict:
        return {
            "diagnoses": self.diagnoses,
            "medications": self.medications,
            "lab_values": self.lab_values,
            "procedures": self.procedures,
            "anatomy": self.anatomy,
            "symptoms": self.symptoms,
            "ecg_findings": self.ecg_findings,
            "lvef": self.lvef,
            "bmi": self.bmi,
            "functional_class": self.functional_class,
            "engine": self.engine,
        }


# ────────────────────────────────────────────────────────────────────────
# Core extraction
# ────────────────────────────────────────────────────────────────────────


def _get_comprehend_client():
    """Create a boto3 ComprehendMedical client.

    Reads credentials from:
      - AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (explicit)
      - AWS_PROFILE (named profile)
      - EC2/ECS instance role (automatic on AWS infra)
      - ~/.aws/credentials (default profile)
    """
    try:
        import boto3
    except ImportError:
        raise RuntimeError(
            "boto3 is not installed. Run: pip install boto3\n"
            "Then set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, or configure "
            "a named profile with: aws configure"
        )

    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("comprehendmedical", region_name=region)


def extract_entities(text: str) -> ComprehendExtractionResult:
    """Run AWS Comprehend Medical DetectEntities_v2 on clinical text.

    The API accepts up to 20,000 UTF-8 characters per call. For longer
    documents, we chunk and merge.

    Returns a ComprehendExtractionResult with structured clinical entities.
    """
    if not text or not text.strip():
        return ComprehendExtractionResult()

    client = _get_comprehend_client()

    # Comprehend Medical limit: 20,000 bytes UTF-8
    MAX_CHARS = 19500
    chunks = _chunk_text(text, MAX_CHARS)

    all_entities: list[dict] = []
    for chunk in chunks:
        try:
            response = client.detect_entities_v2(Text=chunk)
            all_entities.extend(response.get("Entities", []))
        except Exception as e:
            logger.warning("Comprehend Medical API error: %s", e)
            # Partial results are still useful
            continue

    if not all_entities:
        logger.info("Comprehend Medical: no entities extracted")
        return ComprehendExtractionResult()

    logger.info("Comprehend Medical: extracted %d entities", len(all_entities))
    return _structure_entities(all_entities, text)


def _chunk_text(text: str, max_chars: int) -> list[str]:
    """Split text into chunks that fit the API limit, on paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > max_chars:
            if current:
                chunks.append(current)
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current:
        chunks.append(current)
    return chunks


# ────────────────────────────────────────────────────────────────────────
# Entity structuring
# ────────────────────────────────────────────────────────────────────────

# ECG-specific terms
_ECG_TERMS = {
    "lbbb", "left bundle branch block", "rbbb", "right bundle branch block",
    "paced rhythm", "pacemaker rhythm", "ventricular pacing",
    "atrial fibrillation", "atrial flutter", "wpw", "pre-excitation",
    "lvh", "left ventricular hypertrophy", "st depression", "st elevation",
    "sinus rhythm", "sinus bradycardia", "sinus tachycardia",
    "first degree av block", "second degree", "third degree",
    "prolonged qt", "t-wave inversion", "q waves",
}

_SYMPTOM_TERMS = {
    "dyspnea", "chest pain", "angina", "syncope", "palpitations",
    "fatigue", "orthopnea", "edema", "shortness of breath",
    "dizziness", "lightheaded", "presyncope", "exertional",
}


def _structure_entities(entities: list[dict], original_text: str) -> ComprehendExtractionResult:
    """Map Comprehend Medical entities into our structured result."""
    result = ComprehendExtractionResult(raw_entities=entities)

    # Group attributes by entity
    for ent in entities:
        category = ent.get("Category", "")
        ent_type = ent.get("Type", "")
        text = ent.get("Text", "")
        score = ent.get("Score", 0)
        traits = [t.get("Name", "") for t in ent.get("Traits", [])]
        attributes = ent.get("Attributes", [])

        # Skip negated entities
        if "NEGATION" in traits:
            continue

        # ── Medications ──
        if category == "MEDICATION":
            med = {"name": text, "confidence": score}
            for attr in attributes:
                attr_type = attr.get("Type", "")
                if attr_type == "DOSAGE":
                    med["dose"] = attr.get("Text", "")
                elif attr_type == "ROUTE_OR_MODE":
                    med["route"] = attr.get("Text", "")
                elif attr_type == "FREQUENCY":
                    med["frequency"] = attr.get("Text", "")
                elif attr_type == "STRENGTH":
                    med["strength"] = attr.get("Text", "")
            result.medications.append(med)

        # ── Diagnoses ──
        elif category == "MEDICAL_CONDITION":
            if ent_type == "DX_NAME" or "DIAGNOSIS" in traits:
                dx = {"text": text, "confidence": score}
                for attr in attributes:
                    if attr.get("Type") == "ICD_10_CM_CODE":
                        dx["icd_code"] = attr.get("Text", "")
                result.diagnoses.append(dx)
            elif "SIGN" in traits or "SYMPTOM" in traits:
                result.symptoms.append(text)
            else:
                # Check if it's a symptom by keyword
                text_lower = text.lower()
                if any(s in text_lower for s in _SYMPTOM_TERMS):
                    result.symptoms.append(text)
                else:
                    result.diagnoses.append({"text": text, "confidence": score})

        # ── Tests / Procedures ──
        elif category == "TEST_TREATMENT_PROCEDURE":
            if ent_type == "TEST_NAME":
                # Check for lab values with numeric attributes
                test_entry = {"name": text, "confidence": score}
                for attr in attributes:
                    attr_type = attr.get("Type", "")
                    if attr_type == "TEST_VALUE":
                        test_entry["value"] = attr.get("Text", "")
                    elif attr_type == "TEST_UNIT":
                        test_entry["unit"] = attr.get("Text", "")

                if "value" in test_entry:
                    result.lab_values.append(test_entry)
                else:
                    # It's a procedure, not a lab test
                    result.procedures.append(test_entry)

            elif ent_type == "PROCEDURE_NAME":
                result.procedures.append({"name": text, "confidence": score})

            elif ent_type == "TREATMENT_NAME":
                result.procedures.append({"name": text, "confidence": score, "is_treatment": True})

        # ── Anatomy ──
        elif category == "ANATOMY":
            result.anatomy.append({"system": ent_type, "site": text})

    # ── Post-processing: ECG findings ──
    text_lower = original_text.lower()
    for term in _ECG_TERMS:
        if term in text_lower:
            # Extract context around the match
            idx = text_lower.find(term)
            start = max(0, idx - 40)
            end = min(len(original_text), idx + len(term) + 40)
            context = original_text[start:end].strip()
            if context not in result.ecg_findings:
                result.ecg_findings.append(context)

    # ── Post-processing: LVEF extraction ──
    lvef_match = re.search(
        r"(?:lvef|ejection fraction|\bef\b)[\s:]*(?:of)?[\s:]*(\d{1,3})\s*[-–to]+\s*(\d{1,3})\s*%?",
        text_lower,
    )
    if lvef_match:
        low, high = int(lvef_match.group(1)), int(lvef_match.group(2))
        result.lvef = {"value_low": low, "value_high": high, "value_avg": (low + high) / 2,
                       "raw_text": lvef_match.group(0)}
    else:
        lvef_match = re.search(
            r"(?:lvef|ejection fraction|\bef\b)[\s:]*(?:of)?[\s:]*(\d{1,3})\s*%",
            text_lower,
        )
        if lvef_match:
            result.lvef = {"value": int(lvef_match.group(1)), "raw_text": lvef_match.group(0)}

    # ── Post-processing: BMI ──
    bmi_match = re.search(r"bmi\s*(?:of)?[\s:]*(\d{1,2}(?:\.\d)?)", text_lower)
    if bmi_match:
        result.bmi = {"value": float(bmi_match.group(1))}

    # ── Post-processing: Functional class (NYHA/CCS/EHRA) ──
    fc_patterns = [
        r"(?:nyha|new york heart association)\s*(?:class|functional class)?\s*(?:i{1,4}v?|[1-4])",
        r"(?:ccs|canadian cardiovascular society)\s*(?:class|angina class)?\s*(?:i{1,4}v?|[1-4])",
        r"(?:ehra|european heart rhythm association)\s*(?:class|score)?\s*(?:i{1,4}v?|[1-4])",
    ]
    for pat in fc_patterns:
        m = re.search(pat, text_lower)
        if m:
            result.functional_class = m.group(0).strip()
            break

    return result


# ────────────────────────────────────────────────────────────────────────
# Merge into ChartData
# ────────────────────────────────────────────────────────────────────────


def merge_comprehend_into_chart(
    comprehend: ComprehendExtractionResult,
    chart_dict: dict,
) -> dict:
    """Merge Comprehend Medical entities into an existing chart data dict.

    This is additive — Comprehend fills gaps but doesn't overwrite existing
    data that the user or FHIR already provided. The result feeds into
    bucket_chart_evidence() which then goes to Claude for reasoning.
    """
    # ── Medications: add any new ones Comprehend found ──
    existing_med_names = {m.get("name", "").lower() for m in chart_dict.get("relevant_medications", [])}
    for med in comprehend.medications:
        if med["name"].lower() not in existing_med_names:
            chart_dict.setdefault("relevant_medications", []).append({
                "name": med["name"],
                "dose": med.get("dose", "") or med.get("strength", ""),
                "start_date": "",
                "indication": "",
                "_source": "comprehend_medical",
            })

    # ── Lab values: add any new ones ──
    existing_lab_names = {l.get("name", "").lower() for l in chart_dict.get("relevant_labs", [])}
    for lab in comprehend.lab_values:
        if lab["name"].lower() not in existing_lab_names:
            chart_dict.setdefault("relevant_labs", []).append({
                "name": lab["name"],
                "value": lab.get("value", ""),
                "unit": lab.get("unit", ""),
                "date": "",
                "_source": "comprehend_medical",
            })

    # ── Diagnoses: add any new ICD codes ──
    existing_dx = set(chart_dict.get("diagnosis_codes", []))
    for dx in comprehend.diagnoses:
        if dx.get("icd_code") and dx["icd_code"] not in existing_dx:
            chart_dict.setdefault("diagnosis_codes", []).append(dx["icd_code"])

    # ── Comorbidities: add any Comprehend-detected diagnoses as text ──
    existing_comorbidities = {c.lower() for c in chart_dict.get("comorbidities", [])}
    for dx in comprehend.diagnoses:
        if dx["text"].lower() not in existing_comorbidities and dx.get("confidence", 0) > 0.7:
            chart_dict.setdefault("comorbidities", []).append(
                f"{dx['text']} [Comprehend Medical, {dx.get('confidence', 0):.0%}]"
            )

    # ── Symptoms: merge into additional_notes ──
    if comprehend.symptoms:
        existing_notes = chart_dict.get("additional_notes", "") or ""
        new_symptoms = [s for s in comprehend.symptoms
                        if s.lower() not in existing_notes.lower()]
        if new_symptoms:
            sym_text = "Comprehend Medical detected symptoms: " + ", ".join(new_symptoms)
            chart_dict["additional_notes"] = (existing_notes + "\n" + sym_text).strip()

    # ── ECG findings: add to imaging if we found ECG-specific patterns ──
    if comprehend.ecg_findings:
        has_ecg_imaging = any(
            "ecg" in (img.get("type", "")).lower() or "ekg" in (img.get("type", "")).lower()
            for img in chart_dict.get("relevant_imaging", [])
        )
        if not has_ecg_imaging:
            chart_dict.setdefault("relevant_imaging", []).append({
                "type": "ECG (12-lead)",
                "date": "",
                "result_summary": "; ".join(comprehend.ecg_findings),
                "_source": "comprehend_medical",
            })

    # ── LVEF: add if not already present ──
    if comprehend.lvef:
        has_lvef = any(
            "lvef" in (img.get("result_summary", "") + img.get("type", "")).lower()
            or "ejection fraction" in (img.get("result_summary", "") + img.get("type", "")).lower()
            for img in chart_dict.get("relevant_imaging", [])
        )
        if not has_lvef:
            val = comprehend.lvef.get("value") or comprehend.lvef.get("value_avg")
            chart_dict.setdefault("relevant_imaging", []).append({
                "type": "Echocardiogram (LVEF)",
                "date": "",
                "result_summary": f"LVEF {val}% ({comprehend.lvef.get('raw_text', '')})",
                "_source": "comprehend_medical",
            })

    # ── Functional class: add to notes ──
    if comprehend.functional_class:
        existing_notes = chart_dict.get("additional_notes", "") or ""
        if comprehend.functional_class.lower() not in existing_notes.lower():
            chart_dict["additional_notes"] = (
                existing_notes + f"\nFunctional class: {comprehend.functional_class} [Comprehend Medical]"
            ).strip()

    # Tag the chart with extraction engine info
    chart_dict["_comprehend_enriched"] = True
    chart_dict["_comprehend_entity_count"] = len(comprehend.raw_entities)

    return chart_dict


# ────────────────────────────────────────────────────────────────────────
# Convenience: extract from chart text fields
# ────────────────────────────────────────────────────────────────────────


def enrich_chart_with_comprehend(chart_dict: dict) -> dict:
    """Extract text from all chart fields, run Comprehend Medical, merge back.

    Call this BEFORE bucket_chart_evidence() in the pipeline to get
    Comprehend's structured entities into the evidence buckets.
    """
    # Build a single clinical text from all available chart fields
    text_parts = []

    for img in chart_dict.get("relevant_imaging", []):
        summary = img.get("result_summary", "")
        if summary:
            text_parts.append(f"{img.get('type', 'Imaging')}: {summary}")

    for lab in chart_dict.get("relevant_labs", []):
        text_parts.append(f"{lab.get('name', '')}: {lab.get('value', '')} {lab.get('unit', '')}")

    for med in chart_dict.get("relevant_medications", []):
        text_parts.append(f"{med.get('name', '')} {med.get('dose', '')} for {med.get('indication', '')}")

    for c in chart_dict.get("comorbidities", []):
        text_parts.append(str(c))

    for pt in chart_dict.get("prior_treatments", []):
        text_parts.append(str(pt))

    notes = chart_dict.get("additional_notes", "")
    if notes:
        text_parts.append(notes)

    office = chart_dict.get("office_notes", "") or chart_dict.get("consultation_note", "")
    if office:
        text_parts.append(office)

    full_text = "\n".join(text_parts)
    if len(full_text.strip()) < 50:
        logger.info("Comprehend Medical: insufficient text to extract from (%d chars)", len(full_text))
        return chart_dict

    logger.info("Comprehend Medical: processing %d chars from chart fields", len(full_text))
    comprehend_result = extract_entities(full_text)

    return merge_comprehend_into_chart(comprehend_result, chart_dict)


# ────────────────────────────────────────────────────────────────────────
# Availability check
# ────────────────────────────────────────────────────────────────────────


def is_comprehend_available() -> bool:
    """Check if AWS Comprehend Medical credentials are configured."""
    try:
        import boto3
        # Check for explicit credentials or default chain
        session = boto3.Session()
        creds = session.get_credentials()
        return creds is not None
    except Exception:
        return False
