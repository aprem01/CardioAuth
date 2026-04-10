"""Policy chunk schema and JSONL-backed corpus storage.

Each chunk represents a section of a real payer medical policy document
(or a CMS NCD/LCD). Chunks are tagged with the CPT codes they apply to,
the payer, and the source document so retrieval can filter cleanly and
the LLM can produce real citations.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# The corpus is a JSONL file. One line = one chunk.
# In production this becomes a pgvector table or Pinecone index, but
# for the prototype the JSONL file is portable, deterministic, and
# easy to inspect/version.
DEFAULT_CORPUS_PATH = Path(
    os.environ.get(
        "POLICY_CORPUS_PATH",
        Path(__file__).parent.parent.parent / "data" / "policy_corpus.jsonl",
    )
)


@dataclass
class PolicyChunk:
    """A single retrievable section of a payer policy or guideline."""
    id: str
    payer: str
    applies_to_cpt: list[str]            # which CPT codes this chunk covers
    procedure_name: str
    text: str                            # the actual policy text
    source_document: str                 # human-readable doc title
    source_document_number: str = ""     # e.g., "2025T0501U", "NCD 220.6.1"
    section_heading: str = ""            # e.g., "Coverage Criteria"
    page: int | None = None              # page number in the source PDF
    last_updated: str = ""               # ISO date
    source_url: str = ""                 # link to the public document
    chunk_type: str = "policy"           # policy / ncd / lcd / guideline / aha_acc

    @classmethod
    def new(cls, **kwargs) -> "PolicyChunk":
        """Create a chunk with an auto-generated ID if none was supplied."""
        if "id" not in kwargs or not kwargs["id"]:
            payer_prefix = (kwargs.get("payer", "MISC") or "MISC").upper().replace(" ", "")[:6]
            cpts = "".join(kwargs.get("applies_to_cpt", []))[:8] or "GEN"
            kwargs["id"] = f"{payer_prefix}-{cpts}-{uuid.uuid4().hex[:6].upper()}"
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────── Persistence ───────────────────────────


def load_corpus(path: Path | None = None) -> list[PolicyChunk]:
    """Load all policy chunks from the JSONL corpus file."""
    p = Path(path or DEFAULT_CORPUS_PATH)
    if not p.exists():
        return []
    chunks: list[PolicyChunk] = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                chunks.append(PolicyChunk(**d))
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("Bad chunk in corpus: %s", e)
    return chunks


def save_corpus(chunks: Iterable[PolicyChunk], path: Path | None = None) -> int:
    """Write chunks to JSONL. Returns the number of chunks written."""
    p = Path(path or DEFAULT_CORPUS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(p, "w") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict()) + "\n")
            n += 1
    return n


def add_chunks(new_chunks: Iterable[PolicyChunk], path: Path | None = None) -> int:
    """Append chunks to the corpus, deduping by chunk id."""
    existing = load_corpus(path)
    by_id = {c.id: c for c in existing}
    added = 0
    for c in new_chunks:
        if c.id not in by_id:
            by_id[c.id] = c
            added += 1
    save_corpus(by_id.values(), path)
    return added


def get_corpus_stats(path: Path | None = None) -> dict:
    """Summary stats over the corpus for the Policy Library page."""
    chunks = load_corpus(path)
    payers: dict[str, int] = {}
    cpts: dict[str, int] = {}
    docs: set[str] = set()
    for c in chunks:
        payers[c.payer] = payers.get(c.payer, 0) + 1
        for cpt in c.applies_to_cpt:
            cpts[cpt] = cpts.get(cpt, 0) + 1
        if c.source_document:
            docs.add(c.source_document)
    return {
        "total_chunks": len(chunks),
        "unique_payers": len(payers),
        "unique_cpts": len(cpts),
        "unique_documents": len(docs),
        "by_payer": dict(sorted(payers.items(), key=lambda x: -x[1])),
        "by_cpt": dict(sorted(cpts.items(), key=lambda x: -x[1])),
    }


def ensure_corpus_seeded(path: Path | None = None) -> int:
    """Seed the corpus from seed_corpus.py if it's currently empty.

    Returns the number of chunks loaded after seeding.
    """
    p = Path(path or DEFAULT_CORPUS_PATH)
    if p.exists() and p.stat().st_size > 0:
        return len(load_corpus(p))
    from cardioauth.rag.seed_corpus import build_seed_corpus
    seed = build_seed_corpus()
    save_corpus(seed, p)
    logger.info("Seeded policy corpus at %s with %d chunks", p, len(seed))
    return len(seed)
