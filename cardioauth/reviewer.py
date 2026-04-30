"""Phase B.3 — LLM packet reviewer.

Read-only audit of an assembled SubmissionPacket against Peter's
9-question checklist (Apr 30 email). Runs Claude (Haiku) with
prompt caching on the heavy system prompt; emits a structured
ReviewerVerdict that stacks Findings per question.

Usage policy (Phase B.3 launch):
  - Read-only: never rewrites packet artifacts.
  - Only-on-held: runs only when the deterministic pipeline produced
    high/blocking findings, or the reasoner snapshot indicates
    uncertainty. Clean cases skip the LLM call entirely.

Phase C: outcome data + reviewer verdicts are correlated; if reviewer
flags consistently predict denials, we promote to always-run; if
findings prove safe, autonomous-fixer mode for narrowly-scoped CPT
corrections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from cardioauth.submission_packet import (
    Finding,
    ReviewerVerdict,
    SubmissionPacket,
    severity_rank,
)

logger = logging.getLogger(__name__)


REVIEWER_VERSION = "v1"
DEFAULT_REVIEWER_MODEL = "claude-haiku-4-5-20251001"


# ──────────────────────────────────────────────────────────────────────
# 9-question checklist (Peter Apr 30)
# ──────────────────────────────────────────────────────────────────────


CHECKLIST_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "q1",
        "Allowed values + populated fields",
        "Does every required payer field use an allowed value AND either "
        "have a populated answer or be clearly marked 'needs review'?",
    ),
    (
        "q2",
        "CPT consistency across artifacts",
        "Does the resolved CPT/procedure match the physician's intended "
        "order AND remain consistent across the form, attestation, "
        "warnings, and submission payload?",
    ),
    (
        "q3",
        "CPT mismatch flagged not silenced",
        "If there is a CPT or modality mismatch, is it CLEARLY FLAGGED "
        "before submission rather than silently changed by the system?",
    ),
    (
        "q4",
        "Form answers traceable to clinical evidence",
        "Is each key form answer supported by traceable clinical evidence "
        "from the note or ChartData? (Check that populated form fields "
        "carry non-empty evidence references back into the EvidenceGraph.)",
    ),
    (
        "q5",
        "Evidence preserved separately from normalized answer",
        "Is the rich clinical evidence preserved separately from the "
        "normalized payer answer? (Check that select fields snap to "
        "allowed options BUT the pre-normalized rich text is retained "
        "as evidence_text.)",
    ),
    (
        "q6",
        "Cross-packet contradictions flagged",
        "Are contradictions across the packet flagged before submission? "
        "(Form vs narrative vs payload; reasoner score vs form readiness; "
        "alternative modality vs ordered modality.)",
    ),
    (
        "q7",
        "Alternative modality is advisory not silent",
        "Are alternative modality suggestions clearly advisory rather "
        "than silently changing the order? Resolved CPT must remain the "
        "request CPT; alternatives appear as Findings.",
    ),
    (
        "q8",
        "Manual attestations marked for confirmation",
        "Are manual attestations clearly marked needs_verify for human "
        "confirmation before transmission, rather than silently treated "
        "as missing or pre-checked?",
    ),
    (
        "q9",
        "Trustworthy to a human reviewer",
        "Would this packet look clean and trustworthy to a back-office "
        "person or a payer reviewer? Anything visually inconsistent, "
        "incomplete, or contradictory at a glance?",
    ),
)


_CHECKLIST_RENDERED = "\n".join(
    f"  {qid} ({short}): {definition}" for qid, short, definition in CHECKLIST_QUESTIONS
)


SYSTEM_PROMPT = f"""\
You are PACKET_REVIEWER, a read-only auditor of fully-assembled prior-
authorization submission packets in cardiology. Your sole job is to
verify the packet's coherence + completeness against a 9-question
checklist before transmission to a payer.

You DO NOT rewrite or modify the packet. You only emit findings.

═══════════════════════════════════════════════════════════════════════
9-QUESTION CHECKLIST
═══════════════════════════════════════════════════════════════════════
{_CHECKLIST_RENDERED}

