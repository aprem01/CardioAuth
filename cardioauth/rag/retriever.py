"""Policy chunk retriever — metadata filter + BM25 ranking.

For Stage 1 we deliberately keep the retrieval simple and deterministic:

  1. Metadata filter — chunks must explicitly include the requested CPT
     code in `applies_to_cpt` and either match the requested payer or
     be a CMS NCD/LCD that applies regardless of payer.

  2. BM25 ranking — among the filtered set, rank by lexical similarity
     to the query (typically the procedure name + relevant clinical terms).

This avoids any embedding dependency and stays fast and inspectable.
Embeddings can be added in Stage 2 once we have enough chunks for
semantic recall to matter.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Iterable

from cardioauth.rag.corpus import PolicyChunk, load_corpus, ensure_corpus_seeded

logger = logging.getLogger(__name__)


# ─────────────────────────── Tokenization ───────────────────────────


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


# ─────────────────────────── BM25 implementation ───────────────────────────
# Standard Robertson/Sparck-Jones BM25 in ~30 lines, no external deps.


@dataclass
class _BM25Index:
    docs: list[list[str]]
    df: dict[str, int] = field(default_factory=dict)
    avg_dl: float = 0.0
    n: int = 0

    @classmethod
    def build(cls, docs: list[list[str]]) -> "_BM25Index":
        idx = cls(docs=docs, n=len(docs))
        if not docs:
            return idx
        idx.avg_dl = sum(len(d) for d in docs) / len(docs)
        for d in docs:
            for term in set(d):
                idx.df[term] = idx.df.get(term, 0) + 1
        return idx

    def score(self, query: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
        scores: list[float] = [0.0] * self.n
        if not self.n:
            return scores
        for q in query:
            df = self.df.get(q, 0)
            if df == 0:
                continue
            idf = math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)
            for i, doc in enumerate(self.docs):
                tf = doc.count(q)
                if tf == 0:
                    continue
                dl = len(doc) or 1
                denom = tf + k1 * (1 - b + b * dl / max(self.avg_dl, 1))
                scores[i] += idf * (tf * (k1 + 1) / denom)
        return scores


# ─────────────────────────── Result type ───────────────────────────


@dataclass
class RetrievalResult:
    chunk: PolicyChunk
    score: float
    rank: int

    def to_dict(self) -> dict:
        return {
            "id": self.chunk.id,
            "rank": self.rank,
            "score": round(self.score, 4),
            "payer": self.chunk.payer,
            "procedure_name": self.chunk.procedure_name,
            "applies_to_cpt": self.chunk.applies_to_cpt,
            "text": self.chunk.text,
            "source_document": self.chunk.source_document,
            "source_document_number": self.chunk.source_document_number,
            "section_heading": self.chunk.section_heading,
            "page": self.chunk.page,
            "last_updated": self.chunk.last_updated,
            "source_url": self.chunk.source_url,
            "chunk_type": self.chunk.chunk_type,
        }


# ─────────────────────────── Retriever ───────────────────────────


class PolicyRetriever:
    def __init__(self, chunks: list[PolicyChunk] | None = None) -> None:
        if chunks is None:
            ensure_corpus_seeded()
            chunks = load_corpus()
        self.chunks = chunks

    @staticmethod
    def _matches_payer(chunk: PolicyChunk, payer: str) -> bool:
        if chunk.chunk_type in ("ncd", "lcd"):
            return True  # CMS rules apply across all payers
        if not payer:
            return True
        return chunk.payer.lower() == payer.lower()

    def filter(self, cpt_code: str, payer: str) -> list[PolicyChunk]:
        out: list[PolicyChunk] = []
        for c in self.chunks:
            if cpt_code and cpt_code not in c.applies_to_cpt:
                continue
            if not self._matches_payer(c, payer):
                continue
            out.append(c)
        return out

    def retrieve(
        self,
        cpt_code: str,
        payer: str,
        query: str = "",
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """Filter by CPT/payer, then rank by BM25 over the query."""
        filtered = self.filter(cpt_code, payer)
        if not filtered:
            return []

        if not query:
            # No ranking query — return chunks as-is, deterministic order
            return [
                RetrievalResult(chunk=c, score=1.0, rank=i + 1)
                for i, c in enumerate(filtered[:top_k])
            ]

        docs = [_tokenize(c.text + " " + c.section_heading + " " + c.procedure_name) for c in filtered]
        index = _BM25Index.build(docs)
        scores = index.score(_tokenize(query))
        ranked = sorted(zip(filtered, scores), key=lambda x: x[1], reverse=True)
        return [
            RetrievalResult(chunk=c, score=float(s), rank=i + 1)
            for i, (c, s) in enumerate(ranked[:top_k])
        ]


# ─────────────────────────── Convenience helpers ───────────────────────────


def retrieve_for_pa(
    cpt_code: str,
    payer: str,
    procedure_name: str = "",
    top_k: int = 6,
) -> list[RetrievalResult]:
    """High-level retrieval for the POLICY_AGENT.

    Builds a sensible query from the procedure name and asks for the
    top-K chunks that match the requested CPT + payer.
    """
    retriever = PolicyRetriever()
    query = procedure_name or ""
    return retriever.retrieve(cpt_code=cpt_code, payer=payer, query=query, top_k=top_k)
