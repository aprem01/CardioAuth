"""Tests for the synthetic chart feeder.

The synthetic feeder produces FHIR Bundles shape-identical to what
FHIRClient.get_patient_bundle returns from real Epic. We verify the
markdown parser, the bundle assembly, and the end-to-end round-trip
through the corpus mapper — including PDF text extraction.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from cardioauth.synthetic.loader import (
    SyntheticCase,
    CaseSection,
    load_case_markdown,
    case_to_bundle,
    list_available_cases,
    load_case_by_id,
)
from cardioauth.fhir.corpus_mapper import bundle_to_patient_corpus


_MINIMAL_CASE = """\
---
patient_id: TEST-PT-1
patient_name: Test Patient
dob: 1960-01-01
sex: female
member_id: M-123
payer: UnitedHealthcare
ordering_physician: Dr. Tester
ordering_npi: 9999999999
encounter_date: 2026-05-01
procedure_code: 78452
procedure_name: SPECT
diagnoses:
  - code: I25.10
    text: CAD
---

# Current Encounter Note
type: progress_note
date: 2026-05-01
author: Dr. Tester

Patient has chest pain. Plan SPECT.

# Stress Test
type: stress_test
date: 2023-06-01
author: Dr. Stressor
format: pdf

Submaximal treadmill. LBBB. Nondiagnostic.
"""


# ── Markdown parser ────────────────────────────────────────────────────


def test_load_minimal_case() -> None:
    case = load_case_markdown(_MINIMAL_CASE)
    assert case.patient_id == "TEST-PT-1"
    assert case.patient_name == "Test Patient"
    assert case.procedure_code == "78452"
    assert case.payer == "UnitedHealthcare"
    assert case.dob == "1960-01-01"
    assert len(case.sections) == 2


def test_section_props_parsed_correctly() -> None:
    case = load_case_markdown(_MINIMAL_CASE)
    note = case.sections[0]
    stress = case.sections[1]
    assert note.doc_type == "progress_note"
    assert note.format == "html"
    assert note.date == "2026-05-01"
    assert "chest pain" in note.body
    assert stress.format == "pdf"
    assert stress.doc_type == "stress_test"


def test_missing_frontmatter_raises() -> None:
    with pytest.raises(ValueError, match="frontmatter"):
        load_case_markdown("# Just a heading\n\nBody")


def test_missing_required_field_raises() -> None:
    bad = """---
patient_id: X
patient_name: Y
dob: 1960-01-01
payer: UHC
---

# Note
type: progress_note

