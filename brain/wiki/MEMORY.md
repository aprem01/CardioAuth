# CardioAuth wiki — index

Every page listed here with a one-line hook. Don't write content here —
link to the page and summarize in one line.

## People
- [Peter](people/peter.md) — cardiology partner running validation cases; detail-oriented, payer-workflow-savvy

## Payers
- [UnitedHealthcare](payers/UnitedHealthcare.md) — CPB 0337 governs cardiology; Availity portal; 71% first-pass approval on 78492
- [Aetna](payers/Aetna.md) — advanced imaging routed through eviCore

## Criteria (most-discussed)
- [EX-001](criteria/EX-001.md) — specific functional limitation preventing exercise; pathway_group=pharm_stress_justification
- [MED-002](criteria/MED-002.md) — medical therapy duration ≥ 6 weeks; required_elements enforced
- [SX-001](criteria/SX-001.md) — new/worsening symptoms vs baseline; biggest SX concern per Peter
- [SX-002](criteria/SX-002.md) — specific symptom timeline (onset/frequency/progression)
- [NDX-001](criteria/NDX-001.md) — non-diagnostic prior stress test; pathway_group=prior_testing_nondiagnostic

## Decisions
- [2026-04-13 — Element completeness](decisions/2026-04-13-element-completeness.md) — required_elements structural enforcement
- [2026-04-14 — ChartData v2](decisions/2026-04-14-chartdata-v2.md) — 8 explicit buckets to stop schema bleed
- [2026-04-14 — Pathway groups](decisions/2026-04-14-pathway-groups.md) — distinguish blocking gaps from alternatives not used
- [2026-04-14 — Self-consistency ensemble](decisions/2026-04-14-ensemble.md) — 3-run majority vote with agreement score

## Validation batches
- [C1–C5](validation/c1-c5.md) — first validation pass; drove required_elements + ChartAgent prompt overhaul
- [C10–C13](validation/c10-c13.md) — drove pathway_group + narrative constraint + lab safety

## Glossary
- [Glossary](glossary.md)

Last updated: 2026-04-14
