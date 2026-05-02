"""Phase B.2 — composable verification pipeline.

Replaces the ad-hoc `warnings.append({...})` accumulation in
demo_e2e step 5 with a typed pipeline of independent Checker classes.

Each Checker:
  - Has a stable `name` and `version` (recorded on its findings for
    reproducibility — Phase C.1 freezes versions per submission).
  - Takes an immutable SubmissionPacket as input.
  - Returns a list of typed Finding objects.
  - Never mutates the packet directly; the pipeline writes findings
    via packet.add_findings() after collection so partial failure
    is contained.

The default pipeline composition matches today's behavior + adds
EvidenceCompletenessChecker (Peter Apr 30 #4: "Is each key form
answer supported by traceable clinical evidence?"), which is now
mechanically answerable thanks to A.4's evidence references.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from cardioauth.submission_packet import (
    Finding,
    SubmissionPacket,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Checker base class
# ──────────────────────────────────────────────────────────────────────


class Checker(ABC):
    """Abstract verifier. Stateless; safe to share across cases."""

    name: ClassVar[str]
    version: ClassVar[str] = "v1"

    @abstractmethod
    def check(self, packet: SubmissionPacket) -> list[Finding]: ...

    # Convenience for subclasses building Findings with the right `checker`.
    def _f(
        self, *,
        kind: str, severity: str, message: str,
        related_field_keys: tuple[str, ...] = tuple(),
        auto_fixable: bool = False, fix_suggestion: str = "",
    ) -> Finding:
        return Finding(
            kind=kind, severity=severity, message=message,
            related_field_keys=related_field_keys,
            auto_fixable=auto_fixable, fix_suggestion=fix_suggestion,
            checker=f"{self.name}@{self.version}",
        )


# ──────────────────────────────────────────────────────────────────────
# Concrete checkers
# ──────────────────────────────────────────────────────────────────────


class EssentialsChecker(Checker):
    """Hard block when any payer-required identification field is missing.

    The MVP gate (Peter Apr 28): "Only the key clinical and
    identification fields would need to trigger a submission block."

    Peter May rerun: when the chart came back from an API-fallback
    path, every essential will be blank, and EssentialsChecker emits
    six "missing required field" findings even though the data is
    sitting in the raw note. That's the system lying. The skeletal-
    chart guard suppresses the cascade and emits one honest finding
    pointing at the upstream API failure.
    """

    name = "essentials"

    ESSENTIAL_FIELDS = (
        ("patient_name", "Patient name"),
        ("date_of_birth", "Date of birth"),
        ("insurance_id", "Member ID"),
        ("payer_name", "Payer"),
        ("procedure_code", "CPT code"),
        ("attending_physician", "Ordering physician"),
    )

    _API_FALLBACK_PREFIXES = (
        "Anthropic API unavailable",
        "Note extraction failed",
    )

    def _chart_in_api_fallback(self, chart: dict) -> bool:
        """True iff the chart was assembled via one of the
        _extract_chart_with_evidence fallback paths. Detection mirrors
        demo_e2e._chart_extraction_failed_via_api."""
        mf = chart.get("missing_fields") or []
        if not mf:
            return False
        first = mf[0] if isinstance(mf[0], str) else ""
        return any(first.startswith(p) for p in self._API_FALLBACK_PREFIXES)

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        chart = packet.chart_data or {}

        # Peter May rerun: don't flag every essential when the chart is
        # in API-fallback mode. The data is in the raw note; the LLM
        # just couldn't be reached. Surface ONE honest finding instead
        # of six false "missing field" cascades.
        if self._chart_in_api_fallback(chart):
            return [self._f(
                kind="extraction_blocked_by_api",
                severity="blocking",
                message=(
                    "Cannot validate identity fields — chart extraction failed "
                    "due to an upstream API issue. Patient name, DOB, member ID "
                    "and ordering MD are likely present in the raw note but "
                    "couldn't be confirmed. Re-run after the API recovers."
                ),
                fix_suggestion=(
                    "Refill Anthropic credits and re-run, or paste the note "
                    "again once the API is reachable."
                ),
            )]

        out: list[Finding] = []
        for key, label in self.ESSENTIAL_FIELDS:
            v = chart.get(key, "")
            if isinstance(v, str) and not v.strip():
                out.append(self._f(
                    kind="missing_essential",
                    severity="blocking",
                    message=(
                        f"Submission cannot proceed without: {label}. "
                        "Required by every payer; cannot be inferred."
                    ),
                    related_field_keys=(key,),
                ))
        return out


class ReasonerConfidenceChecker(Checker):
    """Reasoner-confidence band findings: low / uncertain / DO_NOT_SUBMIT.

    Reads from packet.reasoner_summary, populated by the packet
    builder. Severity follows the gate's prior thresholds:
      score < 0.5            → high (very low)
      0.50 ≤ score < 0.65    → medium (uncertain)
      label DO_NOT_SUBMIT    → high (clinical recommendation)
    """

    name = "reasoner_confidence"
    LOW_THRESHOLD = 0.5
    UNCERTAIN_LOW = 0.5
    UNCERTAIN_HIGH = 0.65

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        rs = packet.reasoner_summary or {}
        score = rs.get("approval_score")
        label = (rs.get("approval_label") or "").upper()
        out: list[Finding] = []

        if label in ("DO NOT SUBMIT", "INSUFFICIENT", "DO_NOT_SUBMIT"):
            out.append(self._f(
                kind="reasoner_low_confidence",
                severity="high",
                message=(
                    f"Reasoner recommends against submission ({label}"
                    + (f", score {score:.0%}" if score is not None else "")
                    + "). Documentation may not support this procedure under "
                    "payer policy."
                ),
            ))
            return out

        if isinstance(score, (int, float)):
            if score < self.LOW_THRESHOLD:
                out.append(self._f(
                    kind="reasoner_low_score",
                    severity="high",
                    message=(
                        f"Reasoner approval score is low ({score:.0%}). "
                        "Review criterion gaps before submitting."
                    ),
                ))
            elif self.UNCERTAIN_LOW <= score < self.UNCERTAIN_HIGH:
                out.append(self._f(
                    kind="reasoner_uncertain",
                    severity="medium",
                    message=(
                        f"Reasoner score in uncertain band ({score:.0%}). "
                        "Borderline case — physician review recommended."
                    ),
                ))
        return out


class AlternativeModalityChecker(Checker):
    """Surface a finding when the reasoner suggested an alternative
    modality. The cpt_resolver may already have flagged the CPT
    divergence; this is the modality-name view of the same signal.
    """

    name = "alternative_modality"

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        rs = packet.reasoner_summary or {}
        alt = rs.get("alternative_modality")
        if not isinstance(alt, dict) or not alt.get("name"):
            return []
        return [self._f(
            kind="alternative_modality",
            severity="medium",
            message=(
                f"An alternative modality may be more appropriate: "
                f"{alt.get('name')} (CPT {alt.get('cpt', 'unspecified')}). "
                f"{alt.get('rationale', '')}".strip()
            ),
        )]


class ExtractionConfidenceChecker(Checker):
    """Flag cases where the reasoner score was capped by extraction
    confidence — the chart was thin enough that high reasoner
    certainty wasn't licensed."""

    name = "extraction_confidence_cap"

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        rs = packet.reasoner_summary or {}
        if not rs.get("score_capped_by_extraction"):
            return []
        raw = rs.get("approval_score_raw")
        capped = rs.get("approval_score")
        chart_conf = rs.get("chart_confidence")
        if not all(isinstance(v, (int, float)) for v in (raw, capped, chart_conf)):
            return []
        return [self._f(
            kind="extraction_thin",
            severity="low",
            message=(
                f"Reasoner score capped from {raw:.0%} to {capped:.0%} by "
                f"extraction confidence ({chart_conf:.0%}). Strengthen the "
                "note or chart inputs to license higher certainty."
            ),
        )]


