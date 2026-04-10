"""Stage 1 RAG over payer medical policy documents.

Architecture:
  Layer 1 (corpus.py)    — Chunk schema, JSONL persistence, ingestion API
  Layer 2 (retriever.py) — Metadata filter by (cpt_code, payer) + BM25 ranking
  Layer 3 (seed_corpus.py) — Hand-curated, realistic policy chunks for the
                              procedures and payers we currently support
"""

from cardioauth.rag.corpus import (
    PolicyChunk,
    load_corpus,
    save_corpus,
    get_corpus_stats,
    add_chunks,
    delete_chunks,
    delete_document,
    DEFAULT_CORPUS_PATH,
)
from cardioauth.rag.retriever import (
    PolicyRetriever,
    RetrievalResult,
    retrieve_for_pa,
)
from cardioauth.rag.chunker import (
    ChunkDraft,
    chunk_document,
    chunks_from_plain_text,
)

__all__ = [
    "PolicyChunk",
    "PolicyRetriever",
    "RetrievalResult",
    "load_corpus",
    "save_corpus",
    "get_corpus_stats",
    "add_chunks",
    "delete_chunks",
    "delete_document",
    "retrieve_for_pa",
    "DEFAULT_CORPUS_PATH",
    "ChunkDraft",
    "chunk_document",
    "chunks_from_plain_text",
]
