# brain/raw/

Drop zone for unprocessed inputs. Paste emails, meeting notes, commit
summaries, payer policy updates — anything you want the AI to convert
into structured wiki pages.

## Rules

- Name files `<source>-<YYYY-MM-DD>.md` when possible (e.g., `peter-2026-04-13.md`).
- **Never drop PHI here.** No patient names, MRNs, DOBs, or chart contents.
- Raw files are ingested once — do not edit them after ingestion.
- After ingestion, the AI appends an `<!-- INGESTED: ... -->` marker at the end.

## Ingestion

Run Claude Code with the brain ruleset (see `../CLAUDE.md`):

```
claude-code "ingest brain/raw/<file>.md per brain/CLAUDE.md"
```

The AI will:
1. Read the raw file
2. Create or update wiki pages
3. Add backlinks
4. Update MEMORY.md
5. Mark the raw file as ingested
