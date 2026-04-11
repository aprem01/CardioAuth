# Feature #2: Automated PA Submission — Technical Scope

## Overview

When a cardiologist places an order (imaging, intervention, ablation), CardioAuth
automatically detects it, assembles the PA package from chart data, submits it
electronically to the payer, and tracks the outcome — all in the background with
zero clicks.

---

## Architecture

```
┌─────────────┐     CDS Hook      ┌──────────────┐     async      ┌──────────────┐
│  Epic EHR    │ ──────────────▶  │  CardioAuth  │ ────────────▶  │  Task Queue  │
│ (order-sign) │  order-sign hook  │  Webhook API │  Celery/ARQ    │  (Redis)     │
└─────────────┘                   └──────────────┘                └──────┬───────┘
                                                                         │
                    ┌────────────────────────────────────────────────────┘
                    ▼
        ┌───────────────────┐
        │  PA Pipeline Task  │
        │                   │
        │  1. CHART_AGENT   │ ── FHIR R4 queries for patient data
        │  2. POLICY_AGENT  │ ── RAG retrieval + payer criteria
        │  3. REASONING     │ ── criteria matching + gap check
        │  4. SUBMISSION    │ ── package assembly
        │  5. TRANSMIT      │ ── Availity / payer portal API
        │  6. TRACK         │ ── poll for outcome
        └───────────────────┘
                    │
                    ▼
        ┌───────────────────┐         ┌───────────────────┐
        │  Outcome Store    │ ──────▶ │  Analytics Engine  │
        │  (PostgreSQL)     │         │  Monthly reports   │
        └───────────────────┘         └───────────────────┘
```

---

## Component Breakdown

### 1. Epic CDS Hooks Integration (Trigger)

**What:** Epic fires a webhook when a provider signs an order that requires PA.

**How:**
- Register as a CDS Hooks service with Epic App Orchard
- Implement the `order-sign` hook endpoint: `POST /cds-services/pa-check`
- Hook receives: patient ID, order details (CPT, ICD-10, ordering provider)
- Response options:
  - **Card** (optional): "PA submission initiated in background" informational card
  - **System action**: launch async PA pipeline

**Epic Requirements:**
- CDS Hooks 1.0 specification compliance
- Must be registered in App Orchard as a CDS Service
- SMART on FHIR launch context for patient data access
- Must respond within 10 seconds (just acknowledge + queue)

**Endpoint spec:**
```json
{
  "hook": "order-sign",
  "hookInstance": "uuid",
  "context": {
    "userId": "Practitioner/123",
    "patientId": "Patient/456",
    "draftOrders": {
      "resourceType": "Bundle",
      "entry": [
        {
          "resource": {
            "resourceType": "ServiceRequest",
            "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "78492"}]},
            "reasonCode": [{"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I25.10"}]}]
          }
        }
      ]
    }
  }
}
```

**Implementation files:**
- `cardioauth/hooks/cds_hooks.py` — CDS Hooks service discovery + order-sign handler
- `cardioauth/hooks/fhir_context.py` — extract patient/order from hook context

---

### 2. Async Task Queue (Processing)

**What:** Background worker processes PA requests without blocking the EHR.

**Stack options:**
| Option | Pros | Cons |
|--------|------|------|
| **Celery + Redis** | Battle-tested, rich ecosystem | Heavy for prototype |
| **ARQ (async Redis queue)** | Lightweight, Python-native async | Less tooling |
| **FastAPI BackgroundTasks** | Zero infra, built-in | No retry, no persistence |
| **Dramatiq + Redis** | Simple API, good defaults | Smaller community |

**Recommendation:** Start with **ARQ** for prototype (async-native, lightweight),
migrate to Celery when scaling.

**Task states:**
```
QUEUED → CHART_EXTRACTION → POLICY_LOOKUP → REASONING → PACKAGING → SUBMITTING → SUBMITTED → APPROVED/DENIED/PENDING
```

**Implementation files:**
- `cardioauth/tasks/pa_task.py` — main PA pipeline as async task
- `cardioauth/tasks/worker.py` — ARQ worker config
- `cardioauth/tasks/models.py` — TaskStatus, PASubmission Pydantic models

---

### 3. Availity Integration (Electronic Submission)

**What:** Submit PA requests electronically to payers via Availity's API.

**Availity Essentials API:**
- **Prior Auth / Referral API** — submit 278 (X12 278) transactions
- Supports: UHC, Aetna, BCBS, Cigna, Humana, and 100+ payers
- RESTful JSON wrapper over X12 278 ASC standard
- Real-time + batch submission modes

**Auth flow:**
1. Register as Availity developer (free sandbox)
2. OAuth 2.0 client credentials → access token
3. Submit PA request as FHIR-like JSON or X12 278
4. Poll for determination (sync for some payers, async for others)

**Key endpoints:**
```
POST /availity/v1/authorizations          — submit PA
GET  /availity/v1/authorizations/{id}     — check status
GET  /availity/v1/authorizations?patient=  — search by patient
```

