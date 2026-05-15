# Email — Peter — 2026-05-13 — Epic sandbox now wired in code

**To:** Peter
**Subject:** Re: Big-Picture Thoughts — Epic sandbox is wired in code

---

Peter,

Good — and I agree on the sequencing. Since your last note I've stopped refining the synthetic demo path and put the lift entirely into the Epic-connected workflow. Here's what's now actually in code (not just planned), with what you can test yourself.

**Epic sandbox integration — built and partially live.**

1. **JWT backend-services auth.** Done. Our `FHIRClient` signs an RS384 JWT per Epic's spec and exchanges it for a bearer token. I generated a 2048-bit RSA keypair, the private half lives in Railway as an env var, the public JWK Set is served at `https://cardioauth2-production.up.railway.app/.well-known/jwks.json`. I verified the round-trip locally — a JWT we sign here is correctly validated by the public key Epic will fetch.

2. **DocumentReference + Encounter pulls.** These are the resources that make the "treadmill 3 years ago" feature work on real Epic data — without them we only have structured codes, not the prior-stress-test narratives where the historical evidence actually lives. Both now in the resource list `FHIRClient.get_patient_bundle()` fetches. Binary attachments resolve through a separate helper since Epic returns most clinical content as binary refs.

3. **Bundle → corpus mapper.** New module (`cardioauth/fhir/corpus_mapper.py`) that takes Epic's R4 Bundle shape and produces our `PatientCorpus`. Picks document type from LOINC codes first, falls back to display text. Skips attachments with no resolvable body so retrieval doesn't index empty docs. 13 unit tests covering the mapping edge cases.

4. **End-to-end demo route.** `POST /api/demo/epic-sandbox` does the full loop: pull Bundle → map to corpus → run lean pipeline → return chart_summary + the same submission packet the synthetic demo produces. Defaults to Epic's published cardiology-rich test patient (Camila Lopez).

5. **Vendor app registered.** App is created in your Epic vendor account, non-production client ID is loaded into our Railway config (`35e0ce3a-67c0-4b4f-832b-0cd3d59bd76b`).

**What's still your-side to finish on the Epic vendor portal:**

- Application Audience → Backend Systems
- Paste `https://cardioauth2-production.up.railway.app/.well-known/jwks.json` into both the Non-Production and Production JWK Set URL fields (the form has a `http://` dropdown — switch to `https://` first)
- SMART on FHIR Version → R4, SMART Scope Version → SMART v2
- Incoming APIs → Patient, Encounter, Condition, Observation, DocumentReference, Binary, DiagnosticReport, Procedure, MedicationRequest, Coverage (read + search on each)
- Intended Purposes → Administrative Tasks + Clinical Team; Intended Users → Clinical Team + Healthcare Administrator
- Save, then promote from Test to Ready

About 5 minutes after Epic fetches the JWKS, the sandbox endpoint will return a real Bundle from Epic and run the full pipeline against it. That's the directly-testable claim — does the chart-read story hold up on Epic's actual data shape, not just our handcrafted JSON. If it doesn't, we'll know early and I'll know exactly what to fix.

**Payer-form bridge.** I think the cleanest way through without real portal access is to make the output a *staff-submittable packet* — a filled payer-specific PDF (UHC, Aetna, eviCore all publish their PA forms) + cover letter + cited supporting evidence. If your staff can pick that packet up and submit without rework, the bridge is crossed. Real portal API access we can chase later with one of your contracted payers.

**Shadow testing protocol — when you're ready.** Read-only access. CardioAuth produces a packet + dashboard recommendation. Staff submits normally; no auto-submission, no chart writes. We log per case: would staff have submitted what CardioAuth produced, and if not, what they changed. After 20–25 cases we'd have a real agreement rate and a list of the gaps that matter most in your workflow.

**Competitors.** Agreed. From what I've looked at, Humata is general-purpose chart QA, eviCore intelliPath is locked to their payer network, MCG and Rhyme are policy/connectivity layers, Valer is workflow orchestration. None goes deep on cardiology criteria, none does longitudinal corpus retrieval over historical documents, and none builds a learning loop from real outcomes. Staying narrow on cardiology and going deep inside Epic is exactly the right call.

**One thing that would help me.** If you can complete the vendor portal save and toggle the app to Ready when you have ten minutes, I'll watch the first sandbox call and send you the chart_summary + packet from a synthetic patient. That's the deliverable that proves the integration loop and gives us something concrete to take into shadow testing.

Thanks,
Prem

<!-- STATUS: drafted 2026-05-13, awaiting send; supersedes peter-2026-05-12-epic-sandbox-plan.md -->
