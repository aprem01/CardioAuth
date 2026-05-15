"""Render a synthetic case section as a real PDF.

When a markdown section is tagged `format: pdf`, we generate an actual
PDF (using reportlab, which is already a project dependency) so the
attachment looks and reads like the PDFs a real Epic chart serves.

The PDF bytes get base64-embedded into the DocumentReference's
attachment.data field with contentType=application/pdf. Downstream the
corpus mapper's Binary-decode path uses pypdf to extract text — so the
indexed corpus content is the same text the user could see by opening
the PDF, end-to-end realism.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

if TYPE_CHECKING:
    from cardioauth.synthetic.loader import CaseSection, SyntheticCase


def render_section_pdf(case: "SyntheticCase", section: "CaseSection") -> bytes:
    """Produce a PDF document for one chart section. Header carries
    patient + section metadata; body is the markdown section text.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=section.title,
    )
    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=base["Title"], fontSize=14, spaceAfter=4,
        textColor=colors.HexColor("#1a3c6e"),
    )
    meta_style = ParagraphStyle(
        "Meta", parent=base["Normal"], fontSize=9,
        textColor=colors.HexColor("#666666"), spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "Body", parent=base["Normal"], fontSize=10, leading=14,
        spaceAfter=6,
    )

    story: list = [
        Paragraph(_esc(section.title), title_style),
        Paragraph(
            f"Patient: <b>{_esc(case.patient_name)}</b>  ·  DOB: {_esc(case.dob)}  ·  "
            f"Date: {_esc(section.date)}  ·  Author: {_esc(section.author or '—')}",
            meta_style,
        ),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#1a3c6e"), spaceAfter=8),
    ]

    for para in section.body.split("\n\n"):
        text = para.strip()
        if not text:
            continue
        story.append(Paragraph(_esc(text).replace("\n", "<br/>"), body_style))
        story.append(Spacer(1, 4))

    doc.build(story)
    return buf.getvalue()


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
