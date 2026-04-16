"""Criterion validation + audit trail.

Defensive perimeter around the criterion pipeline. Every stage that
consumes or produces a list of criteria routes through validate_criteria_for_cpt(),
which compares against the taxonomy's expected set and raises loud
warnings (not silent drops) on mismatches.

Four production bugs in the last week were all the same bug — a criterion
silently dropped because no validator ran between stages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure, get_criterion

logger = logging.getLogger(__name__)


AuditStage = Literal[
    "taxonomy_filter",
    "policy_agent_output",
    "reasoner_input",
    "reasoner_output",
    "cpt_gating_fill",
    "final_package",
]


@dataclass
class CriterionTrailEntry:
    code: str
    short_name: str = ""
    evidence_type: str = ""
    severity: str = ""
    applicable_to_cpt: bool = False
    stages_passed: list[str] = field(default_factory=list)
    final_status: str = "unknown"
    drop_reason: str = ""
    flags: list[str] = field(default_factory=list)
    # Element-level detail (Apr 13 fix): per-element found/not-found verdicts
    # so a cardiologist can see exactly which part of the definition is missing.
    elements_satisfied: list[dict] = field(default_factory=list)
    missing_elements: list[str] = field(default_factory=list)
    # Self-consistency ensemble (Apr 14): how many reasoner runs agreed on
    # this criterion's status. 1.0 = unanimous, 0.67 = 2-of-3, 0.5 = split.
    ensemble_agreement: float | None = None
    ensemble_n_runs: int | None = None

    def mark(self, stage: AuditStage) -> None:
        if stage not in self.stages_passed:
            self.stages_passed.append(stage)


@dataclass
class ValidationReport:
    cpt_code: str
    payer: str
    expected_codes: set[str]
    received_codes: set[str]
    missing_codes: set[str]
    unknown_codes: set[str]
    valid_codes: set[str]
    warnings: list[dict] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.missing_codes and not self.unknown_codes

    def to_dict(self) -> dict:
        return {
            "cpt_code": self.cpt_code,
            "payer": self.payer,
            "expected_count": len(self.expected_codes),
            "received_count": len(self.received_codes),
            "missing": sorted(self.missing_codes),
            "unknown": sorted(self.unknown_codes),
            "valid": sorted(self.valid_codes),
            "warnings": self.warnings,
            "is_clean": self.is_clean,
        }


def validate_criteria_for_cpt(
    received_codes: list[str] | set[str],
    cpt_code: str,
    payer: str = "",
    stage: str = "",
) -> ValidationReport:
    """Compare criterion codes against taxonomy's expected set for this CPT/payer.

    Call at every agent boundary. Missing or unknown codes indicate
    silent data loss or hallucination — both get logged as warnings.
    """
    expected = {c.code for c in get_criteria_for_procedure(cpt_code, payer)}
    received = set(received_codes)

    missing = expected - received
    unknown = received - expected
    valid = expected & received

    warnings: list[dict] = []

    if missing:
        msg = (
            f"[{stage or 'validator'}] MISSING criteria for CPT {cpt_code}: "
            f"{sorted(missing)} — taxonomy expected but agent output did not return them."
        )
        logger.warning(msg)
        warnings.append({
            "level": "warning",
            "kind": "missing_criteria",
            "stage": stage,
            "cpt": cpt_code,
            "codes": sorted(missing),
            "message": msg,
        })

    if unknown:
        msg = (
            f"[{stage or 'validator'}] UNKNOWN criteria for CPT {cpt_code}: "
            f"{sorted(unknown)} — agent returned codes not in the taxonomy's applicable set."
        )
        logger.warning(msg)
        warnings.append({
            "level": "warning",
            "kind": "unknown_criteria",
            "stage": stage,
            "cpt": cpt_code,
            "codes": sorted(unknown),
            "message": msg,
        })

    return ValidationReport(
        cpt_code=cpt_code,
        payer=payer,
        expected_codes=expected,
        received_codes=received,
        missing_codes=missing,
        unknown_codes=unknown,
        valid_codes=valid,
        warnings=warnings,
    )


def build_audit_trail(
    cpt_code: str,
    payer: str,
    policy_codes: list[str] | None = None,
    reasoner_matches: list[dict] | None = None,
) -> list[CriterionTrailEntry]:
    """Build per-criterion audit trail starting from the taxonomy's expected set.

    Records what happened to each criterion at each stage. The reasoner
    matches may include {code, status, _enforced, ...} — the _enforced
    marker comes from UnifiedReasoner._enforce_cpt_gating.
    """
    expected = get_criteria_for_procedure(cpt_code, payer)
    policy_set = set(policy_codes or [])
    reasoner_by_code = {m.get("code", ""): m for m in (reasoner_matches or []) if m.get("code")}

    entries: list[CriterionTrailEntry] = []
    expected_codes = {c.code for c in expected}

    for crit in expected:
        entry = CriterionTrailEntry(
            code=crit.code,
            short_name=crit.short_name,
            evidence_type=crit.evidence_type,
            severity=crit.severity,
            applicable_to_cpt=True,
        )
        entry.mark("taxonomy_filter")

        if crit.code in policy_set:
            entry.mark("policy_agent_output")
        elif policy_codes is not None:
            entry.flags.append("missing_from_policy_agent")

        match = reasoner_by_code.get(crit.code)
        if match:
            entry.mark("reasoner_output")
            entry.final_status = match.get("status", "unknown")
            if match.get("_enforced"):
                entry.mark("cpt_gating_fill")
                entry.flags.append(f"enforced:{match.get('_enforced')}")
            # Element-level detail: capture per-element verdicts from reasoner
            entry.elements_satisfied = list(match.get("elements_satisfied") or [])
            entry.missing_elements = list(match.get("_missing_elements") or [])
            if entry.missing_elements:
                entry.flags.append("element_incomplete")
            # Ensemble agreement (from self-consistency runs)
            if "_ensemble_agreement" in match:
                entry.ensemble_agreement = match.get("_ensemble_agreement")
                entry.ensemble_n_runs = match.get("_ensemble_n_runs")
                if entry.ensemble_agreement is not None and entry.ensemble_agreement < 0.67:
                    entry.flags.append("low_agreement")
        else:
            entry.final_status = "dropped"
            entry.drop_reason = "Reasoner returned no match — silently skipped without enforcer"
            entry.flags.append("reasoner_skipped")

        entry.mark("final_package")
        entries.append(entry)

    for code, match in reasoner_by_code.items():
        if code not in expected_codes:
            crit = get_criterion(code)
            entries.append(CriterionTrailEntry(
                code=code,
                short_name=crit.short_name if crit else "",
                evidence_type=crit.evidence_type if crit else "",
                severity=crit.severity if crit else "",
                applicable_to_cpt=False,
                stages_passed=["reasoner_output"],
                final_status=match.get("status", "unknown"),
                drop_reason="Code returned by reasoner but not in applicable set for this CPT",
                flags=["unexpected_code"],
            ))

    return entries


def trail_to_dict(entries: list[CriterionTrailEntry]) -> list[dict]:
    return [
        {
            "code": e.code,
            "short_name": e.short_name,
            "evidence_type": e.evidence_type,
            "severity": e.severity,
            "applicable_to_cpt": e.applicable_to_cpt,
            "stages_passed": e.stages_passed,
            "final_status": e.final_status,
            "drop_reason": e.drop_reason,
            "flags": e.flags,
            "elements_satisfied": e.elements_satisfied,
            "missing_elements": e.missing_elements,
            "ensemble_agreement": e.ensemble_agreement,
            "ensemble_n_runs": e.ensemble_n_runs,
        }
        for e in entries
    ]


def extract_reasoner_codes(reasoning_result) -> list[str]:
    """Pull criterion codes from a ReasoningResult (criteria_met + criteria_not_met).

    The legacy ReasoningAgent uses natural-language criterion strings instead
    of taxonomy codes, so we do a best-effort reverse lookup via short_name.
    """
    codes: list[str] = []
    from cardioauth.taxonomy.taxonomy import CRITERION_TAXONOMY
    by_short = {c.short_name.lower(): c.code for c in CRITERION_TAXONOMY.values()}

    def _resolve(label: str) -> str | None:
        if not label:
            return None
        lower = label.lower().strip()
        if lower in CRITERION_TAXONOMY:
            return lower
        for k, v in by_short.items():
            if k and (k in lower or lower in k):
                return v
        return None

    if hasattr(reasoning_result, "criteria_met"):
        for c in reasoning_result.criteria_met or []:
            label = getattr(c, "criterion", "") or (c.get("criterion") if isinstance(c, dict) else "")
            code = _resolve(label)
            if code:
                codes.append(code)
    if hasattr(reasoning_result, "criteria_not_met"):
        for c in reasoning_result.criteria_not_met or []:
            label = getattr(c, "criterion", "") or (c.get("criterion") if isinstance(c, dict) else "")
            code = _resolve(label)
            if code:
                codes.append(code)
    return codes
