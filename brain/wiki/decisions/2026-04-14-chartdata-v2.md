# 2026-04-14 — ChartData v2 (explicit categorization)

**Status:** active
**Driver:** Peter's Apr 14 observations on schema bleed across multiple cases

## Problem

The original ChartData had three over-broad buckets that silently absorbed data belonging elsewhere:

- **comorbidities** — catching symptoms (dyspnea), exam findings (edema), family history
- **relevant_imaging** — catching ECG findings and stress test results
- **prior_treatments** — catching procedures, tests, and past clinical events (MI 2021)

Claude was extracting correctly given the buckets we defined. The buckets themselves were the problem.

## Decision

Split into 8 explicit categories. Every bucket has one and only one kind of data:

| Category | Contents | Used to be in |
|---|---|---|
| `active_comorbidities` | Chronic conditions ONLY (HTN, DM, CKD) | `comorbidities` |
| `past_medical_history` | Prior events with dates (MI 2021) | `prior_treatments` |
| `family_history` | First-degree relative conditions | `comorbidities` |
| `current_symptoms` | Patient-reported/observed symptoms with timeline | `comorbidities` |
| `exam_findings` | Physical exam findings (JVD, edema, murmur) | `comorbidities` |
| `ecg_findings` | Baseline ECG — rhythm, conduction, strain, pacing | `relevant_imaging` |
| `prior_stress_tests` | ETT, SPECT, PET, stress echo | `relevant_imaging` + `prior_treatments` |
| `prior_procedures` | PCI, CABG, TAVR, ablation | `prior_treatments` |

Legacy flat fields (`comorbidities`, `prior_treatments`) retained for backward compatibility. `migrate_legacy_chart()` routes legacy entries into v2 buckets heuristically.

ChartAgent prompt rewritten with explicit category rules + named negative examples for every common misrouting.

## Consequences

- Downstream reasoning is cleaner — no more hunting through `comorbidities` for a symptom.
- ECG criteria (ECG-001..004) now read only from `ecg_findings` — can't accidentally cite an echo report.
- Old custom-request payloads keep working via migration.
- UI clinical summary panel redesigned to render the 8 sections separately with visual distinction.

## Related

- Commit `982af0d`
- [decisions/2026-04-14-pathway-groups.md](2026-04-14-pathway-groups.md)

Last updated: 2026-04-14