class CoherenceChecker(Checker):
    """Wraps the existing deterministic packet_coherence checks.

    Runs the same CPT-mismatch / procedure-name-drift checks, but
    feeds findings into the typed pipeline rather than the legacy
    warnings list.
    """

    name = "coherence"

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        from cardioauth.packet_coherence import check_packet_coherence

        # Reconstruct a chart-like object so the existing function works
        chart = _DictAttrShim(packet.chart_data)
        # The narrative attestation's cpt_referenced was extracted at
        # packet-build time; use the typed value when checking against
        # resolved CPT.
        out: list[Finding] = []

        # 1. Re-run the deterministic legacy coherence pass.
        legacy = check_packet_coherence(
            chart=chart,
            reasoning=_ReasoningShim(text=packet.narrative.text),
            raw_note=packet.raw_note,
        )
        for w in legacy:
            out.append(self._f(
                kind=w.get("kind", "coherence_finding"),
                severity=w.get("severity", "medium"),
                message=w.get("message", ""),
            ))

        # 2. New typed check: resolved_cpt vs narrative.cpt_referenced
        nar_cpt = (packet.narrative.cpt_referenced or "").strip()
        resolved_cpt = (packet.resolved_cpt.cpt or "").strip()
        if nar_cpt and resolved_cpt and nar_cpt != resolved_cpt:
            out.append(self._f(
                kind="cpt_attestation_vs_resolved",
                severity="high",
                message=(
                    f"ResolvedCPT is {resolved_cpt} but the narrative "
                    f"attestation references CPT {nar_cpt}. Internal "
                    "inconsistency; correct before transmission."
                ),
                auto_fixable=False,
            ))
        return out


