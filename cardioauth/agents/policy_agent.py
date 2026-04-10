"""POLICY_AGENT — RAG-grounded payer policy criteria generator.

Stage 1 RAG architecture:
  1. Retrieve relevant policy chunks from the corpus by (CPT, payer)
  2. Pass chunks as grounded context to Claude
  3. Require Claude to extract criteria from the chunks and cite them
  4. Return structured criteria with citation references
"""

from __future__ import annotations

import json
import logging

import anthropic

from cardioauth.config import Config
from cardioauth.models.policy import PolicyData

logger = logging.getLogger(__name__)


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

    def run(
        self,
        procedure_code: str,
        payer_name: str,
        cms_context: dict | None = None,
    ) -> PolicyData:
        logger.info("POLICY_AGENT: retrieving policy chunks for procedure=%s payer=%s", procedure_code, payer_name)

        # ── Stage 1 RAG retrieval ──
        from cardioauth.rag import retrieve_for_pa
        results = retrieve_for_pa(
            cpt_code=procedure_code,
            payer=payer_name,
            procedure_name=cms_context.get("cms_ncd_title", "") if cms_context else "",
            top_k=8,
        )

        if not results:
            logger.warning("POLICY_AGENT: no policy chunks retrieved for %s / %s — model will rely on training data", procedure_code, payer_name)
            chunks_for_prompt: list[dict] = []
        else:
            chunks_for_prompt = [r.to_dict() for r in results]
            logger.info("POLICY_AGENT: retrieved %d chunks (%s)", len(results),
                        ", ".join(r.chunk.id for r in results))

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

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
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

        if policy.auth_required is None:
            logger.warning("POLICY_AGENT: auth_required unknown for %s / %s", procedure_code, payer_name)

        return policy
