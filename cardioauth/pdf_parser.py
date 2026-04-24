"""PDF → text parser for chart + policy ingest.

Backed by LlamaParse (LlamaCloud). Kept behind a thin interface so we
can swap to Textract / DocumentIntelligence / self-hosted LlamaParse
later without touching callers.

Contract:

    parse_pdf_to_text(content: bytes, filename: str) -> ParsedPdf

ParsedPdf.text is markdown (LlamaParse output) — feeds into the same
_extract_chart_from_note path we already hardened, so there's no new
extraction logic to audit.

**HIPAA / BAA**: LlamaCloud does not have a BAA signed with CardioAuth
as of this writing. The caller (endpoint) enforces deidentified-only.
This module does not inspect content — don't change that without
revisiting the BAA posture.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class PdfParserError(Exception):
    """Parser failure — surfaced to the user as a 4xx/5xx."""


@dataclass
class ParsedPdf:
    text: str
    page_count: int
    parser: str           # "llamaparse" etc — for audit/telemetry
    duration_ms: int


# Size/page limits are here (not just in the endpoint) so unit tests and
# future CLI tools hit the same guards. Endpoint enforces these first
# for early rejection before the bytes ever touch the parser.
MAX_BYTES = 10 * 1024 * 1024        # 10 MB
MAX_PAGES = 10                       # forces reviewer discipline; bump once BAA is signed


def parse_pdf_to_text(content: bytes, filename: str = "") -> ParsedPdf:
    """Parse a PDF's bytes into markdown text.

    Raises:
        PdfParserError if the configured parser is disabled, mis-configured,
        or fails. The caller converts this into a user-facing HTTP error.
    """
    from cardioauth.config import Config
    cfg = Config()

    if len(content) > MAX_BYTES:
        raise PdfParserError(
            f"PDF too large ({len(content)//1024} KB) — max {MAX_BYTES//1024} KB. "
            "If you're sending a full chart export, reduce to the encounter of interest."
        )
    if not content.startswith(b"%PDF"):
        raise PdfParserError("Not a valid PDF (missing %PDF header).")

    parser = cfg.pdf_parser
    if parser == "disabled":
        raise PdfParserError(
            "PDF parsing is not configured. Set LLAMAPARSE_API_KEY on the server "
            "or paste the note text directly."
        )
    if parser == "llamaparse":
        return _parse_with_llamaparse(content, filename, cfg)

    raise PdfParserError(f"Unknown PDF parser '{parser}' — expected 'llamaparse' or 'disabled'.")


def _parse_with_llamaparse(content: bytes, filename: str, cfg) -> ParsedPdf:
    """LlamaCloud call. Sync wrapper over the async SDK.

    LlamaParse returns one Document per page; we join them with a page
    break marker so the downstream extractor can reason about page
    boundaries if it needs to. Page count is taken from the response.
    """
    import time

    if not cfg.llamaparse_api_key:
        raise PdfParserError("LLAMAPARSE_API_KEY missing. Set it on the server.")

    try:
        from llama_parse import LlamaParse
    except ImportError as e:
        raise PdfParserError(
            "llama_parse not installed. Add `llama-parse` to requirements.txt."
        ) from e

    parser = LlamaParse(
        api_key=cfg.llamaparse_api_key,
        result_type="markdown",
        verbose=False,
        language="en",
    )

    t0 = time.time()
    try:
        # load_data(content_bytes, extra_info={"file_name": ...}) — sync wrapper.
        # We pass bytes directly to avoid writing PHI to disk.
        extra_info = {"file_name": filename or "upload.pdf"}
        docs = parser.load_data(content, extra_info=extra_info)
    except Exception as e:
        raise PdfParserError(f"LlamaParse failed: {str(e)[:200]}") from e
    duration_ms = int((time.time() - t0) * 1000)

    if not docs:
        raise PdfParserError("LlamaParse returned no pages — PDF may be empty or scanned-image with no OCR.")

    if len(docs) > MAX_PAGES:
        raise PdfParserError(
            f"PDF has {len(docs)} pages — max {MAX_PAGES}. Split or excerpt before upload."
        )

    text = "\n\n---PAGE BREAK---\n\n".join(d.text for d in docs if getattr(d, "text", ""))
    if not text.strip():
        raise PdfParserError("LlamaParse returned empty text — scanned PDF without OCR?")

    return ParsedPdf(
        text=text,
        page_count=len(docs),
        parser="llamaparse",
        duration_ms=duration_ms,
    )