═══════════════════════════════════════════════════════════════════════
SCORING RULES
═══════════════════════════════════════════════════════════════════════

For each question:
  - "yes"           — the packet clearly meets the question's intent.
  - "no"            — the packet violates the question (real problem).
  - "needs_review"  — ambiguous; can't tell from the packet alone.

For every "no" or "needs_review" answer, emit a Finding with the
shape below. Severity guidance:
  - high       — likely causes the payer to deny or reject the packet.
  - medium     — would confuse a back-office reviewer but probably
                 doesn't break submission.
  - low        — minor inconsistency or incompleteness.

═══════════════════════════════════════════════════════════════════════
REASONING DISCIPLINE
═══════════════════════════════════════════════════════════════════════

  1. Cite specific form_field keys, finding kinds, and evidence span
     ids when relevant. The audit trail depends on this.
  2. Do NOT generate clinical opinions. Your scope is structural
     coherence + completeness, NOT clinical appropriateness.
  3. The deterministic pipeline already flagged some issues — those
     appear in `deterministic_findings`. You may reinforce them in
     your own findings if the pattern repeats elsewhere in the
     packet, but don't simply re-emit them.
  4. The system NEVER autonomously rewrites the order. If you see a
     CPT/modality mismatch, your job is to FLAG it, not propose a
     specific code change.

═══════════════════════════════════════════════════════════════════════
OUTPUT
═══════════════════════════════════════════════════════════════════════

Return ONLY valid JSON matching this schema:
{{
  "passed": true | false,
  "overall_recommendation": "transmit" | "hold" | "block",
  "rationale": "1-3 sentence summary of your verdict",
  "questions": {{
    "q1": {{
      "answer": "yes" | "no" | "needs_review",
      "reasoning": "1-2 sentences citing specific fields / findings",
      "findings": [
        {{
          "kind": "short_kind_string",
          "severity": "low" | "medium" | "high",
          "message": "human-readable explanation",
          "related_field_keys": ["form_field_key", ...]
        }}
      ]
    }},
    "q2": {{ ... }},
    ...
    "q9": {{ ... }}
  }}
}}

Recommendation rules:
  - "block"     — at least one Finding in your verdict has severity=high
                  AND describes a real defect (NOT just an advisory like
                  alternative_modality).
  - "hold"      — at least one medium-severity finding OR multiple lows.
  - "transmit"  — every question answered "yes" or only low-severity
                  findings that are clearly advisory.
