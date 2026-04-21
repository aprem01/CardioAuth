# CardioAuth /brain/ — self-maintaining knowledge base

This is **not** product code. It does not ship. It never touches PHI.

It's an AI-maintained wiki about CardioAuth itself — decisions, people,
payers, criteria, validation findings — so the project's tacit knowledge
lives somewhere other than Prem's inbox and head.

## Layout

```
brain/
├── CLAUDE.md        ← instructions for the AI when ingesting / querying / linting
├── README.md        ← this file
├── raw/             ← drop zone. Paste emails, commit notes, call summaries.
│                      AI reads these ONCE and produces structured pages in wiki/.
└── wiki/            ← AI-maintained, human-browsable
    ├── MEMORY.md        ← index — every wiki page listed with one-line hook
    ├── criteria/        ← one page per taxonomy criterion (EX-001.md, MED-002.md, …)
    ├── payers/          ← one page per payer (UnitedHealthcare.md, Aetna.md, …)
    ├── people/          ← collaborators + contacts (peter.md, …)
    ├── decisions/       ← architectural / product decisions with dates
    ├── validation/      ← findings per validation batch (c1-c5.md, c10-c13.md, …)
    └── glossary.md      ← PA + cardiology + internal vocabulary
```

## Three operations (from CLAUDE.md)

1. **Ingest** — drop a new file in `raw/`, ask the AI to ingest.
   AI reads the raw file, creates or updates relevant wiki pages with
   backlinks, and logs the action.

2. **Query** — ask the AI a question. It reads `wiki/` (not `raw/`) to
   answer fast. If useful, it can save the response as a new analysis
   page under `wiki/`.

3. **Lint** — AI scans the wiki for contradictions, orphan pages,
   stale facts, missing cross-references.

## What to put here

- ✅ Peter's emails (pasted)
- ✅ Commit summaries and why-we-did-it rationale
- ✅ Call / meeting notes
- ✅ Payer policy change summaries
- ✅ Physician partner context
- ✅ Operational gotchas ("UHC portal requires attachment filename ≤40 chars")

## What NOT to put here

- ❌ Patient PHI (names, DOBs, MRNs, chart contents)
- ❌ Credentials, API keys, secrets
- ❌ Anything that can't be committed to a public repo

Raw content is committed by default. If you drop something sensitive,
add it to `.gitignore` with a specific path — or better, don't drop it
at all. A HIPAA-audited system does not store PHI in a git-tracked
markdown wiki.

## Using it day-to-day

Typical interaction in Claude Code (or any terminal with Claude Code installed):

```
# After Peter sends a new email
paste the email into brain/raw/peter-YYYY-MM-DD.md
claude-code "ingest brain/raw/peter-YYYY-MM-DD.md per brain/CLAUDE.md"

# When planning how to respond
claude-code "summarize Peter's concerns across the last 2 weeks from brain/wiki/"

# Before a payer-specific feature
claude-code "what do we know about UnitedHealthcare cardiology PA from brain/wiki/payers/"
```

If you never touch this folder, nothing breaks. The product works
without it. It exists to compound knowledge over time.
