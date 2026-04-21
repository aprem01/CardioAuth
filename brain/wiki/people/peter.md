# Peter, Cardiologist / Validation Partner

**Relationship:** Clinical validation partner running case-by-case review of the CardioAuth pipeline.
**First contact:** early April 2026 (Prem to fill in exact date)
**Primary concern:** The system must produce output a cardiologist can defend to a payer and to their own judgment. Speed second, correctness first.

## Profile

- Cardiologist, hands-on reviewer of PA cases.
- Detail-oriented — notices schema bleed, narrative overreach, logical
  over-inference that casual reviewers would miss.
- Payer-workflow-savvy — understands submission mechanics, not just
  clinical correctness.
- Communicates in structured feedback: numbered findings, case-specific
  takeaways, prioritized fix list.

## Interaction log

- **2026-04-13** — First structural feedback. Identified 4 cases (MED-002, SX-001, SX-002, EX-001) where "feature present" was treated as "criterion satisfied." Drove the element-completeness fix. See [decisions/2026-04-13-element-completeness.md](../decisions/2026-04-13-element-completeness.md).
- **2026-04-14** — UX feedback. Flagged overlap between clinical relationships / gaps list / criterion matrix. Asked for split between physician view and debug view. Also asked narrative to evolve toward payer-shape (cover summary + raw note + PDF packet) vs. appeal-shape. Shipped in commit 7735022.
- **2026-04-14** — Schema bleed observation. ECG in imaging, family history in comorbidities, symptoms in comorbidities. Drove [ChartData v2](../decisions/2026-04-14-chartdata-v2.md).
- **2026-04-14** — C10–C13 batch. Six priorities: blocking vs alternative pathways, narrative stay-in-taxonomy, field typing, contradiction/recency, lab source-anchoring, top-3 reasons. See [validation/c10-c13.md](../validation/c10-c13.md). All six shipped in commit 982af0d + follow-ups in faad27c.

## Known preferences

- **Favors Claude Only over Comprehend+Claude** — less dangerous unsupported-data insertion, stays closer to source.
- Wants alternative pathways visibly separated from real blocking gaps.
- Wants narrative constrained to scored criteria; no clinical over-reach into un-coded logic.
- Wants source-anchored labs — no unsupported / future-dated values.
- Wants top 1–3 reasons summarized, not long narratives.
- Views the criterion matrix as the most useful clinical artifact.
- Does NOT want the physician to navigate multiple dashboards — values speed.
- Sees Epic integration + payer-side integration as the long-term moat.

## Open items

- [ ] Send request for 20-30 retrospective labeled cases for `/api/validation/run` → real sensitivity/specificity numbers
- [ ] Close-the-loop email after faad27c deploy — confirm fixes land on C10-C13
- [ ] Re-cut Loom with Epic-first opening (script in brain/raw, not yet recorded)
- [ ] Share before/after example on one of C1-C5 or C10-C13 after his next run

## Related

- [validation/c1-c5.md](../validation/c1-c5.md)
- [validation/c10-c13.md](../validation/c10-c13.md)
- [criteria/EX-001.md](../criteria/EX-001.md)
- [criteria/MED-002.md](../criteria/MED-002.md)

Last updated: 2026-04-14
