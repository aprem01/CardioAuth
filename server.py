"""CardioAuth FastAPI server."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CardioAuth</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#0a0e1a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.container{max-width:720px;padding:3rem 2rem;text-align:center}
.logo{font-size:3rem;font-weight:700;background:linear-gradient(135deg,#3b82f6,#06b6d4);
-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:.5rem}
.tagline{color:#94a3b8;font-size:1.1rem;margin-bottom:3rem}
.pipeline{display:flex;flex-direction:column;gap:1rem;margin-bottom:3rem;text-align:left}
.step{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1rem 1.25rem;
display:flex;align-items:center;gap:1rem;transition:border-color .2s}
.step:hover{border-color:#3b82f6}
.num{background:linear-gradient(135deg,#3b82f6,#06b6d4);color:#fff;width:32px;height:32px;
border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.85rem;flex-shrink:0}
.step-text strong{color:#f1f5f9}
.step-text span{color:#94a3b8;font-size:.9rem}
.arrow{text-align:center;color:#475569;font-size:1.2rem}
.endpoints{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-bottom:2.5rem}
.ep{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:.85rem 1rem;text-align:left}
.ep .method{font-size:.7rem;font-weight:700;padding:2px 6px;border-radius:4px;margin-right:.5rem}
.ep .get{background:#065f46;color:#6ee7b7}
.ep .post{background:#1e3a5f;color:#7dd3fc}
.ep .path{color:#e2e8f0;font-family:monospace;font-size:.85rem}
.ep .desc{color:#64748b;font-size:.78rem;margin-top:.25rem}
.links{display:flex;gap:1rem;justify-content:center}
.links a{display:inline-block;padding:.65rem 1.5rem;border-radius:8px;text-decoration:none;
font-weight:600;font-size:.9rem;transition:opacity .2s}
.links a:hover{opacity:.85}
.btn-primary{background:linear-gradient(135deg,#3b82f6,#06b6d4);color:#fff}
.btn-secondary{background:#1e293b;border:1px solid #334155;color:#e2e8f0}
.footer{margin-top:3rem;color:#475569;font-size:.8rem}
</style>
</head>
<body>
<div class="container">
<div class="logo">CardioAuth</div>
<p class="tagline">Cardiology Prior Authorization Automation</p>

<div class="pipeline">
  <div class="step"><div class="num">1</div><div class="step-text"><strong>CHART_AGENT</strong><br><span>Extracts clinical data from Epic FHIR</span></div></div>
  <div class="arrow">&#8595;</div>
  <div class="step"><div class="num">2</div><div class="step-text"><strong>POLICY_AGENT</strong><br><span>Retrieves payer-specific coverage criteria</span></div></div>
  <div class="arrow">&#8595;</div>
  <div class="step"><div class="num">3</div><div class="step-text"><strong>REASONING_AGENT</strong><br><span>Maps clinical facts against criteria &amp; drafts narrative</span></div></div>
  <div class="arrow">&#8595;</div>
  <div class="step"><div class="num">4</div><div class="step-text"><strong>SUBMISSION_AGENT</strong><br><span>Packages, submits &amp; tracks outcomes</span></div></div>
</div>

<div class="endpoints">
  <div class="ep"><span class="method post">POST</span><span class="path">/api/pa/request</span><div class="desc">Start PA pipeline</div></div>
  <div class="ep"><span class="method post">POST</span><span class="path">/api/pa/approve</span><div class="desc">Physician approval</div></div>
  <div class="ep"><span class="method post">POST</span><span class="path">/api/pa/outcome</span><div class="desc">Process payer decision</div></div>
  <div class="ep"><span class="method get">GET</span><span class="path">/health</span><div class="desc">Health check</div></div>
</div>

<div class="links">
  <a href="/docs" class="btn-primary">API Docs</a>
  <a href="https://github.com/aprem01/CardioAuth" class="btn-secondary">GitHub</a>
</div>

<p class="footer">Human-in-the-loop required before every submission.</p>
</div>
</body>
</html>"""


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

    # In production, look up submission from DB
    # For prototype, require full payer_response with submission context
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
