# Validation batch — template and instructions

## What you send

One JSON object per line (JSONL format). Each object represents a
retrospective case where you already know the payer outcome.

## Required fields per case

```json
{
  "case_id": "your-label",
  "procedure_code": "78492",
  "procedure_name": "Cardiac PET",
  "payer_name": "UnitedHealthcare",
  "raw_note": "full clinical note as plain text...",
  "gold_outcome": "approved",
  "gold_criterion_labels": {
    "EX-001": "met",
    "BMI-001": "met",
    "NDX-001": "not_met"
  }
}
```

### Field details

- **case_id** — any unique label ("PT-01", "H-2025-47", etc.). No PHI.
- **procedure_code** — the CPT submitted
- **procedure_name** — free text
- **payer_name** — "UnitedHealthcare", "Aetna", "Anthem", "Medicare"
- **raw_note** — the clinical note text. **Deidentify before sending** —
  strip names, DOBs, MRNs, facility identifiers.
- **gold_outcome** — `"approved"` or `"denied"`
- **gold_criterion_labels** — your per-criterion adjudication using our
  taxonomy codes. Each value is `"met"`, `"not_met"`, or `"not_applicable"`.
  See `criterion-codes.md` for the full list.

## Easier alternative if JSONL is a pain

Send a spreadsheet with columns:
  `case_id | cpt | payer | note | outcome | met_codes | not_met_codes`

We'll convert it to JSONL on our end.

## Minimum useful batch

**10 cases** gives us a first signal. **20–30** gives us a defensible
calibration. **50+** is what a real validation study looks like.

## What comes back

Within 24 hours of receiving the batch, you'll get:

- **Accuracy per criterion** — where we agree with you, where we don't
- **Overall sensitivity / specificity / PPV / NPV** on approve/deny prediction
- **Calibration curve** — does a 75% score actually yield 75% approvals?
- **Silent-drop rate** — criteria we missed that you labeled met
- **Side-by-side** for any cases where our output disagrees with yours

That report is the first real number on whether CardioAuth is
ready for a pilot practice.

## How to send

Commit to the repo or email as a `.jsonl` attachment. Template file:
`docs/validation-batch/template.jsonl`.
