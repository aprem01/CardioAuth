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
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
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

# In-memory hot cache for the active session. Every write also persists to
# the Store so reviews survive container restarts.
_reviews: dict[str, ReviewPackage] = {}


def _review_to_store_dict(review: ReviewPackage) -> dict:
    """Serialize a ReviewPackage into a JSON-safe dict for persistence.

    We keep the core pydantic fields as model_dump() output and attach the
    auxiliary dataclass fields that are already JSON-compatible. On reload
    we reconstruct a ReviewPackage via _review_from_store_dict().
    """
    return {
        "chart_data": review.chart_data.model_dump(mode="json"),
        "policy_data": review.policy_data.model_dump(mode="json"),
        "reasoning": review.reasoning.model_dump(mode="json"),
        "requires_human_action": list(review.requires_human_action),
        "taxonomy_match": review.taxonomy_match,
        "system_warnings": list(review.system_warnings),
        "retrieved_chunks": list(review.retrieved_chunks),
        "criterion_citations": list(review.criterion_citations),
        "criterion_audit_trail": list(review.criterion_audit_trail),
        "validation_reports": list(review.validation_reports),
        "payer_stats": review.payer_stats,
        "payer_global_rules": list(review.payer_global_rules),
        "policy_freshness": review.policy_freshness,
    }


def _review_from_store_dict(data: dict) -> ReviewPackage:
    from cardioauth.models import ChartData, PolicyData
    from cardioauth.models.reasoning import ReasoningResult
    return ReviewPackage(
        chart_data=ChartData(**data["chart_data"]),
        policy_data=PolicyData(**data["policy_data"]),
        reasoning=ReasoningResult(**data["reasoning"]),
        requires_human_action=data.get("requires_human_action", []),
        taxonomy_match=data.get("taxonomy_match"),
        system_warnings=data.get("system_warnings", []),
        retrieved_chunks=data.get("retrieved_chunks", []),
        criterion_citations=data.get("criterion_citations", []),
        criterion_audit_trail=data.get("criterion_audit_trail", []),
        validation_reports=data.get("validation_reports", []),
        payer_stats=data.get("payer_stats"),
        payer_global_rules=data.get("payer_global_rules", []),
        policy_freshness=data.get("policy_freshness"),
    )


def _save_review(review_id: str, review: ReviewPackage, user_id: str = "") -> None:
    """Persist a review to the Store AND keep it in the hot cache."""
    _reviews[review_id] = review
    try:
        from cardioauth.persistence import get_store
        get_store().save_review(review_id, _review_to_store_dict(review), user_id=user_id)
    except Exception as e:
        logging.warning("persistence: failed to save review %s: %s", review_id, e)


