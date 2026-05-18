"""Routing logic — pick the right PA form for a case context.

Three layers:
  1. Hard filter — payer, state, plan_type must match (with ANY wildcards
     for fallback rows). CPT match weighted heavily but not required.
  2. Score remaining candidates against the case (CPT match, category fit,
     state proximity).
  3. If 1 candidate → confident pick. If 0 → fallback to portal recommendation.
     If >1 ambiguous → return ranked list, top pick marked with confidence
     reflecting the ambiguity.

No LLM in the routing decision today — the rules + YAML catalog are
deterministic enough for UHC's public forms list. The LLM is reserved
for downstream form-field mapping when a PDF is actually filled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


_FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "payer_forms"


@dataclass
class CaseContext:
    payer: str                  # "UnitedHealthcare" / "UHC" / etc.
    state: str = ""             # 2-letter state code or empty
    plan_type: str = ""         # Commercial / Medicare Advantage / Medicaid
    cpt_code: str = ""          # primary CPT
    primary_icd10: str = ""     # primary diagnosis code
    test_type: str = ""         # human-readable hint (SPECT MPI, PET, echo, etc.)


@dataclass
class FormCandidate:
    form_id: str
    name: str
    state: str
    plan_type: str
    pdf_url: str
    portal_url: str = ""
    notes: str = ""
    score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    is_fallback: bool = False


@dataclass
class RoutingResult:
    case: CaseContext
    payer_recognized: bool
    top_pick: FormCandidate | None
    confidence: str             # high / medium / low / portal_fallback / no_match
    candidates: list[FormCandidate] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict:
        out = asdict(self)
        if self.top_pick is None:
            out["top_pick"] = None
        return out


# ── Catalog loading ────────────────────────────────────────────────────


_CATALOG_CACHE: dict[str, dict[str, Any]] = {}


def load_catalog(payer_id: str, fixtures_dir: Path | str | None = None) -> dict[str, Any] | None:
    """Load one payer's catalog by file stem. Cached per process."""
    base = Path(fixtures_dir) if fixtures_dir else _FIXTURES_DIR
    pid = payer_id.lower()
    if pid in _CATALOG_CACHE:
        return _CATALOG_CACHE[pid]
    path = base / f"{pid}.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text())
        _CATALOG_CACHE[pid] = data
        return data
    except Exception as e:
        logger.warning("Failed to load catalog %s: %s", path, e)
        return None


def list_payers(fixtures_dir: Path | str | None = None) -> list[str]:
    """List all payer catalog file stems."""
    base = Path(fixtures_dir) if fixtures_dir else _FIXTURES_DIR
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))


def _resolve_payer_id(payer_name: str, fixtures_dir: Path | str | None = None) -> str | None:
    """Match a free-text payer name (e.g. 'United Healthcare') to a
    catalog id (e.g. 'uhc'). Tries direct file match, then aliases."""
    base = Path(fixtures_dir) if fixtures_dir else _FIXTURES_DIR
    normalized = payer_name.strip().lower().replace(" ", "").replace("-", "")
    for path in base.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text())
            primary = (data.get("payer") or "").lower().replace(" ", "").replace("-", "")
            aliases = [a.lower().replace(" ", "").replace("-", "")
                       for a in (data.get("payer_aliases") or [])]
            if normalized == primary or normalized in aliases:
                return path.stem
        except Exception:
            continue
    return None


# ── Routing ────────────────────────────────────────────────────────────


