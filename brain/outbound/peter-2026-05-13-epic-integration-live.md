# Email — Peter — 2026-05-13 — Epic integration live, packet PDF live, shadow testing built

**To:** Peter
**Subject:** Re: Big-Picture Thoughts — Epic chart-pull working end-to-end; packet PDF and shadow testing shipped

---

Peter,

Quick update — most of the gap between "good demo" and "useful workflow product" closed this week. The biggest item: we are now reading real charts from Epic's sandbox end-to-end, including the historical documents.

**1. Real Epic FHIR integration — live in production**

Open [https://cardioauth2-production.up.railway.app/#epic-sandbox](https://cardioauth2-production.up.railway.app/#epic-sandbox), pick a test patient (Camila Lopez is the cardiology-rich one), pick a CPT and payer, click **Run pipeline against Epic**. About 30–60 seconds later you see:

- Chart pulled from `fhir.epic.com`: 9/9 FHIR resource types (Patient, Encounter, Condition, Observation, MedicationRequest, DiagnosticReport, Procedure, Coverage, DocumentReference)
- Camila's chart: 10 encounters, 7 diagnostic reports, 2 procedures, 14 historical DocumentReferences, 249 observations
- Lean pipeline result: verdict, score, criterion evaluations, retrieved corpus snippets with `[doc_type date]` citations from the actual Epic documents
- **Download PDF** button: staff-submittable packet (cover sheet, populated PA form fields, criterion evaluation with rationale, historical evidence appendix, audit footer)

This is the "treadmill 3 years ago" feature against real Epic data, not handcrafted JSON. Two Epic vendor apps registered: `cardioauth-v2` for Backend Services (headless reads — used by the page above) and `cardioauth-smart` for SMART App Launch (clinician-driven OAuth — the flow that lights up when CardioAuth is launched from inside an Epic chart in production). Both are activated and tokens are issuing.

**2. Staff-submittable PA packet PDF — your stated "critical bridge"**

The `Download PDF` button on the result panel produces a packet your staff can attach to any payer portal submission. Five sections: cover sheet, populated PA form fields (color-coded populated / missing / verify), criterion evaluation with status + rationale, historical evidence appendix with citations from prior chart documents, audit footer with FHIR Provenance reference. 9 unit tests on the generator; verified live (3 pages, ~9KB, valid PDF/1.4).

**3. Shadow Testing capture — your stated #2 priority after the packet PDF**

[/#shadow](https://cardioauth2-production.up.railway.app/#shadow) — for each real case staff runs through CardioAuth, log whether they submitted what we produced as-is, edited it, or didn't submit at all (plus confidence + notes). After 20–25 cases we have a real agreement rate and a list of the gaps that matter most. Captures aggregate rates by payer. Persists across container restarts. Read-only by design — no auto-submit, no chart writes.

**4. Outcome capture loop — closes the feedback loop**

[/#outcomes](https://cardioauth2-production.up.railway.app/#outcomes) — pending-decision queue + headline approval/denial rates + per-(payer × CPT) rollup. Recording an outcome updates rolling stats, files the case as a Pinecone precedent, and drafts an appeal letter when denied. Foundation for the denial-prediction layer.

**5. Whole-chart Demo refresh**

[/#corpus-demo](https://cardioauth2-production.up.railway.app/#corpus-demo) — the synthetic "treadmill 3 years ago" case still loads on first visit. Now has a Download PDF button on the result panel too.

**What I'd like from you next**

Two requests:

1. Try the Epic Sandbox page end-to-end (Camila → SPECT → UnitedHealthcare → Run). Tell me anything that's confusing or off. The flow is meant to be: pick patient, click button, see result, download PDF. If it's not that clean in practice, I want to know.

2. When you're ready for shadow testing, send me 5–10 deidentified real PAs your office submitted recently (current note + what payer decided). I'll seed the outcomes database and the shadow-testing aggregates with real cases so we have a baseline agreement rate before your back office starts logging on the live workflow.

**Architecture status**

| Layer | Status |
|---|---|
| Epic FHIR R4 (Backend Services, JWT private_key_jwt) | Live, 9/9 resources |
| Epic FHIR R4 (SMART App Launch, authorization_code) | Live, awaiting first real launch test |
| Bundle → Corpus mapping (LOINC-aware doc typing) | Live |
| Lean pipeline (criteria + corpus retrieval) | Live against real Epic data |
| Packet PDF generator | Live |
| Outcome capture | Live |
| Shadow testing capture | Live |
| Da Vinci PAS payer-side submission | Not built — CMS-0057-F mandate kicks in 2027, we'll be ready |

Next week I'm calibrating the corpus retrieval against your real cases when you send them.

Thanks,
Prem

<!-- STATUS: drafted 2026-05-13, awaiting send -->
