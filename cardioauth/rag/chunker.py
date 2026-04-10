"""Document chunker for payer policy documents.

Two extraction backends with automatic fallback:

  1. **Fast path** — `pypdf` text extraction.
     Works for clean text PDFs. Fast, free, no API call.

  2. **Vision path** — Claude PDF/image vision extraction.
     Triggers automatically when:
       - The fast path produces too little text (likely scanned PDF)
       - The fast path text contains lots of fragments without spaces
         (likely table-heavy PDF where pypdf can't reconstruct structure)
       - The user uploads an image file (PNG, JPG) directly
     Claude PDF support natively handles complex tables, multi-column
     layouts, embedded images, and scanned documents. It returns
     markdown-style structured text per page so the chunker can split
     on real headings and table boundaries.

After extraction, both paths feed into the same chunking pipeline:
  - Detect headings (markdown, ALL CAPS, numbered sections)
  - Group paragraphs into chunks bounded by headings and target size
  - Return ChunkDraft objects ready to become PolicyChunks
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# How sparse must pypdf's output be before we fall back to Claude vision?
# These are the heuristics that detect a "scanned or table-heavy PDF":
#   - average chars per page below this → probably scanned/empty
#   - ratio of "words containing letters" to "junk fragments" below this
MIN_CHARS_PER_PAGE_FOR_PYPDF = 200
MIN_WORD_QUALITY_RATIO = 0.55


# Target chunk size in characters. 900-1200 chars ~= 150-200 words — big
# enough to carry a full coverage criterion, small enough to retrieve
# precisely.
TARGET_CHUNK_SIZE = 1100
MIN_CHUNK_SIZE = 250
MAX_CHUNK_SIZE = 1800


@dataclass
class ChunkDraft:
    """A draft chunk extracted from a document, pre-metadata."""
    text: str
    section_heading: str = ""
    page: int | None = None


# ─────────────────────────── Text extraction ───────────────────────────


def extract_text_from_pdf(data: bytes) -> list[tuple[int, str]]:
    """Return a list of (page_number, page_text) tuples from a PDF."""
    try:
        import pypdf  # imported lazily so local dev without pypdf still runs
    except ImportError:
        raise RuntimeError(
            "pypdf is not installed. Add `pypdf>=4.0` to requirements.txt"
        )
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            logger.warning("PDF page %d extraction failed: %s", i, e)
            text = ""
        pages.append((i, text))
    return pages


def extract_text_from_plain(data: bytes) -> list[tuple[int, str]]:
    """Plain text / markdown — returns a single page tuple."""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = data.decode("latin-1", errors="replace")
    return [(1, text)]


# ─────────────────────────── Quality detection ───────────────────────────


_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_FRAGMENT_RE = re.compile(r"[^\sA-Za-z0-9]+")


def _looks_garbled(pages: list[tuple[int, str]]) -> tuple[bool, str]:
    """Heuristic: did pypdf extract usable text, or is it scanned/tables?

    Returns (needs_fallback, reason).
    """
    if not pages:
        return True, "no pages extracted"

    total_chars = sum(len(t) for _, t in pages)
    if total_chars == 0:
        return True, "zero characters extracted (likely scanned PDF)"

    avg_per_page = total_chars / len(pages)
    if avg_per_page < MIN_CHARS_PER_PAGE_FOR_PYPDF:
        return True, f"only {avg_per_page:.0f} chars/page (likely scanned or image-heavy)"

    full_text = "\n".join(t for _, t in pages)
    words = _WORD_RE.findall(full_text)
    fragments = _FRAGMENT_RE.findall(full_text)
    if not words:
        return True, "no real words extracted"

    # If the ratio of clean words to non-word fragments is poor, the text
    # is probably broken table-cell extraction (lots of "$1,234 | 12% | 5")
    # that pypdf couldn't reconstruct.
    quality = len(words) / max(len(words) + len(fragments) // 4, 1)
    if quality < MIN_WORD_QUALITY_RATIO:
        return True, f"low word-quality ratio {quality:.2f} (likely complex tables)"

    return False, ""


# ─────────────────────────── Claude vision extraction ───────────────────────────


CLAUDE_VISION_PROMPT = """\
You are extracting structured text from a payer medical policy document.
The document may contain tables, multi-column layouts, embedded figures,
diagrams, and scanned text.

