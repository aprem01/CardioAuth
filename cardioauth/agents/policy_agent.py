"""POLICY_AGENT — RAG-grounded payer policy criteria generator.

Stage 1 RAG architecture:
  1. Retrieve relevant policy chunks from the corpus by (CPT, payer)
  2. Pass chunks as grounded context to Claude
  3. Require Claude to extract criteria from the chunks and cite them
  4. Return structured criteria with citation references

Apr 28: Added confidence-driven re-retrieve loop. The single-shot retrieve
silently fell back to "no chunks" when CPT/payer combo wasn't in the
corpus, which then forced Claude to generate criteria from training data
with no citations. Now: if primary retrieval is empty, try CPT siblings
(same procedure family); if still empty, drop the payer filter and pull
NCD/LCD universal rules. Retrieval strategy + retry count surface on the
returned PolicyData for telemetry.
"""

from __future__ import annotations

import json
import logging

import anthropic

from cardioauth.config import Config
from cardioauth.models.policy import PolicyData

logger = logging.getLogger(__name__)


# CPT sibling families — when retrieval misses on the exact code, broaden
# to other procedures in the same coverage family. Conservative: only
# include codes that share clinical meaning, not every payer-grouped code.
_CPT_SIBLINGS: dict[str, list[str]] = {
    # Cardiac PET (myocardial perfusion)
    "78491": ["78492"],
    "78492": ["78491"],
    # Cardiac SPECT (myocardial perfusion)
    "78451": ["78452"],
    "78452": ["78451"],
    # Cardiac MRI / CT family
    "75557": ["75561", "75565", "75571", "75572", "75573", "75574"],
    "75561": ["75557", "75565", "75571", "75572", "75573", "75574"],
    "75571": ["75572", "75573", "75574"],
    "75572": ["75571", "75573", "75574"],
    "75573": ["75571", "75572", "75574"],
    "75574": ["75571", "75572", "75573"],
    # Stress echo
    "93350": ["93351"],
    "93351": ["93350"],
    # TAVR / SAVR
    "33361": ["33362", "33363", "33364", "33365"],
}


def _siblings_of(cpt: str) -> list[str]:
    return _CPT_SIBLINGS.get(cpt or "", [])


SYSTEM_PROMPT = """\
You are POLICY_AGENT, a payer coverage policy specialist for CardioAuth.

You receive RETRIEVED POLICY CHUNKS from a corpus of real payer medical
policies, CMS National Coverage Determinations, and ACC/AHA clinical
practice guidelines. Your job is to extract the prior authorization
criteria for a specific (CPT, payer) combination, GROUNDED IN THE
RETRIEVED CHUNKS.

═══════════════════════════════════════════════════════════════════════════
GROUNDING RULES — READ CAREFULLY
═══════════════════════════════════════════════════════════════════════════

1. Every criterion you produce MUST come from one or more of the retrieved
   chunks. You may not invent criteria from your training data.
2. Each criterion must include `cited_chunk_ids` — the IDs of the specific
   chunks that support it. If a criterion appears in multiple chunks, list
   all of them.
3. If the retrieved chunks do not contain enough information for a given
   criterion area (e.g., no chunks discuss frequency limits for this
   payer), DO NOT make one up. Note the gap in `cardiologist_review_flags`
   instead.
4. The `policy_source` field must be filled from the chunk metadata
   (source_document + source_document_number) of the most authoritative
   chunk used.
5. The `policy_last_updated` field must be the most recent `last_updated`
   date among the chunks you cited.

═══════════════════════════════════════════════════════════════════════════
CRITERION-LEVEL CITATIONS
═══════════════════════════════════════════════════════════════════════════

Each criterion MUST be one object in clinical_criteria with this shape:
{
  "criterion": "Prior non-diagnostic stress test documented",
  "required": true,
  "evidence_type": "imaging",
  "acceptable_values": "Non-diagnostic ETT, equivocal SPECT, or technically limited stress echo",
  "cited_chunk_ids": ["UHC-78492-001", "UHC-78492-003"]
}

DO NOT skip the cited_chunk_ids field. It is the entire purpose of the
RAG system. A criterion without citations will be discarded.

═══════════════════════════════════════════════════════════════════════════
ALWAYS-CHECK CRITERIA (regardless of payer)
═══════════════════════════════════════════════════════════════════════════

For any cardiac imaging procedure, also include:
  - "Documentation of new or worsening symptoms since prior imaging"
    (cite the chunk that mentions frequency or new-symptom rules)
  - "Pre-test probability stratification documented"

For pharmacologic stress tests, also include:
  - "Specific functional limitation preventing exercise documented"

═══════════════════════════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════════════════════════

Return ONLY valid JSON matching this schema:
{
  "payer": "",
  "procedure": "",
  "cpt_code": "",
  "auth_required": true/false/null,
  "clinical_criteria": [
    {
      "criterion": "",
      "required": true/false,
      "evidence_type": "lab|imaging|ecg|demographic|clinical_note|score|medication",
      "acceptable_values": "",
      "cited_chunk_ids": []
    }
  ],
  "documentation_required": [],
  "submission_format": "portal|fax|clearinghouse|api|unknown",
  "typical_turnaround_days": 0,
  "common_denial_reasons": [],
  "appeal_success_factors": [],
  "policy_source": "",
  "policy_last_updated": "",
  "cardiologist_review_flags": []
}
"""


class PolicyAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    # Payer criteria for a given CPT are effectively stable within a short
    # window. Cache the PolicyData for 1 hour to avoid re-running RAG + Claude
    # on every case during a demo session or burst of validation cases.
    _CACHE_TTL_SECONDS = 3600

    def _retrieve_with_fallbacks(
        self,
        cpt_code: str,
        payer: str,
        procedure_name: str = "",
        top_k: int = 8,
    ) -> tuple[list, str, list[dict]]:
        """Retrieve chunks with progressive broadening on empty results.

        Strategy ladder (stops at the first non-empty result):
          1. primary           — exact (CPT, payer)
          2. cpt_siblings      — same payer, sibling CPTs in the same family
          3. payer_agnostic    — drop the payer filter; NCD/LCD universal
          4. payer_agnostic_siblings  — both broadenings combined

        Returns (results, strategy_label, retry_log) where retry_log is a
        list of {strategy, cpt_codes, payer, chunk_count} for telemetry.
        """
        from cardioauth.rag import retrieve_for_pa
        from cardioauth.rag.retriever import PolicyRetriever, RetrievalResult

        retry_log: list[dict] = []

        # 1. Primary
        results = retrieve_for_pa(
            cpt_code=cpt_code, payer=payer,
            procedure_name=procedure_name, top_k=top_k,
        )
        retry_log.append({
            "strategy": "primary",
            "cpt_codes": [cpt_code], "payer": payer,
            "chunk_count": len(results),
        })
        if results:
            return results, "primary", retry_log

        # 2. Sibling CPTs, same payer
        siblings = _siblings_of(cpt_code)
        if siblings:
            retriever = PolicyRetriever()
            sibling_results: list = []
            for sib in siblings:
                sibling_results.extend(
                    retriever.retrieve(cpt_code=sib, payer=payer,
                                       query=procedure_name, top_k=top_k)
                )
            # Re-rank by score, dedupe by chunk id, cap at top_k
            seen: set[str] = set()
            deduped: list = []
            for r in sorted(sibling_results, key=lambda x: x.score, reverse=True):
                if r.chunk.id in seen:
                    continue
                seen.add(r.chunk.id)
                deduped.append(r)
                if len(deduped) >= top_k:
                    break
            retry_log.append({
                "strategy": "cpt_siblings",
                "cpt_codes": siblings, "payer": payer,
                "chunk_count": len(deduped),
            })
            if deduped:
                return deduped, "cpt_siblings", retry_log

        # 3. Payer-agnostic: drop payer filter, primary CPT
        results = retrieve_for_pa(
            cpt_code=cpt_code, payer="",
            procedure_name=procedure_name, top_k=top_k,
        )
        retry_log.append({
            "strategy": "payer_agnostic",
            "cpt_codes": [cpt_code], "payer": "",
            "chunk_count": len(results),
        })
        if results:
            return results, "payer_agnostic", retry_log

        # 4. Payer-agnostic + siblings
        if siblings:
            retriever = PolicyRetriever()
            broadened: list = []
            for sib in siblings:
                broadened.extend(
                    retriever.retrieve(cpt_code=sib, payer="",
                                       query=procedure_name, top_k=top_k)
                )
            seen = set()
            deduped = []
            for r in sorted(broadened, key=lambda x: x.score, reverse=True):
                if r.chunk.id in seen:
                    continue
                seen.add(r.chunk.id)
                deduped.append(r)
                if len(deduped) >= top_k:
                    break
            retry_log.append({
                "strategy": "payer_agnostic_siblings",
                "cpt_codes": siblings, "payer": "",
                "chunk_count": len(deduped),
            })
            if deduped:
                return deduped, "payer_agnostic_siblings", retry_log

        return [], "exhausted", retry_log

    def run(
        self,
        procedure_code: str,
        payer_name: str,
        cms_context: dict | None = None,
    ) -> PolicyData:
        logger.info("POLICY_AGENT: retrieving policy chunks for procedure=%s payer=%s", procedure_code, payer_name)

        # ── Response cache check (1h TTL) ──
        cache_key = f"policy_agent:v2:{payer_name}:{procedure_code}"
        try:
            from cardioauth.persistence import get_store
            cached = get_store().cache_get(cache_key)
            if cached:
                logger.info("POLICY_AGENT: cache HIT for %s / %s", procedure_code, payer_name)
                policy = PolicyData(**cached["policy"])
                policy.__dict__["_retrieved_chunks"] = cached.get("_retrieved_chunks", [])
                policy.__dict__["_criterion_citations"] = cached.get("_criterion_citations", [])
                policy.__dict__["_payer_stats"] = cached.get("_payer_stats")
                policy.__dict__["_payer_global_rules"] = cached.get("_payer_global_rules", [])
                policy.__dict__["_freshness"] = cached.get("_freshness")
                policy.__dict__["_retrieval_strategy"] = cached.get("_retrieval_strategy", "primary")
                policy.__dict__["_retrieval_retry_log"] = cached.get("_retrieval_retry_log", [])
                policy.__dict__["_cache_hit"] = True
                return policy
        except Exception as e:
            logger.warning("POLICY_AGENT cache_get failed (continuing): %s", e)

        # ── Stage 1 RAG retrieval, with confidence-driven re-retrieve loop ──
        results, retrieval_strategy, retry_log = self._retrieve_with_fallbacks(
            cpt_code=procedure_code,
            payer=payer_name,
            procedure_name=cms_context.get("cms_ncd_title", "") if cms_context else "",
        )

        if not results:
            logger.warning(
                "POLICY_AGENT: no policy chunks retrieved for %s / %s after %d strategies — "
                "model will rely on training data",
                procedure_code, payer_name, len(retry_log),
            )
            chunks_for_prompt: list[dict] = []
        else:
            chunks_for_prompt = [r.to_dict() for r in results]
            logger.info(
                "POLICY_AGENT: retrieved %d chunks via %s strategy (%s)",
                len(results), retrieval_strategy,
                ", ".join(r.chunk.id for r in results),
            )

        cms_section = ""
        if cms_context and cms_context.get("cms_ncd_number"):
            cms_section = (
                f"\n\nCMS COVERAGE ANCHOR:\n"
                f"This procedure is governed by CMS NCD {cms_context['cms_ncd_number']} "
                f"({cms_context['cms_ncd_title']}). The retrieved chunks may "
                f"include this NCD; use it as the regulatory floor."
            )

        chunks_section = ""
        if chunks_for_prompt:
            chunks_section = (
                f"\n\nRETRIEVED POLICY CHUNKS (your source of truth):\n"
                f"{json.dumps(chunks_for_prompt, indent=2, default=str)}\n\n"
                f"Extract criteria ONLY from these chunks. Cite the chunk id "
                f"in `cited_chunk_ids` for every criterion."
            )
        else:
            chunks_section = (
                f"\n\nNo policy chunks were retrieved for this combination. "
                f"Note the gap in cardiologist_review_flags. Generate criteria "
                f"only from your most confident knowledge of CMS NCDs and "
                f"ACC/AHA guidelines, and leave cited_chunk_ids empty."
            )

        from cardioauth.claude_cost import TimedCall, system_with_cache_control, track_usage
        with TimedCall() as _t:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=8000,
                system=system_with_cache_control(SYSTEM_PROMPT),
                messages=[{
                    "role": "user",
                    "content": (
                        f"Build the prior authorization criteria for:\n"
                        f"  Procedure CPT: {procedure_code}\n"
                        f"  Payer: {payer_name}\n"
                        f"{cms_section}"
                        f"{chunks_section}"
                    ),
                }],
            )
        track_usage(response, agent="POLICY_AGENT", model=self.config.model,
                    duration_ms=_t.ms, case_id=f"{payer_name}-{procedure_code}")

        raw = response.content[0].text
        from cardioauth.agents.json_recovery import parse_llm_json
        data = parse_llm_json(raw)
        if not data:
            raise ValueError("POLICY_AGENT: could not parse LLM response")
        data.setdefault("payer", payer_name)
        data.setdefault("procedure", "")
        data.setdefault("cpt_code", procedure_code)
        data.setdefault("clinical_criteria", [])
        data.setdefault("documentation_required", [])
        data.setdefault("common_denial_reasons", [])
        data.setdefault("appeal_success_factors", [])

        # ── Build retrieval context for downstream use ──
        # Strip cited_chunk_ids from clinical_criteria before constructing
        # PolicyData (its model doesn't have this field), but stash them
        # on the returned object so the orchestrator can pass citations
        # through to the API response.
        chunk_lookup = {r.chunk.id: r for r in results}
        criteria_with_citations = []
        for c in data.get("clinical_criteria", []):
            cited_ids = c.pop("cited_chunk_ids", []) if isinstance(c, dict) else []
            criteria_with_citations.append({
                "criterion": c.get("criterion", "") if isinstance(c, dict) else str(c),
                "cited_chunk_ids": cited_ids,
                "citations": [
                    chunk_lookup[cid].to_dict()
                    for cid in cited_ids
                    if cid in chunk_lookup
                ],
            })

        policy = PolicyData(**data)

        # Attach citation metadata as runtime attributes (Pydantic v2 ignores extras)
        # We'll pass these via the orchestrator separately.
        policy.__dict__["_retrieved_chunks"] = [r.to_dict() for r in results]
        policy.__dict__["_criterion_citations"] = criteria_with_citations
        # Retrieval-loop telemetry (Apr 28 agentic-pattern addition)
        policy.__dict__["_retrieval_strategy"] = retrieval_strategy
        policy.__dict__["_retrieval_retry_log"] = retry_log

        # ── Attach calibration layer (P2 stats + global rules + freshness) ──
        # These surface to the reasoner prompt and to the API response.
        from cardioauth.stats import (
            check_policy_freshness,
            get_global_rules,
            get_payer_stats,
        )
        stats = get_payer_stats(payer_name, procedure_code)
        policy.__dict__["_payer_stats"] = stats.to_dict() if stats else None
        policy.__dict__["_payer_global_rules"] = [r.to_dict() for r in get_global_rules(payer_name)]
        freshness = check_policy_freshness(policy.policy_last_updated)
        policy.__dict__["_freshness"] = {
            "level": freshness.level,
            "age_days": freshness.age_days,
            "message": freshness.message,
        }
        if freshness.level in ("stale_warning", "stale_critical"):
            logger.warning("POLICY_AGENT: %s", freshness.message)

        if policy.auth_required is None:
            logger.warning("POLICY_AGENT: auth_required unknown for %s / %s", procedure_code, payer_name)

        # ── Response cache write (1h TTL) ──
        try:
            from cardioauth.persistence import get_store
            get_store().cache_set(
                cache_key,
                {
                    "policy": policy.model_dump(mode="json"),
                    "_retrieved_chunks": policy.__dict__.get("_retrieved_chunks", []),
                    "_criterion_citations": policy.__dict__.get("_criterion_citations", []),
                    "_payer_stats": policy.__dict__.get("_payer_stats"),
                    "_payer_global_rules": policy.__dict__.get("_payer_global_rules", []),
                    "_freshness": policy.__dict__.get("_freshness"),
                    "_retrieval_strategy": policy.__dict__.get("_retrieval_strategy"),
                    "_retrieval_retry_log": policy.__dict__.get("_retrieval_retry_log", []),
                },
                ttl_seconds=self._CACHE_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("POLICY_AGENT cache_set failed (continuing): %s", e)

        return policy
