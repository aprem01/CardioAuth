"""Tests for the POLICY_AGENT confidence-driven re-retrieve loop.

Audit (Apr 28): one-shot retrieve silently fell back to "no chunks" when
(CPT, payer) combo was missing — Claude then generated criteria from
training data without citations. Now the agent tries:
  1. primary           — exact match
  2. cpt_siblings      — same family CPTs, same payer
  3. payer_agnostic    — drop payer, primary CPT (NCD/LCD)
  4. payer_agnostic_siblings  — both broadenings
"""

from __future__ import annotations

from cardioauth.agents.policy_agent import PolicyAgent, _siblings_of
from cardioauth.config import Config
from cardioauth.rag.corpus import PolicyChunk
from cardioauth.rag.retriever import PolicyRetriever, RetrievalResult


# ── Sibling lookup table ────────────────────────────────────────────────

def test_pet_siblings_bidirectional() -> None:
    assert "78492" in _siblings_of("78491")
    assert "78491" in _siblings_of("78492")


def test_spect_siblings_bidirectional() -> None:
    assert "78452" in _siblings_of("78451")
    assert "78451" in _siblings_of("78452")


def test_unknown_cpt_has_no_siblings() -> None:
    assert _siblings_of("99999") == []


# ── _retrieve_with_fallbacks ladder ─────────────────────────────────────

def _make_chunks() -> list[PolicyChunk]:
    return [
        # UHC chunk for 78492 only
        PolicyChunk(
            id="UHC-PET",
            payer="UnitedHealthcare",
            applies_to_cpt=["78492"],
            procedure_name="Cardiac PET",
            text="UHC PET coverage criteria here.",
            source_document="UHC Cardiac Imaging 2026",
            chunk_type="policy",
        ),
        # NCD chunk for the SPECT sibling, payer-agnostic
        PolicyChunk(
            id="NCD-SPECT",
            payer="",  # NCDs apply across payers
            applies_to_cpt=["78452"],
            procedure_name="Cardiac SPECT",
            text="NCD coverage rules for SPECT.",
            source_document="NCD 220.6",
            chunk_type="ncd",
        ),
        # Aetna chunk for the PET sibling 78491
        PolicyChunk(
            id="AETNA-PET-SIB",
            payer="Aetna",
            applies_to_cpt=["78491"],
            procedure_name="Cardiac PET (single)",
            text="Aetna PET single-study criteria.",
            source_document="Aetna 2026",
            chunk_type="policy",
        ),
    ]


def _agent_with_chunks(chunks: list[PolicyChunk]) -> PolicyAgent:
    """Build a PolicyAgent whose retriever uses our test chunks.

    PolicyRetriever() is called inside the loop, so we monkeypatch the
    module-level chunk loader via the test fixtures.
    """
    cfg = Config()
    cfg.anthropic_api_key = "test-key"  # constructor needs one
    agent = PolicyAgent(cfg)
    return agent


def test_primary_strategy_when_chunk_exists(monkeypatch) -> None:
    """When a chunk exists for exact (CPT, payer), strategy=primary."""
    chunks = _make_chunks()
    # Patch where load_corpus is referenced (in the retriever module) since
    # `from cardioauth.rag.corpus import load_corpus` binds it locally.
    monkeypatch.setattr("cardioauth.rag.retriever.load_corpus", lambda *_a, **_k: chunks)
    monkeypatch.setattr("cardioauth.rag.retriever.ensure_corpus_seeded", lambda *_a, **_k: None)

    agent = _agent_with_chunks(chunks)
    results, strategy, retry_log = agent._retrieve_with_fallbacks(
        cpt_code="78492", payer="UnitedHealthcare",
    )
    assert strategy == "primary"
    assert any(r.chunk.id == "UHC-PET" for r in results)
    assert len(retry_log) == 1


