"""Generate professional PDF letters for prior authorization requests."""

from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_BRAND_BLUE = colors.HexColor("#1a3c6e")
_BRAND_LIGHT = colors.HexColor("#e8eef5")
_GREY = colors.HexColor("#666666")
_LIGHT_GREY = colors.HexColor("#f5f5f5")
_TABLE_HEADER_BG = colors.HexColor("#1a3c6e")
_TABLE_ALT_ROW = colors.HexColor("#f0f4f8")


def _build_styles() -> dict[str, ParagraphStyle]:
    """Return a dict of custom paragraph styles."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PATitle",
            parent=base["Title"],
            fontSize=20,
            leading=24,
            textColor=_BRAND_BLUE,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "PASubtitle",
            parent=base["Normal"],
            fontSize=10,
            textColor=_GREY,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "section": ParagraphStyle(
            "PASection",
            parent=base["Heading2"],
            fontSize=13,
            leading=16,
            textColor=_BRAND_BLUE,
            spaceBefore=14,
            spaceAfter=6,
            borderPadding=(0, 0, 2, 0),
        ),
        "body": ParagraphStyle(
            "PABody",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "PASmall",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            textColor=_GREY,
        ),
        "footer": ParagraphStyle(
            "PAFooter",
            parent=base["Normal"],
            fontSize=8,
            textColor=_GREY,
            alignment=TA_CENTER,
        ),
        "label": ParagraphStyle(
            "PALabel",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
            textColor=_GREY,
        ),
        "value": ParagraphStyle(
            "PAValue",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
        ),
        "score_high": ParagraphStyle(
            "ScoreHigh",
            parent=base["Normal"],
            fontSize=12,
            textColor=colors.HexColor("#15803d"),
            leading=16,
        ),
        "score_medium": ParagraphStyle(
            "ScoreMedium",
            parent=base["Normal"],
            fontSize=12,
            textColor=colors.HexColor("#ca8a04"),
            leading=16,
        ),
        "score_low": ParagraphStyle(
            "ScoreLow",
            parent=base["Normal"],
            fontSize=12,
            textColor=colors.HexColor("#dc2626"),
            leading=16,
        ),
    }


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _section_header(text: str, styles: dict) -> list:
    """Return flowables for a styled section header with a rule underneath."""
    return [
        Spacer(1, 6),
        Paragraph(text, styles["section"]),
        HRFlowable(width="100%", thickness=1, color=_BRAND_LIGHT, spaceAfter=6),
    ]


def _info_row(label: str, value: str, styles: dict) -> Paragraph:
    return Paragraph(f"<b>{label}:</b>  {value}", styles["body"])


def _make_table(headers: list[str], rows: list[list[str]], col_widths=None) -> Table:
    """Build a styled Table from headers + rows."""
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds: list = [
        ("BACKGROUND", (0, 0), (-1, 0), _TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LEADING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    # Alternate row shading
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), _TABLE_ALT_ROW))
    t.setStyle(TableStyle(style_cmds))
    return t


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_pa_letter(
    chart_data: dict,
    policy_data: dict,
    reasoning: dict,
    patient_info: dict | None = None,
) -> bytes:
    """Generate a professional PDF prior authorization letter.

    Parameters
    ----------
    chart_data : dict
        Output of ``ChartData.model_dump()``.
    policy_data : dict
        Output of ``PolicyData.model_dump()``.
    reasoning : dict
        Output of ``ReasoningResult.model_dump()``.
    patient_info : dict | None
        Optional overrides with keys ``name``, ``age``, ``sex``, ``mrn``.

    Returns
    -------
    bytes
        The rendered PDF document.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = _build_styles()
    story: list = []

    # ------------------------------------------------------------------
    # Header / branding
    # ------------------------------------------------------------------
    story.append(Paragraph("CardioAuth", styles["title"]))
    story.append(Paragraph("Prior Authorization Request", styles["subtitle"]))
    story.append(
        Paragraph(
            datetime.now().strftime("%B %d, %Y"),
            ParagraphStyle("DateStyle", parent=styles["small"], alignment=TA_RIGHT),
        )
    )
    story.append(HRFlowable(width="100%", thickness=2, color=_BRAND_BLUE, spaceAfter=12))

    # ------------------------------------------------------------------
    # Patient information
    # ------------------------------------------------------------------
    pi = patient_info or {}
    patient_name = pi.get("name", f"Patient {chart_data.get('patient_id', 'N/A')}")
    patient_age = pi.get("age", "N/A")
    patient_sex = pi.get("sex", "N/A")
    patient_mrn = pi.get("mrn", chart_data.get("patient_id", "N/A"))
    insurance_id = chart_data.get("insurance_id", "N/A") or "N/A"

    story.extend(_section_header("Patient Information", styles))
    info_data = [
        ["Patient Name", patient_name, "MRN", patient_mrn],
        ["Age / Sex", f"{patient_age} / {patient_sex}", "Insurance ID", insurance_id],
    ]
    info_table = Table(info_data, colWidths=[1.2 * inch, 2.3 * inch, 1.2 * inch, 2.3 * inch])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), _GREY),
        ("TEXTCOLOR", (2, 0), (2, -1), _GREY),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, -1), _LIGHT_GREY),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#cccccc")),
    ]))
    story.append(info_table)

    # ------------------------------------------------------------------
    # Payer information
    # ------------------------------------------------------------------
    payer_name = policy_data.get("payer", "N/A")
    submission_format = policy_data.get("submission_format", "unknown")

    story.extend(_section_header("Payer Information", styles))
    story.append(_info_row("Payer", payer_name, styles))
    story.append(_info_row("Submission Format", submission_format.title(), styles))

    turnaround = policy_data.get("typical_turnaround_days", 0)
    if turnaround:
        story.append(_info_row("Typical Turnaround", f"{turnaround} business days", styles))

    # ------------------------------------------------------------------
    # Procedure requested
    # ------------------------------------------------------------------
    story.extend(_section_header("Procedure Requested", styles))
    story.append(
        _info_row("Procedure", chart_data.get("procedure_requested", "N/A"), styles)
    )
    story.append(_info_row("CPT Code", chart_data.get("procedure_code", "N/A"), styles))

    # ------------------------------------------------------------------
    # Diagnosis codes
    # ------------------------------------------------------------------
    dx_codes = chart_data.get("diagnosis_codes", [])
    if dx_codes:
        story.extend(_section_header("Diagnosis Codes (ICD-10)", styles))
        for i, code in enumerate(dx_codes):
            prefix = "Primary" if i == 0 else f"Secondary {i}"
            story.append(_info_row(prefix, code, styles))

    # ------------------------------------------------------------------
    # Clinical narrative
    # ------------------------------------------------------------------
    narrative = reasoning.get("pa_narrative_draft", "")
    if narrative:
        story.extend(_section_header("Clinical Narrative", styles))
        # Wrap long narrative text properly
        for para in narrative.split("\n"):
            if para.strip():
                story.append(Paragraph(para.strip(), styles["body"]))
                story.append(Spacer(1, 2))

    # ------------------------------------------------------------------
    # Supporting evidence: Lab values
    # ------------------------------------------------------------------
    labs = chart_data.get("relevant_labs", [])
    if labs:
        story.extend(_section_header("Supporting Evidence — Key Lab Values", styles))
        lab_rows = []
        for lab in labs:
            flag = lab.get("flag", "")
            flag_display = f"  [{flag}]" if flag else ""
            lab_rows.append([
                lab.get("name", ""),
                f"{lab.get('value', '')} {lab.get('unit', '')}{flag_display}",
                lab.get("date", ""),
            ])
        story.append(
            _make_table(
                ["Test", "Result", "Date"],
                lab_rows,
                col_widths=[2.5 * inch, 2.5 * inch, 2 * inch],
            )
        )

    # ------------------------------------------------------------------
    # Supporting evidence: Imaging findings
    # ------------------------------------------------------------------
    imaging = chart_data.get("relevant_imaging", [])
    if imaging:
        story.extend(_section_header("Supporting Evidence — Imaging Findings", styles))
        img_rows = []
        for img in imaging:
            img_rows.append([
                img.get("type", ""),
                img.get("result_summary", ""),
                img.get("date", ""),
            ])
        story.append(
            _make_table(
                ["Imaging Type", "Findings", "Date"],
                img_rows,
                col_widths=[1.8 * inch, 3.2 * inch, 2 * inch],
            )
        )

    # ------------------------------------------------------------------
    # Supporting evidence: Current medications
    # ------------------------------------------------------------------
    meds = chart_data.get("relevant_medications", [])
    if meds:
        story.extend(_section_header("Supporting Evidence — Current Medications", styles))
        med_rows = []
        for med in meds:
            med_rows.append([
                med.get("name", ""),
                med.get("dose", ""),
                med.get("indication", ""),
                med.get("start_date", ""),
            ])
        story.append(
            _make_table(
                ["Medication", "Dose", "Indication", "Start Date"],
                med_rows,
                col_widths=[1.8 * inch, 1.5 * inch, 2 * inch, 1.7 * inch],
            )
        )

    # ------------------------------------------------------------------
    # Supporting evidence: Prior treatments
    # ------------------------------------------------------------------
    prior_tx = chart_data.get("prior_treatments", [])
    if prior_tx:
        story.extend(_section_header("Supporting Evidence — Prior Treatments", styles))
        for tx in prior_tx:
            story.append(Paragraph(f"&bull;  {tx}", styles["body"]))

    # ------------------------------------------------------------------
    # Criteria met / not met summary
    # ------------------------------------------------------------------
    criteria_met = reasoning.get("criteria_met", [])
    criteria_not_met = reasoning.get("criteria_not_met", [])

    if criteria_met or criteria_not_met:
        story.extend(_section_header("Criteria Evaluation Summary", styles))

    if criteria_met:
        met_rows = []
        for c in criteria_met:
            conf = c.get("confidence", 0)
            met_rows.append([
                c.get("criterion", ""),
                "MET",
                c.get("evidence", ""),
                f"{conf:.0%}",
            ])
        story.append(Paragraph("<b>Criteria Met</b>", styles["body"]))
        story.append(Spacer(1, 4))
        t = _make_table(
            ["Criterion", "Status", "Evidence", "Confidence"],
            met_rows,
            col_widths=[2 * inch, 0.7 * inch, 3 * inch, 1.0 * inch],
        )
        story.append(t)
        story.append(Spacer(1, 8))

    if criteria_not_met:
        gap_rows = []
        for c in criteria_not_met:
            gap_rows.append([
                c.get("criterion", ""),
                "NOT MET",
                c.get("gap", ""),
                c.get("recommendation", ""),
            ])
        story.append(Paragraph("<b>Criteria Not Met / Gaps</b>", styles["body"]))
        story.append(Spacer(1, 4))
        t = _make_table(
            ["Criterion", "Status", "Gap", "Recommendation"],
            gap_rows,
            col_widths=[2 * inch, 0.8 * inch, 2 * inch, 2 * inch],
        )
        story.append(t)
        story.append(Spacer(1, 8))

    # ------------------------------------------------------------------
    # Approval likelihood score
    # ------------------------------------------------------------------
    score = reasoning.get("approval_likelihood_score", 0)
    label = reasoning.get("approval_likelihood_label", "LOW")

    story.extend(_section_header("Approval Likelihood", styles))
    score_style_key = (
        "score_high" if label == "HIGH"
        else "score_medium" if label == "MEDIUM"
        else "score_low"
    )
    story.append(
        Paragraph(
            f"<b>{score:.0%}</b> — {label}",
            styles[score_style_key],
        )
    )

    missing_docs = reasoning.get("missing_documentation", [])
    if missing_docs:
        story.append(Spacer(1, 4))
        story.append(Paragraph("<b>Missing Documentation:</b>", styles["body"]))
        for md in missing_docs:
            story.append(Paragraph(f"&bull;  {md}", styles["body"]))

    # ------------------------------------------------------------------
    # Guideline citations
    # ------------------------------------------------------------------
    citations = reasoning.get("guideline_citations", [])
    if citations:
        story.extend(_section_header("Guideline Citations", styles))
        for i, cite in enumerate(citations, 1):
            story.append(Paragraph(f"{i}. {cite}", styles["small"]))

    # ------------------------------------------------------------------
    # Signature line
    # ------------------------------------------------------------------
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="45%", thickness=0.5, color=colors.black, spaceAfter=4))
    attending = chart_data.get("attending_physician", "")
    sig_name = attending if attending else "Attending Physician"
    story.append(Paragraph(f"{sig_name}", styles["body"]))
    story.append(Paragraph("Signature / Date", styles["small"]))

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------
    story.append(Spacer(1, 24))
    story.append(
        HRFlowable(width="100%", thickness=0.5, color=_GREY, spaceAfter=6)
    )
    story.append(
        Paragraph(
            "Generated by CardioAuth  |  Confidential Medical Document",
            styles["footer"],
        )
    )

    doc.build(story)
    return buf.getvalue()
