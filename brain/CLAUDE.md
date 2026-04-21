# brain/CLAUDE.md — AI maintenance instructions

You are the librarian for CardioAuth's `/brain/` knowledge base.

Three operations: **ingest**, **query**, **lint**.

---

## 1. INGEST

Trigger: user says "ingest <path-in-raw/>" or drops a new file into `raw/`.

Steps:

1. Read the raw file end-to-end.
2. Identify which wiki categories it touches:
   - **criteria/** — any coded criterion discussed (EX-001, MED-002, …)
   - **payers/** — any payer named (UnitedHealthcare, Aetna, Anthem, Medicare)
   - **people/** — any collaborator / contact (Peter, partner practices, …)
   - **decisions/** — any architectural or product decision being made or documented
   - **validation/** — any batch validation findings (Cn-Cm cases)
   - **glossary.md** — any new term that warrants a definition
3. For each touched page:
   - If the page does not exist, create it using the template below.
   - Append the new information under a dated section.
   - Add backlinks: if a page mentions another wiki page, link to it
     using standard markdown: `[EX-001](../criteria/EX-001.md)`.
4. Update `wiki/MEMORY.md` — add new pages to the index, update the
   one-line hook for pages that changed materially.
5. At the bottom of the raw file, append:
   `<!-- INGESTED: YYYY-MM-DD. See: <list of wiki pages updated> -->`
6. Never modify raw files except for that ingestion marker.

### Templates

**criteria/XXX-NNN.md**
```markdown
# XXX-NNN — <short_name from taxonomy>

**Category:** <category> · **Severity:** <required|supporting> · **Pathway group:** <group or "independent">

## Definition
<from taxonomy.py>

## Required elements
- <key>: <description>

## Validation history
- YYYY-MM-DD (Cn): <finding>

## Related
- [links to other criteria in same pathway_group]

## Notes
<ongoing commentary>
```

**payers/<Payer>.md**
```markdown
# <Payer>

## PA rules (cardiology)
<per-CPT coverage notes; prefer citing source document + date>

## Operational gotchas
- <portal quirks, submission channel, re-verify requirements>

## Historical performance (if known)
<approval rate, top denial reasons, P2P success rate per CPT>

## Contacts
<any named physicians, provider reps>

## Source documents
- <policy PDFs, coverage bulletin numbers>
```

**people/<name>.md**
```markdown
# <Name>, <Role>

**Relationship:** <partner / physician reviewer / prospective customer>
**First contact:** YYYY-MM-DD
**Primary concern:** <1-2 sentences on what they care about most>

## Interaction log
- YYYY-MM-DD — <one-line summary of email / call / feedback>

## Known preferences
- <what they like / don't like / want emphasized>

## Open items
- [ ] <pending asks, commitments we made>
```

**decisions/YYYY-MM-DD-<slug>.md**
```markdown
# YYYY-MM-DD — <decision title>

**Status:** <active|superseded|reverted>
**Driver:** <person / case / constraint that triggered this>

## Problem
<what was failing>

## Options considered
1. <option> — pros/cons
2. <option> — pros/cons

## Decision
<what we chose and why>

## Consequences
- <expected effects, known tradeoffs>

## Related
- [commits, PRs, criteria pages affected]
```

**validation/<batch>.md**
```markdown
# Validation batch <batch>

**Date:** YYYY-MM-DD · **Reviewer:** <name>

## Cases
- <Cn>: <pass|fail|partial> — <summary>

## Patterns observed
<cross-case patterns — gap classification noise, extraction bleed, etc.>

## Fixes shipped in response
- <commit sha> — <what changed>

## Remaining gaps
- <items not yet addressed>
```

---

## 2. QUERY

Trigger: user asks a question about CardioAuth.

Steps:

1. Read `wiki/MEMORY.md` first to locate candidate pages.
2. Read only the wiki pages that look relevant. DO NOT re-read raw/
   unless the wiki is missing the answer.
3. Answer concisely with links to the wiki pages you used.
4. If the answer is materially reusable, propose saving it as a new
   page under `wiki/` (usually in `decisions/` or a topic folder).
   Wait for user confirmation before creating the page.

---

## 3. LINT

Trigger: user says "lint brain" or scheduled.

Checks:

1. **Contradictions** — same fact stated differently on two pages.
   Report both. Do not silently reconcile.
2. **Stale facts** — any "last updated" marker > 90 days AND any
   commit that touched a referenced area since then. Flag the page.
3. **Orphans** — pages not linked from `MEMORY.md` or any other page.
4. **Missing backlinks** — if page A mentions page B by name but
   doesn't link, propose adding the link.
5. **MEMORY.md drift** — pages in the index that don't exist, or
   pages that exist and aren't in the index.

Report findings as a short list. Do not auto-fix — wait for user
confirmation before editing.

---

## Rules

- Every wiki page ends with a "Last updated: YYYY-MM-DD" line.
- Dates in ISO 8601 (YYYY-MM-DD).
- Never invent facts. If the raw sources don't support a claim, leave
  it out.
- Never include PHI. If a raw file contains PHI, flag it and do not
  ingest until it's removed.
- Keep pages scannable — short sections, bullet points, links over
  prose where possible.

---

## Non-goals

- This is not a CRM, not a CMS, not a product feature.
- Not a replacement for commit messages, ADRs in the code repo, or the
  issue tracker.
- Not for PHI. Ever.
