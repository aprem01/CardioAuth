"""Pre-bucket chart data by evidence type for strict criterion matching.

The matcher used to send the entire chart blob to Claude with a list of
criteria, and the LLM would semantic-match anything that looked clinical.
This led to two failure modes Peter identified:

  1. Reduced LVEF marked "met" with LVEF 50-55% (low normal)
  2. BMI/ECG criteria supported by echocardiogram reports

The fix: physically separate chart data into evidence-type buckets so
the matcher can ONLY look at the right slice for a given criterion type.
For BMI criteria the matcher only sees BMI/demographic fields. For ECG
criteria it only sees the ECG findings field. For lab criteria only labs.
For imaging only imaging. The LLM cannot accidentally cite the wrong
source because the wrong source isn't even in its context for that
evaluation.
"""

from __future__ import annotations

import re
from typing import Any


# ────────────────────────────────────────────────────────────────────────
# Quantitative threshold extraction helpers
# ────────────────────────────────────────────────────────────────────────


def _extract_lvef(chart: dict) -> dict | None:
    """Pull the LVEF value from imaging if present, with units and date."""
    for img in chart.get("relevant_imaging", []) or []:
        text = (img.get("result_summary", "") + " " + img.get("type", "")).lower()
        # Look for explicit LVEF mentions like "LVEF 35%", "EF 50%", "ejection fraction of 45"
        m = re.search(r"(?:lvef|ejection fraction|\bef\b)[\s:]*(?:of)?[\s:]*(\d{1,3})\s*[-–to]+\s*(\d{1,3})\s*%?", text)
        if m:
            low = int(m.group(1))
            high = int(m.group(2))
            return {"value_low": low, "value_high": high, "value_avg": (low + high) / 2,
                    "source_type": "echocardiogram",
                    "source_date": img.get("date", ""),
                    "raw": img.get("result_summary", "")[:200]}
        m = re.search(r"(?:lvef|ejection fraction|\bef\b)[\s:]*(?:of)?[\s:]*(\d{1,3})\s*%?", text)
        if m:
            v = int(m.group(1))
            # Sanity: LVEF is a percentage, so 0-100. Ignore implausible values.
            if 0 < v <= 100:
                return {"value": v, "source_type": "echocardiogram",
                        "source_date": img.get("date", ""),
                        "raw": img.get("result_summary", "")[:200]}
    return None


def _extract_bmi(chart: dict) -> dict | None:
    """Pull BMI from comorbidities, additional notes, or imaging."""
    haystacks = []
    for c in chart.get("comorbidities", []) or []:
        haystacks.append(("comorbidities", c))
    for img in chart.get("relevant_imaging", []) or []:
        haystacks.append(("imaging", img.get("result_summary", "")))
    for src, text in haystacks:
        m = re.search(r"bmi\s*(?:of)?[\s:]*(\d{1,2}(?:\.\d)?)", str(text).lower())
        if m:
            return {"value": float(m.group(1)), "source": src, "raw": str(text)[:200]}
    return None


def _extract_hr_percent(chart: dict) -> dict | None:
    """Pull % maximum predicted HR from prior stress test imaging."""
    for img in chart.get("relevant_imaging", []) or []:
        text = (img.get("result_summary", "") + " " + img.get("type", "")).lower()
        if "stress" not in text and "ett" not in text and "treadmill" not in text:
            continue
        m = re.search(r"(\d{1,3})\s*%\s*(?:of\s*)?(?:max|mphr|maximum predicted)", text)
        if m:
            return {"value": int(m.group(1)),
                    "source_date": img.get("date", ""),
                    "raw": img.get("result_summary", "")[:200]}
    return None


def _extract_ecg_findings(chart: dict) -> str:
    """Pull explicit ECG findings only (not echo, not stress test)."""
    findings = []
    for img in chart.get("relevant_imaging", []) or []:
        img_type = (img.get("type", "")).lower()
        if "ecg" in img_type or "ekg" in img_type or "12-lead" in img_type or "electrocardiogram" in img_type:
            findings.append(f"{img.get('type')} ({img.get('date', '')}): {img.get('result_summary', '')}")
    return "\n".join(findings) if findings else ""


