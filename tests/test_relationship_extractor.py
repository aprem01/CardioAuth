"""Regression tests for the RelationshipExtractor rule-based layer.

Every one of the 16 canonical relationships in SEED_RELATIONSHIPS should
fire on expected input and NOT fire on unrelated input. These are fast,
deterministic, no API calls — run on every commit.
"""

from cardioauth.agents.relationship_extractor import extract_relationships_rule_based


# ── EX-001 ──

def test_ex001_unable_to_tst_dyspnea_obesity():
    """C1-style: 'Unable to do TST due to dyspnea and obesity'."""
    rels = extract_relationships_rule_based("Unable to do TST due to dyspnea and obesity")
    codes = {r.supports_criterion for r in rels}
    assert "EX-001" in codes


def test_ex001_cannot_exercise():
    rels = extract_relationships_rule_based("Patient cannot exercise due to severe osteoarthritis")
    codes = {r.supports_criterion for r in rels}
    assert "EX-001" in codes


def test_ex001_inability_to_exercise():
    rels = extract_relationships_rule_based("Documented inability to exercise adequately")
    codes = {r.supports_criterion for r in rels}
    assert "EX-001" in codes


def test_ex001_exercise_limitation():
    rels = extract_relationships_rule_based("Exercise limitation due to severe COPD")
    codes = {r.supports_criterion for r in rels}
    assert "EX-001" in codes


def test_ex001_does_not_fire_on_normal_exercise():
    rels = extract_relationships_rule_based("Patient exercises daily, runs 3 miles")
    codes = {r.supports_criterion for r in rels}
    assert "EX-001" not in codes


# ── NDX-001 ──

def test_ndx001_attenuation_artifact():
    rels = extract_relationships_rule_based("Prior SPECT showed attenuation artifact")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-001" in codes


def test_ndx001_soft_tissue_attenuation():
    rels = extract_relationships_rule_based("Soft-tissue attenuation limiting interpretation")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-001" in codes


def test_ndx001_breast_attenuation():
    rels = extract_relationships_rule_based("Breast attenuation artifact noted")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-001" in codes


def test_ndx001_equivocal_spect():
    rels = extract_relationships_rule_based("Prior SPECT was equivocal")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-001" in codes


def test_ndx001_non_diagnostic_stress():
    rels = extract_relationships_rule_based("Stress test non-diagnostic")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-001" in codes


# ── NDX-002 (submaximal HR) ──

def test_ndx002_submaximal_hr():
    rels = extract_relationships_rule_based("Submaximal HR achieved, only 68% of MPHR")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-002" in codes


def test_ndx002_percent_mphr():
    rels = extract_relationships_rule_based("Achieved 72% of maximum predicted heart rate")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-002" in codes


def test_ndx002_failed_to_achieve_target_hr():
    rels = extract_relationships_rule_based("Failed to achieve target heart rate")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-002" in codes


# ── ECG-001 (LBBB) ──

def test_ecg001_lbbb_detected():
    rels = extract_relationships_rule_based("LBBB on baseline ECG")
    codes = {r.supports_criterion for r in rels}
    assert "ECG-001" in codes


def test_ecg001_left_bundle_branch_block():
    rels = extract_relationships_rule_based("Left bundle branch block documented")
    codes = {r.supports_criterion for r in rels}
    assert "ECG-001" in codes


# ── ECG-002 (paced rhythm) ──

def test_ecg002_paced_rhythm():
    rels = extract_relationships_rule_based("Paced rhythm on ECG")
    codes = {r.supports_criterion for r in rels}
    assert "ECG-002" in codes


def test_ecg002_ventricular_pacing():
    rels = extract_relationships_rule_based("Ventricular pacing noted")
    codes = {r.supports_criterion for r in rels}
    assert "ECG-002" in codes


# ── BMI-001 ──

def test_bmi001_bmi_38():
    rels = extract_relationships_rule_based("BMI 38 with obesity")
    codes = {r.supports_criterion for r in rels}
    assert "BMI-001" in codes


def test_bmi001_morbidly_obese():
    rels = extract_relationships_rule_based("Morbidly obese patient")
    codes = {r.supports_criterion for r in rels}
    assert "BMI-001" in codes


def test_bmi001_does_not_fire_normal_bmi():
    rels = extract_relationships_rule_based("BMI 24, healthy weight")
    codes = {r.supports_criterion for r in rels}
    assert "BMI-001" not in codes


# ── NDX-004 (technically limited echo) ──

def test_ndx004_technically_limited_echo():
    rels = extract_relationships_rule_based("TTE technically limited due to body habitus")
    codes = {r.supports_criterion for r in rels}
    assert "NDX-004" in codes


# ── MED-001 (failed medical therapy) ──

def test_med001_failed_medical_therapy():
    rels = extract_relationships_rule_based("Failed maximally tolerated medical therapy")
    codes = {r.supports_criterion for r in rels}
    assert "MED-001" in codes