"""


# ──────────────────────────────────────────────────────────────────────
# Decision: should we run the reviewer at all?
# ──────────────────────────────────────────────────────────────────────


def should_review_packet(packet: SubmissionPacket) -> bool:
    """Phase B.3 launch policy: run the LLM reviewer ONLY when the
    deterministic pipeline already flagged something significant or
    the reasoner is uncertain. Clean cases skip the LLM call.
    """
    # Deterministic findings of medium+ severity → review
    if any(severity_rank(f.severity) >= severity_rank("medium")
           for f in packet.deterministic_findings):
        return True

    # Reasoner low/uncertain → review
    rs = packet.reasoner_summary or {}
    score = rs.get("approval_score")
    label = (rs.get("approval_label") or "").upper()
    if isinstance(score, (int, float)) and score < 0.65:
        return True
    if label in ("DO NOT SUBMIT", "INSUFFICIENT", "DO_NOT_SUBMIT", "MEDIUM"):
        return True

    return False


# ──────────────────────────────────────────────────────────────────────
# Packet → reviewer-readable JSON
# ──────────────────────────────────────────────────────────────────────


def serialize_packet_for_review(packet: SubmissionPacket) -> dict:
    """Token-efficient projection of the packet for the reviewer.

    Includes:
      - resolved_cpt + payer
      - reasoner_summary
      - form_fields summary (key, label, status, value, evidence_text,
        evidence_span_count, options, required)
      - narrative (text + extracted cpt/procedure references)
      - deterministic_findings (kind, severity, message, checker)
      - evidence_graph SUMMARY only (count + extractor breakdown) —
        not the full span list, which could blow the context budget.
    """
    spans = packet.evidence_graph.all_spans()
    extractor_counts: dict[str, int] = {}
    for s in spans:
        extractor_counts[s.extractor] = extractor_counts.get(s.extractor, 0) + 1

    return {
        "case_id": packet.case_id,
        "payer": packet.payer,
        "resolved_cpt": {
            "cpt": packet.resolved_cpt.cpt,
            "procedure": packet.resolved_cpt.procedure,
            "source": packet.resolved_cpt.source,
            "rationale": packet.resolved_cpt.rationale,
        },
        "reasoner_summary": packet.reasoner_summary,
        "form_fields": [
            {
                "key": f.key,
                "label": f.label,
                "category": f.category,
                "required": f.required,
                "format": f.format,
                "options": f.options,
                "value": f.value,
                "evidence_text": f.evidence_text,
                "evidence_span_count": len(f.evidence.span_ids),
                "status": f.status,
            }
            for f in packet.form_fields
        ],
        "narrative": {
            "text": packet.narrative.text[:1000],  # truncate long narratives
            "cpt_referenced": packet.narrative.cpt_referenced,
            "procedure_referenced": packet.narrative.procedure_referenced,
        },
        "deterministic_findings": [
            {
                "kind": f.kind, "severity": f.severity,
                "message": f.message, "checker": f.checker,
                "related_field_keys": list(f.related_field_keys),
            }
            for f in packet.deterministic_findings
        ],
        "evidence_graph_summary": {
            "total_spans": len(spans),
            "by_extractor": extractor_counts,
        },
    }


# ──────────────────────────────────────────────────────────────────────
# Verdict parsing
# ──────────────────────────────────────────────────────────────────────


def _parse_reviewer_response(
    raw_text: str,
    *,
    model: str,
    cost_input_tokens: int,
    cost_output_tokens: int,
    duration_ms: int,
) -> ReviewerVerdict:
    """Parse the LLM's JSON output into a ReviewerVerdict.

    Uses parse_llm_json (truncation-tolerant) to recover from the
    common LLM failure modes (markdown fences, trailing comma,
    string truncation).
    """
    from cardioauth.agents.json_recovery import parse_llm_json
    data = parse_llm_json(raw_text, fallback={})
    if not data:
        return ReviewerVerdict(
            passed=False,
            overall_recommendation="hold",
            rationale="Reviewer response could not be parsed; defaulting to hold.",
            findings_per_question={},
            model=model,
            cost_input_tokens=cost_input_tokens,
            cost_output_tokens=cost_output_tokens,
            duration_ms=duration_ms,
            reviewed_at=datetime.now(timezone.utc).isoformat(),
        )

    findings_per_question: dict[str, list[Finding]] = {}
    questions = data.get("questions") or {}
    for qid, qdata in questions.items():
        if not isinstance(qdata, dict):
            continue
        raw_findings = qdata.get("findings") or []
        question_findings: list[Finding] = []
        for f in raw_findings:
            if not isinstance(f, dict):
                continue
            sev = (f.get("severity") or "medium").lower()
            if sev not in ("low", "medium", "high", "info", "blocking"):
                sev = "medium"
            question_findings.append(Finding(
                kind=str(f.get("kind") or "reviewer_finding"),
                severity=sev,                   # type: ignore[arg-type]
                message=str(f.get("message") or ""),
                related_field_keys=tuple(
                    str(k) for k in (f.get("related_field_keys") or [])
                ),
                checker=f"packet_reviewer@{REVIEWER_VERSION}",
            ))
        findings_per_question[qid] = question_findings

    rec = (data.get("overall_recommendation") or "hold").lower()
    if rec not in ("transmit", "hold", "block"):
        rec = "hold"

    return ReviewerVerdict(
        passed=bool(data.get("passed", False)),
        overall_recommendation=rec,             # type: ignore[arg-type]
        rationale=str(data.get("rationale") or ""),
        findings_per_question=findings_per_question,
        model=model,
        cost_input_tokens=cost_input_tokens,
        cost_output_tokens=cost_output_tokens,
        duration_ms=duration_ms,
        reviewed_at=datetime.now(timezone.utc).isoformat(),
    )


# ──────────────────────────────────────────────────────────────────────
# PacketReviewer
# ──────────────────────────────────────────────────────────────────────


@dataclass
class PacketReviewer:
    """Read-only LLM reviewer of a SubmissionPacket."""

    api_key: str = ""
    model: str = DEFAULT_REVIEWER_MODEL
    max_tokens: int = 2000

    @classmethod
    def from_config(cls) -> "PacketReviewer":
        from cardioauth.config import Config
        cfg = Config()
        return cls(
            api_key=cfg.anthropic_api_key,
            model=getattr(cfg, "reviewer_model", DEFAULT_REVIEWER_MODEL),
        )

    def review(self, packet: SubmissionPacket) -> ReviewerVerdict:
        """Run the 9-question audit. Returns ReviewerVerdict.empty()
        when the API key is missing — graceful degradation."""
        if not self.api_key:
            logger.warning("PacketReviewer: no API key; returning empty verdict")
            v = ReviewerVerdict.empty()
            v.rationale = "Reviewer skipped — no Anthropic API key configured."
            v.reviewed_at = datetime.now(timezone.utc).isoformat()
            return v

        import json
        try:
            import anthropic
            from cardioauth.claude_cost import (
                TimedCall, system_with_cache_control, track_usage,
            )
        except ImportError as e:
            logger.warning("PacketReviewer: anthropic SDK not available: %s", e)
            return ReviewerVerdict.empty()

        client = anthropic.Anthropic(api_key=self.api_key)
        packet_payload = serialize_packet_for_review(packet)

        try:
            with TimedCall() as _t:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_with_cache_control(SYSTEM_PROMPT),
                    messages=[{
                        "role": "user",
                        "content": (
                            "Review this submission packet against the "
                            "9-question checklist. Return ONLY the JSON "
                            "verdict.\n\nPACKET:\n"
                            + json.dumps(packet_payload, indent=2, default=str)
                        ),
                    }],
                )
            track_usage(
                response,
                agent="PACKET_REVIEWER",
                model=self.model, duration_ms=_t.ms,
                case_id=packet.case_id,
            )
            usage = getattr(response, "usage", None)
            in_tokens = getattr(usage, "input_tokens", 0) if usage else 0
            out_tokens = getattr(usage, "output_tokens", 0) if usage else 0

            raw_text = response.content[0].text
            return _parse_reviewer_response(
                raw_text,
                model=self.model,
                cost_input_tokens=in_tokens,
                cost_output_tokens=out_tokens,
                duration_ms=_t.ms,
            )
        except Exception as e:
            logger.warning("PacketReviewer: API call failed: %s", e)
            v = ReviewerVerdict.empty()
            v.rationale = f"Reviewer failed: {str(e)[:240]}"
            v.overall_recommendation = "hold"
            v.passed = False
            v.reviewed_at = datetime.now(timezone.utc).isoformat()
            return v


# ──────────────────────────────────────────────────────────────────────
# Convenience: review + attach to packet
# ──────────────────────────────────────────────────────────────────────


def review_and_attach(packet: SubmissionPacket, reviewer: PacketReviewer | None = None) -> ReviewerVerdict:
    """Run the reviewer (when policy says we should) and stash the
    verdict on the packet. Returns the verdict.
    """
    if not should_review_packet(packet):
        v = ReviewerVerdict.empty()
        v.rationale = "Skipped per only-on-held policy: no high/medium findings + reasoner clean."
        v.reviewed_at = datetime.now(timezone.utc).isoformat()
        packet.set_reviewer_verdict(v)
        return v

    if reviewer is None:
        reviewer = PacketReviewer.from_config()
    verdict = reviewer.review(packet)
    packet.set_reviewer_verdict(verdict)
    return verdict
