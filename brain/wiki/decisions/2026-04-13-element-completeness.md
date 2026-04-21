# 2026-04-13 — Definitional completeness (required_elements)

**Status:** active
**Driver:** Peter's Apr 13 feedback across 4 cases (MED-002, SX-001, SX-002, EX-001)

## Problem

Reasoner was treating **presence of a feature** as **satisfaction of the criterion definition**. Examples:

- MED-002 marked met when meds were listed but no duration documented
- SX-001 marked met when symptoms were present but no comparison to baseline
- SX-002 marked met when timeline elements (onset/frequency/progression) were missing
- EX-001 marked met when dyspnea was noted but no explicit link to inability to exercise

Classic "keyword bag" reasoning — the LLM saw the feature mentioned and concluded the criterion was satisfied, regardless of whether the full definition was met.

## Options considered

1. **Tighten reasoner prompt with more negative examples.** Lowest effort but LLM-dependent — could drift on next prompt edit.
2. **Add structural `required_elements` to every criterion + post-hoc enforcement.** Higher upfront cost; deterministic; can't drift.
3. **Do nothing and trust the LLM.** Not serious given Peter would see regressions.

## Decision

Option 2 — structural enforcement.

Added `RequiredElement` dataclass to `Criterion`. Each criterion with a conjunctive definition carries one or more elements. The UnifiedReasoner prompt is updated to return `elements_satisfied: [{key, found, evidence_quote}]` per criterion. After the LLM responds, `_enforce_element_completeness()` walks the required elements — if any is `found=false`, status is forced to `not_met` regardless of what the LLM decided.

Populated for Peter's four cases initially, then expanded to all 31 criteria (100% coverage).

## Consequences

- Some cases that previously passed now fail — by design. Peter's explicit intent.
- Physicians see specific `missing_elements` in the gap — actionable.
- Reasoner can't drift on future prompt edits because enforcement is deterministic.
- Adding a new criterion is now more work (must enumerate elements) — that's OK.

## Related

- Commit `ab32522` — original rollout for 5 criteria
- Commit `d0bb6b5` + `982af0d` — expanded to all 31
- [criteria/EX-001.md](../criteria/EX-001.md)
- [criteria/MED-002.md](../criteria/MED-002.md)
- [criteria/SX-001.md](../criteria/SX-001.md)
- [criteria/SX-002.md](../criteria/SX-002.md)

Last updated: 2026-04-14