def test_med001_gdmt_for_weeks():
    """Peter's specific finding: 'on GDMT for 8 weeks' should trigger MED-001."""
    rels = extract_relationships_rule_based("On GDMT for 8 weeks with persistent symptoms")
    codes = {r.supports_criterion for r in rels}
    assert "MED-001" in codes


def test_med001_maximal_gdmt_6_months():
    rels = extract_relationships_rule_based("Despite maximal GDMT x 6 months")
    codes = {r.supports_criterion for r in rels}
    assert "MED-001" in codes


def test_med001_persistent_symptoms_on_gdmt():
    rels = extract_relationships_rule_based("Persistent symptoms despite optimal GDMT")
    codes = {r.supports_criterion for r in rels}
    assert "MED-001" in codes


# ── SX-004 (functional class) ──

def test_sx004_nyha_class():
    rels = extract_relationships_rule_based("NYHA Class III heart failure")
    codes = {r.supports_criterion for r in rels}
    assert "SX-004" in codes


def test_sx004_ccs_class():
    rels = extract_relationships_rule_based("CCS Class II angina")
    codes = {r.supports_criterion for r in rels}
    assert "SX-004" in codes


def test_sx004_ehra():
    rels = extract_relationships_rule_based("EHRA Class IIb symptoms")
    codes = {r.supports_criterion for r in rels}
    assert "SX-004" in codes


# ── DOC-001 (office note / consultation note) ──

def test_doc001_office_note():
    rels = extract_relationships_rule_based("Office note documents indication")
    codes = {r.supports_criterion for r in rels}
    assert "DOC-001" in codes


def test_doc001_consultation_note():
    rels = extract_relationships_rule_based("Consultation note attached")
    codes = {r.supports_criterion for r in rels}
    assert "DOC-001" in codes


def test_doc001_progress_note():
    rels = extract_relationships_rule_based("Progress note with full assessment")
    codes = {r.supports_criterion for r in rels}
    assert "DOC-001" in codes


def test_doc001_cardiology_consult():
    rels = extract_relationships_rule_based("Cardiology consult completed")
    codes = {r.supports_criterion for r in rels}
    assert "DOC-001" in codes


# ── SX-001 (new or worsening symptoms) ──

def test_sx001_new_symptoms():
    rels = extract_relationships_rule_based("New onset chest pain over past month")
    codes = {r.supports_criterion for r in rels}
    assert "SX-001" in codes


def test_sx001_worsening_symptoms():
    rels = extract_relationships_rule_based("Progressive worsening dyspnea")
    codes = {r.supports_criterion for r in rels}
    assert "SX-001" in codes


# ── SX-003 (anginal symptoms) ──

def test_sx003_exertional_angina():
    rels = extract_relationships_rule_based("Exertional angina on walking upstairs")
    codes = {r.supports_criterion for r in rels}
    assert "SX-003" in codes


def test_sx003_chest_pain_with_exertion():
    rels = extract_relationships_rule_based("Chest pain with exertion, relieved by rest")
    codes = {r.supports_criterion for r in rels}
    assert "SX-003" in codes


# ── Peter's 3 specific bugs (the "must never regress" tests) ──

def test_peter_c1_ex001_dyspnea_obesity_chain():
    """Regression guard for Peter's C1: 'Unable to do TST due to dyspnea and obesity'."""
    note = "Unable to do TST due to dyspnea and obesity"
    rels = extract_relationships_rule_based(note)
    codes = {r.supports_criterion for r in rels}
    assert "EX-001" in codes, f"C1 EX-001 regression — rels: {codes}"


def test_peter_c4_attenuation_artifact_non_diagnostic():
    """Regression guard for Peter's C4: prior SPECT attenuation artifact."""
    note = "Prior SPECT limited by breast attenuation artifact and non-diagnostic for ischemia"
    rels = extract_relationships_rule_based(note)
    codes = {r.supports_criterion for r in rels}
    assert "NDX-001" in codes, f"C4 NDX-001 regression — rels: {codes}"


def test_peter_full_office_note_detection():
    """Regression guard for DOC-001 being missed 5/5 in Peter's v2."""
    note = "CARDIOLOGY OFFICE NOTE\n67M with CAD. Office visit for PA evaluation."
    rels = extract_relationships_rule_based(note)
    codes = {r.supports_criterion for r in rels}
    assert "DOC-001" in codes, f"DOC-001 regression — rels: {codes}"


# ── Comprehensive coverage test ──

def test_all_peter_c1_criteria_detected():
    """Peter's C1 should surface EX-001, NDX-002, BMI-001, SX-004, DOC-001."""
    note = (
        "CARDIOLOGY OFFICE NOTE\n"
        "67M with CAD. CCS Class III angina. Unable to do TST due to dyspnea "
        "and obesity (BMI 38). Prior ETT reached only 68% MPHR and was non-diagnostic."
    )
    rels = extract_relationships_rule_based(note)
    codes = {r.supports_criterion for r in rels}
    expected = {"EX-001", "BMI-001", "NDX-002", "SX-004", "DOC-001"}
    missing = expected - codes
    assert not missing, f"C1 missing: {missing}, got: {codes}"