def _extract_symptoms(chart: dict) -> str:
    """Pull symptom-relevant clinical notes (NOT diagnosis codes)."""
    bits = []
    # Look in comorbidities for symptom phrases (not diagnoses)
    for c in chart.get("comorbidities", []) or []:
        text = str(c).lower()
        if any(sym in text for sym in ["dyspnea", "chest pain", "syncope", "palpitations", "fatigue",
                                        "exertional", "orthopnea", "pnd", "angina", "lightheaded",
                                        "dizziness", "presyncope", "shortness of breath", "edema"]):
            bits.append(c)
    # Sometimes symptoms are mentioned in additional notes / prior treatments
    for pt in chart.get("prior_treatments", []) or []:
        if "symptom" in str(pt).lower() or "stress" in str(pt).lower():
            bits.append(pt)
    # Also check additional_notes for symptom mentions
    notes = chart.get("additional_notes", "") or ""
    if notes:
        notes_lower = notes.lower()
        if any(sym in notes_lower for sym in ["dyspnea", "chest pain", "syncope", "palpitations",
                                               "fatigue", "angina", "shortness of breath", "edema"]):
            bits.append(notes)
    return " | ".join(str(b) for b in bits) if bits else ""


def _extract_functional_class(chart: dict) -> str:
    """Pull NYHA, CCS angina class, or EHRA score from chart data.

    These functional classifications may appear in comorbidities,
    additional_notes, prior_treatments, or imaging result summaries.
    """
    patterns = [
        r"(?:nyha|new york heart association)\s*(?:class|functional class)?\s*(?:i{1,4}v?|[1-4])",
        r"(?:ccs|canadian cardiovascular society)\s*(?:class|angina class)?\s*(?:i{1,4}v?|[1-4])",
        r"(?:ehra|european heart rhythm association)\s*(?:class|score)?\s*(?:i{1,4}v?|[1-4])",
        r"class\s*(?:i{1,4}v?|[1-4])\s*(?:angina|heart failure|hf|symptoms?)",
        r"(?:functional class|functional capacity)\s*(?:i{1,4}v?|[1-4])",
    ]
    haystacks = []
    for c in chart.get("comorbidities", []) or []:
        haystacks.append(str(c))
    haystacks.append(chart.get("additional_notes", "") or "")
    for pt in chart.get("prior_treatments", []) or []:
        haystacks.append(str(pt))
    for img in chart.get("relevant_imaging", []) or []:
        haystacks.append(img.get("result_summary", ""))

    findings = []
    for text in haystacks:
        if not text:
            continue
        text_lower = text.lower()
        for pat in patterns:
            m = re.search(pat, text_lower)
            if m:
                # Extract context around the match
                start = max(0, m.start() - 20)
                end = min(len(text), m.end() + 20)
                findings.append(text[start:end].strip())
                break
    return " | ".join(findings) if findings else ""


def _extract_medication_trials(chart: dict) -> list[dict]:
    """Pull medication list with focus on cardiac drugs."""
    return [
        {
            "name": m.get("name", ""),
            "dose": m.get("dose", ""),
            "start_date": m.get("start_date", ""),
            "indication": m.get("indication", ""),
        }
        for m in (chart.get("relevant_medications", []) or [])
    ]


# ────────────────────────────────────────────────────────────────────────
# Extraction-status wrapper
# ────────────────────────────────────────────────────────────────────────


def _wrap_extraction(
    value: dict | None,
    metric: str,
    searched_in: str,
) -> dict:
    """Wrap a quantitative extraction result with explicit found/not_found status.

    Previously the extract functions returned None on miss, and the reasoner
    couldn't tell "evidence absent" from "I forgot to look" — so criteria
    requiring that evidence got silently marked not_met with no flag.

    This wrapper makes the absence visible in the buckets passed to the LLM:
      found:     {"status":"found", "value":..., "raw":..., "source_type":...}
      not_found: {"status":"not_found", "metric":"lvef", "searched_in":"..."}
    """
    if value is None:
        return {
            "status": "not_found",
            "metric": metric,
            "value": None,
            "searched_in": searched_in,
            "note": f"No {metric} value located in chart; criteria that require it should be flagged not_met with evidence_unavailable",
        }
    wrapped = dict(value)
    wrapped["status"] = "found"
    wrapped["metric"] = metric
    return wrapped