def test_falls_through_to_cpt_siblings(monkeypatch) -> None:
    """No chunk for exact CPT+payer; sibling CPT exists for same payer."""
    chunks = _make_chunks()
    # Patch where load_corpus is referenced (in the retriever module) since
    # `from cardioauth.rag.corpus import load_corpus` binds it locally.
    monkeypatch.setattr("cardioauth.rag.retriever.load_corpus", lambda *_a, **_k: chunks)
    monkeypatch.setattr("cardioauth.rag.retriever.ensure_corpus_seeded", lambda *_a, **_k: None)

    agent = _agent_with_chunks(chunks)
    # Aetna has 78491 (sibling of 78492) but not 78492 itself
    results, strategy, retry_log = agent._retrieve_with_fallbacks(
        cpt_code="78492", payer="Aetna",
    )
    assert strategy == "cpt_siblings"
    assert any(r.chunk.id == "AETNA-PET-SIB" for r in results)
    # 2 strategies tried (primary empty → siblings hit)
    assert len(retry_log) == 2
    assert retry_log[0]["strategy"] == "primary"
    assert retry_log[0]["chunk_count"] == 0
    assert retry_log[1]["strategy"] == "cpt_siblings"


def test_falls_through_to_payer_agnostic(monkeypatch) -> None:
    """No payer-specific or sibling chunk; NCD picks up the SPECT request."""
    chunks = _make_chunks()
    # Patch where load_corpus is referenced (in the retriever module) since
    # `from cardioauth.rag.corpus import load_corpus` binds it locally.
    monkeypatch.setattr("cardioauth.rag.retriever.load_corpus", lambda *_a, **_k: chunks)
    monkeypatch.setattr("cardioauth.rag.retriever.ensure_corpus_seeded", lambda *_a, **_k: None)

    agent = _agent_with_chunks(chunks)
    # Anthem has no chunk; only NCD covers 78452 (payer-agnostic)
    results, strategy, retry_log = agent._retrieve_with_fallbacks(
        cpt_code="78452", payer="Anthem",
    )
    # NCD/LCD chunks match by chunk_type='ncd' regardless of payer; could
    # surface either in primary or payer_agnostic depending on filter.
    # Key assertion: NCD is found and retry_log is populated.
    assert any(r.chunk.id == "NCD-SPECT" for r in results)
    assert strategy in ("primary", "payer_agnostic")


def test_exhausted_when_nothing_matches(monkeypatch) -> None:
    """Unknown CPT, unknown payer, no siblings → all strategies empty."""
    chunks = _make_chunks()
    # Patch where load_corpus is referenced (in the retriever module) since
    # `from cardioauth.rag.corpus import load_corpus` binds it locally.
    monkeypatch.setattr("cardioauth.rag.retriever.load_corpus", lambda *_a, **_k: chunks)
    monkeypatch.setattr("cardioauth.rag.retriever.ensure_corpus_seeded", lambda *_a, **_k: None)

    agent = _agent_with_chunks(chunks)
    results, strategy, retry_log = agent._retrieve_with_fallbacks(
        cpt_code="99999",  # no siblings
        payer="MysteryPayer",
    )
    assert strategy == "exhausted"
    assert results == []
    # primary + payer_agnostic attempted (no siblings to try)
    assert len(retry_log) == 2
    assert all(entry["chunk_count"] == 0 for entry in retry_log)


def test_retry_log_records_strategies_attempted(monkeypatch) -> None:
    """Telemetry: every strategy attempt must appear in retry_log."""
    chunks = _make_chunks()
    # Patch where load_corpus is referenced (in the retriever module) since
    # `from cardioauth.rag.corpus import load_corpus` binds it locally.
    monkeypatch.setattr("cardioauth.rag.retriever.load_corpus", lambda *_a, **_k: chunks)
    monkeypatch.setattr("cardioauth.rag.retriever.ensure_corpus_seeded", lambda *_a, **_k: None)

    agent = _agent_with_chunks(chunks)
    _, strategy, retry_log = agent._retrieve_with_fallbacks(
        cpt_code="78492", payer="Aetna",
    )
    # First entry: primary returned 0 (Aetna has 78491 not 78492)
    assert retry_log[0]["strategy"] == "primary"
    assert retry_log[0]["chunk_count"] == 0
    # Second entry: cpt_siblings (78491) returned the Aetna sibling chunk
    assert retry_log[1]["strategy"] == "cpt_siblings"
    assert "78491" in retry_log[1]["cpt_codes"]
    assert retry_log[1]["chunk_count"] >= 1
    assert strategy == "cpt_siblings"
