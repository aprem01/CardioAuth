# Email — Peter — 2026-05-12 — vision alignment + status against big-picture

**To:** Peter
**Subject:** Re: Big-Picture Thoughts — where we are now

---

Peter,

Thank you for laying it out so clearly. Your bullets describe exactly the product I want to ship, and a lot of it is now live. Quick map of where we stand against each piece:

**1. Reads the entire chart, not just the current note.** Done. The longitudinal corpus + retrieval lives at [/#corpus-demo](https://cardioauth2-production.up.railway.app/#corpus-demo). The "treadmill 3 years ago" example is literally the flagship demo — current note doesn't mention failure to achieve target HR, but the pipeline pulls the 2023 nondiagnostic submaximal stress and the 2021 LBBB ECG and surfaces both into the submission package. On the demo case this moves the verdict from MEDIUM 52% to HIGH 88% with six additional criteria newly met, every one citing the specific historical document.

**2. Auto-populates the payer-specific form.** Lean pipeline produces a typed payload + provenance trail today. What's not yet automated is the last-mile push into each payer portal — that's the next chunk of work.

**3. Dashboard for human review / approval.** [/#demo-e2e](https://cardioauth2-production.up.railway.app/#demo-e2e) for the full end-to-end view; [/#outcomes](https://cardioauth2-production.up.railway.app/#outcomes) for the queue and approval-rate tracking.

**4. Proprietary denial / outcome database.** Shipped yesterday. Every recorded payer decision goes to durable storage, rolls up live approval rates per (payer × CPT), and stores as a Pinecone precedent so future similar cases learn from it. This is the foundation the denial-prediction layer sits on — but it needs real outcomes to be useful, which is my ask below.

**5. Recall — patients who didn't return.** Already in the sidebar under Analytics → Recall Queue. Configurable follow-up window per procedure.

**6. Never modifies the physician note.** Respected by design — the system writes a submission package, never the chart.

**The honest gap:** deep Epic integration. Today the chart-read path runs against a FHIR stub. Real Epic is the next architectural lift and the biggest piece between "demo that wows" and "FTE-replacing in your clinic."

**One concrete ask:** if you can send 5–10 real deidentified PAs you submitted in the last quarter — current note + what the payer actually decided — I'll seed the outcomes database with them and we'll have the first real per-payer approval-rate baseline within a day. Without real outcomes, the denial-prediction layer stays theoretical.

Next two weeks I'm focused on Epic integration design and getting the corpus retrieval calibrated against your real cases.

Thanks,
Prem

<!-- STATUS: drafted 2026-05-12, awaiting send -->
