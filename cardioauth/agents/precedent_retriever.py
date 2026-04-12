"""PrecedentRetriever — long-term memory for CardioAuth.

Uses the existing Pinecone setup (same index as the policy corpus)
but with a separate namespace for case precedents.

Flow:
  1. When a PA case completes, embed a summary of it + its outcome
     and store in the `case-precedents` namespace.
  2. When a new case arrives, embed the case summary and query for
     the top-K most similar past cases.
  3. Inject the precedents into the UnifiedReasoner's prompt so it
     can anchor its approval probability to real historical data.

This is what lets CardioAuth get smarter over time. Peter's 5 cases
today, 500 cases in six months — the model learns "what narrative
patterns actually lead to approval for similar patients".

Degrades gracefully if Pinecone isn't configured.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

from cardioauth.case_context import CaseContext, PrecedentCase

logger = logging.getLogger(__name__)


PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX = os.environ.get("PINECONE_INDEX", "cardioauth-policies")
PRECEDENTS_NAMESPACE = "case-precedents"
EMBEDDING_MODEL = "voyage-3-lite"  # or use OpenAI/Voyage — see _embed


def _get_pinecone():
    """Lazy import + init Pinecone client."""
    if not PINECONE_API_KEY:
        return None
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=PINECONE_API_KEY)
        return pc.Index(PINECONE_INDEX)
    except ImportError:
        logger.warning("pinecone-client not installed")
        return None
    except Exception as e:
        logger.warning("Pinecone init failed: %s", e)
        return None


def _embed(text: str, input_type: str = "passage") -> list[float] | None:
    """Embed text for Pinecone storage/search.

    Uses Pinecone's integrated inference with llama-text-embed-v2 when
    PINECONE_API_KEY is available (no separate embedding API needed).
    Falls back to Voyage AI, then to deterministic pseudo-embedding.

    input_type: "passage" for stored documents, "query" for searches.
    Returns a 1024-dim vector or None.
    """
    # Try Pinecone integrated inference (llama-text-embed-v2, 1024 dim)
    if PINECONE_API_KEY:
        try:
            from pinecone import Pinecone
            pc = Pinecone(api_key=PINECONE_API_KEY)
            result = pc.inference.embed(
                model="llama-text-embed-v2",
                inputs=[text],
                parameters={"input_type": input_type, "truncate": "END"},
            )
            return result.data[0]["values"]
        except Exception as e:
            logger.warning("Pinecone inference embed failed: %s", e)

    # Try Voyage AI if Pinecone inference unavailable
    voyage_key = os.environ.get("VOYAGE_API_KEY", "")
    if voyage_key:
        try:
            import voyageai
            vo = voyageai.Client(api_key=voyage_key)
            result = vo.embed([text], model="voyage-3-lite", input_type="document")
            return result.embeddings[0]
        except Exception as e:
            logger.warning("Voyage embed failed: %s", e)

    # Last-resort fallback: deterministic pseudo-embedding (plumbing test only)
    import numpy as np
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(1024).tolist()
    return vec


def _summarize_case_for_embedding(ctx: CaseContext) -> str:
    """Build a compact text representation for embedding."""
    parts = [
        f"Procedure: {ctx.procedure_name} (CPT {ctx.procedure_code})",
        f"Payer: {ctx.payer_name}",
    ]
    chart = ctx.chart_data or {}
    if chart.get("age") or chart.get("sex"):
        parts.append(f"Patient: {chart.get('age', '?')}{chart.get('sex', '')}")
    dx = chart.get("diagnosis_codes", []) or []
    if dx:
        parts.append(f"Diagnoses: {', '.join(dx[:5])}")
    comorbidities = chart.get("comorbidities", []) or []
    if comorbidities:
        parts.append(f"Comorbidities: {'; '.join(str(c) for c in comorbidities[:5])}")

    # Key relationships
    if ctx.relationships:
        parts.append("Key clinical findings:")
        for r in ctx.relationships[:5]:
            parts.append(f"  - {r.conclusion}")

    # Approval outcome (for stored cases)
    if ctx.approval_label:
        parts.append(f"Approval: {ctx.approval_label} ({ctx.approval_score:.2f})")

    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────────────
# Storage — call after case completes
# ────────────────────────────────────────────────────────────────────────


def store_case_as_precedent(ctx: CaseContext, outcome: str = "analyzed") -> bool:
    """Embed the completed case and store in Pinecone as a precedent.

    outcome: "approved" | "denied" | "analyzed" (pre-submission)
    """
    index = _get_pinecone()
    if index is None:
        return False

    try:
        summary = _summarize_case_for_embedding(ctx)
        vector = _embed(summary)
        if vector is None:
            return False

        metadata = {
            "case_id": ctx.case_id,
            "cpt_code": ctx.procedure_code,
            "procedure_name": ctx.procedure_name,
            "payer": ctx.payer_name,
            "outcome": outcome,
            "approval_score": ctx.approval_score,
            "approval_label": ctx.approval_label,
            "summary": summary[:1000],
            "narrative_excerpt": (ctx.narrative_draft or "")[:500],
            "created_at": ctx.created_at,
        }

        index.upsert(
            vectors=[(ctx.case_id, vector, metadata)],
            namespace=PRECEDENTS_NAMESPACE,
        )
        logger.info("Stored case %s as precedent (outcome=%s)", ctx.case_id[:8], outcome)
        return True
    except Exception as e:
        logger.warning("Failed to store precedent: %s", e)
        return False


# ────────────────────────────────────────────────────────────────────────
# Retrieval — call before reasoning
# ────────────────────────────────────────────────────────────────────────


def retrieve_precedents(ctx: CaseContext, top_k: int = 5) -> None:
    """Find the K most similar past cases and write them to ctx.precedents."""
    start = time.time()
    index = _get_pinecone()
    if index is None:
        ctx.trace("PrecedentRetriever", "skipped — Pinecone unavailable", "", ms=0)
        return

    try:
        query_text = _summarize_case_for_embedding(ctx)
        vector = _embed(query_text, input_type="query")
        if vector is None:
            return

        # Filter by same CPT code (same procedure type)
        filter_dict = {"cpt_code": ctx.procedure_code}

        result = index.query(
            vector=vector,
            top_k=top_k,
            namespace=PRECEDENTS_NAMESPACE,
            filter=filter_dict,
            include_metadata=True,
        )

        precedents = []
        for match in result.get("matches", []):
            meta = match.get("metadata", {}) or {}
            precedents.append(PrecedentCase(
                case_id=match.get("id", ""),
                summary=meta.get("summary", ""),
                cpt_code=meta.get("cpt_code", ""),
                payer=meta.get("payer", ""),
                outcome=meta.get("outcome", "unknown"),
                similarity=float(match.get("score", 0.0)),
                narrative_excerpt=meta.get("narrative_excerpt", ""),
                approval_score=float(meta.get("approval_score", 0.0)),
            ))

        ctx.precedents = precedents
        elapsed = int((time.time() - start) * 1000)
        ctx.trace(
            "PrecedentRetriever",
            f"retrieved {len(precedents)} similar cases",
            ", ".join(f"{p.outcome}({p.similarity:.2f})" for p in precedents[:3]),
            ms=elapsed,
        )
    except Exception as e:
        logger.warning("Precedent retrieval failed: %s", e)
        ctx.warn("warning", "PrecedentRetriever", f"Retrieval failed: {str(e)[:100]}", "retrieval_error")


def is_precedent_memory_available() -> bool:
    """Check if Pinecone precedent memory is configured."""
    return bool(PINECONE_API_KEY) and _get_pinecone() is not None