def _load_review(review_id: str) -> ReviewPackage | None:
    """Return a review from the hot cache, or rehydrate from the Store."""
    cached = _reviews.get(review_id)
    if cached is not None:
        return cached
    try:
        from cardioauth.persistence import get_store
        raw = get_store().get_review(review_id)
        if raw:
            review = _review_from_store_dict(raw)
            _reviews[review_id] = review
            return review
    except Exception as e:
        logging.warning("persistence: failed to load review %s: %s", review_id, e)
    return None


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
    relevant_labs: list[dict[str, Any]] = []
    relevant_imaging: list[dict[str, Any]] = []
    relevant_medications: list[dict[str, Any]] = []

    # v2 canonical fields (Apr 14 — Peter feedback). All optional so old
    # clients that only send comorbidities/prior_treatments keep working.
    active_comorbidities: list[str] = []
    past_medical_history: list[dict[str, Any]] = []
    family_history: list[dict[str, Any]] = []
    current_symptoms: list[dict[str, Any]] = []
    exam_findings: list[dict[str, Any]] = []
    prior_stress_tests: list[dict[str, Any]] = []
    prior_procedures: list[dict[str, Any]] = []
    ecg_findings_v2: list[dict[str, Any]] = []   # structured ECG

    # Legacy flat fields — kept so old integrations keep working. Migrated
    # into v2 buckets via migrate_legacy_chart() on ingest.
    prior_treatments: list[str] = []
    comorbidities: list[str] = []
    ejection_fraction: str = ""
    ecg_findings: str = ""    # legacy free-text ECG string

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
    from cardioauth.models.chart import (
        ChartData, LabResult, ImagingResult, Medication,
        ECGFinding, StressTestResult, ProcedureHistory,
        Symptom, ExamFinding, PMHEntry, FamilyHistoryEntry,
    )
    from cardioauth.models.chart_migration import migrate_legacy_chart
    from cardioauth.demo import get_demo_policy, get_demo_reasoning

    # Build ChartData from custom input — accept both v1 (flat) and v2 (structured)
    labs = [LabResult(name=l.get("name", ""), value=l.get("value", ""), date=l.get("date", ""), unit=l.get("unit", "")) for l in req.relevant_labs]
    imaging = [ImagingResult(type=i.get("type", ""), date=i.get("date", ""), result_summary=i.get("result_summary", "")) for i in req.relevant_imaging]
    meds = [Medication(name=m.get("name", ""), dose=m.get("dose", ""), start_date=m.get("start_date", ""), indication=m.get("indication", "")) for m in req.relevant_medications]

    # v2 structured ECG takes precedence. If client sent structured ecg_findings_v2,
    # use those; otherwise if legacy free-text ecg_findings string was sent, wrap
    # it as a single ECGFinding summary so it still lives in the right bucket.
    v2_ecg = [
        ECGFinding(
            rhythm=e.get("rhythm", ""),
            conduction=e.get("conduction", ""),
            hypertrophy_or_strain=e.get("hypertrophy_or_strain", ""),
            ischemic_changes=e.get("ischemic_changes", ""),
            pacing=e.get("pacing", ""),
            date=e.get("date", ""),
            summary=e.get("summary", ""),
        )
        for e in req.ecg_findings_v2
    ]
    if req.ecg_findings and not v2_ecg:
        v2_ecg = [ECGFinding(summary=req.ecg_findings)]

    # If ejection_fraction sent as legacy standalone field, keep it visible as
    # an ImagingResult so the LVEF extractors pick it up.
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
        # Apr 22 (Peter): demographics now flow through to the payer form
        patient_name=req.patient_name or "",
        age=req.age,
        sex=req.sex or "",
        attending_physician="",
        insurance_id="",
        payer_name=req.payer_name,
        diagnosis_codes=req.diagnosis_codes,

        # v2 structured fields (if client supplied them)
        active_comorbidities=req.active_comorbidities,
        past_medical_history=[PMHEntry(**p) for p in req.past_medical_history],
        family_history=[FamilyHistoryEntry(**f) for f in req.family_history],
        current_symptoms=[Symptom(**s) for s in req.current_symptoms],
        exam_findings=[ExamFinding(**e) for e in req.exam_findings],
        ecg_findings=v2_ecg,
        prior_stress_tests=[StressTestResult(**s) for s in req.prior_stress_tests],
        prior_procedures=[ProcedureHistory(**p) for p in req.prior_procedures],

        relevant_labs=labs,
        relevant_imaging=imaging,
        relevant_medications=meds,

        additional_notes=req.additional_notes or "",

        # Legacy flat fields — migrate_legacy_chart will route them into v2
        prior_treatments=req.prior_treatments,
        comorbidities=req.comorbidities,

        confidence_score=0.95 if labs and imaging else 0.75,
        missing_fields=[],
    )

    # Migrate any legacy-only content into v2 buckets so downstream always
    # sees clean categorization regardless of which format the client sent.
    chart_data = migrate_legacy_chart(chart_data)

    # Safety: drop future-dated labs before reasoning (Peter C10-C13 #5).
    # Collect warnings so the physician sees which labs we rejected.
    from cardioauth.models.chart_migration import validate_lab_source_anchoring
    chart_data, lab_warnings = validate_lab_source_anchoring(chart_data, strict=False)
    lab_safety_warnings = [
        {
            "level": "warning",
            "agent": "LAB_SAFETY",
            "kind": "lab_dropped",
            "message": w,
        }
        for w in lab_warnings
    ]

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
            from cardioauth.models.reasoning import ReasoningResult, CriterionEvaluation, CriterionGap
            criteria_met_objs = []
            criteria_not_met_objs = []
            for m in unified_ctx.criterion_matches:
                if m.get("status") == "met":
                    criteria_met_objs.append(CriterionEvaluation(
                        criterion=m.get("code", "") + ": " + m.get("reasoning", "")[:100],
                        met=True,
                        evidence=m.get("evidence_quote", "") or m.get("reasoning", "")[:200],
                        confidence=float(m.get("confidence", 0.8)),
                    ))
                elif m.get("status") == "not_met":
                    criteria_not_met_objs.append(CriterionGap(
                        criterion=m.get("code", "") + ": " + m.get("reasoning", "")[:100],
                        gap=m.get("gap", "") or m.get("reasoning", "")[:200],
                        recommendation=m.get("recommendation", ""),
                    ))

            # Clamp label — ReasoningResult uses a Literal
            label = unified_ctx.approval_label if unified_ctx.approval_label in ("HIGH", "MEDIUM", "LOW", "DO NOT SUBMIT") else "LOW"
            # INSUFFICIENT from UnifiedReasoner → DO NOT SUBMIT in legacy shape
            if unified_ctx.approval_label == "INSUFFICIENT":
                label = "DO NOT SUBMIT"

            reasoning = ReasoningResult(
                criteria_met=criteria_met_objs,
                criteria_not_met=criteria_not_met_objs,
                approval_likelihood_score=unified_ctx.approval_score,
                approval_likelihood_label=label,
                pa_narrative_draft=unified_ctx.narrative_draft or "Narrative not generated.",
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

    # ── Validation + audit trail ──────────────────────────────────────
    # Compare whatever the reasoner produced against the taxonomy's
    # expected applicable set for this CPT. Missing codes = silent drop
    # (the EX-001 class of bug). Unknown codes = hallucination.
    try:
        from cardioauth.taxonomy.validation import (
            build_audit_trail,
            trail_to_dict,
            validate_criteria_for_cpt,
        )
        # Prefer the raw taxonomy matches when UnifiedReasoner ran —
        # they are keyed by taxonomy code. Otherwise extract from the
        # ReasoningResult shape.
        reasoner_matches = []
        if unified_ctx is not None:
            reasoner_matches = list(unified_ctx.criterion_matches)
        elif taxonomy_match and taxonomy_match.get("matches"):
            reasoner_matches = list(taxonomy_match["matches"])

        reasoner_codes = [m.get("code", "") for m in reasoner_matches if m.get("code")]
        validation_reports = []
        if reasoner_codes:
            vr = validate_criteria_for_cpt(
                reasoner_codes, req.procedure_code, req.payer_name, stage="reasoner_output",
            )
            validation_reports.append(vr.to_dict())
            # Propagate missing/unknown warnings into system_warnings so UI flags them
            if unified_ctx is not None:
                for w in vr.warnings:
                    unified_ctx.system_warnings.append({**w, "agent": "VALIDATOR"})

        audit_trail = build_audit_trail(
            cpt_code=req.procedure_code,
            payer=req.payer_name,
            policy_codes=None,  # POLICY_AGENT returns natural-language, not codes
            reasoner_matches=reasoner_matches,
        )
        criterion_audit_trail = trail_to_dict(audit_trail)

        silently_dropped = [e.code for e in audit_trail if "reasoner_skipped" in e.flags]
        if silently_dropped:
            logging.warning(
                "custom-request: %d criteria silently skipped by reasoner for CPT %s: %s",
                len(silently_dropped), req.procedure_code, silently_dropped,
            )
        review.criterion_audit_trail = criterion_audit_trail
        review.validation_reports = validation_reports
    except Exception as e:
        logging.warning("Audit trail build failed (non-blocking): %s", e)
        criterion_audit_trail = []
        validation_reports = []

    _save_review(request_id, review, user_id=user.id)

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
        "system_warnings": (
            (review.system_warnings if hasattr(review, 'system_warnings') else [])
            + (unified_ctx.system_warnings if unified_ctx else [])
            + lab_safety_warnings
        ),
        "retrieved_chunks": getattr(policy_data, "__dict__", {}).get("_retrieved_chunks", []),
        "criterion_citations": getattr(policy_data, "__dict__", {}).get("_criterion_citations", []),
        "reasoning_mode": reasoning_mode,
        "extraction_engine": getattr(req, "extraction_engine", "claude"),
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
        "criterion_audit_trail": criterion_audit_trail,
        "validation_reports": validation_reports,
        "payer_stats": getattr(policy_data, "__dict__", {}).get("_payer_stats"),
        "payer_global_rules": getattr(policy_data, "__dict__", {}).get("_payer_global_rules", []),
        "policy_freshness": getattr(policy_data, "__dict__", {}).get("_freshness"),
        "ensemble_agreement": (
            unified_ctx.__dict__.get("_ensemble_agreement") if unified_ctx else None
        ),
        # Peter C10-C13: top 1-3 reasons case is strong or weak
        "headline_summary": (
            unified_ctx.__dict__.get("_headline_summary") if unified_ctx else []
        ),
        # Peter C10-C13: not_met criteria split by class
        "gap_classification": (
            unified_ctx.__dict__.get("_gap_classification") if unified_ctx else None
        ),
        "supplemental_clinical_argument": (
            unified_ctx.__dict__.get("_supplemental_clinical_argument", "") if unified_ctx else ""
        ),
        # Short medical-necessity cover summary for portals that accept free text.
        # Derived from the narrative's first ~80 words rather than a separate
        # Claude call to keep latency flat. When we want a purpose-built summary
        # we promote this to its own prompt.
        "cover_summary": _first_n_words(reasoning.pa_narrative_draft or "", 80),
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
    _save_review(request_id, review, user_id=user.id if hasattr(user, "id") else "")

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
    review = _load_review(req.request_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review package not found")

    try:
        submission = orchestrator.submit_after_approval(review, approved_by=req.approved_by)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Keep the review record for audit — don't delete post-submission. Future
    # outcome events need to be able to look it up.
    from cardioauth.persistence import get_store
    try:
        get_store().append_audit(
            actor=req.approved_by,
            action="pa_approved_and_submitted",
            subject_id=req.request_id,
            detail=f"submission_id={submission.submission_id}",
        )
    except Exception:
        pass

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


class OutcomeRecordRequest(BaseModel):
    submission_id: str
    outcome: str                       # APPROVED | DENIED | PENDING | INFO_REQUESTED
    denial_reason: str = ""
    authorization_number: str = ""
    recorded_by: str = ""
    notes: str = ""


@app.post("/api/pa/outcome/record")
def record_outcome(req: OutcomeRecordRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Manually record a payer decision on a submitted PA.

    This is the outcome feedback loop — when a payer responds (via portal
    notification, fax, phone), staff record the outcome here. The pipeline:

      1. Save the outcome to durable storage (persisted, survives restarts)
      2. Update submission status to reflect the decision
      3. Update rolling stats for (payer, cpt_code) so future cases
         calibrate against real outcomes, not just seed data
      4. Store the outcome as a precedent in Pinecone (best-effort) so
         similarity retrieval sees real-world outcomes
      5. If DENIED + appeal recommended, return an appeal draft
      6. Append to the immutable audit log

    This is how the system learns from real submissions.
    """
    from cardioauth.persistence import get_store
    store = get_store()

    outcome_upper = (req.outcome or "").upper()
    if outcome_upper not in ("APPROVED", "DENIED", "PENDING", "INFO_REQUESTED"):
        raise HTTPException(status_code=400, detail=f"Invalid outcome '{req.outcome}'")

    # Look up the submission for context
    submission = store.get_submission(req.submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail=f"Submission {req.submission_id} not found")

    payer = submission.get("payer", "")
    cpt_code = submission.get("cpt_code", "") or submission.get("procedure_code", "")
    procedure = submission.get("procedure", "")

    # 1. Persist the outcome
    outcome_record = {
        "submission_id": req.submission_id,
        "outcome": outcome_upper,
        "denial_reason": req.denial_reason,
        "authorization_number": req.authorization_number,
        "recorded_by": req.recorded_by or user.id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "payer": payer,
        "cpt_code": cpt_code,
        "procedure": procedure,
        "notes": req.notes,
    }
    store.save_outcome(req.submission_id, outcome_record)

    # 2. Update the submission status
    if outcome_upper == "APPROVED":
        store.update_submission_status(req.submission_id, "approved",
                                        note=f"Auth# {req.authorization_number}")
    elif outcome_upper == "DENIED":
        store.update_submission_status(req.submission_id, "denied",
                                        note=req.denial_reason or "no reason stated")
    elif outcome_upper == "PENDING":
        store.update_submission_status(req.submission_id, "pending",
                                        note=req.notes)
    elif outcome_upper == "INFO_REQUESTED":
        store.update_submission_status(req.submission_id, "info_requested",
                                        note=req.notes)

    # 3. Update rolling stats
    if payer and cpt_code:
        store.record_outcome_for_stats(payer, cpt_code, outcome_upper)

    # 4. Best-effort precedent store for Pinecone
    precedent_stored = False
    try:
        from cardioauth.case_context import CaseContext, PrecedentCase
        from cardioauth.agents.precedent_retriever import store_case_as_precedent
        # We don't have a live CaseContext here — build a skeletal one from
        # the submission + outcome metadata. Precedent retriever only needs
        # enough to index.
        ctx = CaseContext(
            case_id=req.submission_id,
            procedure_code=cpt_code,
            procedure_name=procedure,
            payer_name=payer,
            user_id=user.id,
        )
        ctx.criterion_matches = submission.get("criterion_matches", []) or []
        ctx.approval_score = submission.get("approval_score", 0.0) or 0.0
        ctx.approval_label = submission.get("approval_label", "") or ""
        store_case_as_precedent(ctx, outcome=outcome_upper.lower())
        precedent_stored = True
    except Exception as e:
        logging.warning("outcome recording: precedent write failed: %s", e)

    # 5. Draft appeal if denied
    appeal_draft = ""
    if outcome_upper == "DENIED" and req.denial_reason:
        try:
            from cardioauth.demo import get_demo_appeal
            # Rehydrate review if possible for a richer appeal
            review_id = submission.get("review_id", "") or f"{submission.get('patient_id', '')}-{cpt_code}"
            review = _load_review(review_id)
            if review:
                appeal_draft = get_demo_appeal(
                    review.chart_data, review.policy_data, req.denial_reason,
                )
        except Exception as e:
            logging.warning("outcome recording: appeal draft failed: %s", e)

    # 6. Audit
    store.append_audit(
        actor=req.recorded_by or user.id,
        action=f"outcome_recorded_{outcome_upper}",
        subject_id=req.submission_id,
        detail=f"payer={payer} cpt={cpt_code} denial={req.denial_reason[:100]}",
    )

    return {
        "submission_id": req.submission_id,
        "outcome": outcome_upper,
        "persisted": True,
        "stats_updated": bool(payer and cpt_code),
        "precedent_stored": precedent_stored,
        "appeal_draft": appeal_draft,
        "submission_status": outcome_upper.lower(),
    }


@app.get("/api/pa/outcome/{submission_id}")
def get_outcome_record(submission_id: str, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Look up the recorded outcome for a submission."""
    from cardioauth.persistence import get_store
    outcome = get_store().get_outcome(submission_id)
    if not outcome:
        raise HTTPException(status_code=404, detail=f"No outcome recorded for {submission_id}")
    return outcome


class E2EDemoRequest(BaseModel):
    patient_id: str = "DEMO-001"
    procedure_code: str = "78492"
    procedure_name: str = ""
    payer_name: str = "UnitedHealthcare"
    scripted_outcome: str = "APPROVED"   # APPROVED | DENIED | PENDING
    approver_name: str = "Dr. Demo"
    raw_note: str = ""                   # Peter's deidentified-note path


@app.post("/api/demo/end-to-end")
def end_to_end_demo(req: E2EDemoRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run the full pipeline from Epic → submission → payer response.

    Returns a structured timeline so the UI can animate through each stage.
    Uses existing demo patients and MockChannel — never touches real PHI
    or real payer portals. Still runs real Claude + Pinecone calls for
    CHART / POLICY / REASONER so what you see is genuine AI behavior
    against synthetic input.
    """
    log_audit(user, "e2e_demo", f"patient={req.patient_id} cpt={req.procedure_code}")
    from cardioauth.demo_e2e import run_end_to_end_demo

    scripted = (req.scripted_outcome or "APPROVED").upper()
    if scripted not in ("APPROVED", "DENIED", "PENDING"):
        raise HTTPException(status_code=400, detail=f"scripted_outcome must be APPROVED|DENIED|PENDING, got {scripted}")

    try:
        timeline = run_end_to_end_demo(
            patient_id=req.patient_id,
            procedure_code=req.procedure_code,
            procedure_name=req.procedure_name or "",
            payer_name=req.payer_name,
            scripted_outcome=scripted,  # type: ignore[arg-type]
            approver_name=req.approver_name,
            raw_note=req.raw_note or "",
        )
    except Exception as e:
        logging.exception("E2E demo failed")
        raise HTTPException(status_code=500, detail=f"Demo failed: {e}")

    return timeline.to_dict()


@app.post("/api/demo/end-to-end-pdf")
async def end_to_end_demo_pdf(
    file: UploadFile = File(...),
    patient_id: str = Form("CUSTOM-PDF"),
    procedure_code: str = Form("78492"),
    procedure_name: str = Form(""),
    payer_name: str = Form("UnitedHealthcare"),
    scripted_outcome: str = Form("APPROVED"),
    approver_name: str = Form("Dr. Demo"),
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Run the E2E pipeline on an uploaded PDF.

    BAA IS NOT SIGNED with LlamaCloud yet. Callers MUST upload
    deidentified PDFs only — the UI warns and the filename is
    lightly sanity-checked here, but this endpoint does not do
    deidentification. Treat it as demo input.
    """
    log_audit(user, "e2e_demo_pdf", f"file={file.filename or 'unknown'} cpt={procedure_code}")
    from cardioauth.demo_e2e import run_end_to_end_demo
    from cardioauth.pdf_parser import PdfParserError, parse_pdf_to_text

    scripted = (scripted_outcome or "APPROVED").upper()
    if scripted not in ("APPROVED", "DENIED", "PENDING"):
        raise HTTPException(status_code=400, detail=f"scripted_outcome must be APPROVED|DENIED|PENDING, got {scripted}")

    contents = await file.read()
    try:
        parsed = parse_pdf_to_text(contents, filename=file.filename or "upload.pdf")
    except PdfParserError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.exception("PDF parse failed")
        raise HTTPException(status_code=500, detail=f"PDF parse failed: {e}")

    try:
        timeline = run_end_to_end_demo(
            patient_id=patient_id,
            procedure_code=procedure_code,
            procedure_name=procedure_name or "",
            payer_name=payer_name,
            scripted_outcome=scripted,  # type: ignore[arg-type]
            approver_name=approver_name,
            raw_note=parsed.text,
        )
    except Exception as e:
        logging.exception("E2E demo (PDF) failed")
        raise HTTPException(status_code=500, detail=f"Demo failed: {e}")

    result = timeline.to_dict()
    result["pdf_meta"] = {
        "filename": file.filename,
        "page_count": parsed.page_count,
        "parser": parsed.parser,
        "parse_duration_ms": parsed.duration_ms,
        "text_preview": parsed.text[:400],
    }
    return result


# ──────────────────────────────────────────────────────────────────────
# Lean Hybrid pipeline endpoints (Peter May rerun architecture answer)
# ──────────────────────────────────────────────────────────────────────


@app.post("/api/demo/end-to-end-lean")
def end_to_end_lean(req: E2EDemoRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run the lean hybrid state machine on a deidentified note.

    This is the alternative to /api/demo/end-to-end built around the
    Peter May rerun architectural critique: 1 unified LLM call instead
    of 4 staged calls, surrounded by deterministic guardrails. Same
    scaling/safety/auditability properties; ~70% latency reduction
    and ~75% cost reduction expected.

    Returns a JSON-serializable LeanRunResult.
    """
    log_audit(
        user, "e2e_demo_lean",
        f"patient={req.patient_id} cpt={req.procedure_code}",
    )
    from cardioauth.lean_pipeline import run_lean_pipeline

    if not (req.raw_note or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "Lean pipeline needs note text — please switch to "
                "'Paste deidentified note' or 'Upload deidentified PDF', "
                "or pick the Current engine to run a demo patient."
            ),
        )

    try:
        result = run_lean_pipeline(
            case_id=f"{req.patient_id}-{req.procedure_code}",
            raw_note=req.raw_note,
            request_cpt=req.procedure_code,
            payer=req.payer_name,
        )
    except Exception as e:
        logging.exception("Lean pipeline failed")
        raise HTTPException(status_code=500, detail=f"Lean pipeline failed: {e}")

    return result.to_dict()


@app.post("/api/demo/end-to-end-lean-pdf")
async def end_to_end_lean_pdf(
    file: UploadFile = File(...),
    patient_id: str = Form("CUSTOM-PDF"),
    procedure_code: str = Form("78492"),
    payer_name: str = Form("UnitedHealthcare"),
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Same lean hybrid pipeline, PDF-upload variant. Mirrors the
    existing /api/demo/end-to-end-pdf endpoint shape so the UI can
    swap pipelines with a single toggle."""
    log_audit(
        user, "e2e_demo_lean_pdf",
        f"file={file.filename or 'unknown'} cpt={procedure_code}",
    )
    from cardioauth.lean_pipeline import run_lean_pipeline
    from cardioauth.pdf_parser import PdfParserError, parse_pdf_to_text

    contents = await file.read()
    try:
        parsed = parse_pdf_to_text(contents, filename=file.filename or "upload.pdf")
    except PdfParserError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.exception("PDF parse failed")
        raise HTTPException(status_code=500, detail=f"PDF parse failed: {e}")

    try:
        result = run_lean_pipeline(
            case_id=f"{patient_id}-{procedure_code}",
            raw_note=parsed.text,
            request_cpt=procedure_code,
            payer=payer_name,
        )
    except Exception as e:
        logging.exception("Lean pipeline (PDF) failed")
        raise HTTPException(status_code=500, detail=f"Lean pipeline failed: {e}")

    out = result.to_dict()
    out["pdf_meta"] = {
        "filename": file.filename,
        "page_count": parsed.page_count,
        "parser": parsed.parser,
        "parse_duration_ms": parsed.duration_ms,
        "text_preview": parsed.text[:400],
    }
    return out


@app.post("/api/demo/end-to-end-ab")
async def end_to_end_ab(req: E2EDemoRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Side-by-side run: same input, both pipelines, one comparison
    report. The Peter-facing artifact for the lean-vs-current
    benchmark."""
    log_audit(
        user, "e2e_demo_ab",
        f"patient={req.patient_id} cpt={req.procedure_code}",
    )
    from cardioauth.lean_ab_harness import compare_one_case

    if not (req.raw_note or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "A/B mode needs note text — please switch to "
                "'Paste deidentified note' or 'Upload deidentified PDF', "
                "or pick the Current engine to run a demo patient."
            ),
        )

    try:
        comp = compare_one_case({
            "case_id": f"{req.patient_id}-{req.procedure_code}",
            "patient_id": req.patient_id,
            "request_cpt": req.procedure_code,
            "payer": req.payer_name,
            "raw_note": req.raw_note,
            "scripted_outcome": (req.scripted_outcome or "APPROVED").upper(),
        })
    except Exception as e:
        logging.exception("A/B run failed")
        raise HTTPException(status_code=500, detail=f"A/B run failed: {e}")

    return comp.to_dict()


# ──────────────────────────────────────────────────────────────────────
# Lean Hybrid agentic generators (taxonomy / payer-form / safety
# extractor) — turn manual PA-domain authoring into clinician REVIEW
# of auto-drafted candidates. See cardioauth/lean_*_generator.py.
# ──────────────────────────────────────────────────────────────────────


class TaxonomyGenRequest(BaseModel):
    payer: str = "UnitedHealthcare"
    target_cpts: list[str]
    policy_text: str


class FormGenRequest(BaseModel):
    payer: str
    form_pdf_text: str


class SafetyExtractorGenRequest(BaseModel):
    criterion_codes: list[str]
    criterion_definition: str
    positive_samples: list[str]
    negative_samples: list[str] = []


@app.post("/api/generators/taxonomy")
def gen_taxonomy_endpoint(req: TaxonomyGenRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Drafts CRITERION_TAXONOMY entries from a payer policy text.
    Output is REVIEWED BY A CLINICIAN before merging — this endpoint
    returns the draft, the run trace, and ready-to-paste Python source."""
    log_audit(user, "gen_taxonomy", f"payer={req.payer} cpts={','.join(req.target_cpts)}")
    from cardioauth.lean_taxonomy_generator import generate_taxonomy_candidates

    try:
        result = generate_taxonomy_candidates(
            payer=req.payer,
            target_cpts=req.target_cpts,
            policy_text=req.policy_text,
        )
    except Exception as e:
        logging.exception("Taxonomy generator failed")
        raise HTTPException(status_code=500, detail=f"Generator failed: {e}")

    out = result.to_dict()
    out["python_source"] = result.to_python_source()
    return out


@app.post("/api/generators/payer-form")
def gen_payer_form_endpoint(req: FormGenRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Drafts a PayerForm from a blank PA-form PDF text. Reviewed
    before merging."""
    log_audit(user, "gen_payer_form", f"payer={req.payer}")
    from cardioauth.lean_form_generator import generate_payer_form

    try:
        result = generate_payer_form(payer=req.payer, form_pdf_text=req.form_pdf_text)
    except Exception as e:
        logging.exception("Form generator failed")
        raise HTTPException(status_code=500, detail=f"Generator failed: {e}")

    out = result.to_dict()
    out["python_source"] = result.to_python_source()
    return out


@app.post("/api/generators/safety-extractor")
def gen_safety_extractor_endpoint(req: SafetyExtractorGenRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Drafts a safety_verifier extractor from criterion def + sample
    notes. Reviewed before merging."""
    log_audit(
        user, "gen_safety_extractor",
        f"codes={','.join(req.criterion_codes)} pos={len(req.positive_samples)} neg={len(req.negative_samples)}",
    )
    from cardioauth.lean_safety_extractor_generator import generate_safety_extractor

    try:
        result = generate_safety_extractor(
            criterion_codes=req.criterion_codes,
            criterion_definition=req.criterion_definition,
            positive_samples=req.positive_samples,
            negative_samples=req.negative_samples,
        )
    except Exception as e:
        logging.exception("Safety extractor generator failed")
        raise HTTPException(status_code=500, detail=f"Generator failed: {e}")

    out = result.to_dict()
    out["python_source"] = result.to_python_source()
    return out


@app.post("/api/demo/end-to-end-ab-pdf")
async def end_to_end_ab_pdf(
    file: UploadFile = File(...),
    patient_id: str = Form("CUSTOM-PDF"),
    procedure_code: str = Form("78492"),
    payer_name: str = Form("UnitedHealthcare"),
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """A/B + PDF: parse PDF once, run both pipelines on the parsed
    text, return the side-by-side comparison. This is what makes the
    PDF-mode A/B run cleanly without paying the LlamaParse cost twice."""
    log_audit(
        user, "e2e_demo_ab_pdf",
        f"file={file.filename or 'unknown'} cpt={procedure_code}",
    )
    from cardioauth.lean_ab_harness import compare_one_case
    from cardioauth.pdf_parser import PdfParserError, parse_pdf_to_text

    contents = await file.read()
    try:
        parsed = parse_pdf_to_text(contents, filename=file.filename or "upload.pdf")
    except PdfParserError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.exception("PDF parse failed")
        raise HTTPException(status_code=500, detail=f"PDF parse failed: {e}")

    try:
        comp = compare_one_case({
            "case_id": f"{patient_id}-{procedure_code}",
            "patient_id": patient_id,
            "request_cpt": procedure_code,
            "payer": payer_name,
            "raw_note": parsed.text,
            "scripted_outcome": "APPROVED",
        })
    except Exception as e:
        logging.exception("A/B (PDF) run failed")
        raise HTTPException(status_code=500, detail=f"A/B run failed: {e}")

    out = comp.to_dict()
    out["pdf_meta"] = {
        "filename": file.filename,
        "page_count": parsed.page_count,
        "parser": parsed.parser,
        "parse_duration_ms": parsed.duration_ms,
        "text_preview": parsed.text[:400],
    }
    return out


@app.get("/api/submissions/{submission_id}")
def get_submission_record(submission_id: str, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Look up a persisted submission by ID (survives container restart)."""
    from cardioauth.persistence import get_store
    submission = get_store().get_submission(submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")
    outcome = get_store().get_outcome(submission_id)
    return {"submission": submission, "outcome": outcome}


@app.get("/api/stats/packet-correlation")
def packet_correlation_endpoint(
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Phase C.3 — outcome correlation across resolved CPT / reviewer
    recommendation / decision / finding kinds / taxonomy + model version.

    Joins frozen packets (submission_packets) with outcomes and
    reports approval/denial rates per dimension. Becomes meaningful
    once 20+ decisive outcomes accumulate; below that, the `notes`
    field flags the report as directional.
    """
    log_audit(user, "packet_correlation", "")
    from cardioauth.packet_correlation import correlate_outcomes
    return correlate_outcomes().to_dict()


@app.get("/api/packets/{case_id}")
def get_archived_packet(
    case_id: str,
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Phase C.2 — replay: return the frozen SubmissionPacket for a case.

    The full typed packet (form fields, evidence graph, findings,
    reviewer verdict) is reconstructed from persisted state alone.
    Verdicts are reproducible: the taxonomy / form-schema / model
    versions are stamped on each row so a replay six months later
    runs against the same logic the original case was decided under.
    """
    log_audit(user, "packet_replay", case_id)
    from cardioauth.packet_archive import load_packet
    packet = load_packet(case_id)
    if packet is None:
        raise HTTPException(status_code=404, detail=f"Packet {case_id} not found")
    return packet.to_dict()


@app.get("/api/packets")
def list_archived_packets_endpoint(
    payer: str = "",
    resolved_cpt: str = "",
    decision: str = "",
    limit: int = 50,
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Index of frozen packets — payer, CPT, decision, severity counts.

    The full packet is fetched per case via /api/packets/{case_id}.
    """
    log_audit(user, "packet_list", f"payer={payer} cpt={resolved_cpt} decision={decision}")
    from cardioauth.packet_archive import list_archived_packets
    rows = list_archived_packets(
        payer=payer, resolved_cpt=resolved_cpt,
        decision=decision, limit=max(1, min(limit, 200)),
    )
    return {"packets": rows, "count": len(rows)}


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
    review = _load_review(req.request_id)
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


@app.get("/api/stats/payer")
def get_payer_stats_endpoint(payer: str, cpt_code: str) -> dict[str, Any]:
    """Historical approval rate, denial reasons, P2P + appeal rates for a (payer, CPT)."""
    from cardioauth.stats import get_payer_stats, get_global_rules
    stats = get_payer_stats(payer, cpt_code)
    rules = [r.to_dict() for r in get_global_rules(payer)]
    return {
        "payer": payer,
        "cpt_code": cpt_code,
        "stats": stats.to_dict() if stats else None,
        "global_rules": rules,
    }


@app.get("/api/stats/cost")
def cost_summary(
    hours: int = 24,
    agent: str = "",
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Token usage + cache hit rate per agent over the last N hours.

    Tells us which agent is burning the Anthropic spend cap when it
    gets hit. Also shows prompt-caching effectiveness: cache_hit_rate
    close to 1.0 means we're getting the full discount.
    """
    from cardioauth.persistence import get_store
    window_hours = max(1, min(int(hours), 720))  # 1 hour .. 30 days
    return get_store().summarize_cost(window_hours=window_hours, agent=agent)


@app.get("/api/recall-queue")
def recall_queue_list(
    status: str = "",
    procedure_code: str = "",
    payer: str = "",
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """List recall-queue entries with optional filters and KPIs.

    Tier 1: derived from approved submissions; last_encounter_date is
    the back-office's manual record (defaults to submission date).
    Tier 2 (later): replaced by Epic FHIR Encounter sync.
    """
    log_audit(user, "recall_queue_list", f"status={status} cpt={procedure_code} payer={payer}")
    from cardioauth.recall_queue import (
        list_recall_queue, queue_kpis, seed_demo_recalls,
    )
    seed_demo_recalls()  # idempotent — only seeds when queue is empty
    entries = list_recall_queue(status=status, procedure_code=procedure_code, payer=payer)
    return {
        "entries": [e.to_dict() for e in entries],
        "kpis": queue_kpis(entries),
    }


class RecallActionRequest(BaseModel):
    action: str   # mark_outreach | mark_scheduled | mark_seen | remove | reset
    note: str = ""
    new_encounter_date: str = ""


@app.post("/api/recall-queue/{submission_id}/action")
def recall_queue_action(
    submission_id: str,
    req: RecallActionRequest,
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply a back-office action to a recall row."""
    log_audit(user, f"recall_action_{req.action}", submission_id)
    from cardioauth.recall_queue import apply_action
    try:
        updated = apply_action(
            submission_id, req.action,
            actor=getattr(user, "user_id", None) or "office",
            note=req.note,
            new_encounter_date=req.new_encounter_date,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No recall entry for {submission_id}")
    return {"entry": updated.to_dict()}


@app.post("/api/recall-queue/backfill")
def recall_queue_backfill(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Backfill recall_queue from existing approved submissions."""
    log_audit(user, "recall_queue_backfill", "")
    from cardioauth.recall_queue import backfill_from_submissions
    return backfill_from_submissions()


@app.get("/api/stats/calibration")
def calibration_stats(
    payer: str = "",
    cpt_code: str = "",
    n_bins: int = 10,
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Reliability dashboard: predicted approval likelihood vs actual rate.

    Failure-aware AI review (Apr 25): we needed to actually measure
    whether approval_likelihood_score=0.85 corresponds to ~85% approval.
    Now we do.

    Returns reliability bins + Brier + ECE + an over_confident_score
    (positive = the system over-promises). Below 20 decisive outcomes
    a reliability warning is included so the dashboard can render the
    appropriate caveat.
    """
    log_audit(user, "calibration_stats", f"payer={payer} cpt={cpt_code}")
    from cardioauth.calibration import (
        collect_rows_from_store,
        compute_calibration,
        report_to_dict,
    )
    rows = collect_rows_from_store(payer=payer, cpt_code=cpt_code)
    report = compute_calibration(rows, n_bins=n_bins)
    return report_to_dict(report)


@app.get("/api/stats/criterion-outcome-correlation")
def criterion_outcome_correlation(
    payer: str = "",
    cpt_code: str = "",
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Per-criterion approval correlation from persisted outcomes.

    Peter's ask: identify which criteria are load-bearing for each payer.
    When MED-002 is met, what's the approval rate? When it's not_met, what?
    The difference is the criterion's predictive weight.

    Becomes meaningful once 20+ decisive outcomes accumulate per filter.
    Returns empty/directional data gracefully before that.
    """
    log_audit(user, "criterion_correlation", f"payer={payer} cpt={cpt_code}")
    from cardioauth.stats.criterion_correlation import compute_criterion_correlation
    return compute_criterion_correlation(payer=payer, cpt_code=cpt_code)


@app.get("/api/stats/payer/all")
def list_all_payer_stats() -> dict[str, Any]:
    """Return every seeded (payer, CPT) statistics entry — for admin views."""
    from cardioauth.stats import list_payer_stats
    return {"stats": [s.to_dict() for s in list_payer_stats()]}


class ValidationRunRequest(BaseModel):
    cases: list[dict]   # LabeledCase dicts — case_id, procedure_code, payer_name, raw_note, gold_outcome, gold_criterion_labels


class ConsistencyCheckRequest(BaseModel):
    procedure_code: str
    procedure_name: str
    payer_name: str
    raw_note: str
    chart_data: dict = {}
    n_runs: int = 5


@app.post("/api/validation/consistency")
def check_consistency(req: ConsistencyCheckRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run a case N times through the reasoner and report variance.

    Produces a per-criterion status-distribution table plus overall
    approval-score variance. Used to answer "is this system reproducible?"
    for compliance and clinical trust conversations.
    """
    log_audit(user, "consistency_check", f"n={req.n_runs} cpt={req.procedure_code}")
    from cardioauth.agents.unified_reasoner import reason_with_unified_agent
    from cardioauth.case_context import CaseContext

    runs: list[dict] = []
    for i in range(max(2, min(req.n_runs, 10))):  # clamp 2..10
        ctx = CaseContext(
            case_id=f"consistency-run-{i}",
            procedure_code=req.procedure_code,
            procedure_name=req.procedure_name,
            payer_name=req.payer_name,
            user_id=user.id,
            raw_note=req.raw_note,
            chart_data=req.chart_data,
        )
        ctx.build_clinical_narrative()
        # Force single-run with temperature so each call is independent
        single_cfg = type(config)(
            anthropic_api_key=config.anthropic_api_key,
            model=config.model,
            epic_base_url=config.epic_base_url,
            epic_client_id=config.epic_client_id,
            epic_private_key_path=config.epic_private_key_path,
            epic_private_key=config.epic_private_key,
            epic_token_url=config.epic_token_url,
            pinecone_api_key=config.pinecone_api_key,
            pinecone_index=config.pinecone_index,
            aws_region=config.aws_region,
            use_comprehend_medical=config.use_comprehend_medical,
            chart_confidence_threshold=config.chart_confidence_threshold,
            approval_likelihood_threshold=config.approval_likelihood_threshold,
            reasoning_ensemble_n=1,
            reasoning_ensemble_temperature=0.4,
            reasoning_agreement_flag_threshold=config.reasoning_agreement_flag_threshold,
        )
        reason_with_unified_agent(ctx, single_cfg)
        runs.append({
            "run_index": i,
            "approval_score": ctx.approval_score,
            "approval_label": ctx.approval_label,
            "criterion_matches": [
                {"code": m.get("code"), "status": m.get("status")} for m in ctx.criterion_matches
            ],
        })

    # Aggregate: per-criterion status distribution + score variance
    per_criterion: dict[str, dict] = {}
    for r in runs:
        for m in r["criterion_matches"]:
            code = m["code"]
            per_criterion.setdefault(code, {"met": 0, "not_met": 0, "other": 0})
            s = m.get("status", "not_met")
            if s == "met":
                per_criterion[code]["met"] += 1
            elif s == "not_met":
                per_criterion[code]["not_met"] += 1
            else:
                per_criterion[code]["other"] += 1

    n = len(runs)
    for code, d in per_criterion.items():
        agreed = max(d["met"], d["not_met"], d["other"])
        d["agreement"] = round(agreed / n, 3) if n else None
        d["majority"] = "met" if d["met"] >= d["not_met"] else "not_met"

    scores = [r["approval_score"] for r in runs]
    mean_score = sum(scores) / n if n else 0.0
    variance = sum((s - mean_score) ** 2 for s in scores) / n if n else 0.0
    spread = max(scores) - min(scores) if scores else 0.0

    low_agreement = [c for c, d in per_criterion.items() if d["agreement"] is not None and d["agreement"] < 0.67]

    return {
        "n_runs": n,
        "approval_score_mean": round(mean_score, 3),
        "approval_score_min": round(min(scores), 3) if scores else None,
        "approval_score_max": round(max(scores), 3) if scores else None,
        "approval_score_spread": round(spread, 3),
        "approval_score_variance": round(variance, 4),
        "per_criterion_distribution": per_criterion,
        "low_agreement_criteria": sorted(low_agreement),
        "runs": runs,
    }


@app.post("/api/validation/run")
def run_validation(req: ValidationRunRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run a batch of labeled cases and return a calibration report.

    Response includes sensitivity, specificity, PPV, NPV, per-criterion accuracy,
    calibration curve, silent-drop rate, and per-case detail.
    """
    log_audit(user, "run_validation", f"n={len(req.cases)}")
    from cardioauth.validation_harness import LabeledCase, run_validation_batch

    cases = []
    for c in req.cases:
        try:
            cases.append(LabeledCase(
                case_id=c["case_id"],
                procedure_code=c["procedure_code"],
                procedure_name=c.get("procedure_name", c["procedure_code"]),
                payer_name=c["payer_name"],
                raw_note=c["raw_note"],
                gold_outcome=c["gold_outcome"],
                gold_criterion_labels=c.get("gold_criterion_labels", {}),
                chart_data=c.get("chart_data", {}),
            ))
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"missing field {e} in case {c.get('case_id', '?')}")

    report = run_validation_batch(cases, config)
    return report.to_dict()


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
    review = _load_review(req.request_id)
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


@app.post("/api/pa/submission-packet")
def export_submission_packet(req: ApprovalRequest):
    """Generate the payer submission packet PDF (Apr 14 — Peter feedback).

    Unlike export-pdf (which produces the appeal-shaped long narrative),
    this produces what first-pass submissions actually look like:
      - Cover sheet (patient / procedure / ICD / CPT / payer)
      - 80-word medical necessity summary
      - Criterion summary table
      - The raw clinical note verbatim

    This is the artifact back-office staff send to the payer portal.
    """
    review = _load_review(req.request_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review package not found")

    from cardioauth.pdf_generator import generate_submission_packet
    from cardioauth.case_context import CaseContext

    # Derive cover summary (first ~80 words of the narrative)
    narrative = review.reasoning.pa_narrative_draft or ""
    cover_summary = _first_n_words(narrative, 80)

    # Try to get the raw note from the stored CaseContext (unified path stores
    # it), fall back to a reconstructed narrative from chart_data
    raw_note = ""
    ctx = _contexts.get(req.request_id) if "_contexts" in globals() else None
    if ctx is not None:
        raw_note = ctx.raw_note
    else:
        tmp_ctx = CaseContext(
            case_id=req.request_id,
            procedure_code=review.chart_data.procedure_code,
            procedure_name=review.chart_data.procedure_requested,
            payer_name=review.policy_data.payer,
            chart_data=review.chart_data.model_dump(),
        )
        raw_note = tmp_ctx.build_clinical_narrative()

    pdf_bytes = generate_submission_packet(
        chart_data=review.chart_data.model_dump(),
        policy_data=review.policy_data.model_dump(),
        reasoning=review.reasoning.model_dump(),
        cover_summary=cover_summary,
        criterion_audit_trail=review.criterion_audit_trail,
        raw_note=raw_note,
    )

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=PA-SUBMISSION-{req.request_id}.pdf"
        },
    )


def _first_n_words(text: str, n: int) -> str:
    """Extract the first N words of a text, breaking on a sentence boundary if possible."""
    if not text:
        return ""
    words = text.strip().split()
    if len(words) <= n:
        return text.strip()
    # Try to end on a sentence boundary within the N-word window
    cut = " ".join(words[:n])
    for punct in (". ", ".\n", "."):
        last = cut.rfind(punct)
        if last > int(len(cut) * 0.6):
            return cut[: last + 1]
    return cut + "…"


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


# ---------------------------------------------------------------------------
# Physician feedback / RLHF-lite
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    case_id: str
    criterion_code: str
    system_said: str            # "met" | "not_met" | "not_applicable"
    physician_said: str         # corrected value
    system_evidence: str = ""
    correct_evidence: str = ""
    reason: str = ""
    note_context: str = ""
    procedure_code: str = ""
    payer: str = ""


@app.post("/api/feedback/correction")
async def submit_correction(req: FeedbackRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Physician submits a correction on a criterion evaluation."""
    import uuid
    from cardioauth.feedback import CriterionCorrection, record_correction

    correction = CriterionCorrection(
        correction_id=str(uuid.uuid4()),
        case_id=req.case_id,
        user_id=user.id,
        procedure_code=req.procedure_code,
        payer=req.payer,
        criterion_code=req.criterion_code,
        system_said=req.system_said,
        physician_said=req.physician_said,
        system_evidence=req.system_evidence,
        correct_evidence=req.correct_evidence,
        reason=req.reason,
        note_context=req.note_context,
    )
    log_audit(user, "correction", f"{req.criterion_code}: {req.system_said}→{req.physician_said}")
    ok = record_correction(correction)
    return {
        "status": "ok" if ok else "partial",
        "correction_id": correction.correction_id,
        "message": "Thanks — this correction will be used to improve future evaluations on similar cases.",
    }


# ---------------------------------------------------------------------------
# Training — gold-standard labeled cases for supervised learning
# ---------------------------------------------------------------------------

class CriterionLabelReq(BaseModel):
    code: str
    gold_status: str             # "met" | "not_met" | "not_applicable"
    gold_evidence: str = ""
    physician_note: str = ""


class TrainingCaseReq(BaseModel):
    case_id: str = ""
    title: str
    procedure_code: str
    procedure_name: str
    payer: str
    raw_note: str
    actual_outcome: str = "unknown"
    gold_approval_label: str = ""
    gold_approval_score: float = 0.0
    criterion_labels: list[CriterionLabelReq] = []
    source: str = "manual"
    notes: str = ""


@app.get("/api/training/cases")
async def list_training_cases(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """List all gold-standard labeled training cases."""
    from cardioauth.training import get_all_training_cases
    return {"cases": get_all_training_cases()}


@app.get("/api/training/cases/{case_id}")
async def get_training_case_endpoint(case_id: str, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Fetch a single training case."""
    from cardioauth.training import get_training_case
    case = get_training_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Training case not found")
    return case


@app.post("/api/training/cases")
async def save_training_case_endpoint(req: TrainingCaseReq, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Save or update a gold-standard labeled training case."""
    import uuid as _uuid
    from cardioauth.training import TrainingCase, CriterionLabel, save_training_case

    case_id = req.case_id or f"TC-{_uuid.uuid4().hex[:8].upper()}"
    case = TrainingCase(
        case_id=case_id,
        title=req.title,
        procedure_code=req.procedure_code,
        procedure_name=req.procedure_name,
        payer=req.payer,
        raw_note=req.raw_note,
        actual_outcome=req.actual_outcome,
        gold_approval_label=req.gold_approval_label,
        gold_approval_score=req.gold_approval_score,
        criterion_labels=[
            CriterionLabel(
                code=l.code,
                gold_status=l.gold_status,
                gold_evidence=l.gold_evidence,
                physician_note=l.physician_note,
            )
            for l in req.criterion_labels
        ],
        labeled_by=user.id,
        source=req.source,
        notes=req.notes,
    )
    log_audit(user, "training_label", f"{case.case_id} {req.procedure_code}")
    ok = save_training_case(case)
    return {"status": "ok" if ok else "partial", "case_id": case.case_id}


@app.post("/api/training/evaluate/{case_id}")
async def evaluate_training_case(case_id: str, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run the UnifiedReasoner on a training case and compare to gold labels."""
    from cardioauth.training import get_training_case, evaluate_case_against_gold
    from cardioauth.case_context import CaseContext
    from cardioauth.agents.relationship_extractor import extract_relationships
    from cardioauth.agents.precedent_retriever import retrieve_precedents
    from cardioauth.agents.unified_reasoner import reason_with_unified_agent

    case = get_training_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Training case not found")

    ctx = CaseContext(
        case_id=case_id,
        procedure_code=case["procedure_code"],
        procedure_name=case["procedure_name"],
        payer_name=case["payer"],
        user_id=user.id,
        raw_note=case["raw_note"],
    )
    extract_relationships(ctx, config)
    retrieve_precedents(ctx, top_k=5)
    reason_with_unified_agent(ctx, config)

    reasoner_output = {
        "criterion_matches": ctx.criterion_matches,
        "approval_likelihood": {"score": ctx.approval_score, "label": ctx.approval_label},
        "narrative_draft": ctx.narrative_draft,
    }

    scorecard = evaluate_case_against_gold(case, reasoner_output)
    return {
        "scorecard": scorecard,
        "reasoner_output": reasoner_output,
        "relationships": [
            {"supports": r.supports_criterion, "conclusion": r.conclusion, "quote": r.evidence_quote}
            for r in ctx.relationships
        ],
        "reasoning_trace": [
            {"agent": t.agent_name, "action": t.action, "summary": t.output_summary}
            for t in ctx.reasoning_trace
        ],
    }


@app.get("/api/training/stats")
async def get_training_stats(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Overall training dataset statistics."""
    from cardioauth.training import get_training_accuracy_stats
    return get_training_accuracy_stats()


@app.post("/api/training/evaluate-all")
async def evaluate_all_training_cases(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Run the UnifiedReasoner on every training case and return aggregate scorecard.

    This is the core regression test — proves the system's accuracy against
    the physician-verified gold standard dataset.
    """
    from cardioauth.training import (
        get_all_training_cases, evaluate_case_against_gold,
    )
    from cardioauth.case_context import CaseContext
    from cardioauth.agents.relationship_extractor import extract_relationships
    from cardioauth.agents.precedent_retriever import retrieve_precedents
    from cardioauth.agents.unified_reasoner import reason_with_unified_agent

    cases = get_all_training_cases()
    if not cases:
        return {"status": "no_cases", "total": 0}

    log_audit(user, "regression_run", f"{len(cases)} cases")

    per_case = []
    total_agreement = 0
    total_labels = 0
    total_mismatch = 0
    total_missing = 0
    score_deltas = []

    for case in cases:
        try:
            ctx = CaseContext(
                case_id=case["case_id"],
                procedure_code=case["procedure_code"],
                procedure_name=case.get("procedure_name", ""),
                payer_name=case["payer"],
                user_id=user.id,
                raw_note=case["raw_note"],
            )
            extract_relationships(ctx, config)
            retrieve_precedents(ctx, top_k=5)
            reason_with_unified_agent(ctx, config)

            reasoner_output = {
                "criterion_matches": ctx.criterion_matches,
                "approval_likelihood": {"score": ctx.approval_score, "label": ctx.approval_label},
            }
            scorecard = evaluate_case_against_gold(case, reasoner_output)
            per_case.append({
                "case_id": case["case_id"],
                "title": case.get("title", ""),
                "accuracy": scorecard["accuracy"],
                "agreement": scorecard["agreement"],
                "disagreement": scorecard["disagreement"],
                "missing": scorecard["missing"],
                "gold_score": scorecard["gold_approval_score"],
                "reasoner_score": scorecard["reasoner_approval_score"],
                "score_delta": scorecard["score_delta"],
                "details": scorecard["details"],
            })
            total_agreement += scorecard["agreement"]
            total_labels += scorecard["total_criteria"]
            total_mismatch += scorecard["disagreement"]
            total_missing += scorecard["missing"]
            score_deltas.append(scorecard["score_delta"])
        except Exception as e:
            logging.warning("Failed to evaluate case %s: %s", case.get("case_id"), e)
            per_case.append({
                "case_id": case.get("case_id"),
                "title": case.get("title", ""),
                "error": str(e)[:200],
            })

    overall_accuracy = total_agreement / total_labels if total_labels > 0 else 0.0
    avg_score_delta = sum(score_deltas) / len(score_deltas) if score_deltas else 0.0

    return {
        "status": "ok",
        "total_cases": len(cases),
        "cases_evaluated": len([p for p in per_case if "error" not in p]),
        "overall_accuracy": round(overall_accuracy, 3),
        "total_labels": total_labels,
        "total_agreement": total_agreement,
        "total_mismatch": total_mismatch,
        "total_missing": total_missing,
        "avg_score_delta": round(avg_score_delta, 3),
        "per_case": per_case,
    }


@app.post("/api/training/bulk-import")
async def bulk_import_training_cases(
    body: dict,
    user: AuthUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Bulk import training cases from JSONL / CSV / structured list."""
    from cardioauth.training import TrainingCase, CriterionLabel, save_training_case
    import csv as _csv
    import io as _io
    import uuid as _uuid

    fmt = (body.get("format") or "jsonl").lower()
    raw = body.get("data", "")

    cases_in = []
    errors = []

    if fmt == "jsonl":
        for i, line in enumerate((raw or "").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases_in.append(json.loads(line))
            except json.JSONDecodeError as e:
                errors.append(f"Line {i}: {e}")
    elif fmt == "csv":
        try:
            reader = _csv.DictReader(_io.StringIO(raw or ""))
            for row in reader:
                c = {
                    "case_id": row.get("case_id") or "",
                    "title": row.get("title") or row.get("case_id", ""),
                    "procedure_code": row.get("procedure_code") or row.get("cpt", ""),
                    "procedure_name": row.get("procedure_name", ""),
                    "payer": row.get("payer", ""),
                    "raw_note": row.get("raw_note") or row.get("note", ""),
                    "actual_outcome": row.get("actual_outcome") or row.get("outcome", "unknown"),
                    "gold_approval_label": row.get("gold_approval_label", ""),
                    "gold_approval_score": float(row.get("gold_approval_score") or 0),
                    "source": row.get("source", "bulk-csv"),
                    "criterion_labels": [],
                }
                if row.get("criterion_labels"):
                    try:
                        c["criterion_labels"] = json.loads(row["criterion_labels"])
                    except Exception:
                        pass
                cases_in.append(c)
        except Exception as e:
            errors.append(f"CSV parse: {e}")
    elif fmt == "cases":
        cases_in = raw if isinstance(raw, list) else []
    else:
        raise HTTPException(status_code=400, detail=f"Unknown format: {fmt}")

    saved = 0
    for c in cases_in:
        try:
            labels = []
            for l in (c.get("criterion_labels") or []):
                labels.append(CriterionLabel(
                    code=l.get("code", ""),
                    gold_status=l.get("gold_status", ""),
                    gold_evidence=l.get("gold_evidence", ""),
                    physician_note=l.get("physician_note", ""),
                ))
            case = TrainingCase(
                case_id=c.get("case_id") or f"BULK-{_uuid.uuid4().hex[:8].upper()}",
                title=c.get("title") or c.get("case_id") or "Imported case",
                procedure_code=c.get("procedure_code", ""),
                procedure_name=c.get("procedure_name", ""),
                payer=c.get("payer", ""),
                raw_note=c.get("raw_note", ""),
                actual_outcome=c.get("actual_outcome", "unknown"),
                gold_approval_label=c.get("gold_approval_label", ""),
                gold_approval_score=float(c.get("gold_approval_score") or 0),
                criterion_labels=labels,
                labeled_by=user.id,
                source=c.get("source", "bulk"),
                notes=c.get("notes", ""),
            )
            if save_training_case(case):
                saved += 1
        except Exception as e:
            errors.append(f"{c.get('case_id', '?')}: {str(e)[:100]}")

    log_audit(user, "bulk_import", f"{saved}/{len(cases_in)} saved, format={fmt}")
    return {"status": "ok", "parsed": len(cases_in), "saved": saved, "errors": errors[:20]}


class AutoLabelRequest(BaseModel):
    raw_note: str
    procedure_code: str
    payer: str
    actual_outcome: str = "unknown"


@app.post("/api/training/auto-label")
async def auto_label_case(req: AutoLabelRequest, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Use Claude to pre-populate gold criterion labels from a raw note.

    Peter pastes a historical note + outcome → Claude drafts all criterion
    labels with evidence quotes → Peter reviews/adjusts and saves.
    """
    from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure

    if not config.anthropic_api_key:
        raise HTTPException(status_code=503, detail="Claude unavailable")

    applicable = get_criteria_for_procedure(req.procedure_code, req.payer)
    if not applicable:
        raise HTTPException(status_code=400, detail=f"No taxonomy criteria for CPT {req.procedure_code}")

    criteria_summary = json.dumps([
        {"code": c.code, "short_name": c.short_name, "definition": c.definition}
        for c in applicable
    ], indent=2)

    prompt = (
        "You are a cardiologist pre-labeling a training case for CardioAuth.\n\n"
        f"Clinical note:\n{req.raw_note}\n\n"
        "─────────\n"
        f"Procedure: CPT {req.procedure_code}\nPayer: {req.payer}\n"
        f"Actual outcome: {req.actual_outcome}\n\n"
        f"Applicable criteria:\n{criteria_summary}\n\n"
        "For each criterion, produce a label:\n"
        "  - gold_status: \"met\" | \"not_met\" | \"not_applicable\"\n"
        "  - gold_evidence: verbatim quote from the note (for met criteria)\n"
        "  - physician_note: brief clinical reasoning\n\n"
        "Also estimate gold_approval_score (0.0-1.0) and gold_approval_label "
        "(HIGH/MEDIUM/LOW). If case was approved, calibrate score ≥0.75.\n\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "criterion_labels": [{"code": "EX-001", "gold_status": "met", "gold_evidence": "...", "physician_note": "..."}, ...],\n'
        '  "gold_approval_score": 0.85,\n'
        '  "gold_approval_label": "HIGH",\n'
        '  "title": "Short descriptive title"\n'
        "}"
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        response = client.messages.create(
            model=config.model,
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        from cardioauth.agents.json_recovery import parse_llm_json
        data = parse_llm_json(raw, fallback={"criterion_labels": []})
        log_audit(user, "auto_label", f"CPT={req.procedure_code} labels={len(data.get('criterion_labels', []))}")
        return {"status": "ok", "auto_label": data}
    except Exception as e:
        logging.warning("Auto-label failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.get("/api/training/export-for-finetuning")
async def export_for_finetuning(
    format: str = "messages",
    user: AuthUser = Depends(get_current_user),
) -> StreamingResponse:
    """Export labeled training cases as JSONL for fine-tuning.

    Supported formats:
      - "messages" (default) — Anthropic / OpenAI chat-completion format
      - "prompt_completion" — Claude SFT format with raw prompt + completion

    Returns a downloadable JSONL file ready to upload to:
      - Anthropic console.anthropic.com (Claude Haiku fine-tuning)
      - OpenAI platform.openai.com (GPT-4o-mini fine-tuning)
      - Hugging Face / Unsloth for Llama 3.1 8B
    """
    from cardioauth.training import get_all_training_cases
    from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure
    import io as _io

    cases = [c for c in get_all_training_cases() if c.get("criterion_labels")]
    if not cases:
        raise HTTPException(status_code=404, detail="No labeled cases to export")

    log_audit(user, "export_finetuning", f"{len(cases)} cases, format={format}")

    buf = _io.StringIO()

    for case in cases:
        applicable = get_criteria_for_procedure(case["procedure_code"], case["payer"])
        criteria_spec = json.dumps([
            {"code": c.code, "short_name": c.short_name}
            for c in applicable
        ])

        # Build the prompt (what the model sees at inference time)
        user_prompt = (
            f"Evaluate this prior authorization case.\n\n"
            f"Procedure: CPT {case['procedure_code']} ({case.get('procedure_name', '')})\n"
            f"Payer: {case['payer']}\n\n"
            f"Clinical note:\n{case['raw_note']}\n\n"
            f"Applicable criteria: {criteria_spec}\n\n"
            f"For each criterion, assign status (met/not_met/not_applicable) "
            f"with evidence quote from the note."
        )

        # Build the completion (physician-verified gold answer)
        gold = {
            "criterion_matches": [
                {
                    "code": l["code"],
                    "status": l["gold_status"],
                    "evidence_quote": l.get("gold_evidence", ""),
                    "reasoning": l.get("physician_note", ""),
                }
                for l in case["criterion_labels"]
            ],
            "approval_score": case.get("gold_approval_score", 0.0),
            "approval_label": case.get("gold_approval_label", ""),
        }
        completion = json.dumps(gold, indent=None)

        if format == "messages":
            # Anthropic / OpenAI chat format
            record = {
                "messages": [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": completion},
                ],
                "metadata": {
                    "case_id": case["case_id"],
                    "cpt_code": case["procedure_code"],
                    "payer": case["payer"],
                    "outcome": case.get("actual_outcome", "unknown"),
                    "source": case.get("source", "manual"),
                },
            }
        else:
            # Prompt/completion format (legacy, some SFT APIs)
            record = {
                "prompt": user_prompt,
                "completion": completion,
                "metadata": {
                    "case_id": case["case_id"],
                    "cpt_code": case["procedure_code"],
                    "outcome": case.get("actual_outcome", "unknown"),
                },
            }

        buf.write(json.dumps(record) + "\n")

    buf.seek(0)
    return StreamingResponse(
        _io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="application/jsonl",
        headers={"Content-Disposition": f'attachment; filename="cardioauth_finetuning_{format}_{len(cases)}cases.jsonl"'},
    )


@app.post("/api/training/seed-peter")
async def seed_peter_cases(user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """Seed Peter's 5 validation cases as gold-labeled training cases.

    Follows Peter's scoring rules exactly:
      - Criteria NOT applying to the CPT → excluded from labels (handled by upstream filter)
      - Criteria APPLYING to the CPT and met by evidence → gold_status='met' with quote
      - All OTHER applicable criteria → gold_status='not_met' (default, Peter's rule #3)

    Never uses 'not_applicable' for criteria that pass the CPT filter.
    """
    from cardioauth.training import TrainingCase, CriterionLabel, save_training_case
    from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure

    def _make_case(case_id, title, cpt, payer, outcome, score, label, raw_note, met_labels):
        """Helper — fill in all applicable criteria. Met criteria have quotes,
        everything else defaults to not_met per Peter's rule #3."""
        applicable_codes = {c.code for c in get_criteria_for_procedure(cpt, payer)}
        met_codes = {l.code for l in met_labels}
        labels = list(met_labels)
        for code in sorted(applicable_codes - met_codes):
            labels.append(CriterionLabel(
                code=code,
                gold_status="not_met",
                gold_evidence="",
                physician_note="Applicable to CPT but evidence not present in note (Peter's rule #3)",
            ))
        return TrainingCase(
            case_id=case_id, title=title,
            procedure_code=cpt, procedure_name={
                "78492": "Cardiac Stress PET", "78452": "Lexiscan SPECT",
                "93458": "Left Heart Catheterization", "33361": "TAVR",
                "93656": "AF Catheter Ablation",
            }.get(cpt, cpt),
            payer=payer, raw_note=raw_note,
            actual_outcome=outcome, gold_approval_label=label,
            gold_approval_score=score, criterion_labels=labels,
            labeled_by=user.id, source="peter-v2",
        )


    # Build the 5 cases using _make_case (auto-fills non-met criteria as not_met)
    PETER_CASES = [
        _make_case(
            case_id="PETER-C1",
            title="C1 — 68M CAD + obesity + failed TST (PET)",
            cpt="78492", payer="UnitedHealthcare",
            outcome="approved", score=0.88, label="HIGH",
            raw_note=(
                "CARDIOLOGY OFFICE NOTE\n"
                "67-year-old male with known CAD presents for PA evaluation. Patient reports "
                "CCS Class III exertional angina, worsening over 3 months despite optimal "
                "medical therapy (aspirin, metoprolol 50 BID, atorvastatin 80 daily, lisinopril). "
                "Unable to do TST due to dyspnea and obesity (BMI 38). Prior exercise treadmill "
                "test was non-diagnostic, achieving only 68% of maximum predicted heart rate. "
                "Active diagnoses: I25.10, R07.89, E11.65, I10. HbA1c 7.8%, BNP 245. "
                "Normal sinus rhythm. LVEF 55%."
            ),
            met_labels=[
                CriterionLabel(code="EX-001", gold_status="met", gold_evidence="Unable to do TST due to dyspnea and obesity"),
                CriterionLabel(code="BMI-001", gold_status="met", gold_evidence="BMI 38"),
                CriterionLabel(code="NDX-002", gold_status="met", gold_evidence="only 68% of maximum predicted heart rate"),
                CriterionLabel(code="SX-003", gold_status="met", gold_evidence="CCS Class III exertional angina"),
                CriterionLabel(code="SX-004", gold_status="met", gold_evidence="CCS Class III"),
                CriterionLabel(code="DOC-001", gold_status="met", gold_evidence="CARDIOLOGY OFFICE NOTE"),
                CriterionLabel(code="MED-001", gold_status="met", gold_evidence="worsening over 3 months despite optimal medical therapy"),
                CriterionLabel(code="SX-001", gold_status="met", gold_evidence="worsening over 3 months"),
                CriterionLabel(code="RISK-002", gold_status="met", gold_evidence="I25.10, E11.65, I10, HbA1c 7.8%"),
                CriterionLabel(code="NDX-001", gold_status="met", gold_evidence="Prior exercise treadmill test was non-diagnostic"),
            ],
        ),
        _make_case(
            case_id="PETER-C2",
            title="C2 — 72F CAD + attenuation artifact (PET)",
            cpt="78492", payer="Blue Cross Blue Shield",
            outcome="approved", score=0.85, label="HIGH",
            raw_note=(
                "CONSULTATION NOTE\n"
                "72F with CAD s/p PCI (LAD 2022). Referred for cardiac stress PET. Prior SPECT "
                "stress test (2025-10-15) showed attenuation artifact in inferior wall, likely "
                "false positive, rendering it non-diagnostic. Recurrent atypical chest pain, "
                "CCS Class II angina. BMI 36. On metoprolol 100 daily, clopidogrel, rosuvastatin. "
                "HbA1c 7.4. LVEF 50-55% by echo. Normal sinus rhythm."
            ),
            met_labels=[
                CriterionLabel(code="NDX-001", gold_status="met", gold_evidence="attenuation artifact, likely false positive, rendering it non-diagnostic"),
                CriterionLabel(code="BMI-001", gold_status="met", gold_evidence="BMI 36"),
                CriterionLabel(code="DOC-001", gold_status="met", gold_evidence="CONSULTATION NOTE"),
                CriterionLabel(code="SX-003", gold_status="met", gold_evidence="Recurrent atypical chest pain, CCS Class II angina"),
                CriterionLabel(code="SX-004", gold_status="met", gold_evidence="CCS Class II"),
            ],
        ),
        _make_case(
            case_id="PETER-C3",
            title="C3 — 65M OA + paced rhythm (Lexiscan SPECT)",
            cpt="78452", payer="Blue Cross Blue Shield",
            outcome="approved", score=0.86, label="HIGH",
            raw_note=(
                "OFFICE VISIT NOTE\n"
                "65M with CAD and severe osteoarthritis of bilateral knees preventing ambulation. "
                "Unable to exercise adequately. Also has baseline paced rhythm on ECG "
                "(ventricular pacing). Referred for pharmacologic stress imaging. "
                "Active dx: I25.10, I48.91. Paroxysmal AFib on Eliquis 5mg BID, metoprolol. "
                "CCS Class II angina. On GDMT (optimal medical therapy) for 8 weeks with "
                "persistent symptoms. Troponin negative. LVEF 55%."
            ),
            met_labels=[
                CriterionLabel(code="EX-001", gold_status="met", gold_evidence="severe osteoarthritis preventing ambulation. Unable to exercise"),
                CriterionLabel(code="ECG-002", gold_status="met", gold_evidence="baseline paced rhythm on ECG (ventricular pacing)"),
                CriterionLabel(code="DOC-001", gold_status="met", gold_evidence="OFFICE VISIT NOTE"),
                CriterionLabel(code="SX-003", gold_status="met", gold_evidence="CCS Class II angina"),
                CriterionLabel(code="SX-004", gold_status="met", gold_evidence="CCS Class II"),
            ],
        ),
        _make_case(
            case_id="PETER-C4",
            title="C4 — 70F HFpEF + BMI 42 + attenuation (PET)",
            cpt="78492", payer="Blue Cross Blue Shield",
            outcome="approved", score=0.86, label="HIGH",
            raw_note=(
                "CARDIOLOGY CONSULTATION NOTE\n"
                "70F morbidly obese (BMI 42) with CAD and HFpEF. Prior SPECT (2025-09-10) "
                "limited by breast attenuation artifact and non-diagnostic for ischemia. "
                "PET strongly favored given BMI. NYHA Class II functional capacity. "
                "Continuing CCS Class II angina despite maximal medical therapy x 12 weeks "
                "(carvedilol 12.5 BID, lisinopril, furosemide, metformin). BNP 450. HbA1c 8.2. "
                "LVEF 55% by TTE (technically limited study due to body habitus)."
            ),
            met_labels=[
                CriterionLabel(code="BMI-001", gold_status="met", gold_evidence="morbidly obese (BMI 42)"),
                CriterionLabel(code="NDX-001", gold_status="met", gold_evidence="Prior SPECT limited by breast attenuation artifact and non-diagnostic"),
                CriterionLabel(code="NDX-004", gold_status="met", gold_evidence="TTE technically limited study due to body habitus"),
                CriterionLabel(code="DOC-001", gold_status="met", gold_evidence="CARDIOLOGY CONSULTATION NOTE"),
                CriterionLabel(code="SX-004", gold_status="met", gold_evidence="NYHA Class II"),
                CriterionLabel(code="SX-003", gold_status="met", gold_evidence="CCS Class II angina"),
                CriterionLabel(code="MED-001", gold_status="met", gold_evidence="maximal medical therapy x 12 weeks"),
                CriterionLabel(code="MED-002", gold_status="met", gold_evidence="maximal medical therapy x 12 weeks"),
            ],
        ),
        _make_case(
            case_id="PETER-C5",
            title="C5 — 62M ischemic CM + LBBB + HFrEF (PET)",
            cpt="78492", payer="UnitedHealthcare",
            outcome="approved", score=0.90, label="HIGH",
            raw_note=(
                "CARDIOLOGY OFFICE NOTE — H&P\n"
                "62M with ischemic cardiomyopathy (LVEF 35% by TTE, global hypokinesis), "
                "LBBB on baseline ECG. NYHA Class III symptoms despite maximal GDMT x 6 months "
                "(Entresto 49/51 BID, metoprolol succinate 100, empagliflozin 10, spironolactone 25). "
                "LBBB precludes standard stress ECG interpretation. BNP 680, troponin 0.04. "
                "Active dx: I25.10, I50.22. Prior PCI to RCA (2020). "
                "Cardiac stress PET requested to evaluate viable myocardium."
            ),
            met_labels=[
                CriterionLabel(code="LVEF-002", gold_status="met", gold_evidence="LVEF 35% by TTE"),
                CriterionLabel(code="ECG-001", gold_status="met", gold_evidence="LBBB on baseline ECG. LBBB precludes standard stress ECG interpretation"),
                CriterionLabel(code="SX-004", gold_status="met", gold_evidence="NYHA Class III"),
                CriterionLabel(code="MED-001", gold_status="met", gold_evidence="maximal GDMT x 6 months"),
                CriterionLabel(code="MED-002", gold_status="met", gold_evidence="maximal GDMT x 6 months"),
                CriterionLabel(code="DOC-001", gold_status="met", gold_evidence="CARDIOLOGY OFFICE NOTE — H&P"),
                CriterionLabel(code="SX-001", gold_status="met", gold_evidence="despite maximal GDMT x 6 months"),
            ],
        ),
    ]

    saved = 0
    for case in PETER_CASES:
        if save_training_case(case):
            saved += 1

    log_audit(user, "seed_peter", f"saved {saved}/5 cases (with CPT-gated auto-fill)")
    return {"status": "ok", "saved": saved, "total": len(PETER_CASES)}


@app.get("/api/feedback/corrections")
async def list_corrections(limit: int = 50, user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    """List recent corrections (admin view)."""
    from cardioauth.feedback import get_all_corrections
    return {"corrections": get_all_corrections(limit=limit)}


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
