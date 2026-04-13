"""Regression tests for Peter's CPT gating enforcer.

Peter's core diagnostic: "the system is not consistently enforcing that every
criterion must be evaluated for every case." These tests verify the
_enforce_cpt_gating() function he suggested — the deterministic post-processor
that guarantees:
  1. Every applicable criterion has an entry (no silent skipping)
  2. Applicable criteria cannot be marked not_applicable
  3. Missing criteria default to not_met (21)
"""

from cardioauth.agents.unified_reasoner import _enforce_cpt_gating
from cardioauth.taxonomy.taxonomy import get_criteria_for_procedure


def _criteria_for(cpt):
    """Helper — get applicable criteria list."""
    return get_criteria_for_procedure(cpt, "UnitedHealthcare")


# ── Completeness: no silent skipping ──


def test_enforcer_fills_missing_criteria_as_not_met():
    """LLM returns 2 criteria, enforcer adds not_met for the rest."""
    applicable = _criteria_for("78492")
    assert len(applicable) > 2
    # LLM only returned entries for the first 2
    llm_output = [
        {"code": applicable[0].code, "status": "met", "evidence_quote": "foo"},
        {"code": applicable[1].code, "status": "not_met", "gap": "bar"},
    ]
    result = _enforce_cpt_gating(llm_output, applicable)
    # Every applicable criterion must now have an entry
    result_codes = {r["code"] for r in result}
    assert result_codes == {c.code for c in applicable}
    # The filled-in ones should be not_met
    missing_filled = [r for r in result if r.get("_enforced") == "missing_filled"]
    assert len(missing_filled) == len(applicable) - 2
    assert all(r["status"] == "not_met" for r in missing_filled)


def test_enforcer_returns_exactly_applicable_count():
    """Output count equals applicable count regardless of LLM output."""
    applicable = _criteria_for("78492")
    result = _enforce_cpt_gating([], applicable)  # LLM returned nothing
    assert len(result) == len(applicable)


# ── Coercion: not_applicable → not_met ──


def test_enforcer_coerces_not_applicable_to_not_met():
    """The CPT filter upstream already decided applicability. not_applicable is invalid."""
    applicable = _criteria_for("78492")
    code = applicable[0].code
    llm_output = [
        {"code": code, "status": "not_applicable", "reasoning": "doesn't apply"},
    ]
    result = _enforce_cpt_gating(llm_output, applicable)
    target = next(r for r in result if r["code"] == code)
    assert target["status"] == "not_met", f"expected not_met, got {target['status']}"
    assert "coerced_from" in target.get("_enforced", "")


def test_enforcer_coerces_empty_status_to_not_met():
    """Unknown/empty status coerces to not_met."""
    applicable = _criteria_for("78492")
    code = applicable[0].code
    llm_output = [
        {"code": code, "status": "", "reasoning": "idk"},
    ]
    result = _enforce_cpt_gating(llm_output, applicable)
    target = next(r for r in result if r["code"] == code)
    assert target["status"] == "not_met"


def test_enforcer_coerces_garbage_status_to_not_met():
    """Any status that isn't met/not_met is forced to not_met."""
    applicable = _criteria_for("78492")
    code = applicable[0].code
    llm_output = [
        {"code": code, "status": "maybe", "reasoning": "..."},
    ]
    result = _enforce_cpt_gating(llm_output, applicable)
    target = next(r for r in result if r["code"] == code)
    assert target["status"] == "not_met"


# ── Preservation of valid results ──


def test_enforcer_preserves_met_entries():
    """If LLM returned met with good evidence, keep it."""
    applicable = _criteria_for("78492")
    code = applicable[0].code
    llm_output = [
        {"code": code, "status": "met", "evidence_quote": "solid evidence",
         "confidence": 0.9, "reasoning": "clearly met"},
    ]
    result = _enforce_cpt_gating(llm_output, applicable)
    target = next(r for r in result if r["code"] == code)
    assert target["status"] == "met"
    assert target["evidence_quote"] == "solid evidence"
    assert target.get("confidence") == 0.9
    assert "_enforced" not in target


def test_enforcer_preserves_not_met_entries():
    applicable = _criteria_for("78492")
    code = applicable[0].code
    llm_output = [
        {"code": code, "status": "not_met", "gap": "no evidence"},
    ]
    result = _enforce_cpt_gating(llm_output, applicable)
    target = next(r for r in result if r["code"] == code)
    assert target["status"] == "not_met"
    assert target["gap"] == "no evidence"


def test_enforcer_normalizes_case():
    """Accept 'MET' or 'Met' → 'met'."""
    applicable = _criteria_for("78492")
    code = applicable[0].code
    llm_output = [{"code": code, "status": "MET", "evidence_quote": "x"}]
    result = _enforce_cpt_gating(llm_output, applicable)
    target = next(r for r in result if r["code"] == code)
    assert target["status"] == "met"


# ── CPT applicability is determined upstream ──


def test_criteria_filtered_by_cpt():
    """get_criteria_for_procedure must only return CPT-applicable criteria."""
    pet_criteria = _criteria_for("78492")
    tavr_criteria = _criteria_for("33361")
    # PET and TAVR have different criterion sets
    pet_codes = {c.code for c in pet_criteria}
    tavr_codes = {c.code for c in tavr_criteria}
    assert pet_codes != tavr_codes

    # TAVR-specific criteria (STS-PROM, Heart Team, CTA) should NOT be in PET list
    assert "RISK-001" not in pet_codes  # STS-PROM is TAVR-only
    assert "HT-001" not in pet_codes    # Heart Team is TAVR-only
    assert "IMG-001" not in pet_codes   # Coronary anatomy for TAVR-only
    assert "IMG-002" not in pet_codes   # CTA for TAVR-only


def test_cpt_specific_criteria_ndx002_for_pet():
    """NDX-002 (submaximal HR) applies to PET but not TAVR."""
    pet_codes = {c.code for c in _criteria_for("78492")}
    tavr_codes = {c.code for c in _criteria_for("33361")}
    assert "NDX-002" in pet_codes
    assert "NDX-002" not in tavr_codes


def test_af_ablation_has_anticoagulation_criteria():
    """CPT 93656 (AF Ablation) must include ANTI-001 (CHA2DS2-VASc) and ANTI-002 (TEE)."""
    codes = {c.code for c in _criteria_for("93656")}
    assert "ANTI-001" in codes
    assert "ANTI-002" in codes
    assert "MED-003" in codes  # Failed antiarrhythmic


# ── Regression guard: Peter's specific finding ──


def test_peter_no_silent_skipping():
    """The exact behavior Peter documented: LLM skips several applicable criteria."""
    applicable = _criteria_for("78492")
    # LLM only addressed half the criteria
    half = applicable[:len(applicable) // 2]
    llm_output = [{"code": c.code, "status": "met"} for c in half]

    result = _enforce_cpt_gating(llm_output, applicable)

    # All applicable criteria must have an entry (no "14 missing")
    assert len(result) == len(applicable)
    # None should have status="not_applicable"
    assert all(r["status"] in ("met", "not_met") for r in result), \
        f"Found not_applicable in: {[r for r in result if r['status'] not in ('met', 'not_met')]}"
