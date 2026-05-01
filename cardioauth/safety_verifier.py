"""Independent safety-verification layer.

Peter's May rerun framing: "the reviewer should be an independent
fallback/check layer, like aviation or anesthesia safety systems."
Today's reviewer is downstream of the primary path (Claude
extraction + Claude reasoner); it can't catch errors that path
introduced because it reads that path's output.

This module is a SECOND, INDEPENDENT path that re-extracts atomic
clinical facts from the raw note using deterministic rules (no LLM,
no shared state with the primary path). A comparator then cross-
checks the independent facts against:
  - chart_data (did Claude's chart extraction capture this?)
  - reasoner_summary.criteria_met / criteria_not_met (did the
    reasoner correctly map the fact to its criterion?)

When the two paths disagree, that's a high-confidence signal that
something went wrong upstream. Peter's Case 1 (LBBB + inability to
exercise + nondiagnostic ETT all in note, reasoner returned 0%) is
the exact pattern this catches: the comparator sees three facts the
reasoner should have honored as met criteria but didn't.

Design points:
  - Pure functions per fact (testable in isolation, deterministic)
  - Each return carries a verbatim quote + char offset for audit
  - The comparator returns an audit log AND a list of Finding objects
  - Audit log surfaces in the UI as "What was checked" — visibility
    matters even when nothing fires (Peter's safety-system framing)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Independent fact extractors
# ──────────────────────────────────────────────────────────────────────


@dataclass
class FactCheck:
    """One atomic-fact check result.

    `present` is the verifier's verdict from the raw note alone.
    `quote` is a verbatim snippet with surrounding context (for audit).
    `char_start` / `char_end` mark where in the note the match was found.
    """

    fact_id: str                 # e.g. "lbbb_present", "inability_to_exercise"
    label: str                   # human-readable
    present: bool
    quote: str = ""
    char_start: int = 0
    char_end: int = 0
    rule_matched: str = ""       # which pattern fired

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "label": self.label,
            "present": self.present,
            "quote": self.quote,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "rule_matched": self.rule_matched,
        }


def _check_pattern(
    note: str,
    *,
    fact_id: str,
    label: str,
    patterns: list[str],
    denial_patterns: list[str] = None,
    context_chars: int = 60,
) -> FactCheck:
    """Generic positive-pattern checker with optional denial guard."""
    if not note:
        return FactCheck(fact_id=fact_id, label=label, present=False)

    # Denial guard: if any denial phrase precedes/contains the fact phrase,
    # treat as not-present (e.g., "denies LBBB" → false).
    if denial_patterns:
        for dp in denial_patterns:
            if re.search(dp, note, re.IGNORECASE):
                return FactCheck(
                    fact_id=fact_id, label=label, present=False,
                    rule_matched=f"denied: {dp}",
                )

    for p in patterns:
        m = re.search(p, note, re.IGNORECASE)
        if m:
            start = max(0, m.start() - context_chars)
            end = min(len(note), m.end() + context_chars)
            return FactCheck(
                fact_id=fact_id, label=label, present=True,
                quote=note[start:end].strip(),
                char_start=m.start(), char_end=m.end(),
                rule_matched=p,
            )
    return FactCheck(fact_id=fact_id, label=label, present=False)


# Conduction abnormalities
def check_lbbb(note: str) -> FactCheck:
    return _check_pattern(
        note,
        fact_id="lbbb_present",
        label="Left bundle branch block (LBBB)",
        patterns=[
            r"\bLBBB\b",
            r"\bleft\s+bundle\s+branch\s+block\b",
        ],
        denial_patterns=[r"\b(?:no|denies|without)\s+LBBB\b",
                         r"\bno\s+left\s+bundle\b"],
    )


def check_rbbb(note: str) -> FactCheck:
    return _check_pattern(
        note,
        fact_id="rbbb_present",
        label="Right bundle branch block (RBBB)",
        patterns=[r"\bRBBB\b", r"\bright\s+bundle\s+branch\s+block\b"],
        denial_patterns=[r"\b(?:no|denies|without)\s+RBBB\b"],
    )


def check_paced_rhythm(note: str) -> FactCheck:
    return _check_pattern(
        note,
        fact_id="paced_rhythm",
        label="Paced rhythm / pacemaker",
        patterns=[
            r"\bpaced\s+rhythm\b",
            r"\bventricular\s+paced\b",
            r"\bA-?V\s+paced\b",
            r"\bpacemaker\s+rhythm\b",
            r"\bs/p\s+pacemaker\b",
            r"\bpacemaker\s+(?:in|placed)\b",
        ],
    )


# Exercise capacity
def check_inability_to_exercise(note: str) -> FactCheck:
    return _check_pattern(
        note,
        fact_id="inability_to_exercise",
        label="Inability to perform exercise stress test",
        patterns=[
            r"\b(?:unable|cannot|can'?t)\s+(?:to\s+)?(?:exercise|walk|ambulate|perform)\b",
            r"\btreadmill\s+(?:testing\s+)?cannot\s+be\s+performed\b",
            r"\bcannot\s+perform\s+treadmill\b",
            r"\bdeconditioned\b",
            r"\bwheelchair[\s-]?bound\b",
            r"\bnon[\s-]?ambulatory\b",
            r"\bbed[\s-]?bound\b",
            r"\bsevere(?:ly)?\s+limited\s+exercise\s+tolerance\b",
            r"\bpoor\s+exercise\s+tolerance\b",
            r"\bsevere(?:ly)?\s+arthrit",
        ],
        denial_patterns=[
            r"\bable\s+to\s+exercise\b",
            r"\bcan\s+walk\s+on\s+(?:a\s+)?treadmill\b",
        ],
    )


def check_can_exercise_adequately(note: str) -> FactCheck:
    return _check_pattern(
        note,
        fact_id="can_exercise_adequately",
        label="Patient can exercise adequately",
        patterns=[
            r"\bcan\s+(?:walk|exercise|ambulate)\s+on\s+(?:a\s+)?treadmill\b",
            r"\bcompleted\s+(?:a\s+)?treadmill\b",
            r"\bachieved?\s+target\s+heart\s+rate\b",
            r"\bachieved?\s+\d+\s*METs?\b",
            r"\bgood\s+exercise\s+tolerance\b",
            r"\bable\s+to\s+exercise\b",
        ],
    )


# Prior testing
def check_nondiagnostic_prior_testing(note: str) -> FactCheck:
    return _check_pattern(
        note,
        fact_id="nondiagnostic_prior_testing",
        label="Prior stress test was nondiagnostic / equivocal",
        patterns=[
            r"\bnon[\s-]?diagnostic\b",
            r"\bequivocal\b",
            r"\btechnically\s+limited\b",
            r"\binconclusive\b",
            r"\bsuboptimal\s+(?:study|images?|results?)\b",
            r"\battenuation\s+artifact\b",
            r"\bfalse[\s-]?positive\b",
        ],
    )


def check_attenuation_artifact(note: str) -> FactCheck:
    return _check_pattern(
        note,
        fact_id="attenuation_artifact",
        label="Attenuation artifact on prior imaging",
        patterns=[
            r"\battenuation\s+artifact\b",
            r"\bbreast\s+attenuation\b",
            r"\bdiaphragmatic\s+attenuation\b",
            r"\battenuation[\s-]?related\s+(?:defect|artifact)\b",
        ],
    )


# BMI
def check_bmi_above_35(note: str) -> FactCheck:
    """Numeric BMI extraction. Returns present=True iff a value ≥ 35
    is documented (with the captured number in `quote`)."""
    if not note:
        return FactCheck(fact_id="bmi_above_35", label="BMI ≥ 35", present=False)
    pat = re.compile(r"\bBMI\s*(?:of\s+|=\s*|:\s*|is\s+)?(\d{2,3}(?:\.\d)?)\b", re.IGNORECASE)
    for m in pat.finditer(note):
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        if value >= 35:
            start = max(0, m.start() - 30)
            end = min(len(note), m.end() + 30)
            return FactCheck(
                fact_id="bmi_above_35",
                label=f"BMI ≥ 35 (documented: {value})",
                present=True,
                quote=note[start:end].strip(),
                char_start=m.start(), char_end=m.end(),
                rule_matched=f"BMI value ≥ 35 ({value})",
            )
    return FactCheck(fact_id="bmi_above_35", label="BMI ≥ 35", present=False)


# CPT propagation
_CPT_TAG = re.compile(r"\bCPT\s*[#:]?\s*(\d{5})\b")
_CPT_BAREWORD = re.compile(r"\b(?:33|75|78|92|93)\d{3}\b")


def cpts_mentioned_in_note(note: str) -> list[str]:
    """All cardiology CPT codes referenced in the note. Explicit
    'CPT NNNNN' tags + cardiology-range barewords."""
    if not note:
        return []
    seen: list[str] = []
    for m in _CPT_TAG.finditer(note):
        code = m.group(1)
        if code not in seen:
            seen.append(code)
    for m in _CPT_BAREWORD.finditer(note):
        code = m.group(0)
        if code not in seen:
            seen.append(code)
    return seen


# ──────────────────────────────────────────────────────────────────────
# Comparator: cross-check independent facts vs chart vs reasoner
# ──────────────────────────────────────────────────────────────────────


# Mapping from independent fact → criterion code that should be MET
# when the fact is present + the criterion applies to the resolved CPT.
_FACT_TO_CRITERION: dict[str, list[str]] = {
    "lbbb_present":              ["ECG-001"],
    "paced_rhythm":              ["ECG-002"],
    "inability_to_exercise":     ["EX-001"],
    "nondiagnostic_prior_testing": ["NDX-001", "NDX-002", "NDX-003", "NDX-004"],
    "attenuation_artifact":      ["BMI-002"],
    "bmi_above_35":              ["BMI-001"],
}


# Chart bucket paths the comparator examines for "did the chart
# extractor also see this fact?" The check is loose — just looks for
# ANY string match in the bucket's serialized values.
_FACT_TO_CHART_PATHS: dict[str, list[str]] = {
    "lbbb_present":              ["ecg_findings", "additional_notes"],
    "paced_rhythm":              ["ecg_findings", "additional_notes"],
    "inability_to_exercise":     ["current_symptoms", "additional_notes"],
    "nondiagnostic_prior_testing": ["prior_stress_tests", "additional_notes"],
    "attenuation_artifact":      ["prior_stress_tests", "additional_notes"],
    "bmi_above_35":              ["active_comorbidities", "additional_notes"],
}


_FACT_KEYWORDS_IN_CHART: dict[str, list[str]] = {
    "lbbb_present": ["lbbb", "left bundle"],
    "paced_rhythm": ["paced", "pacemaker"],
    "inability_to_exercise": ["unable", "cannot", "deconditioned",
                               "wheelchair", "non-ambulatory", "treadmill cannot"],
    "nondiagnostic_prior_testing": ["non-diagnostic", "nondiagnostic",
                                    "equivocal", "technically limited",
                                    "inconclusive", "attenuation",
                                    "false positive", "suboptimal"],
    "attenuation_artifact": ["attenuation"],
    "bmi_above_35": ["bmi"],   # the comparator also checks numeric value separately
}


@dataclass
class FactComparison:
    """One row of the comparator audit log.

    Captures whether the fact was present in each pipeline stage:
    note, chart, reasoner. Used by the UI to show "what was checked
    and what each stage said."
    """

    fact: FactCheck
    present_in_chart: bool
    chart_evidence: str = ""       # which chart bucket carried the fact
    relevant_criteria: list[str] = field(default_factory=list)
    criterion_met_by_reasoner: bool | None = None  # None = criterion not in evaluated set

    def to_dict(self) -> dict:
        return {
            "fact": self.fact.to_dict(),
            "present_in_chart": self.present_in_chart,
            "chart_evidence": self.chart_evidence,
            "relevant_criteria": list(self.relevant_criteria),
            "criterion_met_by_reasoner": self.criterion_met_by_reasoner,
        }


@dataclass
class SafetyAuditLog:
    """Full audit log emitted by the SafetyVerifier — what was checked
    and what each stage said about it."""

    comparisons: list[FactComparison] = field(default_factory=list)
    cpts_in_note: list[str] = field(default_factory=list)
    cpt_in_chart: str = ""
    note_chart_cpt_mismatch: bool = False

    def to_dict(self) -> dict:
        return {
            "comparisons": [c.to_dict() for c in self.comparisons],
            "cpts_in_note": list(self.cpts_in_note),
            "cpt_in_chart": self.cpt_in_chart,
            "note_chart_cpt_mismatch": self.note_chart_cpt_mismatch,
        }


# ──────────────────────────────────────────────────────────────────────
# Top-level verifier
# ──────────────────────────────────────────────────────────────────────


_FACT_CHECKERS = (
    ("lbbb_present", check_lbbb),
    ("paced_rhythm", check_paced_rhythm),
    ("inability_to_exercise", check_inability_to_exercise),
    ("can_exercise_adequately", check_can_exercise_adequately),
    ("nondiagnostic_prior_testing", check_nondiagnostic_prior_testing),
    ("attenuation_artifact", check_attenuation_artifact),
    ("bmi_above_35", check_bmi_above_35),
    ("rbbb_present", check_rbbb),
)


def _serialize_chart_bucket(chart_data: dict, bucket_path: str) -> str:
    """Flatten a chart bucket's contents into a lowercase string for
    keyword presence checks."""
    val = chart_data.get(bucket_path)
    if val is None:
        return ""
    if isinstance(val, str):
        return val.lower()
    if isinstance(val, list):
        flat: list[str] = []
        for item in val:
            if isinstance(item, str):
                flat.append(item)
            elif isinstance(item, dict):
                flat.extend(str(v) for v in item.values() if v is not None)
        return " ".join(flat).lower()
    if isinstance(val, dict):
        return " ".join(str(v) for v in val.values() if v is not None).lower()
    return str(val).lower()


def _chart_has_fact(fact_id: str, chart_data: dict) -> tuple[bool, str]:
    """Loose match — does ANY chart bucket contain a keyword for this fact?"""
    keywords = _FACT_KEYWORDS_IN_CHART.get(fact_id, [])
    if not keywords:
        return False, ""
    for path in _FACT_TO_CHART_PATHS.get(fact_id, []):
        flat = _serialize_chart_bucket(chart_data, path)
        if not flat:
            continue
        for kw in keywords:
            if kw.lower() in flat:
                return True, path
    # Numeric BMI special case
    if fact_id == "bmi_above_35":
        # Search active_comorbidities + additional_notes for "BMI <num>" with num >= 35
        haystack = _serialize_chart_bucket(chart_data, "active_comorbidities") + " " \
                   + _serialize_chart_bucket(chart_data, "additional_notes")
        m = re.search(r"bmi\s*(?:of\s+|=\s*|:\s*)?(\d{2,3}(?:\.\d)?)", haystack, re.IGNORECASE)
        if m:
            try:
                if float(m.group(1)) >= 35:
                    return True, "active_comorbidities/additional_notes"
            except ValueError:
                pass
    return False, ""


def _reasoner_met_set(reasoner_summary: dict) -> set[str]:
    """Extract the set of criterion codes the reasoner marked met.

    reasoner_summary may carry criteria_met directly OR
    criteria_evaluated with status. Forward-compatible.
    """
    out: set[str] = set()
    for entry in reasoner_summary.get("criteria_met") or []:
        if isinstance(entry, str):
            out.add(entry)
        elif isinstance(entry, dict) and entry.get("code"):
            out.add(entry["code"])
    for entry in reasoner_summary.get("criteria_evaluated") or []:
        if isinstance(entry, dict) and entry.get("status") == "met":
            code = entry.get("code")
            if code:
                out.add(code)
    return out


def _reasoner_evaluated_set(reasoner_summary: dict) -> set[str]:
    """Set of criterion codes the reasoner evaluated (met or not_met)."""
    out: set[str] = set()
    for entry in reasoner_summary.get("criteria_met") or []:
        if isinstance(entry, str):
            out.add(entry)
        elif isinstance(entry, dict) and entry.get("code"):
            out.add(entry["code"])
    for entry in reasoner_summary.get("criteria_not_met") or []:
        if isinstance(entry, str):
            out.add(entry)
        elif isinstance(entry, dict) and entry.get("code"):
            out.add(entry["code"])
    for entry in reasoner_summary.get("criteria_evaluated") or []:
        if isinstance(entry, dict) and entry.get("code"):
            out.add(entry["code"])
    return out


def run_safety_verification(
    *,
    raw_note: str,
    chart_data: dict,
    reasoner_summary: dict | None = None,
    resolved_cpt: str = "",
) -> SafetyAuditLog:
    """Run every independent fact check and build a comparator audit log.

    Pure function — no side effects, no IO. Safe to call multiple times.
    """
    reasoner_summary = reasoner_summary or {}
    met = _reasoner_met_set(reasoner_summary)
    evaluated = _reasoner_evaluated_set(reasoner_summary)

    comparisons: list[FactComparison] = []
    for fact_id, fn in _FACT_CHECKERS:
        try:
            fact = fn(raw_note)
        except Exception as e:
            logger.warning("Safety check %s failed: %s", fact_id, e)
            continue

        chart_present, chart_evidence = _chart_has_fact(fact_id, chart_data)

        relevant = _FACT_TO_CRITERION.get(fact_id, [])
        crit_met: bool | None = None
        if relevant:
            # If at least one relevant criterion was evaluated by the
            # reasoner, report whether ANY of them was met.
            if any(c in evaluated for c in relevant):
                crit_met = any(c in met for c in relevant)
            else:
                crit_met = None  # not evaluated; can't compare

        comparisons.append(FactComparison(
            fact=fact,
            present_in_chart=chart_present,
            chart_evidence=chart_evidence,
            relevant_criteria=relevant,
            criterion_met_by_reasoner=crit_met,
        ))

    cpts_in_note = cpts_mentioned_in_note(raw_note)
    cpt_in_chart = (chart_data.get("procedure_code") or "").strip()
    note_chart_cpt_mismatch = bool(
        cpts_in_note and cpt_in_chart and cpt_in_chart not in cpts_in_note
    )

    return SafetyAuditLog(
        comparisons=comparisons,
        cpts_in_note=cpts_in_note,
        cpt_in_chart=cpt_in_chart,
        note_chart_cpt_mismatch=note_chart_cpt_mismatch,
    )
