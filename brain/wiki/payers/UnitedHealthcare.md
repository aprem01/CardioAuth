# UnitedHealthcare

## PA rules (cardiology)

### Cardiac PET (CPT 78492)
- Auth required.
- Typical turnaround: 4–5 days.
- Requires one of the `pharm_stress_justification` pathway: EX-001, ECG-001, ECG-002, ECG-003, or ECG-004.
- Requires one of `prior_testing_nondiagnostic`: NDX-001 through NDX-004.
- BMI ≥ 35 (BMI-001) strengthens PET-over-SPECT case.
- Top historical denial reasons (seeded):
  - Prior non-diagnostic stress test not documented
  - No specific functional limitation preventing exercise
  - Failure to document new/worsening symptoms vs baseline
  - BMI ≥35 not documented when PET is requested over SPECT

### Lexiscan SPECT (CPT 78452)
- Auth required.
- Typical turnaround: 3–4 days.
- Top historical denial reasons:
  - Prior exercise stress test result not documented
  - Pharmacologic stress justification incomplete
  - No documented symptoms warranting imaging

### Stress echo (CPT 93351)
- Auth required. 3-day turnaround.
- Strict symptom documentation required.

### Left heart cath (CPT 93458)
- Auth required. 5-day turnaround.
- Requires noninvasive testing attempted or documented.
- Medical therapy trial duration ≥ 6 weeks ([MED-002](../criteria/MED-002.md)) frequently cited.

### TAVR (CPT 33361)
- Auth required. 7-day turnaround.
- Heart Team evaluation ([HT-001](../criteria/HT-001.md)) mandatory.
- STS-PROM score must be documented.

## Operational gotchas

- **Ordering physician must be in-network.** Out-of-network ordering triggers automatic denial even if service is covered.
- **Authorization voids if patient coverage lapses** between approval and service date. Re-verify eligibility within 48 hours of service.
- **All PA requests require ICD-10 primary diagnosis code** AND at least one documented symptom or clinical finding supporting medical necessity.

## Historical performance (seeded from public data + industry reports)

| CPT | First-pass approval | P2P success | Appeal win | Avg decision (days) |
|---|---|---|---|---|
| 78492 | 71% | 84% | 68% | 4.2 |
| 78452 | 78% | 81% | 72% | 3.7 |
| 93351 | 82% | — | 65% | 3.0 |
| 93458 | 69% | 77% | 61% | 5.2 |
| 33361 | 73% | 85% | 74% | 7.1 |
| J0595 | 61% | 72% | 79% | — |

## Submission channels

- Primary: Availity portal
- No EDI 278 configured for CardioAuth yet (future work)

## Source documents

- UHC Commercial Medical Policy — CPB 0337 (cardiology coverage)
- UHC Commercial Provider Manual 2025
- UHC PA Requirement Guide 2025

## Notes

UHC revises cardiology CPBs roughly quarterly. Policy freshness >90 days
triggers a warning; >365 days triggers stale_critical. Re-ingest any
revised CPB via `/api/rag/ingest` when released.

Last updated: 2026-04-14
