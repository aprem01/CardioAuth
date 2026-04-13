"""Tests for the criterion taxonomy — CPT filtering and stability."""

from cardioauth.taxonomy.taxonomy import (
    TAXONOMY_VERSION,
    CRITERION_TAXONOMY,
    get_criterion,
    get_criteria_for_procedure,
    get_categories,
)


def test_taxonomy_version_set():
    assert TAXONOMY_VERSION == "1.0.0"


def test_taxonomy_has_criteria():
    assert len(CRITERION_TAXONOMY) >= 30


def test_every_criterion_has_required_fields():
    for code, c in CRITERION_TAXONOMY.items():
        assert c.code == code
        assert c.category
        assert c.short_name
        assert c.definition
        assert c.evidence_type in ("lab", "imaging", "clinical_note", "score",
                                    "medication", "ecg", "demographic")
        assert c.severity in ("required", "supporting")
        assert isinstance(c.applies_to, list)


def test_get_criterion_by_code():
    c = get_criterion("EX-001")
    assert c is not None
    assert c.code == "EX-001"
    assert "exercise" in c.definition.lower() or "exercise" in c.short_name.lower()


def test_get_criteria_for_pet():
    criteria = get_criteria_for_procedure("78492", "UnitedHealthcare")
    codes = {c.code for c in criteria}
    # Key PET criteria
    assert "EX-001" in codes or "NDX-002" in codes
    assert "BMI-001" in codes
    assert "DOC-001" in codes
    # TAVR-specific should NOT be in PET list
    assert "RISK-001" not in codes  # STS-PROM TAVR-only
    assert "HT-001" not in codes    # Heart Team TAVR-only


def test_get_criteria_for_tavr():
    criteria = get_criteria_for_procedure("33361", "")
    codes = {c.code for c in criteria}
    assert "RISK-001" in codes  # STS-PROM
    assert "HT-001" in codes     # Heart Team
    assert "IMG-002" in codes    # CTA sizing


def test_get_criteria_for_af_ablation():
    criteria = get_criteria_for_procedure("93656", "")
    codes = {c.code for c in criteria}
    assert "MED-003" in codes    # Failed antiarrhythmic
    assert "ANTI-001" in codes   # CHA2DS2-VASc
    assert "ANTI-002" in codes   # TEE/LAA


def test_get_criteria_unknown_cpt_returns_empty():
    criteria = get_criteria_for_procedure("99999", "")
    assert criteria == []


def test_categories_present():
    cats = get_categories()
    assert "NDX" in cats
    assert "SX" in cats
    assert "MED" in cats
    assert "BMI" in cats
    assert "ECG" in cats


# ── Peter's specific criteria fix ──


def test_doc001_applies_to_all_5_cpts():
    """DOC-001 must apply to cardiac imaging AND interventional CPTs."""
    c = get_criterion("DOC-001")
    assert c is not None
    assert "78492" in c.applies_to
    assert "78452" in c.applies_to
    assert "93458" in c.applies_to
    assert "33361" in c.applies_to
    assert "93656" in c.applies_to


def test_sx004_applies_to_cardiac_imaging():
    """Peter's fix: SX-004 should apply to PET/SPECT, not just interventional."""
    c = get_criterion("SX-004")
    assert c is not None
    assert "78492" in c.applies_to
    assert "78452" in c.applies_to


def test_sx004_evidence_type_is_clinical_note():
    """After Peter's fix, SX-004 is clinical_note (not score)."""
    c = get_criterion("SX-004")
    assert c.evidence_type == "clinical_note"