def _wrap_text_extraction(text: str, metric: str, searched_in: str) -> dict:
    """Same shape as _wrap_extraction but for free-text extractions."""
    if not text:
        return {
            "status": "not_found",
            "metric": metric,
            "value": "",
            "searched_in": searched_in,
        }
    return {"status": "found", "metric": metric, "value": text}


# ────────────────────────────────────────────────────────────────────────
# Main bucketing function
# ────────────────────────────────────────────────────────────────────────


def bucket_chart_evidence(chart: dict) -> dict[str, Any]:
    """Reshape chart data into typed evidence buckets.

    The output dict maps each evidence_type used in the taxonomy to a
    specific slice of chart data — and ONLY that slice. The matcher
    will be told to look only in the bucket matching the criterion's
    evidence_type, making cross-type contamination impossible.
    """
    lvef = _extract_lvef(chart)
    bmi = _extract_bmi(chart)
    hr_pct = _extract_hr_percent(chart)

    return {
        # Imaging — keep only actual imaging studies
        "imaging": [
            {
                "type": img.get("type", ""),
                "date": img.get("date", ""),
                "result_summary": img.get("result_summary", ""),
            }
            for img in (chart.get("relevant_imaging", []) or [])
            if img.get("type") and img.get("result_summary")
        ],

        # Labs — only lab values, never imaging or notes
        "lab": [
            {
                "name": l.get("name", ""),
                "value": l.get("value", ""),
                "unit": l.get("unit", ""),
                "date": l.get("date", ""),
                "flag": l.get("flag", ""),
            }
            for l in (chart.get("relevant_labs", []) or [])
            if l.get("name") and l.get("value")
        ],

        # ECG — only documented ECG findings (ECG modality, not echo or stress)
        "ecg": _extract_ecg_findings(chart),
        "ecg_extraction": _wrap_text_extraction(
            _extract_ecg_findings(chart), "ecg_findings",
            "relevant_imaging entries with type containing 'ECG' or 'EKG'",
        ),

        # Demographic — age, sex, BMI, body habitus
        "demographic": {
            "age": chart.get("age"),
            "sex": chart.get("sex"),
            "bmi": bmi,
            "body_habitus_notes": [c for c in (chart.get("comorbidities", []) or [])
                                   if "obesity" in str(c).lower() or "bmi" in str(c).lower()],
        },

        # Medications — for medical-therapy criteria
        "medication": _extract_medication_trials(chart),

        # Clinical notes — symptom documentation, NOT diagnoses
        "clinical_note": {
            "symptoms": _extract_symptoms(chart),
            "functional_class": _extract_functional_class(chart),
            "prior_treatments": chart.get("prior_treatments", []) or [],
            "comorbidities_full": chart.get("comorbidities", []) or [],
            "additional_notes": chart.get("additional_notes", "") or "",
            "office_notes": chart.get("office_notes", "") or chart.get("consultation_note", "") or "",
        },

        # Scores — extracted quantitative measurements. Each value is wrapped
        # with an explicit status so "not_found" is visible to the LLM, rather
        # than silently absent (which used to cause criteria to be marked
        # not_met without any evidence_unavailable flag).
        "score": {
            "lvef": _wrap_extraction(
                lvef, "lvef",
                "result_summary of relevant_imaging entries (patterns: 'LVEF X%', 'ejection fraction')",
            ),
            "bmi": _wrap_extraction(
                bmi, "bmi",
                "comorbidities list and relevant_imaging summaries (pattern: 'BMI X')",
            ),
            "max_hr_percent": _wrap_extraction(
                hr_pct, "max_hr_percent",
                "relevant_imaging summaries containing 'stress', 'ETT', or 'treadmill' (pattern: 'X% MPHR')",
            ),
        },

        # Diagnoses — separate so we don't conflate ICD-10 with symptom notes
        "diagnosis_codes": chart.get("diagnosis_codes", []) or [],
    }


def chart_section_for_evidence_type(buckets: dict, evidence_type: str) -> Any:
    """Return ONLY the section of chart data appropriate for an evidence_type."""
    return buckets.get(evidence_type, None)


# ────────────────────────────────────────────────────────────────────────
# Deterministic threshold validation
# ────────────────────────────────────────────────────────────────────────


