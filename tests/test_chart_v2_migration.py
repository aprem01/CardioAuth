"""Regression tests for ChartData v2 migration (Peter Apr 14 feedback).

Legacy flat comorbidities / prior_treatments lists must route into the
correct v2 bucket so symptoms don't end up under comorbidities, past MIs
don't end up under procedures, etc.
"""

from __future__ import annotations

from cardioauth.models import (
    ChartData,
    ECGFinding,
    ExamFinding,
    FamilyHistoryEntry,
    PMHEntry,
    ProcedureHistory,
    StressTestResult,
    Symptom,
    migrate_legacy_chart,
)


def _base_chart(**kwargs) -> ChartData:
    """Build a minimal ChartData, caller overrides via kwargs."""
    defaults = dict(
        patient_id="T-001",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        diagnosis_codes=["I25.10"],
        confidence_score=0.9,
    )
    defaults.update(kwargs)
    return ChartData(**defaults)


# ── Legacy comorbidities routing ────────────────────────────────────────

def test_dyspnea_routes_to_symptoms_not_comorbidities() -> None:
    """Peter: 'dyspnea on exertion' was landing in comorbidities."""
    chart = _base_chart(comorbidities=["dyspnea on exertion"])
    migrated = migrate_legacy_chart(chart)
    assert len(migrated.current_symptoms) == 1
    assert migrated.current_symptoms[0].name == "dyspnea"
    assert migrated.current_symptoms[0].character == "exertional"
    # Confirm it did NOT stay in legacy comorbidities nor active_comorbidities
    assert not migrated.comorbidities
    assert "dyspnea on exertion" not in migrated.active_comorbidities


def test_edema_routes_to_exam_findings() -> None:
    chart = _base_chart(comorbidities=["2+ pedal edema on exam"])
    migrated = migrate_legacy_chart(chart)
    assert len(migrated.exam_findings) == 1
    assert "edema" in migrated.exam_findings[0].finding.lower()
    assert "2+ pedal edema on exam" not in migrated.active_comorbidities


def test_family_history_routes_correctly() -> None:
    chart = _base_chart(comorbidities=["Father had MI at 52", "Family history of premature CAD"])
    migrated = migrate_legacy_chart(chart)
    assert len(migrated.family_history) == 2
    relations = {f.relation for f in migrated.family_history}
    assert "father" in relations
    # None of these should pollute comorbidities
    assert not any("family" in c.lower() or "father" in c.lower() for c in migrated.active_comorbidities)


def test_past_mi_routes_to_pmh_not_procedures() -> None:
    """Peter: 'MI 2021' was landing in prior_treatments/procedures."""
    chart = _base_chart(prior_treatments=["History of MI 2021"])
    migrated = migrate_legacy_chart(chart)
    assert len(migrated.past_medical_history) == 1
    assert migrated.past_medical_history[0].date == "2021"
    # Not a procedure
    assert not migrated.prior_procedures


def test_chronic_condition_stays_in_active_comorbidities() -> None:
    """HTN, DM, CKD — real comorbidities — must stay in the comorbidity bucket."""
    chart = _base_chart(comorbidities=["HTN", "Type 2 diabetes", "CKD stage 3"])
    migrated = migrate_legacy_chart(chart)
    assert len(migrated.active_comorbidities) == 3
    assert "HTN" in migrated.active_comorbidities
    # No symptom / exam / family misrouting
    assert not migrated.current_symptoms
    assert not migrated.exam_findings
    assert not migrated.family_history


# ── Legacy prior_treatments routing ─────────────────────────────────────

def test_stress_test_routes_to_prior_stress_tests() -> None:
    chart = _base_chart(prior_treatments=[
        "Prior SPECT showed attenuation artifact",
        "ETT submaximal at 68% MPHR",
    ])
    migrated = migrate_legacy_chart(chart)
    assert len(migrated.prior_stress_tests) == 2
    modalities = {s.modality for s in migrated.prior_stress_tests}
    assert "SPECT" in modalities
    assert "ETT" in modalities
    # Not procedures, not just prior_treatments
    assert not migrated.prior_procedures
    assert not migrated.prior_treatments


