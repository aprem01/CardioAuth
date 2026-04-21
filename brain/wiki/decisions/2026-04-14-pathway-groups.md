# 2026-04-14 — Pathway groups (blocking vs alternative-not-used)

**Status:** active
**Driver:** Peter C10-C13 — biggest finding across the batch

## Problem

Reasoner was flagging every `not_met` criterion as a "gap." But some unmet criteria are just alternative pathways the case didn't need. Example: pharmacologic stress can be justified by ANY of:

- EX-001 (exercise limitation)
- ECG-001 (LBBB)
- ECG-002 (paced rhythm)
- ECG-003 (WPW)
- ECG-004 (LVH with strain)

If EX-001 is met, the case qualifies. But the other four showing as gaps made Peter's cases look much weaker than they were — creating noise for the physician and eroding trust in the output.

## Decision

Add `pathway_group: str` to `Criterion`. Criteria sharing a pathway_group are alternatives — the group is satisfied if ANY member is met. Added `classify_gaps()` helper that partitions `not_met` criteria into three buckets:

- **blocking** — required criterion AND no pathway alternative is met → real gap
- **alternative_not_used** — pathway alternative IS met → NOT a gap, just alternative not needed
- **supporting_unmet** — supporting severity → reduces score, non-blocking

Two pathway groups seeded:
- `pharm_stress_justification` — EX-001, ECG-001..004
- `prior_testing_nondiagnostic` — NDX-001..004

UI restructured: "Blocking deficiencies" is a prominent red card. "Alternative pathways not used" is collapsed and labeled explicitly as NOT gaps. "Supporting criteria not met" is collapsed separately.

## Consequences

- Peter's case noise problem should be eliminated for the two seeded groups.
- Adding more pathway groups requires domain analysis — which criteria are genuinely alternatives vs. independent requirements? This must be done carefully; over-grouping would let weak cases skate through.
- Narrative generator must also respect pathway groups — see [2026-04-14-narrative-constraint.md](2026-04-14-narrative-constraint.md).

## Related

- Commit `982af0d`
- [validation/c10-c13.md](../validation/c10-c13.md)
- [criteria/EX-001.md](../criteria/EX-001.md)
- [criteria/NDX-001.md](../criteria/NDX-001.md)

Last updated: 2026-04-14