class EvidenceCompletenessChecker(Checker):
    """Peter Apr 30 #4 made mechanical: every populated form field
    should reference at least one EvidenceSpan in the graph. Empty
    references on populated fields → low-severity flag (the field
    might still be correct, but its lineage is unverifiable).
    """

    name = "evidence_completeness"

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        out: list[Finding] = []
        for f in packet.form_fields:
            if f.status != "populated":
                continue
            if f.evidence.is_empty():
                out.append(self._f(
                    kind="form_field_unsupported",
                    severity="low",
                    message=(
                        f"Form field '{f.label}' is populated but has no "
                        "traceable evidence span. The value may be correct, "
                        "but the source can't be verified mechanically."
                    ),
                    related_field_keys=(f.key,),
                ))
        return out


class SafetyVerifierChecker(Checker):
    """Independent safety-verification layer (Peter's aviation-style
    safety system). Runs the rule-based fact extractor against the
    raw note INDEPENDENTLY of the primary path, then cross-checks
    each fact against the chart and the reasoner verdict.

    Disagreements between the independent path and the primary path
    are high-confidence signals that something went wrong upstream:
      - Note has LBBB + chart has LBBB + reasoner says ECG-001 not_met
        → reasoner missed signal that's clearly there.
      - Note has LBBB + chart has no LBBB
        → chart extractor missed signal.
      - Note CPT differs from chart CPT (already caught by
        cpt_resolver, but the audit log surfaces the comparison).

    The audit_log is attached to the FIRST emitted finding via the
    fix_suggestion field as serialized JSON, so the UI can render
    the full "what was checked" panel even when no findings fire.
    Phase D: a structured audit attribute on the packet.
    """

    name = "safety_verifier"

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        from cardioauth.safety_verifier import run_safety_verification
        import json

        try:
            audit = run_safety_verification(
                raw_note=packet.raw_note or "",
                chart_data=packet.chart_data or {},
                reasoner_summary=packet.reasoner_summary or {},
                resolved_cpt=packet.resolved_cpt.cpt or "",
            )
        except Exception as e:
            logger.warning("Safety verifier failed (continuing): %s", e)
            return []

        # Stash audit log on the packet for the UI to render even when
        # no findings fire ("what was checked" panel).
        try:
            packet.reasoner_summary = {
                **(packet.reasoner_summary or {}),
                "_safety_audit": audit.to_dict(),
            }
        except Exception:
            pass

        findings: list[Finding] = []
        for cmp in audit.comparisons:
            if not cmp.fact.present:
                continue
            # Reasoner-missed-signal: note has it, criterion was evaluated
            # but came back not_met. High severity: the reasoner output is
            # likely wrong on this case.
            if cmp.relevant_criteria and cmp.criterion_met_by_reasoner is False:
                findings.append(self._f(
                    kind="safety_reasoner_missed_signal",
                    severity="high",
                    message=(
                        f"Independent verification found {cmp.fact.label} in the "
                        f"note (\"{cmp.fact.quote[:120].strip()}\"), and the chart "
                        f"{'also captured it' if cmp.present_in_chart else 'did NOT capture it'}, "
                        f"but the reasoner returned not_met for criterion(s) "
                        f"{', '.join(cmp.relevant_criteria)}. The reasoner verdict "
                        "is likely wrong on this case."
                    ),
                    auto_fixable=False,
                    fix_suggestion=(
                        f"Re-emphasize {cmp.fact.label} in the note assessment so "
                        f"the reasoner credits the matching criterion ("
                        f"{', '.join(cmp.relevant_criteria)})."
                    ),
                ))
            # Chart-extraction-missed: note has it, chart doesn't. Even if
            # the criterion didn't apply or wasn't evaluated, this is a
            # silent extraction loss the office should know about.
            if cmp.fact.present and not cmp.present_in_chart and cmp.relevant_criteria:
                findings.append(self._f(
                    kind="safety_chart_extraction_gap",
                    severity="medium",
                    message=(
                        f"Independent verification found {cmp.fact.label} in the "
                        f"note (\"{cmp.fact.quote[:120].strip()}\"), but the chart "
                        f"extractor did not capture it. Downstream stages may have "
                        "judged the case without this signal."
                    ),
                    fix_suggestion=(
                        f"Restate {cmp.fact.label} in clinical-summary form so the "
                        "chart extractor picks it up on the next pass."
                    ),
                ))

        # CPT mismatch between note and chart — cpt_resolver may already
        # have flagged this; the safety verifier flags it again with the
        # specific verbatim quote so the UI can show context.
        if audit.note_chart_cpt_mismatch:
            findings.append(self._f(
                kind="safety_note_chart_cpt_mismatch",
                severity="high",
                message=(
                    f"Independent verification: the note references CPT "
                    f"{', '.join(audit.cpts_in_note)} but the chart's "
                    f"procedure_code is {audit.cpt_in_chart}. The submission "
                    "would carry an inconsistent CPT across the package."
                ),
                fix_suggestion=(
                    f"Decide whether the correct CPT is "
                    f"{', '.join(audit.cpts_in_note)} or {audit.cpt_in_chart}, "
                    "then update the note (and the request) so they agree."
                ),
            ))

        return findings


