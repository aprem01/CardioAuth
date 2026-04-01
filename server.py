"""CardioAuth FastAPI server."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cardioauth.config import Config
from cardioauth.orchestrator import Orchestrator, ReviewPackage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(
    title="CardioAuth",
    description="Cardiology prior authorization automation API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

config = Config()
orchestrator = Orchestrator(config)

# In-memory store for review packages (use Redis/DB in production)
_reviews: dict[str, ReviewPackage] = {}

STATIC_DIR = Path(__file__).parent / "static"


class PARequest(BaseModel):
    patient_id: str
    procedure_code: str
    payer_id: str
    payer_name: str


class ApprovalRequest(BaseModel):
    request_id: str
    approved_by: str


class PayerResponseRequest(BaseModel):
    submission_id: str
    payer_response: dict[str, Any]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/reference")
def get_reference_data() -> dict[str, Any]:
    """Return ICD-10 descriptions and demo patient info for frontend lookups."""
    from cardioauth.demo import ICD10_DESCRIPTIONS, DEMO_PATIENT_INFO
    return {"icd10": ICD10_DESCRIPTIONS, "patients": DEMO_PATIENT_INFO}


@app.post("/api/pa/request")
def create_pa_request(req: PARequest) -> dict[str, Any]:
    """Step 1-3: Extract chart data, get payer criteria, reason, and draft narrative."""
    try:
        review = orchestrator.process_request(
            patient_id=req.patient_id,
            procedure_code=req.procedure_code,
            payer_id=req.payer_id,
            payer_name=req.payer_name,
        )
    except Exception as e:
        logging.exception("PA request failed")
        raise HTTPException(status_code=500, detail=str(e))

    request_id = review.chart_data.patient_id + "-" + review.chart_data.procedure_code
    _reviews[request_id] = review

    from cardioauth.demo import DEMO_PATIENT_INFO

    return {
        "request_id": request_id,
        "patient_info": DEMO_PATIENT_INFO.get(req.patient_id, {}),
        "approval_likelihood": {
            "score": review.reasoning.approval_likelihood_score,
            "label": review.reasoning.approval_likelihood_label,
        },
        "narrative_draft": review.reasoning.pa_narrative_draft,
        "criteria_met": [c.model_dump() for c in review.reasoning.criteria_met],
        "criteria_not_met": [c.model_dump() for c in review.reasoning.criteria_not_met],
        "missing_documentation": review.reasoning.missing_documentation,
        "guideline_citations": review.reasoning.guideline_citations,
        "requires_human_action": review.requires_human_action,
        "chart_confidence": review.chart_data.confidence_score,
        "chart_data": review.chart_data.model_dump(),
        "policy_data": review.policy_data.model_dump(),
    }


@app.post("/api/pa/approve")
def approve_and_submit(req: ApprovalRequest) -> dict[str, Any]:
    """Step 4: Cardiologist approves — submit to payer."""
    review = _reviews.get(req.request_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review package not found")

    try:
        submission = orchestrator.submit_after_approval(review, approved_by=req.approved_by)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    del _reviews[req.request_id]

    return submission.model_dump()


@app.post("/api/pa/outcome")
def process_outcome(req: PayerResponseRequest) -> dict[str, Any]:
    """Process payer decision and handle denial/appeal."""
    from cardioauth.models.submission import SubmissionResult

    try:
        outcome = orchestrator.submission_agent.process_outcome(
            submission=SubmissionResult(
                submission_id=req.submission_id,
                payer="",
                procedure="",
                patient_id="",
                submission_channel="",
                submission_timestamp="",
            ),
            payer_response=req.payer_response,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return outcome.model_dump()


class AppealRequest(BaseModel):
    request_id: str
    denial_reason: str


@app.post("/api/pa/appeal")
def generate_appeal(req: AppealRequest) -> dict[str, Any]:
    """Generate an appeal draft for a denied PA."""
    review = _reviews.get(req.request_id)
    if review:
        chart_data = review.chart_data
        policy_data = review.policy_data
    else:
        # Parse request_id format: "DEMO-001-93458"
        from cardioauth.demo import get_demo_chart, get_demo_policy
        parts = req.request_id.rsplit("-", 1)
        patient_id = parts[0] if len(parts) > 0 else "DEMO-001"
        proc_code = parts[1] if len(parts) > 1 else "93458"
        try:
            chart_data = get_demo_chart(patient_id, proc_code)
            policy_data = get_demo_policy(proc_code, chart_data.payer_name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"No demo data found: {e}")

    from cardioauth.demo import get_demo_appeal
    appeal_text = get_demo_appeal(chart_data, policy_data, req.denial_reason)

    return {
        "request_id": req.request_id,
        "denial_reason": req.denial_reason,
        "appeal_draft": appeal_text,
        "appeal_deadline": "2026-04-15",
        "recommendation": "Submit appeal with peer-to-peer review request",
    }


@app.post("/api/pa/export-pdf")
def export_pdf(req: ApprovalRequest):
    """Export the PA review package as a PDF letter."""
    review = _reviews.get(req.request_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review package not found")

    from cardioauth.pdf_generator import generate_pa_letter

    pdf_bytes = generate_pa_letter(
        chart_data=review.chart_data.model_dump(),
        policy_data=review.policy_data.model_dump(),
        reasoning=review.reasoning.model_dump(),
    )

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=PA-{req.request_id}.pdf"
        },
    )


@app.get("/api/analytics")
def get_analytics():
    """Return mock analytics data for the dashboard."""
    return {
        "total_requests": 47,
        "approved": 38,
        "denied": 5,
        "pending": 4,
        "approval_rate": 0.88,
        "avg_turnaround_days": 4.2,
        "denial_reasons": [
            {"reason": "Incomplete documentation", "count": 2},
            {"reason": "Medical necessity not established", "count": 1},
            {"reason": "Prior treatment requirement not met", "count": 1},
            {"reason": "Outdated imaging studies", "count": 1},
        ],
        "monthly_trend": [
            {"month": "Oct", "approved": 8, "denied": 1},
            {"month": "Nov", "approved": 10, "denied": 2},
            {"month": "Dec", "approved": 9, "denied": 1},
            {"month": "Jan", "approved": 11, "denied": 1},
        ],
        "payer_breakdown": [
            {"payer": "UnitedHealthcare", "total": 20, "approved": 17},
            {"payer": "Aetna", "total": 15, "approved": 12},
            {"payer": "Blue Cross Blue Shield", "total": 12, "approved": 9},
        ],
    }


# Mount static files for favicon and assets
app.mount("/static", StaticFiles(directory="static"), name="static")

# Serve frontend — must be last so it doesn't shadow API routes
@app.get("/", response_class=HTMLResponse)
def serve_frontend() -> str:
    index = STATIC_DIR / "index.html"
    if index.exists():
        return index.read_text()
    return "<h1>CardioAuth</h1><p>Frontend not found. Visit <a href='/docs'>/docs</a> for API.</p>"