**Payload mapping (CardioAuth → Availity):**
```
ChartData.patient_info    → subscriber/patient demographics
ChartData.diagnosis_codes → diagnosis (ICD-10)
procedure_code (CPT)      → service type + procedure code
PolicyData.payer          → payer ID routing
ReasoningResult.narrative → clinical justification text
attached documents        → supporting documentation
```

**Alternative payer portals (direct, no Availity):**
- **CoverMyMeds** — mostly pharmacy PA but expanding to medical
- **Surescripts** — pharmacy PA network
- **Direct payer APIs** — UHC, Aetna have their own (fragmented)

**Recommendation:** Start with **Availity sandbox** — broadest payer coverage,
single integration point.

**Implementation files:**
- `cardioauth/submission/availity.py` — Availity API client
- `cardioauth/submission/x278_builder.py` — build X12 278 payload from PA data
- `cardioauth/submission/tracker.py` — poll and update submission status

---

### 4. Outcome Tracking + Storage

**What:** Store every PA submission and its outcome for analytics.

**Schema:**
```sql
CREATE TABLE pa_submissions (
    id              UUID PRIMARY KEY,
    patient_mrn     TEXT NOT NULL,
    provider_npi    TEXT NOT NULL,
    payer           TEXT NOT NULL,
    cpt_code        TEXT NOT NULL,
    icd10_codes     TEXT[] NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL,  -- submitted/approved/denied/pending/peer_review
    determination_at TIMESTAMPTZ,
    denial_reason   TEXT,
    denial_code     TEXT,
    predicted_score FLOAT,
    criteria_met    INTEGER,
    criteria_total  INTEGER,
    turnaround_hrs  FLOAT,
    availity_ref    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_submissions_provider ON pa_submissions(provider_npi);
CREATE INDEX idx_submissions_payer ON pa_submissions(payer);
CREATE INDEX idx_submissions_status ON pa_submissions(status);
```

**Implementation files:**
- `cardioauth/analytics/models.py` — SQLAlchemy/Pydantic models
- `cardioauth/analytics/store.py` — CRUD operations

---

### 5. Retrospective Analytics Dashboard

**What:** Monthly report per Peter's spec: volume, payer mix, approval rates, denial reasons.

**Report contents:**
- Tests/procedures ordered per provider (bar chart)
- Payer mix breakdown (pie chart)
- Approval rate by payer (bar chart with trend line)
- Top denial reasons (ranked list with frequency)
- Average turnaround time by payer
- Month-over-month trend

**API endpoints:**
```
GET /api/analytics/monthly?month=2026-03&provider_npi=
GET /api/analytics/denial-reasons?payer=&period=90d
GET /api/analytics/turnaround?payer=&period=90d
GET /api/analytics/provider-summary?provider_npi=
```

**Implementation files:**
- `cardioauth/analytics/reports.py` — query builders for each report type
- New frontend page: `#analytics` with charts (Chart.js or similar)

---

## Implementation Phases

### Phase 1: Foundation (2-3 weeks)
- [ ] Async task queue with ARQ + Redis
- [ ] PA pipeline as background task (reuse existing orchestrator)
- [ ] PostgreSQL outcome store (Supabase or Railway Postgres)
- [ ] Submission status tracking API + UI page

### Phase 2: Availity Integration (2-3 weeks)
- [ ] Availity developer sandbox registration
- [ ] OAuth client + API wrapper
- [ ] X12 278 payload builder from CardioAuth data
- [ ] Submit → poll → update status loop
- [ ] Error handling + retry logic

### Phase 3: Epic CDS Hooks (2-3 weeks)
- [ ] CDS Hooks service discovery endpoint
- [ ] order-sign hook handler
- [ ] SMART on FHIR launch context
- [ ] App Orchard registration (sandbox)
- [ ] End-to-end: order → hook → queue → submit → track

### Phase 4: Analytics (1-2 weeks)
- [ ] Monthly report queries
- [ ] Analytics API endpoints
- [ ] Dashboard UI with charts
- [ ] Export to PDF/CSV

### Phase 5: Production Hardening
- [ ] HIPAA audit logging
- [ ] Rate limiting + circuit breakers
- [ ] Alerting on failed submissions
- [ ] Provider notification (in-basket message on determination)
- [ ] Multi-practice support

---

## Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Availity sandbox ≠ prod behavior | Submission failures in prod | Test with real payer sandbox credentials early |
| Epic CDS Hooks latency requirement (10s) | Hook timeout, no PA triggered | Queue immediately, respond with info card only |
| Payer determination delays (days-weeks) | Analytics lag | Async polling + webhook where payers support it |
| X12 278 format complexity | Submission rejections | Use Availity's JSON wrapper to avoid raw X12 |
| HIPAA compliance for stored data | Legal risk | Encrypt at rest, audit logs, BAA with all vendors |

---

## Cost Estimate (Infrastructure)

| Component | Service | Monthly Cost |
|-----------|---------|-------------|
| Task queue | Redis (Railway) | ~$5-10 |
| Database | PostgreSQL (Railway/Supabase) | ~$0-25 |
| Availity API | Free (no per-transaction fee) | $0 |
| Claude API | Anthropic (per PA processed) | ~$0.05-0.15/PA |
| Hosting | Railway (existing) | ~$5-20 |
| **Total** | | **~$10-70/month** |
