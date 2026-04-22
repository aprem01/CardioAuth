# Criterion codes reference (for validation batch labeling)

Use these codes in `gold_criterion_labels`. Each value is `"met"`,
`"not_met"`, or `"not_applicable"`.

## Symptoms (SX)

- `SX-001` — New or worsening symptoms since baseline
- `SX-002` — Specific symptom timeline (onset + frequency + progression)
- `SX-003` — Anginal equivalent or chest pain documented
- `SX-004` — Symptom burden quantified (NYHA / CCS / EHRA class)

## Prior testing non-diagnostic (NDX)

- `NDX-001` — Non-diagnostic prior stress test
- `NDX-002` — Submaximal exercise stress (<85% MPHR)
- `NDX-003` — Equivocal ST changes on prior ETT
- `NDX-004` — Technically limited prior echocardiogram

## Medical therapy (MED)

- `MED-001` — Failed maximally tolerated medical therapy
- `MED-002` — Medical therapy duration ≥ 6 weeks documented
- `MED-003` — Failed Class I or III antiarrhythmic (AF)

## Body habitus (BMI)

- `BMI-001` — BMI ≥ 35 documented
- `BMI-002` — Attenuation artifact on prior SPECT

## ECG

- `ECG-001` — LBBB
- `ECG-002` — Paced rhythm
- `ECG-003` — WPW / pre-excitation
- `ECG-004` — Severe LVH with strain

## LVEF

- `LVEF-001` — LVEF documented within 90 days
- `LVEF-002` — LVEF ≤ 40% (reduced)

## Risk / stratification (RISK)

- `RISK-001` — STS-PROM score documented (TAVR)
- `RISK-002` — CV risk factors enumerated
- `RISK-003` — Pre-test probability stratification

## Heart Team (HT)

- `HT-001` — Heart Team evaluation completed (TAVR)

## Anticoagulation (ANTI)

- `ANTI-001` — CHA₂DS₂-VASc calculated
- `ANTI-002` — Pre-procedure TEE / LAA imaging

## Imaging / frequency (IMG, FREQ)

- `IMG-001` — Coronary anatomy assessment within 12 months (TAVR)
- `IMG-002` — Pre-procedural CTA for TAVR sizing
- `FREQ-001` — No prior similar imaging within 12 months (or new symptoms)

## Exercise capacity (EX)

- `EX-001` — Specific functional limitation preventing exercise

## Guidelines + documentation (GUI, DOC)

- `GUI-001` — ACC AUC score Appropriate (7-9)
- `DOC-001` — Cardiology consultation / office note attached