def test_pci_routes_to_prior_procedures() -> None:
    chart = _base_chart(prior_treatments=["PCI with DES to LAD in 2019", "CABG x 3 in 2015"])
    migrated = migrate_legacy_chart(chart)
    assert len(migrated.prior_procedures) == 2
    # Not stress tests, not PMH
    assert not migrated.prior_stress_tests


def test_unclassifiable_prior_treatment_preserved() -> None:
    """We don't silently drop things we can't classify — preserve in legacy field."""
    chart = _base_chart(prior_treatments=["Some unclassifiable entry"])
    migrated = migrate_legacy_chart(chart)
    assert "Some unclassifiable entry" in migrated.prior_treatments


# ── Idempotency ─────────────────────────────────────────────────────────

def test_migration_is_idempotent() -> None:
    """Running migrate twice produces the same output."""
    chart = _base_chart(comorbidities=["HTN", "dyspnea"], prior_treatments=["PCI 2019"])
    once = migrate_legacy_chart(chart)
    twice = migrate_legacy_chart(once)
    assert twice.active_comorbidities == once.active_comorbidities
    assert [s.name for s in twice.current_symptoms] == [s.name for s in once.current_symptoms]
    assert [p.name for p in twice.prior_procedures] == [p.name for p in once.prior_procedures]


# ── v2 fields passthrough ───────────────────────────────────────────────

def test_v2_fields_preserved_unchanged() -> None:
    """If caller supplies v2 fields directly, they must not be modified."""
    chart = _base_chart(
        current_symptoms=[Symptom(name="angina", change_vs_baseline="worsening")],
        ecg_findings=[ECGFinding(conduction="LBBB")],
        prior_procedures=[ProcedureHistory(name="CABG", date="2019")],
    )
    migrated = migrate_legacy_chart(chart)
    assert migrated.current_symptoms[0].name == "angina"
    assert migrated.ecg_findings[0].conduction == "LBBB"
    assert migrated.prior_procedures[0].name == "CABG"


# ── Evidence buckets see v2 data ────────────────────────────────────────

def test_evidence_buckets_read_v2_ecg() -> None:
    """The reasoner's ECG bucket must pull from ecg_findings, not imaging."""
    from cardioauth.taxonomy.evidence_buckets import bucket_chart_evidence
    chart = _base_chart(
        ecg_findings=[ECGFinding(conduction="LBBB", rhythm="sinus")],
    )
    buckets = bucket_chart_evidence(chart.model_dump())
    assert "LBBB" in buckets["ecg"].lower() or "lbbb" in buckets["ecg"].lower()


def test_evidence_buckets_read_v2_symptoms() -> None:
    """Symptoms bucket pulls from current_symptoms, not comorbidities."""
    from cardioauth.taxonomy.evidence_buckets import bucket_chart_evidence
    chart = _base_chart(
        current_symptoms=[Symptom(name="angina", change_vs_baseline="worsening")],
    )
    buckets = bucket_chart_evidence(chart.model_dump())
    assert "angina" in buckets["clinical_note"]["symptoms"].lower()


def test_evidence_buckets_read_v2_stress_test_hr_percent() -> None:
    """HR% MPHR extraction pulls from prior_stress_tests first."""
    from cardioauth.taxonomy.evidence_buckets import bucket_chart_evidence
    chart = _base_chart(
        prior_stress_tests=[StressTestResult(modality="ETT", max_hr_percent="68%")],
    )
    buckets = bucket_chart_evidence(chart.model_dump())
    hr = buckets["score"]["max_hr_percent"]
    assert hr["status"] == "found"
    assert hr["value"] == 68
    assert hr["source"] == "prior_stress_tests"