def _is_found(wrapped: dict | None) -> bool:
    """A score-bucket entry counts as found only if it exists AND status=='found'.

    Handles both new wrapped format ({"status": ..., "value": ...}) and legacy
    bare-dict format during the transition.
    """
    if not wrapped:
        return False
    if "status" in wrapped:
        return wrapped.get("status") == "found"
    return True  # legacy format — assume found if dict exists


def validate_threshold(criterion_code: str, buckets: dict) -> dict | None:
    """For criteria with hard quantitative thresholds, validate
    deterministically before trusting the LLM's judgment.

    Returns:
      None if no threshold rule exists for this criterion
      {"met": True/False, "value": ..., "rule": "...", "evidence_status": "found|not_found"}
    """
    scores = buckets.get("score", {})

    # LVEF-002: LVEF ≤ 40% (reduced)
    if criterion_code == "LVEF-002":
        lvef = scores.get("lvef")
        if not _is_found(lvef):
            return {"met": False, "rule": "LVEF ≤ 40", "value": None,
                    "evidence_status": "not_found",
                    "explanation": "No LVEF documented in imaging"}
        v = lvef.get("value") or lvef.get("value_avg") or lvef.get("value_high")
        if v is None:
            return None
        return {"met": v <= 40, "rule": "LVEF ≤ 40", "value": v,
                "evidence_status": "found",
                "explanation": f"Documented LVEF {v}%, threshold ≤40%"}

    # LVEF-001: LVEF documented at all
    if criterion_code == "LVEF-001":
        lvef = scores.get("lvef")
        if not _is_found(lvef):
            return {"met": False, "rule": "LVEF documented", "value": None,
                    "evidence_status": "not_found",
                    "explanation": "No LVEF found in imaging"}
        v = lvef.get("value") or lvef.get("value_avg") or lvef.get("value_high")
        return {"met": v is not None, "rule": "LVEF documented", "value": v,
                "evidence_status": "found",
                "explanation": f"Documented LVEF {v}% from {lvef.get('source_type', 'imaging')} on {lvef.get('source_date', 'unknown date')}"}

    # BMI-001: BMI ≥ 35
    if criterion_code == "BMI-001":
        bmi = scores.get("bmi")
        if not _is_found(bmi):
            return {"met": False, "rule": "BMI ≥ 35 documented", "value": None,
                    "evidence_status": "not_found",
                    "explanation": "No BMI value found in chart"}
        v = bmi.get("value")
        return {"met": v is not None and v >= 35, "rule": "BMI ≥ 35", "value": v,
                "evidence_status": "found",
                "explanation": f"Documented BMI {v}, threshold ≥35"}

    # NDX-002: Submaximal HR <85% MPHR
    if criterion_code == "NDX-002":
        hr = scores.get("max_hr_percent")
        if not _is_found(hr):
            return None  # not necessarily missing, may not have had stress test
        v = hr.get("value")
        if v is None:
            return None
        return {"met": v < 85, "rule": "HR <85% MPHR", "value": v,
                "evidence_status": "found",
                "explanation": f"Achieved {v}% MPHR, threshold <85%"}

    # ECG-001: LBBB on ECG
    if criterion_code == "ECG-001":
        ecg_text = (buckets.get("ecg") or "").lower()
        if not ecg_text:
            return {"met": False, "rule": "LBBB on ECG", "value": None,
                    "explanation": "No ECG findings documented in chart"}
        has_lbbb = "lbbb" in ecg_text or "left bundle branch block" in ecg_text
        return {"met": has_lbbb, "rule": "LBBB on ECG", "value": ecg_text[:100],
                "explanation": f"ECG findings: {ecg_text[:120]}"}

    # ECG-002: Paced rhythm
    if criterion_code == "ECG-002":
        ecg_text = (buckets.get("ecg") or "").lower()
        if not ecg_text:
            return {"met": False, "rule": "Paced rhythm on ECG", "value": None,
                    "explanation": "No ECG findings documented"}
        has_paced = "paced" in ecg_text or "pacemaker rhythm" in ecg_text or "ventricular pacing" in ecg_text
        return {"met": has_paced, "rule": "Paced rhythm", "value": ecg_text[:100],
                "explanation": f"ECG findings: {ecg_text[:120]}"}

    return None