class CriteriaMatchResolvedCPTChecker(Checker):
    """Peter May rerun (Case 5): the reasoner evaluated PET-specific
    logic against a SPECT case (CPT 78452). Every criterion code the
    reasoner evaluated MUST be applicable to the resolved CPT per the
    canonical taxonomy. If reasoner_summary lists criteria_evaluated
    that don't apply to the resolved CPT, that's a high-severity
    coherence failure.

    Today reasoner_summary doesn't carry criteria_evaluated; this
    checker is forward-compatible — it lights up the moment the
    reasoner snapshot includes the list.
    """

    name = "criteria_match_resolved_cpt"

    def check(self, packet: SubmissionPacket) -> list[Finding]:
        from cardioauth.taxonomy.taxonomy import CRITERION_TAXONOMY

        rs = packet.reasoner_summary or {}
        evaluated = rs.get("criteria_evaluated") or []
        resolved_cpt = (packet.resolved_cpt.cpt or "").strip()
        if not evaluated or not resolved_cpt:
            return []

        misapplied: list[str] = []
        for code in evaluated:
            criterion = CRITERION_TAXONOMY.get(code)
            if criterion is None:
                continue
            applies = criterion.applies_to or []
            if applies and resolved_cpt not in applies:
                misapplied.append(code)

        if not misapplied:
            return []

        return [self._f(
            kind="criteria_evaluated_outside_resolved_cpt",
            severity="high",
            message=(
                f"Reasoner evaluated {len(misapplied)} criterion code(s) that "
                f"don't apply to the resolved CPT {resolved_cpt} per the "
                f"taxonomy: {', '.join(misapplied)}. The reasoner's verdict "
                "may be reasoning over the wrong procedure family."
            ),
        )]


# ──────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────


class VerificationPipeline:
    """Composable pipeline of Checkers.

    Pipeline.run(packet) collects findings without mutating the packet.
    The caller decides whether to add_findings(...) onto the packet,
    which is what the gate does today (so the typed packet carries the
    full record).
    """

    def __init__(self, checkers: list[Checker]) -> None:
        self.checkers = checkers

    def run(self, packet: SubmissionPacket) -> list[Finding]:
        all_findings: list[Finding] = []
        for c in self.checkers:
            try:
                all_findings.extend(c.check(packet))
            except Exception as e:
                logger.warning("Checker %s failed (continuing): %s", c.name, e)
        return all_findings


def default_pipeline() -> VerificationPipeline:
    """Default checker composition — runs every deterministic check
    we've built. Order matters only for human-readable finding order;
    findings are appended in pipeline order to packet.deterministic_findings."""
    return VerificationPipeline([
        EssentialsChecker(),
        ReasonerConfidenceChecker(),
        AlternativeModalityChecker(),
        ExtractionConfidenceChecker(),
        CoherenceChecker(),
        EvidenceCompletenessChecker(),
        CriteriaMatchResolvedCPTChecker(),
        SafetyVerifierChecker(),
    ])


# ──────────────────────────────────────────────────────────────────────
# Internal shims for legacy interop
# ──────────────────────────────────────────────────────────────────────


class _DictAttrShim:
    """Lightweight wrapper so legacy attribute-access functions
    (getattr(chart, 'procedure_code', '')) work over a plain dict."""

    def __init__(self, data: dict) -> None:
        self._data = data or {}

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name, "")


class _ReasoningShim:
    """Minimal stand-in for legacy code that reads
    `reasoning.pa_narrative_draft`."""

    def __init__(self, text: str) -> None:
        self.pa_narrative_draft = text
