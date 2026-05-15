# Email — Peter — 2026-05-12 — Epic sandbox plan + shadow testing

**To:** Peter
**Subject:** Re: Big-Picture Thoughts — Epic sandbox + shadow plan

---

Peter,

Agree across the board. Trying to deidentify embedded PDFs from your live charts is too high-friction for what we'd get out of it, and you're right that the real proof is whether we can walk into an Epic environment and reliably do the whole loop. Re-sequencing accordingly.

**Epic sandbox — this week.**
The transport layer is already in our code — Epic's R4 endpoint, JWT backend-services auth (RS384), token caching, a `get_patient_bundle()` call wired into the chart agent. What's never run end-to-end is the part that matters: we don't yet pull `DocumentReference` (historical notes, prior stress-test PDFs) or `Encounter`, and we haven't registered our app with Epic so the client has never made a real call. Three things to close this week:

1. Register the app on Epic's vendor sandbox (fhir.epic.com — developer tier doesn't need your health-system IT) and load the production keypair so the client can actually authenticate
2. Add `DocumentReference` + `Encounter` to the resource pull and index attachments into the longitudinal corpus — this is the bridge between "we have the transport" and "we can do the treadmill-3-years-ago feature on real Epic data"
3. Run a SPECT (78452) and a PET (78492) order against a synthetic cardiology patient end-to-end: chart pull → corpus index → retrieval → packet assembly. Send you the link to step through the first clean run.

That's the directly-testable claim — does the chart-read story hold up against Epic's real bundle shape, not just our handcrafted JSON.

**Payer-form bridge.** I think the cleanest way through this without real portal access is to make the output a *staff-submittable packet* — a filled payer-specific PDF (UHC, Aetna, eviCore all publish their PA forms) + cover letter + cited supporting evidence — and treat the submission itself as out-of-scope for now. If your staff can pick that packet up and submit without rework, the bridge is crossed. We can wire real portals later when one of your contracted payers will give us API access.

**Shadow testing protocol — when you're ready.**
Read-only access. CardioAuth produces a packet + a dashboard recommendation. Staff submits normally; no auto-submission, no chart writes. We log per case: would staff have submitted what CardioAuth produced, and if not, what they changed. After 20–25 cases we'd have a real agreement rate and a list of the gaps that matter most in your workflow.

**On the competitors:** I looked at the same list. Humata is general-purpose chart QA, eviCore intelliPath is locked to their own payer network, MCG and Rhyme are policy/connectivity layers, Valer is workflow orchestration. None of them goes deep on cardiology-specific criteria, none of them does longitudinal corpus retrieval over historical documents, and none builds a learning loop from outcomes. That's our wedge — staying narrow on cardiology and going deep inside Epic is exactly the right strategic call.

Will send you the sandbox link as soon as the first synthetic case runs cleanly.

Thanks,
Prem

<!-- STATUS: drafted 2026-05-12, awaiting send -->
