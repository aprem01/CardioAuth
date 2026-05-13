"""Map Epic FHIR resources into our PatientCorpus.

The goal: given an Epic Bundle (whatever shape Epic's R4 search returns
for one patient), produce a `PatientCorpus` the lean pipeline can run
retrieval against.

What we extract:
  - DocumentReference → one CorpusDocument per attachment.
    The `type.coding.code` (LOINC) tells us whether it's a discharge
    summary, ECG narrative, stress-test report, cath note, etc.
  - Encounter → one prior_encounter CorpusDocument per encounter that
    has a `reasonReference` or significant narrative.

Note: this module does NOT fetch Binary attachments — that's a
network call the caller decides whether to make (Binary fetches are
expensive and only matter for retrieval coverage). The mapper accepts
already-fetched text per DocumentReference and falls back to the
attachment's inline `data` if present.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from cardioauth.patient_corpus import CorpusDocument, DocType, PatientCorpus

logger = logging.getLogger(__name__)


# LOINC code → our coarse DocType. We don't need 1:1 mapping coverage —
# unknown LOINC codes fall through to "outside_records" which still
# participates in retrieval.
_LOINC_TO_DOCTYPE: dict[str, DocType] = {
    # Stress / exercise testing
    "11502-2": "stress_test",   # Laboratory report (often where stress lives)
    "29554-3": "stress_test",   # Cardiac exercise study
    "18746-1": "stress_test",   # Cardiac stress study report
    # ECG
    "11524-6": "ecg_report",    # EKG study
    "9279-1":  "ecg_report",    # 12-lead EKG narrative
    # Echo
    "29274-8": "echo_report",   # Echocardiography study
    "34038-0": "echo_report",   # Echo report
    # Cath
    "18748-7": "cath_report",   # Cardiac catheterization study
    "28010-7": "cath_report",   # Cath report
    # Office / encounter notes
    "11506-3": "prior_encounter",  # Progress note
    "34117-2": "prior_encounter",  # History and physical
    "18842-5": "prior_encounter",  # Discharge summary
    # Imaging
    "18747-9": "imaging_report",   # Diagnostic imaging study
    "30954-2": "imaging_report",   # Relevant diagnostic tests/laboratory data
}


def _doctype_from_loinc(loinc_code: str) -> DocType:
    """Pick a DocType bucket from a LOINC code. Unknown → outside_records."""
    return _LOINC_TO_DOCTYPE.get(loinc_code, "outside_records")


def _doctype_from_text(display: str) -> DocType:
    """Last-ditch type guess from the human-readable type display text.

    Epic sometimes emits no LOINC code but a useful display string like
    "ECG Report" or "Echocardiogram". Coverage here is intentionally small —
    real production will hit LOINC first.
    """
    s = (display or "").lower()
    if "stress" in s or "treadmill" in s or "exercise" in s:
        return "stress_test"
    if "ecg" in s or "ekg" in s or "electrocardio" in s:
        return "ecg_report"
    if "echo" in s:
        return "echo_report"
    if "cath" in s or "catheter" in s:
        return "cath_report"
    if "discharge" in s or "progress" in s or "h&p" in s or "history and physical" in s:
        return "prior_encounter"
    if "imaging" in s or "ct " in s or "mri" in s or "nuclear" in s:
        return "imaging_report"
    return "outside_records"


def _extract_attachment_text(attachment: dict) -> str:
    """Pull text out of a DocumentReference.content[].attachment.

    The attachment may have:
      - `data`: base64-encoded inline body (Epic does this for short docs)
      - `url`: pointer to a Binary resource (Epic does this for everything else)

    For url-only attachments we cannot fetch here — caller must resolve
    Binary refs separately and inject the text via `attachment_text_resolver`.
    """
    data_b64 = attachment.get("data", "")
    if not data_b64:
        return ""
    try:
        raw = base64.b64decode(data_b64)
        # Most clinical attachments are plain text or HTML; ignore non-text
        # mime types — caller can layer in PDF parsing if needed.
        ctype = (attachment.get("contentType") or "").lower()
        if "text" in ctype or "xml" in ctype or "html" in ctype or not ctype:
            return raw.decode("utf-8", errors="replace")
        return ""
    except Exception as e:
        logger.warning("DocumentReference attachment decode failed: %s", e)
        return ""


def document_reference_to_corpus_doc(
    docref: dict,
    *,
    text_override: str = "",
) -> CorpusDocument | None:
    """Map one FHIR DocumentReference resource to a CorpusDocument.

    Returns None if the resource has no usable text (no inline data and
    no text_override provided).
    """
    doc_id = docref.get("id", "")
    if not doc_id:
        return None

    # Type — prefer LOINC, fall back to display
    doctype: DocType = "outside_records"
    type_obj = docref.get("type") or {}
    for coding in (type_obj.get("coding") or []):
        if coding.get("system") == "http://loinc.org" and coding.get("code"):
            doctype = _doctype_from_loinc(coding["code"])
            break
    if doctype == "outside_records":
        display = type_obj.get("text") or ""
        if not display and (type_obj.get("coding") or []):
            display = type_obj["coding"][0].get("display", "")
        doctype = _doctype_from_text(display)

    # Date — date on DocumentReference is `date` (ISO 8601 datetime)
    date_str = (docref.get("date") or "")[:10]

    # Title — author + type display is usually enough
    title = type_obj.get("text") or ""
    if not title and (type_obj.get("coding") or []):
        title = type_obj["coding"][0].get("display", "")
    if not title:
        title = doctype.replace("_", " ").title()

    # Source — facility, if available
    source = ""
    context = docref.get("context") or {}
    facility = (context.get("facilityType") or {}).get("text", "")
    if facility:
        source = facility

    # Text
    text = text_override
    if not text:
        for content in (docref.get("content") or []):
            text = _extract_attachment_text(content.get("attachment") or {})
            if text:
                break

    if not text.strip():
        # No retrievable body — skip; participating in BM25 would only
        # introduce noise.
        return None

    return CorpusDocument(
        doc_id=doc_id,
        doc_type=doctype,
        date=date_str,
        title=title,
        text=text,
        source=source,
    )


def encounter_to_corpus_doc(encounter: dict) -> CorpusDocument | None:
    """Map a FHIR Encounter to a prior_encounter CorpusDocument, if it
    has enough narrative content to bother indexing.

    Encounters are mostly metadata (period, status, location). What we
    care about: `reasonReference[].display`, `reason[].text`,
    `diagnosis[].condition.display`. We synthesize a short narrative
    text from these so retrieval can hit them.
    """
    enc_id = encounter.get("id", "")
    if not enc_id:
        return None

    parts: list[str] = []

    # Reason
    for reason in (encounter.get("reasonReference") or []):
        d = reason.get("display") or ""
        if d:
            parts.append(f"Reason: {d}")
    for r in (encounter.get("reason") or []):
        t = (r.get("text") or "").strip()
        if t:
            parts.append(f"Reason: {t}")

    # Diagnoses
    for dx in (encounter.get("diagnosis") or []):
        cond = (dx.get("condition") or {}).get("display") or ""
        if cond:
            parts.append(f"Diagnosis: {cond}")

    # Encounter type
    for t in (encounter.get("type") or []):
        for coding in (t.get("coding") or []):
            d = coding.get("display") or ""
            if d:
                parts.append(f"Type: {d}")
                break

    if not parts:
        # No meaningful content — encounter is just a billing row, skip.
        return None

    period = encounter.get("period") or {}
    date_str = (period.get("start") or "")[:10]

    return CorpusDocument(
        doc_id=enc_id,
        doc_type="prior_encounter",
        date=date_str,
        title=f"Encounter {date_str}".strip(),
        text="\n".join(parts),
        source=(encounter.get("serviceProvider") or {}).get("display", ""),
    )


def bundle_to_patient_corpus(
    bundle: dict[str, Any],
    *,
    current_note_text: str = "",
    current_note_date: str = "",
    attachment_texts: dict[str, str] | None = None,
) -> PatientCorpus:
    """Build a PatientCorpus from a `FHIRClient.get_patient_bundle()` result.

    `bundle` has the shape: {"patient_id": ..., "resources": {ResType: SearchSet}}

    `attachment_texts` is an optional map from DocumentReference.id → already-
    fetched plaintext, for attachments whose body lives behind a Binary URL
    that the caller has resolved separately.
    """
    patient_id = bundle.get("patient_id", "")
    resources = bundle.get("resources", {}) or {}
    attachment_texts = attachment_texts or {}

    documents: list[CorpusDocument] = []

    # Current encounter note (if caller provided one)
    if current_note_text.strip():
        documents.append(CorpusDocument(
            doc_id="current",
            doc_type="current_note",
            date=current_note_date,
            title="Current encounter note",
            text=current_note_text,
        ))

    # DocumentReferences
    docref_bundle = resources.get("DocumentReference") or {}
    for entry in (docref_bundle.get("entry") or []):
        docref = entry.get("resource") or {}
        if docref.get("resourceType") != "DocumentReference":
            continue
        cd = document_reference_to_corpus_doc(
            docref,
            text_override=attachment_texts.get(docref.get("id", ""), ""),
        )
        if cd:
            documents.append(cd)

    # Encounters
    encounter_bundle = resources.get("Encounter") or {}
    for entry in (encounter_bundle.get("entry") or []):
        encounter = entry.get("resource") or {}
        if encounter.get("resourceType") != "Encounter":
            continue
        cd = encounter_to_corpus_doc(encounter)
        if cd:
            documents.append(cd)

    return PatientCorpus(patient_id=patient_id, documents=documents)
