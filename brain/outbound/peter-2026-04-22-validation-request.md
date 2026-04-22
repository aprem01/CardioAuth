# Email — Peter — 2026-04-22 — validation batch request

**To:** Peter
**Subject:** Validation batch — first real numbers on CardioAuth

---

Peter,

The E2E demo is live — you can step through it at [https://cardioauth2-production.up.railway.app/#demo-e2e](https://cardioauth2-production.up.railway.app/#demo-e2e). Runs a full FHIR-to-payer-outcome pipeline in under 25 seconds so you can see where latency lives at each stage.

Next unlock: a real validation number. The validation harness at `POST /api/validation/run` is ready; I just need your labeled cases to feed it.

**What I'm asking for:** 20–30 retrospective cases you already know the payer outcome on. For each:

- deidentified clinical note (full text)
- CPT + payer
- what the payer actually decided (approved / denied; denial reason if denied)
- your per-criterion adjudication using our taxonomy codes (list attached)

Minimum useful batch is 10 cases. 20–30 gives defensible calibration. 50+ is a real validation study — but let's start with whatever you can pull in an hour.

**Two formats, whichever is easier:**

1. **JSONL** — one JSON per line. Template in the repo at `docs/validation-batch/template.jsonl`. Criterion codes reference at `docs/validation-batch/criterion-codes.md`.
2. **Spreadsheet** — columns: `case_id | cpt | payer | note | outcome | met_codes | not_met_codes`. I'll convert to JSONL on our end.

**What comes back within 24 hours:**

- Per-criterion accuracy — where we agree with you, where we don't
- Overall sensitivity / specificity / PPV / NPV on approve/deny prediction
- Calibration curve — does our 75% score actually yield 75% approvals?
- Silent-drop rate — criteria we missed that you labeled met
- Side-by-side diff for every case we disagreed on

That report is what tells us whether we're pilot-ready or need another iteration. Every conversation with a practice after this stalls on "how accurate is it" and we need to answer with numbers, not anecdotes.

**PHI handling:** please deidentify before sending — no names, DOBs, MRNs, facility identifiers. We don't have BAAs with our vendors yet, so the cases need to be synthetic or fully scrubbed. If that's a blocker, I can send you a deidentification checklist.

No rush — whenever you can pull them. Even 10 cases lets us start calibrating.

Thanks,
Prem

<!-- STATUS: drafted 2026-04-22, awaiting send -->
