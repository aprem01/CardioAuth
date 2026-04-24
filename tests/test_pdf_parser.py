"""Tests for the PDF parser wrapper.

LlamaParse itself is mocked — we validate the input guards (size, header,
page limit), config switch behavior, and the ParsedPdf contract. A live
network test is out of scope (covered by manual smoke).
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cardioauth.pdf_parser import (
    MAX_BYTES,
    MAX_PAGES,
    ParsedPdf,
    PdfParserError,
    parse_pdf_to_text,
)


# ── Guard tests (run independent of LlamaParse availability) ────────────

def test_rejects_oversized_pdf(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "test-key")
    too_big = b"%PDF" + b"x" * (MAX_BYTES + 10)
    with pytest.raises(PdfParserError, match="too large"):
        parse_pdf_to_text(too_big, filename="big.pdf")


def test_rejects_non_pdf(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "test-key")
    with pytest.raises(PdfParserError, match="valid PDF"):
        parse_pdf_to_text(b"<html>not a pdf</html>", filename="fake.pdf")


def test_disabled_parser_returns_helpful_error(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "disabled")
    monkeypatch.delenv("LLAMAPARSE_API_KEY", raising=False)
    with pytest.raises(PdfParserError, match="not configured"):
        parse_pdf_to_text(b"%PDF-1.4\n...", filename="x.pdf")


def test_unknown_parser_raises(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "bogus")
    with pytest.raises(PdfParserError, match="Unknown PDF parser"):
        parse_pdf_to_text(b"%PDF-1.4\n...", filename="x.pdf")


def test_llamaparse_without_api_key_raises(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.delenv("LLAMAPARSE_API_KEY", raising=False)
    with pytest.raises(PdfParserError, match="LLAMAPARSE_API_KEY"):
        parse_pdf_to_text(b"%PDF-1.4\n...", filename="x.pdf")


# ── LlamaParse integration (mocked) ─────────────────────────────────────

class _FakeDoc:
    def __init__(self, text: str) -> None:
        self.text = text


def _install_fake_llama_parse(monkeypatch, docs: list[_FakeDoc] | Exception) -> MagicMock:
    """Install a fake `llama_parse` module so the parser uses it.

    Returns the mock LlamaParse class for further inspection.
    """
    fake_module = types.ModuleType("llama_parse")

    class FakeLlamaParse:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def load_data(self, content: Any, extra_info: Any = None):
            if isinstance(docs, Exception):
                raise docs
            return docs

    fake_module.LlamaParse = FakeLlamaParse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_parse", fake_module)
    return FakeLlamaParse  # type: ignore[return-value]


def test_llamaparse_success(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "test-key")
    _install_fake_llama_parse(
        monkeypatch,
        [_FakeDoc("# Page 1\nContent."), _FakeDoc("# Page 2\nMore.")],
    )

    parsed = parse_pdf_to_text(b"%PDF-1.4\n...", filename="note.pdf")
    assert isinstance(parsed, ParsedPdf)
    assert parsed.page_count == 2
    assert parsed.parser == "llamaparse"
    assert "Page 1" in parsed.text and "Page 2" in parsed.text
    assert "---PAGE BREAK---" in parsed.text


def test_llamaparse_empty_result_raises(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "test-key")
    _install_fake_llama_parse(monkeypatch, [])
    with pytest.raises(PdfParserError, match="no pages"):
        parse_pdf_to_text(b"%PDF-1.4\n...", filename="empty.pdf")


def test_llamaparse_all_empty_text_raises(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "test-key")
    _install_fake_llama_parse(monkeypatch, [_FakeDoc(""), _FakeDoc("   ")])
    with pytest.raises(PdfParserError, match="empty text"):
        parse_pdf_to_text(b"%PDF-1.4\n...", filename="scan.pdf")


def test_llamaparse_page_limit_enforced(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "test-key")
    too_many = [_FakeDoc(f"page {i}") for i in range(MAX_PAGES + 5)]
    _install_fake_llama_parse(monkeypatch, too_many)
    with pytest.raises(PdfParserError, match="max"):
        parse_pdf_to_text(b"%PDF-1.4\n...", filename="long.pdf")


def test_llamaparse_sdk_error_surfaces_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("PDF_PARSER", "llamaparse")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "test-key")
    _install_fake_llama_parse(monkeypatch, RuntimeError("network timeout"))
    with pytest.raises(PdfParserError, match="LlamaParse failed"):
        parse_pdf_to_text(b"%PDF-1.4\n...", filename="x.pdf")
