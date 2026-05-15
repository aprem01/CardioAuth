# Email — Peter — 2026-05-14 — Binary-content fix + SMART simulator how-to

**To:** Peter
**Subject:** Re: Epic integration — blank-fields bug fixed (good catch)

---

Peter,

Great catch, and you used it exactly right. You found a real gap and there's a second piece worth knowing about.

**What you hit (now fixed)**

Epic serves DocumentReference note bodies as separate Binary resources — not inline. Our chart mapper was only reading inline attachments, so the pipeline saw encounter headers but never the actual note text. That's why fields came back blank. Shipped the fix: the system now fetches and decodes each Binary (HTML clinical notes → plaintext) before building the corpus. Verified against Camila — 22 documents now resolve to real note text instead of metadata shells.

**The second piece — why it still blocks on Camila**

Even with the note bodies resolved, running Camila Lopez still produces a blocked submission. That's not a bug — it's the test patient. Epic's published sandbox patients (Camila, Derrick, etc.) are generic primary-care synthetic charts: vaccine records, medication instructions, routine progress notes. There's no cardiology history in there — no LBBB, no prior stress test, no ejection fraction. So retrieval correctly finds nothing cardiology-relevant, and the coherence gate blocks rather than inventing content. The system under-claiming on a thin chart is the safety behavior working — same thing you noted approvingly.

To see the pipeline do real work against Epic data, the input has to be a real cardiology case. Which is the ask below.

**What I need from you**

5–10 deidentified real PAs your office submitted recently — current encounter note + what the payer decided. With those I can: (1) run them through the Epic-connected pipeline so you see it produce real packets, and (2) calibrate corpus retrieval against your actual documentation style instead of Epic's generic synthetic patients.

---

**Bonus — how to use the "Production flow simulator" (SMART App Launch)**

On the Epic Sandbox page, below the patient-picker form, there's a collapsed section: **▸ Production flow simulator — SMART App Launch (clinician OAuth)**. This simulates how a physician would actually launch CardioAuth from inside an Epic chart in a real deployment. To test it:

1. Expand the section, click **Launch from Epic**
2. Epic's Hyperspace login appears — sign in with a clinician test account:

   | User ID | Password | Notes |
   |---|---|---|
   | `FHIRTWO` | `EpicFhir11!` | **Use this one** — has a linked provider record (PractitionerRole), so the launch carries a real clinician identity |
   | `FHIR` | `EpicFhir11!` | No linked provider record — works, but no PractitionerRole |

3. Epic prompts you to authorize the scopes CardioAuth requests → click **Allow**
4. Browser redirects back to CardioAuth with a session token + patient context — you'll see an "Active session" card with the patient ID, encounter, expiry, and granted scopes
5. Click **Run pipeline using session token** — pulls the chart with that session's user-scoped token and runs the pipeline

Two notes on this flow:
- If you get *"you cannot authenticate while another process is already logged in"* — close all browser windows for that session and start fresh, or use a private/incognito window. It's a stale Epic session, not an app error.
- The patient-picker form *above* the simulator does the same chart pull without the OAuth dance. For day-to-day testing use the form; the simulator is there specifically to validate the production-deployment login flow.

In a real Epic install this would be invisible: the physician is already logged into Epic, clicks "Launch CardioAuth" inside the chart, and lands directly in the app with the patient already in context. The sandbox just makes those steps explicit because we're outside a real Epic environment.

Thanks,
Prem

<!-- STATUS: drafted 2026-05-14, awaiting send -->