Return the document as clean MARKDOWN, page by page. For each page:

  - Use `# Page N` (where N is the page number) at the start of every page
  - Preserve section headings as `## Heading` or `### Subheading`
  - Keep paragraphs as plain prose with blank lines between them
  - Convert tables into GitHub-flavored markdown tables (| col | col |)
  - For embedded figures, write `[Figure: brief description]` inline
  - For scanned text, transcribe what you can read; mark unreadable
    regions as `[unreadable]`
  - DO NOT summarize. DO NOT skip content. DO NOT add commentary.
  - Preserve all numeric values, CPT codes, ICD-10 codes, dates,
    document numbers, page numbers, and policy version identifiers
    EXACTLY as they appear.
  - Preserve list structure with `-` or `1.` markers.

Output ONLY the markdown. No JSON wrapper, no preamble.
"""


def extract_text_with_vision(
    data: bytes,
    media_type: str,
    api_key: str | None = None,
) -> list[tuple[int, str]]:
    """Use Claude vision to extract structured markdown from a PDF or image.

    Returns a list of (page_number, markdown_text) tuples — one entry per
    page for PDFs, one entry total for images.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY required for vision-based PDF extraction")

    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK is not installed")

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.standard_b64encode(data).decode("utf-8")

    if media_type == "application/pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
    else:
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }

    logger.info("Vision extraction starting (%s, %d KB)", media_type, len(data) // 1024)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": [content_block, {"type": "text", "text": CLAUDE_VISION_PROMPT}],
        }],
    )

    raw = response.content[0].text
    # Split by `# Page N` markers to recover per-page texts. If the model
    # didn't produce page markers, dump everything as page 1.
    pages: list[tuple[int, str]] = []
    parts = re.split(r"(?m)^#\s*Page\s*(\d+)\s*$", raw)
    if len(parts) > 1:
        # parts looks like: ['', '1', 'page 1 content', '2', 'page 2 content', ...]
        for i in range(1, len(parts), 2):
            try:
                page_num = int(parts[i])
            except ValueError:
                page_num = (i + 1) // 2
            page_text = parts[i + 1].strip() if i + 1 < len(parts) else ""
            pages.append((page_num, page_text))
    else:
        pages.append((1, raw.strip()))

    logger.info("Vision extraction complete: %d pages, %d total chars",
                len(pages), sum(len(t) for _, t in pages))
    return pages


# ─────────────────────────── Heading detection ───────────────────────────


# Lines that look like section headings in a policy document:
# - ALL CAPS lines
# - Numbered sections (1., 1.1, I., A.)
# - Lines ending with colons that are short (< 80 chars)
# - Markdown headings (#, ##, ###)
_HEADING_PATTERNS = [
    re.compile(r"^(#{1,4})\s+(.+)$"),                         # markdown
    re.compile(r"^([IVX]+\.|[0-9]+\.[0-9]*|[A-Z]\.)\s+([A-Z].{3,80})$"),  # numbered
    re.compile(r"^([A-Z][A-Z\s/&,]{4,80})$"),                 # ALL CAPS line
    re.compile(r"^([A-Z][A-Za-z0-9 ,/&-]{3,70}):\s*$"),        # trailing colon
]


def _is_heading(line: str) -> str | None:
    """Return the heading text if the line looks like a heading, else None."""
    s = line.strip()
    if not s or len(s) > 120:
        return None
    for pat in _HEADING_PATTERNS:
        m = pat.match(s)
        if m:
            # The last group is the most descriptive piece
            return m.groups()[-1].strip().rstrip(":")
    return None