Body
"""
    # missing procedure_code
    with pytest.raises(ValueError, match="procedure_code"):
        load_case_markdown(bad)


def test_diagnoses_normalize_to_dict_list() -> None:
    case = load_case_markdown(_MINIMAL_CASE)
    assert case.diagnoses == [{"code": "I25.10", "text": "CAD"}]


# ── Bundle assembly ────────────────────────────────────────────────────


def test_bundle_has_epic_compatible_shape() -> None:
    case = load_case_markdown(_MINIMAL_CASE)
    bundle = case_to_bundle(case)
    assert bundle["patient_id"] == "TEST-PT-1"
    resources = bundle["resources"]
    # Same resource types Epic returns
    for rtype in ("Patient", "Coverage", "Encounter", "Procedure",
                  "Condition", "DocumentReference"):
        assert rtype in resources, f"missing {rtype}"
        assert resources[rtype]["resourceType"] == "Bundle"


def test_patient_resource_has_correct_demographics() -> None:
    case = load_case_markdown(_MINIMAL_CASE)
    bundle = case_to_bundle(case)
    patient = bundle["resources"]["Patient"]["entry"][0]["resource"]
    assert patient["birthDate"] == "1960-01-01"
    assert patient["gender"] == "female"
    assert patient["name"][0]["family"] == "Patient"


def test_coverage_carries_payer_and_member_id() -> None:
    case = load_case_markdown(_MINIMAL_CASE)
    bundle = case_to_bundle(case)
    coverage = bundle["resources"]["Coverage"]["entry"][0]["resource"]
    assert coverage["subscriberId"] == "M-123"
    assert coverage["payor"][0]["display"] == "UnitedHealthcare"


def test_each_section_becomes_a_documentreference() -> None:
    case = load_case_markdown(_MINIMAL_CASE)
    bundle = case_to_bundle(case)
    docrefs = bundle["resources"]["DocumentReference"]["entry"]
    assert len(docrefs) == 2
    titles = {d["resource"]["type"]["text"] for d in docrefs}
    assert titles == {"Current Encounter Note", "Stress Test"}


def test_pdf_section_attachment_is_real_pdf() -> None:
    """Sections tagged format=pdf must produce attachments with
    contentType=application/pdf containing valid PDF bytes."""
    import base64
    case = load_case_markdown(_MINIMAL_CASE)
    bundle = case_to_bundle(case)
    pdf_docref = next(
        d["resource"] for d in bundle["resources"]["DocumentReference"]["entry"]
        if d["resource"]["type"]["text"] == "Stress Test"
    )
    att = pdf_docref["content"][0]["attachment"]
    assert att["contentType"] == "application/pdf"
    raw = base64.b64decode(att["data"])
    assert raw.startswith(b"%PDF-")


# ── End-to-end through corpus mapper ───────────────────────────────────


def test_full_pipeline_round_trip() -> None:
    """The synthetic bundle, when run through bundle_to_patient_corpus,
    must produce CorpusDocuments with the actual note text — including
    text extracted from the rendered PDFs."""
    case = load_case_markdown(_MINIMAL_CASE)
    bundle = case_to_bundle(case)
    corpus = bundle_to_patient_corpus(bundle)
    assert len(corpus.documents) == 2
    titles = {d.title for d in corpus.documents}
    assert titles == {"Current Encounter Note", "Stress Test"}
    # The PDF section's body must be extractable back from the PDF
    stress = next(d for d in corpus.documents if d.title == "Stress Test")
    assert "Submaximal" in stress.text or "LBBB" in stress.text


def test_doc_types_map_correctly_through_loinc() -> None:
    """The LOINC codes we put on synthetic DocumentReferences must
    cause the corpus mapper to assign the right DocType."""
    case = load_case_markdown(_MINIMAL_CASE)
    bundle = case_to_bundle(case)
    corpus = bundle_to_patient_corpus(bundle)
    by_title = {d.title: d for d in corpus.documents}
    assert by_title["Stress Test"].doc_type == "stress_test"


# ── Whitford template ──────────────────────────────────────────────────


def test_whitford_template_loads_and_produces_5_sections() -> None:
    """The shipped Whitford SPECT template must parse, assemble, and
    flow through the corpus mapper without error. Acts as a fixture
    regression test."""
    case = load_case_by_id("whitford-spect")
    assert case.patient_name == "Eleanor R. Whitford"
    assert len(case.sections) == 5
    assert sum(1 for s in case.sections if s.format == "pdf") == 3

    bundle = case_to_bundle(case)
    corpus = bundle_to_patient_corpus(bundle)
    assert len(corpus.documents) == 5
    # The treadmill stress test (PDF) text must be retrievable
    stress = next(d for d in corpus.documents if "Stress" in d.title)
    assert stress.doc_type == "stress_test"
    assert "LBBB" in stress.text or "submaximal" in stress.text.lower()


# ── User-authored cases (DB-stored) ─────────────────────────────────────


def _fresh_store():
    import tempfile
    from cardioauth.persistence import SQLiteStore
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return SQLiteStore(tmp.name)


def test_db_save_and_load_synthetic_case() -> None:
    """Save a case to the DB, retrieve it by ID, confirm round-trip."""
    store = _fresh_store()
    store.save_synthetic_case(
        case_id="custom-1",
        markdown=_MINIMAL_CASE,
        author_id="u1",
        patient_name="Test Patient",
        procedure_code="78452",
        payer="UnitedHealthcare",
        section_count=2,
        pdf_section_count=1,
    )
    got = store.get_synthetic_case("custom-1")
    assert got is not None
    assert got["case_id"] == "custom-1"
    assert got["patient_name"] == "Test Patient"
    assert got["pdf_section_count"] == 1
    assert "TEST-PT-1" in got["markdown"]


def test_db_upsert_overwrites_existing() -> None:
    """Saving with the same case_id twice should update, not duplicate."""
    store = _fresh_store()
    md_a = _MINIMAL_CASE
    md_b = _MINIMAL_CASE.replace("Test Patient", "Updated Patient")
    store.save_synthetic_case(case_id="case-1", markdown=md_a, patient_name="A", section_count=2)
    store.save_synthetic_case(case_id="case-1", markdown=md_b, patient_name="B", section_count=3)
    items = store.list_synthetic_cases()
    assert len(items) == 1
    assert items[0]["patient_name"] == "B"
    assert items[0]["section_count"] == 3
    got = store.get_synthetic_case("case-1")
    assert "Updated Patient" in got["markdown"]


def test_db_list_orders_newest_first() -> None:
    import time
    store = _fresh_store()
    store.save_synthetic_case(case_id="a", markdown=_MINIMAL_CASE, patient_name="A")
    time.sleep(0.01)
    store.save_synthetic_case(case_id="b", markdown=_MINIMAL_CASE, patient_name="B")
    time.sleep(0.01)
    store.save_synthetic_case(case_id="c", markdown=_MINIMAL_CASE, patient_name="C")
    ids = [r["case_id"] for r in store.list_synthetic_cases()]
    assert ids == ["c", "b", "a"]


def test_db_delete_returns_existed_flag() -> None:
    store = _fresh_store()
    store.save_synthetic_case(case_id="x", markdown=_MINIMAL_CASE)
    assert store.delete_synthetic_case("x") is True
    assert store.delete_synthetic_case("x") is False
    assert store.get_synthetic_case("x") is None


def test_db_stored_case_loadable_via_load_case_by_id(monkeypatch, tmp_path) -> None:
    """A case persisted in the DB must be loadable through the same
    load_case_by_id entry point built-ins use, so the API doesn't have
    two code paths."""
    from cardioauth.persistence import SQLiteStore
    from cardioauth import persistence as persistence_mod

    # Point the singleton store at a fresh DB
    fresh = SQLiteStore(str(tmp_path / "test.db"))
    monkeypatch.setattr(persistence_mod, "_store_singleton", fresh)

    fresh.save_synthetic_case(case_id="db-only-1", markdown=_MINIMAL_CASE)
    case = load_case_by_id("db-only-1")
    assert case.patient_id == "TEST-PT-1"
    assert len(case.sections) == 2


def test_list_available_cases_merges_builtin_and_custom(monkeypatch, tmp_path) -> None:
    """list_available_cases must return both the shipped Whitford
    template AND any DB-stored custom cases, with source tags."""
    from cardioauth.persistence import SQLiteStore
    from cardioauth import persistence as persistence_mod

    fresh = SQLiteStore(str(tmp_path / "test.db"))
    monkeypatch.setattr(persistence_mod, "_store_singleton", fresh)

    fresh.save_synthetic_case(case_id="custom-foo", markdown=_MINIMAL_CASE,
                              patient_name="Custom Foo", procedure_code="78452",
                              payer="Aetna", section_count=2)

    from cardioauth.synthetic import list_available_cases
    cases = list_available_cases()
    by_id = {c["id"]: c for c in cases}
    assert "whitford-spect" in by_id, "built-in template missing"
    assert by_id["whitford-spect"]["source"] == "builtin"
    assert "custom-foo" in by_id, "custom case missing"
    assert by_id["custom-foo"]["source"] == "custom"
    assert by_id["custom-foo"]["patient_name"] == "Custom Foo"
