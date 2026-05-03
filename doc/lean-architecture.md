# Lean Hybrid State Machine

The lean-hybrid path is the production architecture for prior-auth
inference at CardioAuth. It replaces the staged 4-LLM-call pipeline
(`CHART_AGENT → POLICY_AGENT → UnifiedReasoner → Reviewer`) with one
structured-output LLM call surrounded by deterministic guardrails.

**Why:** the staged pipeline was right in spirit but over-segmented.
At ~5 cases (Peter's May rerun sample), the architecture choice didn't
matter. At ~80+ cases per practice per week (real production), the
Mount Sinai Nature 2026 paper showed:

- Multi-agent (staged) accuracy stays at 65% under load
- Single-agent (one giant prompt) accuracy collapses to 16%
- A *hybrid* (single LLM call + deterministic guardrails + independent
  verification) hits 90%+ at the same load

The lean hybrid is that hybrid.

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│ STATE 1: PRE-PASS (deterministic, ~10ms, $0)           │
│ • note_essentials regex backstop (6 fields)            │
│ • CPT validation against canonical registry            │
│ • Payer form schema lookup                             │
│ • Policy retrieval (RAG: payer + CPT + top-k)          │
│ • Taxonomy filter (only criteria where                 │
│   applies_to ⊇ {request_cpt})                          │
└────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────┐
│ STATE 2: UNIFIED CALL (1 LLM call, ~6-12s, ~$0.13)     │
│ Input: note + policy chunks + form schema +            │
│        taxonomy slice                                  │
│ Mode:  Anthropic tool-use with flattened JSON schema   │
│        (no $refs — Anthropic's validator chokes on     │
│        nested $defs)                                   │
│ Output (typed Pydantic model):                         │
│   - chart_data (essentials echoed; clinical facts)     │
│   - cpt_resolution (with rationale if mismatched)      │
│   - criteria_evaluated[] (met/not_met/                 │
│     ambiguous/not_evaluated, evidence quotes,          │
│     confidence)                                        │
│   - approval_verdict (score, label)                    │
│   - narrative (text + cpt_referenced)                  │
│   - form_field_values (per PayerForm key)              │
│   - documentation_quality (4-tier note format,         │
│     extraction warnings, overall_extraction_           │
│     confidence)                                        │
│ Validation: schema-validated by Anthropic AND          │
│   re-validated by Pydantic at the boundary.            │
│   Failure → retry with the validation errors as        │
│   feedback prompt.                                     │
└────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────┐
│ STATE 3: SAFETY VERIFY (deterministic, ~50ms, $0)      │
│ • Independent regex re-extraction of atomic clinical   │
│   facts (8 extractors today: LBBB, RBBB, paced rhythm, │
│   inability_to_exercise, ...)                          │
│ • Cross-checks LLM's criteria_evaluated against the    │
│   re-extracted facts                                   │
│ • Flags `safety_reasoner_missed_signal` and            │
│   `safety_note_chart_cpt_mismatch` findings            │
│                                                        │
│ The aviation principle: a single LLM cannot be its own │
│ second witness. State 3 is the second witness.        │
└────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────┐
│ STATE 4: COHERENCE + GATE (deterministic, ~10ms, $0)   │
│ • Essentials check (regex pre-pass overlay or LLM-     │
│   emitted)                                             │
│ • CPT alignment (note ↔ form ↔ narrative)              │
│ • Criterion-CPT applicability (e.g. PET-only criteria  │
│   on a SPECT case = high finding)                     │
│ • Block on missing essentials                          │
│ • Hold on high-severity findings, ambiguous criteria, │
│   LOW/INSUFFICIENT/DO_NOT_SUBMIT label, or LLM-        │
│   recommended physician review                         │
│ • Otherwise transmit                                   │
└────────────────────────────────────────────────────────┘
                       ↓
┌────────────────────────────────────────────────────────┐
│ STATE 5: PROVENANCE + FREEZE (deterministic, ~20ms, $0)│
│ • Emit FHIR R4 Provenance (CMS-0057-F audit trail)     │
│   - Three FHIR agent roles: author (pipeline),        │
│     performer (LLM), enterer (operator)                │
│   - SHA-256 signature over canonical decision payload  │
│   - Stable provenance.id (idempotent on replay)        │
│ • Freeze: write LeanRunResult + Provenance to durable  │
│   archive at $CARDIOAUTH_ARCHIVE_DIR                   │
└────────────────────────────────────────────────────────┘
```

Total: ~6-12 seconds, ~$0.13 per case (Opus); ~$0.02 (Sonnet via
`LEAN_STATE2_MODEL=claude-sonnet-4-6`).

---

## Modules

| Module | Role |
|---|---|
| `cardioauth/lean_schema.py` | Pydantic schema for State 2 + flattening for tool-use |
| `cardioauth/lean_prompt.py` | System prompt + user prompt builder + retry-with-error-feedback |
| `cardioauth/lean_pipeline.py` | State machine orchestrator (`run_lean_pipeline`) |
| `cardioauth/lean_provenance.py` | State 5 (FHIR Provenance + freeze) |
| `cardioauth/lean_ab_harness.py` | Side-by-side comparison harness |
| `cardioauth/lean_taxonomy_generator.py` | Generator 1 — taxonomy from policy PDFs |
| `cardioauth/lean_form_generator.py` | Generator 2 — payer form from PDF |
| `cardioauth/lean_safety_extractor_generator.py` | Generator 3 — safety_verifier extractors |
| `cardioauth/note_essentials.py` | State 1 regex backstop (deterministic) |
| `cardioauth/safety_verifier.py` | State 3 independent re-extraction |
| `cardioauth/verification.py` | Existing 8-checker pipeline (still used by current path) |
| `cardioauth/cpt_resolver.py` | CPT canonical resolver (deterministic) |
| `cardioauth/payer_forms.py` | PayerForm schemas |

---

## Endpoints

### Inference
| Endpoint | Engine | Input |
|---|---|---|
| `POST /api/demo/end-to-end` | Current (multi-stage) | Demo / paste / —  |
| `POST /api/demo/end-to-end-pdf` | Current (multi-stage) | PDF |
| `POST /api/demo/end-to-end-lean` | Lean | Paste |
| `POST /api/demo/end-to-end-lean-pdf` | Lean | PDF |
| `POST /api/demo/end-to-end-ab` | Both, parallel | Paste |
| `POST /api/demo/end-to-end-ab-pdf` | Both, parallel | PDF |

### Generators
| Endpoint | Input | Output |
|---|---|---|
| `POST /api/generators/taxonomy` | payer + cpts + policy_text | TaxonomyGenResult + `python_source` |
| `POST /api/generators/payer-form` | payer + form_pdf_text | FormGenResult + `python_source` |
| `POST /api/generators/safety-extractor` | criterion_codes + def + samples | SafetyExtGenResult + `python_source` |

UI for generators: `https://<host>/#generators`

---

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | LLM access |
| `LEAN_STATE2_MODEL` | `cfg.model` | Per-stage model override (try `claude-sonnet-4-6` for ~5x speedup, ~5x cost reduction) |
| `CARDIOAUTH_LEAN_FREE_FORM` | unset | Set to `1` to opt out of tool-use mode (escape hatch) |
| `CARDIOAUTH_ARCHIVE_DIR` | `/tmp/cardioauth-archive` | Where to write frozen runs |

---

## Why this scales (per Peter's "many procedures, many payers" critique)

The architecture handles scale; the **content** (taxonomy, forms,
safety extractors) is what grows linearly with each new procedure ×
payer pair. The three agentic generators turn that growth from
manual curation into clinician *review*:

- **Generator 1**: payer policy PDF → CRITERION_TAXONOMY entries
  (with verbatim policy quotes). 4-6 months → ~30 minutes of review
  per procedure family.
- **Generator 2**: blank PA-form PDF → PayerForm definition.
  Same ratio.
- **Generator 3**: criterion + samples → safety_verifier extractor.
  Phase 5 measures empirical recall + FP rate against the supplied
  samples, so the reviewer sees the regression evidence inline.

Each generator follows the same 5-phase recipe (analyse → optimise →
design → generate → report) with deterministic and LLM phases
interleaved. Output is reviewed before merging — the generator
drafts, humans approve.

---

## Tests

867+ tests covering:
- Schema invariants (cross-field validators)
- Each pipeline stage in isolation
- End-to-end with fake LLMs (deterministic, fast)
- Failure modes (Anthropic spend limit cascade)
- A/B harness metrics
- FHIR Provenance shape + signature determinism
- Each generator's 5 phases + Python-source emission

Run `pytest tests/ -q`.

---

## References

- [Mount Sinai 2026 — Multi-agent vs single-agent at clinical scale (Nature npj Health Systems)](https://www.nature.com/articles/s44401-026-00077-0)
- [CMS-0057-F final rule (effective Jan 1, 2026)](https://www.cms.gov/cms-interoperability-and-prior-authorization-final-rule-cms-0057-f)
- [FHIR R4 Provenance](https://hl7.org/fhir/R4/provenance.html)
- [Vellum — 2026 Agentic Workflows Best Practices](https://www.vellum.ai/blog/agentic-workflows-emerging-architectures-and-design-patterns)
- [Cohere Health — Production analog (90% automation, agentic + rules)](https://intuitionlabs.ai/articles/cohere-health-ai-prior-authorization)