def route_case(case: CaseContext, *, fixtures_dir: Path | str | None = None) -> RoutingResult:
    """Run a case through the catalog and return ranked form candidates
    plus the top pick + confidence."""
    payer_id = _resolve_payer_id(case.payer, fixtures_dir=fixtures_dir)
    if not payer_id:
        return RoutingResult(
            case=case, payer_recognized=False, top_pick=None,
            confidence="no_match",
            explanation=f"No catalog for payer '{case.payer}'. "
                        f"Available payers: {list_payers(fixtures_dir=fixtures_dir) or '(none)'}.",
        )

    catalog = load_catalog(payer_id, fixtures_dir=fixtures_dir)
    if not catalog or not catalog.get("forms"):
        return RoutingResult(
            case=case, payer_recognized=True, top_pick=None,
            confidence="no_match",
            explanation=f"Catalog for '{case.payer}' is empty.",
        )

    # Score each form
    scored: list[FormCandidate] = []
    for form in catalog["forms"]:
        score, reasons = _score_form(form, case)
        if score < 0:
            continue  # hard-filter rejected
        scored.append(FormCandidate(
            form_id=form.get("id", ""),
            name=form.get("name", ""),
            state=form.get("state", ""),
            plan_type=form.get("plan_type", ""),
            pdf_url=form.get("pdf_url", ""),
            portal_url=form.get("portal_url", ""),
            notes=form.get("notes", ""),
            score=score,
            match_reasons=reasons,
            is_fallback=bool(form.get("fallback")),
        ))

    scored.sort(key=lambda c: c.score, reverse=True)

    # Separate the fallback so it doesn't sneak past stronger matches.
    non_fallback = [c for c in scored if not c.is_fallback]
    fallback_only = [c for c in scored if c.is_fallback]

    if not non_fallback and fallback_only:
        top = fallback_only[0]
        return RoutingResult(
            case=case, payer_recognized=True, top_pick=top, candidates=scored,
            confidence="portal_fallback",
            explanation=f"No state-specific UHC PA form matches {case.state or 'this case'}/"
                        f"{case.cpt_code or 'this CPT'}. Recommend portal submission with a "
                        f"pre-built portal-ready packet that has the clinical answers and "
                        f"cited evidence ready to paste.",
        )

    if not non_fallback:
        return RoutingResult(
            case=case, payer_recognized=True, top_pick=None, candidates=[],
            confidence="no_match",
            explanation=f"No catalog form matched {case.state}/{case.plan_type}/{case.cpt_code}.",
        )

    top = non_fallback[0]
    # Confidence based on score margin and CPT match
    cpt_matched = any("cpt_match" in r for r in top.match_reasons)
    state_matched = any("state_match" in r for r in top.match_reasons)
    if cpt_matched and state_matched:
        conf = "high"
    elif state_matched:
        conf = "medium"
    else:
        conf = "low"

    explanation = (
        f"Routed {case.state or '?'}/{case.plan_type or '?'} {case.cpt_code or 'this CPT'} "
        f"to {top.name}. Match reasons: {', '.join(top.match_reasons)}."
    )
    return RoutingResult(
        case=case, payer_recognized=True, top_pick=top, candidates=scored,
        confidence=conf, explanation=explanation,
    )


def _score_form(form: dict, case: CaseContext) -> tuple[float, list[str]]:
    """Score a form against the case. Returns (score, [reasons]).
    Score < 0 means hard-filter rejected; >= 0 means candidate."""
    reasons: list[str] = []
    score = 0.0

    form_state = (form.get("state") or "").upper()
    case_state = (case.state or "").upper()
    state_any = form_state in ("ANY", "")
    if form_state and not state_any:
        if form_state == case_state:
            score += 50
            reasons.append(f"state_match:{form_state}")
        else:
            # Hard reject — state-specific form for a different state
            return -1, [f"state_mismatch:form={form_state},case={case_state}"]
    elif state_any:
        reasons.append("state_any")
        score += 5

    # Plan type match
    form_plan = (form.get("plan_type") or "").lower()
    case_plan = (case.plan_type or "").lower()
    if form_plan in ("any", ""):
        score += 2
    elif case_plan and form_plan and form_plan == case_plan:
        score += 15
        reasons.append(f"plan_match:{form_plan}")
    elif case_plan and form_plan and form_plan != case_plan:
        # Soft penalty — wrong plan type. Don't reject outright; lots of
        # payer forms aren't strictly tagged by plan and the user often
        # leaves plan_type blank.
        score -= 5

    # CPT match
    cpts = [str(c) for c in (form.get("cpt_codes") or [])]
    if case.cpt_code and case.cpt_code in cpts:
        score += 40
        reasons.append(f"cpt_match:{case.cpt_code}")
    elif not cpts:
        # Form with no CPT list = fallback or catch-all
        score += 1
        reasons.append("cpt_open")
    elif case.cpt_code:
        # CPT was specified but doesn't match this form's list
        score -= 10

    # Category fit — soft text-match against test_type
    cats = [str(c).lower() for c in (form.get("categories") or [])]
    test_type = (case.test_type or "").lower()
    if test_type and cats:
        for cat in cats:
            if cat in test_type or test_type in cat or any(
                tok in cat for tok in test_type.split() if len(tok) >= 3
            ):
                score += 8
                reasons.append(f"category_hit:{cat}")
                break

    return score, reasons
