"""CardioAuth FastAPI server."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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

    return {
        "request_id": request_id,
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


# Serve frontend — must be last so it doesn't shadow API routes
@app.get("/", response_class=HTMLResponse)
def serve_frontend() -> str:
    index = STATIC_DIR / "index.html"
    if index.exists():
        return index.read_text()
    return "<h1>CardioAuth</h1><p>Frontend not found. Visit <a href='/docs'>/docs</a> for API.</p>"
