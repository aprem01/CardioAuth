"""CardioAuth FastAPI server."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import base64
import json
import os
import tempfile

import anthropic
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cardioauth.config import Config
from cardioauth.engines.auth_tracker import (
    get_all_authorizations,
    get_expiring_soon,
)
from cardioauth.engines.device_monitor import (
    get_device_patients,
    get_upcoming_eligible,
)
from cardioauth.engines.pre_procedure import (
    get_blocked_procedures,
    get_upcoming_procedures,
)
from cardioauth.orchestrator import Orchestrator, ReviewPackage
from cardioauth.engines.payer_rules import (
    check_auth_required,
    get_payer_matrix,
    flag_at_order_time,
)
from cardioauth.engines.icd10_checker import (
    check_code_pairing,
    suggest_stronger_codes,
    estimate_clean_claim_impact,
)
from cardioauth.engines.medical_necessity import (
    analyze_documentation,
    generate_recommendations,
    score_documentation_strength as score_doc_strength,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(
    title="CardioAuth",
    description="Cardiology prior authorization automation API",
    version="0.2.0",
)

# CORS — restrict to known origins in production
_ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "").split(",") if os.environ.get("ALLOWED_ORIGINS") else []
_ALLOWED_ORIGINS = [o.strip() for o in _ALLOWED_ORIGINS if o.strip()]
if not _ALLOWED_ORIGINS:
    # Default: allow the Railway domain + localhost for dev
    _ALLOWED_ORIGINS = [
        "https://cardioauth-production.up.railway.app",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

config = Config()

# Validate config at startup — fail loud, not silent
_missing_config = config.validate()
if _missing_config:
    logging.warning("CardioAuth starting with missing config: %s (some features will be unavailable)", _missing_config)

orchestrator = Orchestrator(config)

# In-memory store for review packages (use Redis/DB in production)
_reviews: dict[str, ReviewPackage] = {}


# Global exception handler — never leak stack traces to the client
from fastapi.responses import JSONResponse
from starlette.requests import Request

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again or contact support."},
    )


# Audit middleware — logs every API call with user context
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """Log every API request for HIPAA audit trail."""
    import time
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000)

    # Only audit /api/ routes (skip static files, health checks)
    path = request.url.path
    if path.startswith("/api/"):
        # Try to extract user from auth header (non-blocking)
        user_id = "anonymous"
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                import jwt as _jwt
                from cardioauth.auth import SUPABASE_JWT_SECRET
                if SUPABASE_JWT_SECRET:
                    payload = _jwt.decode(
                        auth_header[7:], SUPABASE_JWT_SECRET,
                        algorithms=["HS256"], audience="authenticated",
                    )
                    user_id = payload.get("sub", "unknown")[:8] + "***"
            except Exception:
                user_id = "invalid_token"

        logging.info(
            "AUDIT: %s %s | user=%s | status=%d | %dms",
            request.method, path, user_id, response.status_code, elapsed,
        )

        # Write to database audit log (non-blocking, best-effort)
        try:
            from cardioauth.db import save_audit_log, is_db_available
            if is_db_available():
                save_audit_log(
                    user_id=user_id,
                    method=request.method,
                    path=path,
                    status_code=response.status_code,
                    latency_ms=elapsed,
                    ip_address=request.client.host if request.client else "",
                )
        except Exception:
            pass  # Never block requests for audit failures

    return response

# Auth
from cardioauth.auth import (
    AuthUser,
    get_current_user,
    require_auth,
    require_admin,
    require_provider_or_admin,
    log_audit,
    is_auth_configured,
)

STATIC_DIR = Path(__file__).parent / "static"


class PARequest(BaseModel):
    patient_id: str
    procedure_code: str
    payer_id: str
    payer_name: str


class CustomPARequest(BaseModel):
    patient_name: str
    age: int
    sex: str
    procedure_name: str
    procedure_code: str
    payer_name: str
    diagnosis_codes: list[str] = []
    relevant_labs: list[dict[str, str]] = []
    relevant_imaging: list[dict[str, str]] = []
    relevant_medications: list[dict[str, str]] = []
    prior_treatments: list[str] = []
    comorbidities: list[str] = []
    ejection_fraction: str = ""
    ecg_findings: str = ""
    additional_notes: str = ""
    extraction_engine: str = "claude"  # "claude" | "comprehend" | "comprehend+claude"
    reasoning_mode: str = "unified"    # "unified" (new, default) | "multi-agent" (legacy)


class ApprovalRequest(BaseModel):
    request_id: str
    approved_by: str


class PayerResponseRequest(BaseModel):
    submission_id: str
    payer_response: dict[str, Any]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status() -> dict[str, Any]:
    """Return auth configuration status for the frontend."""
    from cardioauth.auth import SUPABASE_URL, SUPABASE_ANON_KEY, AUTH_DISABLED
    return {
        "auth_enabled": not AUTH_DISABLED,
        "auth_configured": is_auth_configured(),
        "supabase_url": SUPABASE_URL if SUPABASE_URL else None,
        "supabase_anon_key": SUPABASE_ANON_KEY if SUPABASE_ANON_KEY else None,
    }


@app.get("/api/auth/me")
async def get_me(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Return the current authenticated user."""
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "name": user.name,
        "is_authenticated": user.is_authenticated,
    }


EXTRACT_PROMPT = """\
You are a clinical data extraction specialist. Extract structured clinical data from this medical document image or PDF.

Return ONLY valid JSON with these fields (omit any field you cannot find):
{
  "patient_name": "",
  "age": 0,
  "sex": "M or F",
  "procedure_name": "",
  "procedure_code": "",
  "diagnosis_codes": ["ICD-10 codes found"],
  "ejection_fraction": "",
  "ecg_findings": "rhythm and any abnormalities — e.g. NSR, LBBB, paced rhythm, AFib, WPW",
  "relevant_labs": [{"name": "", "value": "", "unit": "", "date": ""}],
  "relevant_imaging": [{"type": "", "date": "", "result_summary": ""}],
  "relevant_medications": [{"name": "", "dose": "", "indication": ""}],
  "prior_treatments": ["prior procedures and interventions"],
  "comorbidities": [""],
  "additional_notes": ""
}

Extract every clinical detail you can find. For imaging reports, capture the full impression/findings.
For lab reports, capture all values with units. For clinical notes, extract diagnoses, medications, history.
Pay special attention to ECG findings (LBBB, paced rhythm, etc.) as they are critical for cardiac imaging PA approvals.
Be thorough — this data will be used for a prior authorization submission.
"""


