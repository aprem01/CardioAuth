# Glossary

PA / cardiology / CardioAuth-internal terminology.

## Prior authorization

- **PA** — Prior authorization. Payer approval required before a service is rendered and covered.
- **Peer-to-peer (P2P)** — Direct physician-to-physician review after a denial. Often overturns denials when the original reviewer was non-specialist.
- **Appeal** — Formal written request to reconsider a denial. Usually with supporting documentation.
- **CPT** — Current Procedural Terminology code. Identifies the procedure being requested.
- **ICD-10** — Diagnosis code. Pairs with CPT to establish medical necessity.
- **CPB** — Clinical Policy Bulletin. Aetna's published coverage policy document.
- **LCD** — Local Coverage Determination. Medicare's regional coverage policy.
- **NCD** — National Coverage Determination. Medicare's national coverage policy.
- **MAC** — Medicare Administrative Contractor. Administers Medicare by region.
- **ABN** — Advance Beneficiary Notice. Medicare form signed when a service may not meet coverage.
- **EDI 278** — X12 electronic data interchange format for PA requests. Standardized but adoption-limited.

## Cardiology

- **CAD** — Coronary artery disease.
- **CCS class** — Canadian Cardiovascular Society angina classification (I–IV).
- **NYHA class** — New York Heart Association heart failure classification (I–IV).
- **EHRA class** — European Heart Rhythm Association AF symptom severity (I–IV).
- **AUC** — Appropriate Use Criteria (ACC). Scored 1–9: 1-3 rarely appropriate, 4-6 may be appropriate, 7-9 appropriate.
- **LVEF** — Left ventricular ejection fraction. ≤40% = reduced (HFrEF); 41–49% = mildly reduced; ≥50% = preserved.
- **MPHR** — Maximum predicted heart rate. Used to judge stress test adequacy (≥85% = maximal).
- **GDMT** — Guideline-directed medical therapy.
- **ETT** — Exercise treadmill test.
- **SPECT** — Single-photon emission CT (nuclear stress imaging).
- **PET** — Positron emission tomography (advanced nuclear stress / viability imaging).
- **TAVR / SAVR** — Transcatheter / surgical aortic valve replacement.
- **STS-PROM** — Society of Thoracic Surgeons Predicted Risk of Mortality score.

## CardioAuth internal

- **Pathway group** — Set of criteria that are alternatives to each other. Case qualifies if ANY ONE is met. See [decisions/2026-04-14-pathway-groups.md](decisions/2026-04-14-pathway-groups.md).
- **Blocking gap** — Required criterion not met AND no alternative pathway satisfied. Real deficiency.
- **Alternative not used** — Unmet criterion whose pathway group has a satisfied member. NOT a gap.
- **Required element** — An atomic fact that must be documented for a criterion to be marked met. Any missing element forces `not_met`. See [decisions/2026-04-13-element-completeness.md](decisions/2026-04-13-element-completeness.md).
- **CPT gating** — The requirement that every applicable criterion for a CPT must be evaluated. Prevents silent drops.
- **Source anchor** — Verbatim quote from source document supporting a structured field (e.g., a lab row). Safety-critical for labs.
- **Headline summary** — Top 1–3 reasons case is strong or weak, ≤12 words each. Rendered at top of review.
- **Self-consistency ensemble** — N reasoner runs with mild temperature, majority vote per criterion, agreement score surfaces noise.

Last updated: 2026-04-14
