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


# ── Binary attachment resolution ────────────────────────────────────────
# Epic returns DocumentReference attachments as Binary URL refs, not
# inline data. Without resolving them, the corpus only sees encounter
# headers and document metadata — never the actual clinical note text.
# This is what made Peter's first real test produce a packet with blank
# fields. resolve_document_attachments() does the network fetches.

import re as _re
from html.parser import HTMLParser as _HTMLParser


class _TextExtractor(_HTMLParser):
    """Minimal HTML → plaintext. Clinical notes from Epic come as
    text/html; we want the readable text, not the markup."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # Collapse runs of whitespace within lines, keep paragraph breaks
        lines = [_re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _strip_html(html: str) -> str:
    """Best-effort HTML → plaintext for clinical note bodies."""
    try:
        parser = _TextExtractor()
        parser.feed(html)
        return parser.text()
    except Exception as e:
        logger.warning("HTML strip failed, returning raw: %s", e)
        return html


def _decode_attachment_bytes(content_type: str, raw: bytes) -> str:
    """Turn fetched Binary bytes into plaintext based on content type.

    Handles text/html (strip tags), text/plain, text/xml. RTF and PDF
    are skipped — Epic always offers an HTML variant alongside RTF, and
    PDF parsing isn't wired yet.
    """
    ct = (content_type or "").lower()
    if "rtf" in ct or "pdf" in ct:
        return ""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    if "html" in ct or text.lstrip().lower().startswith(("<!doctype", "<html", "<div", "<body")):
        return _strip_html(text)
    return text.strip()


def resolve_document_attachments(
    bundle: dict[str, Any],
    fhir_client: Any,
    *,
    max_docs: int = 25,
) -> dict[str, str]:
    """Fetch the actual body text for every DocumentReference in the bundle.

    For each DocumentReference, pick the best attachment (prefer HTML over
    RTF/PDF), fetch the Binary it points at, decode to plaintext. Returns
    {docref_id: text} for feeding into bundle_to_patient_corpus's
    attachment_texts param.

    Network-heavy — capped at max_docs. Failures on individual documents
    are logged and skipped, never raised: a partial corpus beats no corpus.
    """
    resources = bundle.get("resources", {}) or {}
    docref_bundle = resources.get("DocumentReference") or {}
    entries = docref_bundle.get("entry") or []

    resolved: dict[str, str] = {}
    fetched = 0
    for entry in entries:
        if fetched >= max_docs:
            break
        docref = entry.get("resource") or {}
        if docref.get("resourceType") != "DocumentReference":
            continue
        doc_id = docref.get("id", "")
        if not doc_id:
            continue

        # Pick the best attachment: prefer text/html, then anything with a
        # url, skip rtf/pdf if an html sibling exists.
        contents = docref.get("content") or []
        candidates = [c.get("attachment") or {} for c in contents]
        html_att = next(
            (a for a in candidates if "html" in (a.get("contentType") or "").lower() and a.get("url")),
            None,
        )
        any_url_att = next((a for a in candidates if a.get("url")), None)
        inline_att = next((a for a in candidates if a.get("data")), None)

        text = ""
        chosen = html_att or any_url_att
        if chosen and chosen.get("url"):
            try:
                ctype, raw = fhir_client.fetch_binary(chosen["url"])
                text = _decode_attachment_bytes(ctype or chosen.get("contentType", ""), raw)
                fetched += 1
            except Exception as e:
                logger.warning("Binary fetch failed for DocumentReference %s: %s", doc_id, e)
        elif inline_att and inline_att.get("data"):
            text = _extract_attachment_text(inline_att)

        if text.strip():
            resolved[doc_id] = text

    logger.info("Resolved %d/%d DocumentReference bodies (fetched %d Binaries)",
                len(resolved), len(entries), fetched)
    return resolved