@app.post("/api/extract-document")
async def extract_document(file: UploadFile = File(...), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Extract clinical data from an uploaded document image or PDF using Claude vision."""
    log_audit(user, "extract_document", file.filename or "unknown")
    api_key = config.anthropic_api_key
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")

    # Determine media type
    ct = file.content_type or ""
    if "pdf" in ct:
        media_type = "application/pdf"
    elif "png" in ct:
        media_type = "image/png"
    elif "jpeg" in ct or "jpg" in ct:
        media_type = "image/jpeg"
    elif "webp" in ct:
        media_type = "image/webp"
    else:
        # Try to detect from extension
        ext = (file.filename or "").lower().rsplit(".", 1)[-1]
        media_map = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
        media_type = media_map.get(ext, "image/jpeg")

    b64 = base64.standard_b64encode(contents).decode("utf-8")

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # Build content based on type
        if media_type == "application/pdf":
            image_content = {
                "type": "document",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            }
        else:
            image_content = {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            }

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": [
                    image_content,
                    {"type": "text", "text": EXTRACT_PROMPT},
                ],
            }],
        )

        raw = response.content[0].text
        # Try to parse JSON from the response
        try:
            # Handle markdown code blocks
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
            extracted = json.loads(raw)
        except json.JSONDecodeError:
            extracted = {"additional_notes": raw, "parse_error": True}

        return {"status": "ok", "extracted": extracted, "filename": file.filename}

    except anthropic.BadRequestError as e:
        logging.warning("Anthropic BadRequest during extraction: %s", e)
        msg = str(e)
        if "usage limit" in msg.lower() or "spend limit" in msg.lower():
            raise HTTPException(
                status_code=429,
                detail="The Anthropic API spend limit has been reached for this billing period. "
                       "The administrator needs to raise the spend cap at console.anthropic.com → Limits. "
                       "In the meantime, you can still enter clinical data manually using the form below.",
            )
        raise HTTPException(status_code=400, detail=f"Document could not be processed: {msg}")
    except anthropic.RateLimitError as e:
        logging.warning("Anthropic rate limit during extraction: %s", e)
        raise HTTPException(
            status_code=429,
            detail="API rate limit hit. Please wait a moment and try again, or enter the data manually.",
        )
    except Exception as e:
        logging.exception("Document extraction failed")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")


@app.get("/api/reference")
def get_reference_data() -> dict[str, Any]:
    """Return ICD-10 descriptions and demo patient info for frontend lookups."""
    from cardioauth.demo import ICD10_DESCRIPTIONS, DEMO_PATIENT_INFO
    return {"icd10": ICD10_DESCRIPTIONS, "patients": DEMO_PATIENT_INFO}


@app.post("/api/pa/custom-request")
def create_custom_pa_request(req: CustomPARequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run PA pipeline with user-provided clinical data."""
    log_audit(user, "create_custom_pa", f"CPT={req.procedure_code} payer={req.payer_name}")
    import uuid
    from cardioauth.models.chart import ChartData, LabResult, ImagingResult, Medication
    from cardioauth.demo import get_demo_policy, get_demo_reasoning

    # Build ChartData from custom input
    labs = [LabResult(name=l.get("name", ""), value=l.get("value", ""), date=l.get("date", ""), unit=l.get("unit", "")) for l in req.relevant_labs]
    imaging = [ImagingResult(type=i.get("type", ""), date=i.get("date", ""), result_summary=i.get("result_summary", "")) for i in req.relevant_imaging]
    meds = [Medication(name=m.get("name", ""), dose=m.get("dose", ""), start_date=m.get("start_date", ""), indication=m.get("indication", "")) for m in req.relevant_medications]

    # If ECG findings provided, add as imaging entry — critical for PET/SPECT approvals
    if req.ecg_findings:
        imaging.append(ImagingResult(
            type="ECG (12-lead)",
            date="",
            result_summary=req.ecg_findings,
        ))

    # If ejection fraction provided as standalone field, add to imaging summary
    if req.ejection_fraction:
        imaging.append(ImagingResult(
            type="Ejection Fraction (LVEF)",
            date="",
            result_summary=f"LVEF {req.ejection_fraction}",
        ))

    chart_data = ChartData(
        patient_id=f"CUSTOM-{uuid.uuid4().hex[:6].upper()}",
        procedure_requested=req.procedure_name,
        procedure_code=req.procedure_code,
        diagnosis_codes=req.diagnosis_codes,
        relevant_labs=labs,
        relevant_imaging=imaging,
        relevant_medications=meds,
        prior_treatments=req.prior_treatments,
        comorbidities=req.comorbidities,
        attending_physician="",
        insurance_id="",
        payer_name=req.payer_name,
        confidence_score=0.95 if labs and imaging else 0.75,
        missing_fields=[],
    )

    # ── Optional: AWS Comprehend Medical preprocessing ──
    # When extraction_engine includes "comprehend", run Comprehend Medical
    # as a preprocessing step before Claude reasoning. This produces cleaner
    # structured entities (meds, LVEF, ECG) that make Claude's job easier.
    comprehend_stats = None
    use_comprehend = req.extraction_engine in ("comprehend", "comprehend+claude")
    if use_comprehend:
        try:
            from cardioauth.agents.comprehend_medical import enrich_chart_with_comprehend
            chart_dict = chart_data.model_dump()
            chart_dict["additional_notes"] = req.additional_notes
            enriched = enrich_chart_with_comprehend(chart_dict)
            comprehend_stats = {
                "enriched": enriched.get("_comprehend_enriched", False),
                "entity_count": enriched.get("_comprehend_entity_count", 0),
            }
            # Rebuild ChartData from enriched dict
            chart_data = ChartData(**{
                k: v for k, v in enriched.items()
                if k in ChartData.model_fields and not k.startswith("_")
            })
            logging.info("Comprehend Medical enrichment: %s", comprehend_stats)
        except Exception as e:
            logging.warning("Comprehend Medical enrichment failed (continuing with Claude only): %s", e)
            comprehend_stats = {"enriched": False, "error": str(e)}

    # Get policy — use Claude POLICY_AGENT with real CMS context (no hardcoding)
    policy_data = None
    if config.anthropic_api_key:
        try:
            from cardioauth.agents.policy_agent import PolicyAgent
            from cardioauth.integrations.cms_coverage import get_cms_coverage_context
            policy_agent = PolicyAgent(config)
            cms_ctx = get_cms_coverage_context(req.procedure_code)
            policy_data = policy_agent.run(req.procedure_code, req.payer_name, cms_context=cms_ctx)
        except Exception as e:
            logging.warning("POLICY_AGENT failed for custom request: %s", e)

    if policy_data is None:
        # Last-resort fallback only if Claude unavailable
        try:
            policy_data = get_demo_policy(req.procedure_code, req.payer_name)
        except KeyError:
            from cardioauth.models.policy import PolicyData
            policy_data = PolicyData(
                payer=req.payer_name,
                procedure=req.procedure_name,
                cpt_code=req.procedure_code,
                auth_required=True,
                clinical_criteria=[],
                documentation_required=[],
                submission_format="portal",
                typical_turnaround_days=5,
            )

    # ── NEW: Unified reasoning path (default) ──
    # Combines raw clinical narrative + extracted relationships + precedents
    # into a single Claude call that reasons + scores taxonomy at once.
    # Preserves clinical relationships that multi-agent bucketing destroyed.
    reasoning_mode = getattr(req, 'reasoning_mode', 'unified')
    unified_ctx = None

    if reasoning_mode == "unified" and config.anthropic_api_key:
        try:
            from cardioauth.case_context import CaseContext
            from cardioauth.agents.relationship_extractor import extract_relationships
            from cardioauth.agents.precedent_retriever import retrieve_precedents, store_case_as_precedent
            from cardioauth.agents.unified_reasoner import reason_with_unified_agent

            unified_ctx = CaseContext(
                case_id=f"{chart_data.patient_id}-{req.procedure_code}",
                procedure_code=req.procedure_code,
                procedure_name=req.procedure_name,
                payer_name=req.payer_name,
                user_id=user.id,
                chart_data={**chart_data.model_dump(), "additional_notes": req.additional_notes, "patient_name": req.patient_name},
                policy_data=policy_data.model_dump() if policy_data else {},
            )
            # Build full clinical narrative (raw note) from all fields
            unified_ctx.build_clinical_narrative()

            # Step: extract clinical relationships (rule-based, fast)
            extract_relationships(unified_ctx, config)

            # Step: retrieve similar past cases from Pinecone (if configured)
            retrieve_precedents(unified_ctx, top_k=5)

            # Step: unified reasoning — one Claude call with full context
            reason_with_unified_agent(unified_ctx, config)

            # Translate UnifiedReasoner output into ReasoningResult shape so
            # the rest of the response building code works unchanged.
            from cardioauth.models.reasoning import ReasoningResult, CriterionCheck
            criteria_met_objs = []
            criteria_not_met_objs = []
            for m in unified_ctx.criterion_matches:
                if m.get("status") == "met":
                    criteria_met_objs.append(CriterionCheck(
                        criterion=m.get("code", "") + ": " + m.get("reasoning", "")[:100],
                        met=True,
                        evidence=m.get("evidence_quote", "") or m.get("reasoning", "")[:200],
                        confidence=float(m.get("confidence", 0.8)),
                    ))
                elif m.get("status") == "not_met":
                    criteria_not_met_objs.append(CriterionCheck(
                        criterion=m.get("code", "") + ": " + m.get("reasoning", "")[:100],
                        met=False,
                        gap=m.get("gap", "") or m.get("reasoning", "")[:200],
                        recommendation=m.get("recommendation", ""),
                        confidence=float(m.get("confidence", 0.8)),
                    ))

            reasoning = ReasoningResult(
                criteria_met=criteria_met_objs,
                criteria_not_met=criteria_not_met_objs,
                approval_likelihood_score=unified_ctx.approval_score,
                approval_likelihood_label=unified_ctx.approval_label,
                pa_narrative_draft=unified_ctx.narrative_draft,
                missing_documentation=[],
                guideline_citations=[],
                cardiologist_review_flags=[],
            )
            logging.info("Unified reasoning complete: %s (%.2f), %d matches",
                         unified_ctx.approval_label, unified_ctx.approval_score,
                         len(unified_ctx.criterion_matches))
        except Exception as e:
            logging.warning("Unified reasoning failed, falling back to multi-agent: %s", e)
            reasoning_mode = "multi-agent"
            unified_ctx = None

    if reasoning_mode != "unified" or unified_ctx is None:
        # Legacy multi-agent path
        if config.anthropic_api_key:
            from cardioauth.agents.reasoning_agent import ReasoningAgent
            try:
                reasoning_agent = ReasoningAgent(config)
                reasoning = reasoning_agent.run(chart_data, policy_data)
            except Exception as e:
                logging.warning("Claude reasoning failed for custom request: %s", e)
                reasoning = get_demo_reasoning(chart_data, policy_data)
        else:
            reasoning = get_demo_reasoning(chart_data, policy_data)

    # Store for approval
    from cardioauth.orchestrator import ReviewPackage
    review = ReviewPackage(
        chart_data=chart_data,
        policy_data=policy_data,
        reasoning=reasoning,
        requires_human_action=[],
    )
    request_id = chart_data.patient_id + "-" + chart_data.procedure_code

    # Taxonomy match:
    #   - If we ran unified reasoning, build the matrix from its output.
    #   - Otherwise, run the legacy TAXONOMY_MATCHER.
    taxonomy_match = None
    if unified_ctx is not None:
        try:
            # Build taxonomy_match dict directly from unified_ctx
            from cardioauth.taxonomy.taxonomy import TAXONOMY_VERSION
            taxonomy_match = {
                "case_id": request_id,
                "procedure_code": req.procedure_code,
                "payer": req.payer_name,
                "taxonomy_version": TAXONOMY_VERSION,
                "matches": unified_ctx.criterion_matches,
                "emerging_criteria": [],
                "overall_score": unified_ctx.approval_score,
                "label": unified_ctx.approval_label,
                "score_required": unified_ctx.approval_score,
                "score_supporting": unified_ctx.approval_score,
                "validation_warnings": [],
                "reasoning_trace": [
                    {"agent": t.agent_name, "action": t.action, "summary": t.output_summary}
                    for t in unified_ctx.reasoning_trace
                ],
                "relationships": [
                    {"conclusion": r.conclusion, "supports": r.supports_criterion, "quote": r.evidence_quote}
                    for r in unified_ctx.relationships
                ],
                "precedents": [
                    {"case_id": p.case_id, "outcome": p.outcome, "similarity": p.similarity, "summary": p.summary[:200]}
                    for p in unified_ctx.precedents
                ],
            }
        except Exception as e:
            logging.warning("Failed to build taxonomy_match from unified ctx: %s", e)

    if taxonomy_match is None and config.anthropic_api_key:
        try:
            from cardioauth.taxonomy import match_case_to_taxonomy, record_emerging_criterion
            tax_result = match_case_to_taxonomy(
                chart_data.model_dump(),
                req.procedure_code,
                req.payer_name,
                config,
                case_id=request_id,
            )
            for ec in tax_result.emerging_criteria:
                try:
                    record_emerging_criterion(
                        suggested_code=ec.get("suggested_code", "MISC"),
                        category=ec.get("category", "MISC"),
                        description=ec.get("description", ""),
                        rationale=ec.get("rationale", ""),
                        case_id=request_id,
                        procedure_code=req.procedure_code,
                        payer=req.payer_name,
                    )
                except Exception:
                    pass
            taxonomy_match = tax_result.to_dict()
        except Exception as e:
            logging.warning("Taxonomy matcher failed for custom case: %s", e)

    # Store completed case as precedent for future retrievals
    if unified_ctx is not None:
        try:
            from cardioauth.agents.precedent_retriever import store_case_as_precedent
            store_case_as_precedent(unified_ctx, outcome="analyzed")
        except Exception:
            pass

    review.taxonomy_match = taxonomy_match
    _reviews[request_id] = review

    # Persist to database (best-effort — don't block response if DB fails)
    try:
        from cardioauth.db import save_pa_submission, is_db_available
        if is_db_available():
            tax = taxonomy_match or {}
            save_pa_submission(
                user_id=user.id,
                patient_id=chart_data.patient_id,
                patient_name=req.patient_name,
                age=req.age,
                sex=req.sex,
                payer=req.payer_name,
                procedure_code=req.procedure_code,
                procedure_name=req.procedure_name,
                icd10_codes=req.diagnosis_codes,
                extraction_engine=getattr(req, 'extraction_engine', 'claude'),
                approval_score=reasoning.approval_likelihood_score,
                approval_label=reasoning.approval_likelihood_label,
                criteria_met=len(reasoning.criteria_met),
                criteria_not_met=len(reasoning.criteria_not_met),
                criteria_total=len(reasoning.criteria_met) + len(reasoning.criteria_not_met),
                narrative_draft=reasoning.pa_narrative_draft[:2000],
                status="analyzed",
            )
    except Exception as e:
        logging.warning("DB save failed (non-blocking): %s", e)

    return {
        "request_id": request_id,
        "patient_info": {"name": req.patient_name, "age": req.age, "sex": req.sex, "mrn": chart_data.patient_id},
        "approval_likelihood": {
            "score": reasoning.approval_likelihood_score,
            "label": reasoning.approval_likelihood_label,
        },
        "narrative_draft": reasoning.pa_narrative_draft,
        "criteria_met": [c.model_dump() for c in reasoning.criteria_met],
        "criteria_not_met": [c.model_dump() for c in reasoning.criteria_not_met],
        "missing_documentation": reasoning.missing_documentation,
        "guideline_citations": reasoning.guideline_citations,
        "requires_human_action": review.requires_human_action,
        "chart_confidence": chart_data.confidence_score,
        "chart_data": chart_data.model_dump(),
        "policy_data": policy_data.model_dump(),
        "taxonomy_match": taxonomy_match,
        "system_warnings": (review.system_warnings if hasattr(review, 'system_warnings') else []) + (unified_ctx.system_warnings if unified_ctx else []),
        "retrieved_chunks": getattr(policy_data, "__dict__", {}).get("_retrieved_chunks", []),
        "criterion_citations": getattr(policy_data, "__dict__", {}).get("_criterion_citations", []),
        "reasoning_mode": reasoning_mode,
        "reasoning_trace": (
            [{"agent": t.agent_name, "action": t.action, "summary": t.output_summary, "ms": t.duration_ms}
             for t in unified_ctx.reasoning_trace]
            if unified_ctx else []
        ),
        "clinical_relationships": (
            [{"conclusion": r.conclusion, "supports": r.supports_criterion, "quote": r.evidence_quote, "confidence": r.confidence}
             for r in unified_ctx.relationships]
            if unified_ctx else []
        ),
    }


@app.post("/api/pa/request")
def create_pa_request(req: PARequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
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
        "taxonomy_match": review.taxonomy_match,
        "system_warnings": review.system_warnings,
        "retrieved_chunks": review.retrieved_chunks,
        "criterion_citations": review.criterion_citations,
    }


@app.post("/api/pa/approve")
def approve_and_submit(req: ApprovalRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
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


class ModifierCheckRequest(BaseModel):
    cpt_codes: list[str]
    modifiers: list[str] = []


class BundlingCheckRequest(BaseModel):
    cpt_codes: list[str]


class P2PRequest(BaseModel):
    patient_id: str
    procedure_code: str
    payer: str


class StrengthScoreRequest(BaseModel):
    patient_id: str
    procedure_code: str
    payer: str


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


class AuthCheckRequest(BaseModel):
    cpt_code: str
    payer: str


class CodeCheckRequest(BaseModel):
    cpt_code: str
    icd10_codes: list[str]


class DocCheckRequest(BaseModel):
    patient_id: str
    procedure_code: str


@app.get("/api/payer-matrix")
def get_payer_matrix(payer: str | None = None) -> dict[str, Any]:
    """Get the full payer authorization matrix."""
    from cardioauth.engines.payer_rules import get_payer_matrix as _get
    return {"matrix": _get(payer)}


@app.post("/api/check-auth")
def check_auth(req: AuthCheckRequest) -> dict[str, Any]:
    """Check if a procedure requires auth for a specific payer."""
    from cardioauth.engines.payer_rules import check_auth_required
    return check_auth_required(req.cpt_code, req.payer)


@app.post("/api/check-codes")
def check_codes(req: CodeCheckRequest) -> dict[str, Any]:
    """Validate CPT + ICD-10 code pairings for clean claims."""
    from cardioauth.engines.icd10_checker import check_code_pairing
    return check_code_pairing(req.cpt_code, req.icd10_codes)


@app.post("/api/check-documentation")
def check_documentation(req: DocCheckRequest) -> dict[str, Any]:
    """Analyze medical necessity documentation completeness."""
    from cardioauth.engines.medical_necessity import analyze_documentation
    from cardioauth.demo import get_demo_chart
    try:
        chart = get_demo_chart(req.patient_id, req.procedure_code)
        return analyze_documentation(chart.model_dump(), req.procedure_code)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ─────────────────────────── RAG API ───────────────────────────


@app.get("/api/rag/corpus")
def rag_corpus(payer: str | None = None, cpt: str | None = None) -> dict[str, Any]:
    """List all policy chunks in the corpus, optionally filtered."""
    from cardioauth.rag import load_corpus, get_corpus_stats
    from cardioauth.rag.corpus import ensure_corpus_seeded
    ensure_corpus_seeded()
    chunks = load_corpus()
    if payer:
        chunks = [c for c in chunks if c.payer.lower() == payer.lower() or c.chunk_type in ("ncd", "lcd")]
    if cpt:
        chunks = [c for c in chunks if cpt in c.applies_to_cpt]
    return {
        "stats": get_corpus_stats(),
        "chunks": [c.to_dict() for c in chunks],
        "filter": {"payer": payer, "cpt": cpt},
        "count": len(chunks),
    }


@app.get("/api/rag/stats")
def rag_stats() -> dict[str, Any]:
    """Aggregate stats over the corpus for the Policy Library page."""
    from cardioauth.rag import get_corpus_stats
    from cardioauth.rag.corpus import ensure_corpus_seeded
    ensure_corpus_seeded()
    return get_corpus_stats()


class RAGSearchRequest(BaseModel):
    cpt_code: str
    payer: str
    procedure_name: str = ""
    top_k: int = 6


@app.post("/api/rag/search")
def rag_search(req: RAGSearchRequest) -> dict[str, Any]:
    """Run a retrieval against the policy corpus and return ranked chunks."""
    from cardioauth.rag import retrieve_for_pa
    results = retrieve_for_pa(
        cpt_code=req.cpt_code,
        payer=req.payer,
        procedure_name=req.procedure_name,
        top_k=req.top_k,
    )
    return {
        "query": {"cpt_code": req.cpt_code, "payer": req.payer, "procedure_name": req.procedure_name},
        "count": len(results),
        "results": [r.to_dict() for r in results],
    }


class RAGIngestChunk(BaseModel):
    payer: str
    applies_to_cpt: list[str]
    procedure_name: str
    text: str
    source_document: str
    source_document_number: str = ""
    section_heading: str = ""
    page: int | None = None
    last_updated: str = ""
    source_url: str = ""
    chunk_type: str = "policy"


class RAGIngestRequest(BaseModel):
    chunks: list[RAGIngestChunk]


@app.post("/api/rag/ingest")
def rag_ingest(req: RAGIngestRequest) -> dict[str, Any]:
    """Add pre-chunked policy text to the corpus."""
    from cardioauth.rag import PolicyChunk, add_chunks
    new_chunks = [PolicyChunk.new(**c.model_dump()) for c in req.chunks]
    added = add_chunks(new_chunks)
    return {"requested": len(req.chunks), "added": added}


@app.post("/api/rag/upload-document")
async def rag_upload_document(
    file: UploadFile = File(...),
    payer: str = "",
    applies_to_cpt: str = "",
    procedure_name: str = "",
    source_document: str = "",
    source_document_number: str = "",
    last_updated: str = "",
    source_url: str = "",
    chunk_type: str = "policy",
    preview: bool = False,
    force_vision: bool = False,
) -> dict[str, Any]:
    """Upload a payer policy document, chunk it, and add to the corpus.

    Accepts PDF, plain text, markdown, and images (PNG/JPG/WEBP).

    Routing inside the chunker:
      - Clean text PDFs go through pypdf (fast, free)
      - Scanned, table-heavy, or image-heavy PDFs auto-fall-back to
        Claude vision PDF extraction (handles tables, multi-column,
        embedded figures, scans)
      - Image uploads always go through Claude vision
      - force_vision=true skips the pypdf attempt entirely

    If preview=true, returns the extracted chunks WITHOUT writing them
    to the corpus so the user can review before confirming.
    """
    contents = await file.read()
    if len(contents) > 30 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 30MB)")
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file")

    # Parse CPT list (comma-separated string from the form)
    cpts = [c.strip() for c in (applies_to_cpt or "").split(",") if c.strip()]
    if not cpts:
        raise HTTPException(
            status_code=400,
            detail="At least one CPT code is required so the chunks can be retrieved.",
        )
    if not payer:
        raise HTTPException(status_code=400, detail="Payer name is required.")
    if not source_document:
        source_document = file.filename or "Uploaded document"

    from cardioauth.rag import PolicyChunk, add_chunks, chunk_document
    try:
        drafts = chunk_document(
            data=contents,
            content_type=file.content_type or "",
            filename=file.filename or "",
            force_vision=force_vision,
        )
    except Exception as e:
        logging.exception("Document chunking failed")
        raise HTTPException(status_code=500, detail=f"Could not parse document: {e}")

    if not drafts:
        raise HTTPException(
            status_code=400,
            detail="No usable text could be extracted from this document. "
                   "If this is a scanned PDF, OCR is required (not supported yet).",
        )

    # Build PolicyChunk objects from the drafts + form metadata
    new_chunks = [
        PolicyChunk.new(
            payer=payer,
            applies_to_cpt=cpts,
            procedure_name=procedure_name or source_document,
            text=d.text,
            source_document=source_document,
            source_document_number=source_document_number,
            section_heading=d.section_heading,
            page=d.page,
            last_updated=last_updated,
            source_url=source_url,
            chunk_type=chunk_type,
        )
        for d in drafts
    ]

    if preview:
        # Don't persist; return the chunks so the user can review.
        return {
            "filename": file.filename,
            "preview": True,
            "extracted_chunks": [c.to_dict() for c in new_chunks],
            "chunk_count": len(new_chunks),
            "total_chars": sum(len(c.text) for c in new_chunks),
        }

    added = add_chunks(new_chunks)
    return {
        "filename": file.filename,
        "preview": False,
        "extracted_chunks": [c.to_dict() for c in new_chunks],
        "chunk_count": len(new_chunks),
        "added": added,
        "total_chars": sum(len(c.text) for c in new_chunks),
    }


class DeleteChunksRequest(BaseModel):
    chunk_ids: list[str] = []
    source_document: str = ""


@app.post("/api/rag/delete")
def rag_delete(req: DeleteChunksRequest) -> dict[str, Any]:
    """Delete chunks from the corpus by id, or delete all chunks of a document."""
    from cardioauth.rag import delete_chunks, delete_document
    removed_by_id = 0
    removed_by_doc = 0
    if req.chunk_ids:
        removed_by_id = delete_chunks(req.chunk_ids)
    if req.source_document:
        removed_by_doc = delete_document(req.source_document)
    return {
        "removed_by_id": removed_by_id,
        "removed_by_document": removed_by_doc,
        "total_removed": removed_by_id + removed_by_doc,
    }


# ─────────────────────────── Taxonomy API ───────────────────────────


@app.get("/api/taxonomy")
def get_taxonomy(procedure_code: str = "", category: str = "") -> dict[str, Any]:
    """Return the full criterion taxonomy, optionally filtered."""
    from cardioauth.taxonomy import (
        CRITERION_TAXONOMY, TAXONOMY_VERSION, get_categories,
        get_criteria_for_procedure,
    )
    if procedure_code:
        criteria = get_criteria_for_procedure(procedure_code)
    else:
        criteria = list(CRITERION_TAXONOMY.values())
    if category:
        criteria = [c for c in criteria if c.category == category]
    return {
        "version": TAXONOMY_VERSION,
        "categories": get_categories(),
        "total": len(criteria),
        "criteria": [
            {
                "code": c.code,
                "category": c.category,
                "short_name": c.short_name,
                "definition": c.definition,
                "evidence_type": c.evidence_type,
                "applies_to": c.applies_to,
                "guideline_source": c.guideline_source,
                "severity": c.severity,
                "introduced_version": c.introduced_version,
            }
            for c in criteria
        ],
    }


@app.get("/api/taxonomy/categories")
def get_taxonomy_categories() -> dict[str, Any]:
    """Return the category code → label map."""
    from cardioauth.taxonomy import get_categories
    return {"categories": get_categories()}


@app.get("/api/taxonomy/emerging")
def get_emerging() -> dict[str, Any]:
    """Return the emerging criteria queue."""
    from cardioauth.taxonomy import get_emerging_queue
    return get_emerging_queue()


class PromoteRequest(BaseModel):
    suggested_code: str
    formal_code: str


@app.post("/api/taxonomy/promote")
def promote_emerging(req: PromoteRequest) -> dict[str, Any]:
    """Mark an emerging criterion as promoted (manual taxonomy update still required)."""
    from cardioauth.taxonomy import promote_to_taxonomy
    return promote_to_taxonomy(req.suggested_code, req.formal_code)


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


@app.get("/api/authorizations")
def list_authorizations():
    """Return all tracked authorizations with computed alert fields."""
    return get_all_authorizations()


# ---------------------------------------------------------------------------
# Database-backed endpoints (submission history, real analytics)
# ---------------------------------------------------------------------------

@app.get("/api/submissions")
async def list_submissions(
    payer: str = "",
    status: str = "",
    limit: int = 50,
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """List PA submission history from the database."""
    try:
        from cardioauth.db import get_submission_history, is_db_available
        if not is_db_available():
            return {"submissions": [], "source": "unavailable"}
        # Non-admin users only see their own submissions
        uid = "" if user.role == "admin" else user.id
        submissions = get_submission_history(user_id=uid, payer=payer, status=status, limit=limit)
        return {"submissions": submissions, "total": len(submissions), "source": "database"}
    except Exception as e:
        logging.warning("Submissions query failed: %s", e)
        return {"submissions": [], "source": "error"}


@app.get("/api/analytics/live")
async def get_live_analytics(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Real analytics from the database (vs mock /api/analytics)."""
    try:
        from cardioauth.db import get_analytics_from_db, is_db_available
        if not is_db_available():
            return {"source": "unavailable"}
        uid = "" if user.role == "admin" else user.id
        return get_analytics_from_db(user_id=uid)
    except Exception as e:
        logging.warning("Live analytics failed: %s", e)
        return {"source": "error", "error": str(e)}


@app.get("/api/db/status")
def db_status() -> dict[str, Any]:
    """Check database connectivity."""
    try:
        from cardioauth.db import is_db_available
        available = is_db_available()
        return {"available": available, "retention_days": int(os.environ.get("DATA_RETENTION_DAYS", "365"))}
    except Exception:
        return {"available": False}


@app.get("/api/authorizations/expiring")
def list_expiring_authorizations(days: int = 5):
    """Return authorizations expiring within the given number of days."""
    return get_expiring_soon(days)


@app.get("/api/devices")
def list_device_patients():
    """Return all monitored device patients with billing status."""
    return get_device_patients()


@app.get("/api/devices/eligible")
def list_eligible_devices(days: int = 14):
    """Return device patients becoming billing-eligible soon."""
    return get_upcoming_eligible(days)


@app.get("/api/pre-check")
def list_pre_checks(days: int = 7):
    """Return upcoming procedures with pre-procedure check status."""
    return get_upcoming_procedures(days)


@app.get("/api/pre-check/blocked")
def list_blocked_procedures():
    """Return procedures that cannot proceed due to unresolved issues."""
    return get_blocked_procedures()


# ---------------------------------------------------------------------------
# AWS Comprehend Medical
# ---------------------------------------------------------------------------

@app.get("/api/comprehend/status")
def get_comprehend_status() -> dict[str, Any]:
    """Check if AWS Comprehend Medical is available and configured."""
    try:
        from cardioauth.agents.comprehend_medical import is_comprehend_available
        available = is_comprehend_available()
    except Exception:
        available = False
    return {
        "available": available,
        "enabled_by_default": config.use_comprehend_medical,
        "region": config.aws_region,
        "info": {
            "service": "AWS Comprehend Medical",
            "hipaa_eligible": True,
            "free_tier": "25,000 units/month (1 unit = 100 UTF-8 chars)",
            "use_case": "Clinical NLP preprocessing before Claude reasoning",
            "benefits": [
                "Purpose-built for clinical entity extraction (medications, labs, LVEF, ECG)",
                "HIPAA-eligible with BAA — safe for real PHI",
                "Catches entities Claude might miss on first pass",
                "Free tier covers ~50-100 clinical documents/month",
            ],
        },
    }


@app.post("/api/comprehend/test")
def test_comprehend_extraction(body: dict) -> dict[str, Any]:
    """Test Comprehend Medical extraction on sample text.

    Send {"text": "clinical text..."} to see what entities are extracted.
    Useful for comparing against the current Claude-only pipeline.
    """
    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    try:
        from cardioauth.agents.comprehend_medical import extract_entities
        result = extract_entities(text)
        return {
            "status": "ok",
            "engine": "aws_comprehend_medical",
            "result": result.to_dict(),
            "entity_count": len(result.raw_entities),
        }
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Comprehend Medical error: {str(e)}")


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


# ---------------------------------------------------------------------------
# Modifier Checker & P2P Prevention Endpoints
# ---------------------------------------------------------------------------

from cardioauth.engines.modifier_checker import (
    check_modifiers,
    check_bundling,
    suggest_modifiers,
)
from cardioauth.engines.p2p_prevention import (
    predict_p2p_likelihood,
    get_strength_recommendations,
    score_documentation_strength,
    estimate_approval_without_p2p,
)


@app.post("/api/check-modifiers")
def api_check_modifiers(req: ModifierCheckRequest) -> dict[str, Any]:
    """Validate CPT code + modifier combinations against NCCI edit pairs."""
    result = check_modifiers(req.cpt_codes, req.modifiers)
    suggestions = suggest_modifiers(req.cpt_codes)
    result["modifier_suggestions"] = suggestions
    return result


@app.post("/api/check-bundling")
def api_check_bundling(req: BundlingCheckRequest) -> dict[str, Any]:
    """Identify bundled code pairs that cannot be billed together."""
    return check_bundling(req.cpt_codes)


@app.post("/api/predict-p2p")
def api_predict_p2p(req: P2PRequest) -> dict[str, Any]:
    """Predict likelihood of peer-to-peer review for a patient/procedure/payer."""
    from cardioauth.demo import get_demo_chart, get_demo_policy

    try:
        chart_data = get_demo_chart(req.patient_id, req.procedure_code)
    except (KeyError, Exception) as e:
        raise HTTPException(status_code=404, detail=f"Patient/procedure not found: {e}")

    try:
        policy_data = get_demo_policy(req.procedure_code, req.payer)
    except (KeyError, Exception):
        policy_data = None

    result = predict_p2p_likelihood(chart_data, policy_data, req.payer, req.procedure_code)
    return result


@app.post("/api/strength-score")
def api_strength_score(req: StrengthScoreRequest) -> dict[str, Any]:
    """Score documentation strength and return improvement recommendations."""
    from cardioauth.demo import get_demo_chart

    try:
        chart_data = get_demo_chart(req.patient_id, req.procedure_code)
    except (KeyError, Exception) as e:
        raise HTTPException(status_code=404, detail=f"Patient/procedure not found: {e}")

    doc_score = score_documentation_strength(chart_data)
    recommendations = get_strength_recommendations(chart_data, req.procedure_code, req.payer)
    projected = estimate_approval_without_p2p(
        current_score=doc_score,
        fixes_applied=[r["recommended_language"] for r in recommendations],
        payer=req.payer,
        cpt_code=req.procedure_code,
    )

    return {
        "patient_id": req.patient_id,
        "procedure_code": req.procedure_code,
        "payer": req.payer,
        "documentation_strength": doc_score,
        "strength_label": (
            "Strong" if doc_score >= 0.75 else
            "Moderate" if doc_score >= 0.50 else
            "Weak" if doc_score >= 0.25 else
            "Critical — significant gaps"
        ),
        "projected_approval_after_fixes": projected,
        "recommendations": recommendations,
        "recommendation_count": len(recommendations),
    }


# ---------------------------------------------------------------------------
# Payer Rules / ICD-10 Checker / Medical Necessity Endpoints
# ---------------------------------------------------------------------------


class CheckAuthRequest(BaseModel):
    cpt_code: str
    payer: str


class CheckCodesRequest(BaseModel):
    cpt_code: str
    icd10_codes: list[str]


class CheckDocumentationRequest(BaseModel):
    patient_id: str
    procedure_code: str


@app.get("/api/payer-matrix")
def api_payer_matrix(payer: str | None = None) -> dict[str, Any]:
    """Return the full payer authorization matrix, optionally filtered by payer name."""
    return get_payer_matrix(payer=payer)


@app.post("/api/check-auth")
def api_check_auth(req: CheckAuthRequest) -> dict[str, Any]:
    """Check whether prior auth is required for a CPT code + payer combination."""
    auth_result = check_auth_required(req.cpt_code, req.payer)
    flag_result = flag_at_order_time(req.cpt_code, req.payer)
    return {
        "auth_check": auth_result,
        "order_alert": flag_result,
    }


@app.post("/api/check-codes")
def api_check_codes(req: CheckCodesRequest) -> dict[str, Any]:
    """Validate CPT + ICD-10 code pairings and return strength assessment."""
    pairing_result = check_code_pairing(req.cpt_code, req.icd10_codes)

    # If there are weak codes, include upgrade suggestions and impact estimate
    upgrade_suggestions = []
    for assessment in pairing_result.get("code_assessments", []):
        if assessment.get("strength") == "weak":
            suggestions = suggest_stronger_codes(req.cpt_code, assessment["icd10_code"])
            upgrade_suggestions.append({
                "weak_code": assessment["icd10_code"],
                "suggestions": suggestions,
            })

    # Estimate clean claim impact if upgrades are available
    impact = None
    if upgrade_suggestions:
        suggested_codes = []
        for item in upgrade_suggestions:
            if item["suggestions"]:
                suggested_codes.append(item["suggestions"][0]["code"])
            else:
                suggested_codes.append(item["weak_code"])
        # Replace weak codes with suggested; keep strong codes as-is
        current = req.icd10_codes
        impact = estimate_clean_claim_impact(current, suggested_codes)

    pairing_result["detailed_upgrade_suggestions"] = upgrade_suggestions
    pairing_result["clean_claim_impact"] = impact
    return pairing_result


@app.post("/api/check-documentation")
def api_check_documentation(req: CheckDocumentationRequest) -> dict[str, Any]:
    """Analyze chart documentation completeness for a procedure and return gaps + recommendations."""
    from cardioauth.demo import get_demo_chart

    try:
        chart = get_demo_chart(req.patient_id, req.procedure_code)
        chart_data = chart.model_dump()
    except (KeyError, Exception) as e:
        raise HTTPException(status_code=404, detail=f"Patient/procedure not found: {e}")

    analysis = analyze_documentation(chart_data, req.procedure_code)

    # Generate recommendations for missing elements
    recommendations = []
    if analysis.get("found") and analysis.get("missing_elements"):
        recommendations = generate_recommendations(analysis["missing_elements"])

    # Score
    doc_score = score_doc_strength(chart_data, req.procedure_code)

    return {
        "patient_id": req.patient_id,
        "procedure_code": req.procedure_code,
        "analysis": analysis,
        "documentation_score": doc_score,
        "recommendations": recommendations,
        "recommendation_count": len(recommendations),
    }


# ---------------------------------------------------------------------------
# Denial Analytics Endpoints
# ---------------------------------------------------------------------------

from cardioauth.engines.denial_analytics import (
    get_denial_summary,
    get_denials_by_payer,
    get_denials_by_procedure,
    get_denials_by_physician,
    get_denials_by_reason,
    get_denial_trends,
    identify_patterns,
    get_pending_at_risk,
    calculate_revenue_impact,
)


@app.get("/api/denials/summary")
def denials_summary():
    """Overall denial statistics — total denials, appeal rate, overturn rate."""
    return get_denial_summary()


@app.get("/api/denials/by-payer")
def denials_by_payer():
    """Denial breakdown by insurance payer with rates and top reasons."""
    return get_denials_by_payer()


@app.get("/api/denials/by-procedure")
def denials_by_procedure():
    """Denial breakdown by cardiology procedure type."""
    return get_denials_by_procedure()


@app.get("/api/denials/by-physician")
def denials_by_physician():
    """Denial breakdown by physician with documentation scores."""
    return get_denials_by_physician()


@app.get("/api/denials/by-reason")
def denials_by_reason():
    """Denials grouped by category (documentation, coding, medical necessity, etc.)."""
    return get_denials_by_reason()


@app.get("/api/denials/trends")
def denials_trends(months: int = 6):
    """Monthly denial trend data."""
    return get_denial_trends(months=months)


@app.get("/api/denials/patterns")
def denials_patterns():
    """AI-detected denial patterns with actionable insights."""
    return identify_patterns()


@app.get("/api/denials/at-risk")
def denials_at_risk():
    """Pending requests that match historical denial patterns."""
    return get_pending_at_risk()


@app.get("/api/denials/revenue-impact")
def denials_revenue_impact():
    """Revenue impact analysis — lost, recovered, and preventable amounts."""
    return calculate_revenue_impact()


# ---------------------------------------------------------------------------
# Government API Integrations — ICD-10 (NLM) & RxNorm (NLM)
# ---------------------------------------------------------------------------

from cardioauth.integrations.icd10_api import (
    lookup_icd10,
    search_icd10,
    validate_codes as validate_icd10_codes,
    suggest_codes as suggest_icd10_codes,
)
from cardioauth.integrations.rxnorm_api import (
    lookup_medication,
    get_ndc_codes,
    check_interactions,
    normalize_medication,
)


class ICD10ValidateRequest(BaseModel):
    codes: list[str]


class RxNormInteractionsRequest(BaseModel):
    medications: list[str]


@app.get("/api/icd10/search")
def api_icd10_search(q: str = "", max_results: int = 10) -> dict[str, Any]:
    """Search ICD-10 codes by keyword or partial code.

    Example: GET /api/icd10/search?q=chest+pain&max_results=5
    """
    results = search_icd10(q, max_results=max_results)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/icd10/lookup/{code}")
def api_icd10_lookup(code: str) -> dict[str, Any]:
    """Look up a single ICD-10 code.

    Example: GET /api/icd10/lookup/I25.10
    Response: {"code": "I25.10", "description": "Atherosclerotic heart disease ...", "found": true}
    """
    return lookup_icd10(code)


@app.post("/api/icd10/validate")
def api_icd10_validate(req: ICD10ValidateRequest) -> dict[str, Any]:
    """Validate a list of ICD-10 codes.

    Body: {"codes": ["I25.10", "R07.9", "INVALID"]}
    Response: {"total": 3, "valid_count": 2, "invalid_count": 1, "results": [...]}
    """
    results = validate_icd10_codes(req.codes)
    valid_count = sum(1 for r in results if r["valid"])
    return {
        "total": len(results),
        "valid_count": valid_count,
        "invalid_count": len(results) - valid_count,
        "results": results,
    }


@app.get("/api/icd10/suggest")
def api_icd10_suggest(keyword: str = "", procedure_code: str = "") -> dict[str, Any]:
    """Suggest ICD-10 codes for a clinical keyword.

    Example: GET /api/icd10/suggest?keyword=chest+pain&procedure_code=93458
    """
    results = suggest_icd10_codes(keyword, procedure_code=procedure_code)
    return {"keyword": keyword, "procedure_code": procedure_code, "count": len(results), "suggestions": results}


@app.get("/api/rxnorm/search")
def api_rxnorm_search(name: str = "") -> dict[str, Any]:
    """Search for a medication by name.

    Example: GET /api/rxnorm/search?name=metoprolol
    Response: {"name": "metoprolol", "rxcui": "6918", "found": true, "forms": [...]}
    """
    return lookup_medication(name)


@app.get("/api/rxnorm/ndc/{name}")
def api_rxnorm_ndc(name: str) -> dict[str, Any]:
    """Get NDC codes for a medication.

    Example: GET /api/rxnorm/ndc/metoprolol
    Response: {"medication": "metoprolol", "ndc_count": 15, "ndc_codes": ["0093-7385-56", ...]}
    """
    ndcs = get_ndc_codes(name)
    return {"medication": name, "ndc_count": len(ndcs), "ndc_codes": ndcs}


@app.post("/api/rxnorm/interactions")
def api_rxnorm_interactions(req: RxNormInteractionsRequest) -> dict[str, Any]:
    """Check drug-drug interactions between a list of medications.

    Body: {"medications": ["warfarin", "aspirin", "metoprolol"]}
    Response: {"medications": [...], "interaction_count": 2, "interactions": [...]}
    """
    results = check_interactions(req.medications)
    return {
        "medications": req.medications,
        "interaction_count": len(results),
        "interactions": results,
    }


@app.get("/api/rxnorm/normalize/{name}")
def api_rxnorm_normalize(name: str) -> dict[str, Any]:
    """Normalize a medication name to standard RxNorm terminology.

    Example: GET /api/rxnorm/normalize/lopressor
    Response: {"input": "lopressor", "normalized_name": "metoprolol tartrate", "rxcui": "6918", "found": true}
    """
    return normalize_medication(name)


# ---------------------------------------------------------------------------
# NPI Registry & CMS FHIR Endpoints
# ---------------------------------------------------------------------------

from cardioauth.integrations.nppes_api import (
    lookup_npi as _lookup_npi,
    search_providers as _search_providers,
)
from cardioauth.integrations.cms_fhir import CMSFHIRClient

_cms_client = CMSFHIRClient(
    client_id=os.getenv("CMS_CLIENT_ID", ""),
    client_secret=os.getenv("CMS_CLIENT_SECRET", ""),
)


@app.get("/api/npi/lookup/{npi}")
def api_npi_lookup(npi: str) -> dict[str, Any]:
    """Look up a provider by NPI number via the NPPES registry."""
    result = _lookup_npi(npi)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/npi/search")
def api_npi_search(
    last_name: str = "",
    first_name: str = "",
    state: str = "",
    specialty: str = "cardiology",
    limit: int = 10,
) -> dict[str, Any]:
    """Search the NPPES registry for providers by name and specialty."""
    providers = _search_providers(
        last_name=last_name,
        first_name=first_name,
        state=state,
        specialty=specialty,
        limit=limit,
    )
    return {"count": len(providers), "providers": providers}


@app.get("/api/cms/eligibility/{medicare_id}")
def api_cms_eligibility(medicare_id: str) -> dict[str, Any]:
    """Check Medicare eligibility for a beneficiary."""
    return _cms_client.check_medicare_eligibility(medicare_id)


@app.get("/api/cms/coverage/{medicare_id}")
def api_cms_coverage(medicare_id: str) -> dict[str, Any]:
    """Get Medicare coverage details for a beneficiary."""
    return _cms_client.get_coverage_details(medicare_id)


# Mount static files for favicon and assets
app.mount("/static", StaticFiles(directory="static"), name="static")

# Serve frontend — must be last so it doesn't shadow API routes
@app.get("/", response_class=HTMLResponse)
def serve_frontend() -> str:
    index = STATIC_DIR / "index.html"
    if index.exists():
        return index.read_text()
    return "<h1>CardioAuth</h1><p>Frontend not found. Visit <a href='/docs'>/docs</a> for API.</p>"