# ─────────────────────────── Paragraph splitter ───────────────────────────


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on blank-line boundaries."""
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse 3+ newlines to 2 (paragraph separator)
    text = re.sub(r"\n{3,}", "\n\n", text)
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paras


# ─────────────────────────── Main chunker ───────────────────────────


def _detect_media_type(content_type: str, filename: str) -> str:
    ct = (content_type or "").lower()
    name = (filename or "").lower()
    if "pdf" in ct or name.endswith(".pdf"):
        return "application/pdf"
    if "png" in ct or name.endswith(".png"):
        return "image/png"
    if "jpeg" in ct or "jpg" in ct or name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if "webp" in ct or name.endswith(".webp"):
        return "image/webp"
    return "text/plain"


def chunk_document(
    data: bytes,
    content_type: str = "",
    filename: str = "",
    target_size: int = TARGET_CHUNK_SIZE,
    force_vision: bool = False,
) -> list[ChunkDraft]:
    """Extract text from a document and return a list of ChunkDraft objects.

    Routing:
      - Plain text / markdown            → extract_text_from_plain
      - Image (PNG/JPG/WEBP)             → extract_text_with_vision (Claude)
      - PDF (text-based, clean layout)   → pypdf, then check quality
      - PDF (scanned or table-heavy)     → fall back to vision automatically
      - PDF with force_vision=True       → skip pypdf, go straight to vision

    Vision-extracted markdown is then run through the same heading/paragraph
    chunking pipeline as native text, so the downstream output shape is
    identical regardless of source.
    """
    media_type = _detect_media_type(content_type, filename)
    pages: list[tuple[int, str]]

    if media_type == "application/pdf":
        if force_vision:
            logger.info("PDF: force_vision=True, going straight to Claude vision")
            pages = extract_text_with_vision(data, media_type)
        else:
            try:
                pages = extract_text_from_pdf(data)
                needs_fallback, reason = _looks_garbled(pages)
                if needs_fallback:
                    logger.info("PDF: pypdf output insufficient (%s) — falling back to vision", reason)
                    pages = extract_text_with_vision(data, media_type)
            except Exception as e:
                logger.warning("pypdf extraction failed (%s); falling back to vision", e)
                pages = extract_text_with_vision(data, media_type)
    elif media_type.startswith("image/"):
        pages = extract_text_with_vision(data, media_type)
    else:
        pages = extract_text_from_plain(data)

    drafts: list[ChunkDraft] = []
    current_heading = ""
    current_buf = ""
    current_page = None

    def _flush():
        nonlocal current_buf
        if current_buf.strip() and len(current_buf.strip()) >= MIN_CHUNK_SIZE:
            drafts.append(ChunkDraft(
                text=current_buf.strip(),
                section_heading=current_heading,
                page=current_page,
            ))
        current_buf = ""

    for page_num, page_text in pages:
        if not page_text.strip():
            continue
        lines = page_text.split("\n")
        # First pass: detect headings line by line and reassemble paragraphs
        blocks: list[tuple[str, str]] = []  # (kind, text): kind in {"heading", "para"}
        buf_para: list[str] = []
        for line in lines:
            heading = _is_heading(line)
            if heading:
                if buf_para:
                    blocks.append(("para", " ".join(buf_para).strip()))
                    buf_para = []
                blocks.append(("heading", heading))
            elif line.strip() == "":
                if buf_para:
                    blocks.append(("para", " ".join(buf_para).strip()))
                    buf_para = []
            else:
                buf_para.append(line.strip())
        if buf_para:
            blocks.append(("para", " ".join(buf_para).strip()))

        # Second pass: walk blocks, accumulate paragraphs into chunks that
        # respect the target size and heading boundaries.
        for kind, content in blocks:
            if not content:
                continue
            if kind == "heading":
                _flush()
                current_heading = content
                current_page = page_num
                continue

            if current_page is None:
                current_page = page_num

            # If adding this para would blow the max, flush first
            candidate_len = len(current_buf) + len(content) + 2
            if current_buf and candidate_len > MAX_CHUNK_SIZE:
                _flush()
                current_page = page_num

            current_buf = (current_buf + "\n\n" + content).strip() if current_buf else content

            # If we crossed target size, flush at a natural stopping point
            if len(current_buf) >= target_size:
                _flush()
                current_page = page_num

    _flush()

    # Filter out any weirdly short chunks that slipped through
    drafts = [d for d in drafts if len(d.text) >= MIN_CHUNK_SIZE]
    return drafts


# ─────────────────────────── Convenience ───────────────────────────


def chunks_from_plain_text(
    text: str,
    heading: str = "",
    target_size: int = TARGET_CHUNK_SIZE,
) -> list[ChunkDraft]:
    """Chunk a raw string directly without going through the extractor."""
    return chunk_document(text.encode("utf-8"), content_type="text/plain", target_size=target_size)
