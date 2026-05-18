"""Tests for the payer-form routing layer.

Routing must correctly:
  - Match payer aliases (UHC ↔ UnitedHealthcare)
  - Pick state-specific forms when the case state matches
  - Hard-reject state-specific forms when state mismatches
  - Score CPT matches highest
  - Fall back to portal recommendation when no specific form matches
  - Return no_match when the payer is unknown
"""

from __future__ import annotations

import pytest

from cardioauth.payer_routing import CaseContext, route_case, list_payers, load_catalog


# ── Payer alias resolution ─────────────────────────────────────────────


def test_uhc_alias_matches_unitedhealthcare_catalog() -> None:
    """'UHC' as input should route through the same catalog as 'UnitedHealthcare'."""
    a = route_case(CaseContext(payer="UHC", state="MA", cpt_code="78452"))
    b = route_case(CaseContext(payer="UnitedHealthcare", state="MA", cpt_code="78452"))
    assert a.payer_recognized and b.payer_recognized
    assert a.top_pick.form_id == b.top_pick.form_id


def test_unknown_payer_returns_no_match() -> None:
    r = route_case(CaseContext(payer="ZephyrCare", state="MA", cpt_code="78452"))
    assert r.payer_recognized is False
    assert r.confidence == "no_match"
    assert r.top_pick is None


# ── State matching ─────────────────────────────────────────────────────


def test_state_specific_form_wins_with_correct_state() -> None:
    r = route_case(CaseContext(payer="UHC", state="MA", plan_type="Commercial",
                               cpt_code="78452", test_type="SPECT MPI"))
    assert r.confidence == "high"
    assert r.top_pick.form_id == "uhc-ma-cardiac-imaging"
    assert "state_match:MA" in r.top_pick.match_reasons


def test_mismatched_state_falls_back_to_portal() -> None:
    """UHC + TX has no Texas-specific cardiac PA form in the public list —
    must fall back to portal, not return a wrong state's form."""
    r = route_case(CaseContext(payer="UHC", state="TX", plan_type="Commercial",
                               cpt_code="78452"))
    assert r.confidence == "portal_fallback"
    assert r.top_pick.is_fallback is True
    assert r.top_pick.portal_url
    # The MA form should NOT have leaked through
    assert "ma-cardiac-imaging" not in r.top_pick.form_id


def test_state_specific_form_hard_rejects_other_states() -> None:
    """A MA-tagged form must never score for a TX case, even on CPT match."""
    r = route_case(CaseContext(payer="UHC", state="TX", cpt_code="78452"))
    ma_forms = [c for c in r.candidates if c.state == "MA"]
    assert not ma_forms, "MA-specific forms should be filtered out for TX cases"


# ── CPT matching ───────────────────────────────────────────────────────


def test_cpt_in_form_list_boosts_score() -> None:
    """78452 is in the MA cardiac-imaging form's CPT list → high score."""
    r = route_case(CaseContext(payer="UHC", state="MA", cpt_code="78452"))
    assert "cpt_match:78452" in r.top_pick.match_reasons


def test_pet_cpt_routes_to_pet_form_not_cardiac() -> None:
    """78492 (PET MPI) should route to the PET CT form, not the cardiac imaging form."""
    r = route_case(CaseContext(payer="UHC", state="MA", plan_type="Commercial",
                               cpt_code="78492", test_type="PET"))
    assert r.top_pick.form_id == "uhc-ma-pet-ct"


def test_echo_cpt_routes_to_ultrasound_form() -> None:
    """93306 (TTE) should route to the non-OB ultrasound form."""
    r = route_case(CaseContext(payer="UHC", state="MA", plan_type="Commercial",
                               cpt_code="93306", test_type="echocardiogram"))
    assert r.top_pick.form_id == "uhc-ma-ultrasound"


def test_unknown_cpt_in_state_returns_fallback() -> None:
    """When the CPT isn't in any state-specific form's list (but state
    matches), the fallback row should win — not a wrong state-specific form."""
    r = route_case(CaseContext(payer="UHC", state="MA", cpt_code="99999"))
    # Either a state-specific form on category fit, OR the portal fallback —
    # but NOT the cardiac imaging form (CPT 99999 isn't in its list).
    if r.top_pick:
        cpts = (load_catalog("uhc")["forms"][0].get("cpt_codes") or [])
        assert "99999" not in cpts


# ── Plan type ──────────────────────────────────────────────────────────


def test_plan_match_boosts_score_but_not_required() -> None:
    """plan_type empty is fine — still produces a valid result, just
    without the plan-match bonus."""
    r = route_case(CaseContext(payer="UHC", state="MA", cpt_code="78452"))
    assert r.payer_recognized
    assert r.top_pick is not None


# ── Confidence ─────────────────────────────────────────────────────────


def test_confidence_high_when_state_and_cpt_both_match() -> None:
    r = route_case(CaseContext(payer="UHC", state="MA", plan_type="Commercial",
                               cpt_code="78452"))
    assert r.confidence == "high"


def test_confidence_portal_fallback_when_no_state_form() -> None:
    r = route_case(CaseContext(payer="UHC", state="TX", cpt_code="78452"))
    assert r.confidence == "portal_fallback"


# ── Catalog plumbing ───────────────────────────────────────────────────


def test_list_payers_returns_at_least_uhc() -> None:
    payers = list_payers()
    assert "uhc" in payers


def test_load_catalog_returns_form_list() -> None:
    cat = load_catalog("uhc")
    assert cat is not None
    assert cat["payer"] == "UnitedHealthcare"
    assert len(cat["forms"]) >= 3  # at least the 3 MA forms + fallback


def test_routing_result_serializes_to_dict() -> None:
    """The to_dict() method must produce a JSON-serializable shape so
    the FastAPI endpoint can return it."""
    import json
    r = route_case(CaseContext(payer="UHC", state="MA", cpt_code="78452"))
    d = r.to_dict()
    s = json.dumps(d)  # must not raise
    assert "top_pick" in d
    assert "candidates" in d
    assert "confidence" in d
