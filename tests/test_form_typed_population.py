"""Tests for Phase A.4 — typed FormFieldEntry population with EvidenceReference."""

from __future__ import annotations

from cardioauth.evidence import EvidenceGraph, EvidenceSpan
from cardioauth.evidence_extraction import emit_spans_for_chart_dict
from cardioauth.models.chart import (
    ChartData, ECGFinding, StressTestResult, Symptom,
)
from cardioauth.models.reasoning import ReasoningResult
from cardioauth.ontology import default_ontology
from cardioauth.payer_forms import (
    _evidence_reference_for_form_field,
    get_payer_form,
    populate_payer_form,
    populate_payer_form_entries,
)
from cardioauth.submission_packet import FormFieldEntry


def _populated_chart() -> ChartData:
    return ChartData(
        patient_id="P-1",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        patient_name="Jane Synthetic",
        date_of_birth="1958-01-15",
        age=67,
        sex="F",
        attending_physician="Dr. John Doe",
        attending_npi="1234567890",
        insurance_id="UHC-456",
        payer_name="UnitedHealthcare",
        diagnosis_codes=["I25.10"],
        active_comorbidities=["HTN", "DM", "BMI 38"],
        current_symptoms=[
            Symptom(name="dyspnea on exertion", change_vs_baseline="new"),
        ],
        ecg_findings=[ECGFinding(conduction="LBBB")],
        prior_stress_tests=[StressTestResult(modality="SPECT", interpretation="non-diagnostic")],
        confidence_score=0.9,
    )


def _populated_graph(chart: ChartData, raw_note: str) -> EvidenceGraph:
    g = EvidenceGraph()
    emit_spans_for_chart_dict(
        chart_dict=chart.model_dump(mode="json"),
        raw_note=raw_note, graph=g,
        extractor="test_extractor",
    )
    return g


# ── populate_payer_form_entries returns typed FormFieldEntry list ──────

def test_typed_population_returns_form_field_entries() -> None:
    chart = _populated_chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    entries = populate_payer_form_entries(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
    )
    assert all(isinstance(e, FormFieldEntry) for e in entries)
    assert len(entries) > 0


def test_typed_population_preserves_normalization() -> None:
    """The Apr 30 allowed-value normalizer still snaps select fields
    in the typed path."""
    chart = _populated_chart()
    chart.additional_notes = "Treadmill testing cannot be performed due to fatigue."
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    entries = populate_payer_form_entries(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
    )
    ex_cap = next(e for e in entries if e.key == "exercise_capacity")
    assert ex_cap.value == "No — see functional limitation"
    assert "functional limitation" in ex_cap.evidence_text


# ── Evidence reference emission ────────────────────────────────────────

def test_evidence_ref_built_for_ecg_field_via_ontology() -> None:
    """ecg_findings field is ontology-bound to ECG-001..004 (evidence_type
    'ecg', mapped to chart.ecg_findings). When the graph has a span at
    chart.ecg_findings[0], the field's evidence_ref must resolve to it.
    """
    chart = _populated_chart()
    raw_note = "Baseline ECG: sinus rhythm with LBBB."
    graph = _populated_graph(chart, raw_note)
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    entries = populate_payer_form_entries(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
        evidence_graph=graph,
    )
    ecg_field = next(e for e in entries if e.key == "ecg_findings")
    assert not ecg_field.evidence.is_empty()
    # The referenced spans must resolve in the graph
    assert graph.references_resolve(ecg_field.evidence)


def test_evidence_ref_empty_when_no_spans_for_field() -> None:
    chart = _populated_chart()
    # Graph has no spans for the ecg_findings path
    graph = EvidenceGraph()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    entries = populate_payer_form_entries(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
        evidence_graph=graph,
    )
    ecg_field = next(e for e in entries if e.key == "ecg_findings")
    assert ecg_field.evidence.is_empty()


def test_evidence_ref_via_direct_chart_path_match() -> None:
    """patient_name field has populated_from='chart_data.patient_name'.
    The graph has a span at chart.patient_name. The direct-path match
    should pick it up even though patient_name has no ontology binding.
    """
    chart = _populated_chart()
    raw_note = "Patient: Jane Synthetic"
    graph = _populated_graph(chart, raw_note)
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    entries = populate_payer_form_entries(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
        evidence_graph=graph,
    )
    name_field = next(e for e in entries if e.key == "patient_name")
    assert not name_field.evidence.is_empty()
    # The span(s) referenced point at chart.patient_name
    spans = graph.get_many(name_field.evidence.span_ids)
    assert any(s.field_path == "chart.patient_name" for s in spans)


def test_evidence_ref_unions_ontology_and_direct_paths() -> None:
    """Both sources of evidence should combine without duplication."""
    chart = _populated_chart()
    raw_note = "Various clinical content."
    graph = _populated_graph(chart, raw_note)
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    entries = populate_payer_form_entries(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
        evidence_graph=graph,
    )

    # No duplicate span_ids in any field's reference
    for e in entries:
        ids = list(e.evidence.span_ids)
        assert len(ids) == len(set(ids)), f"duplicate spans in {e.key}: {ids}"


def test_evidence_ref_only_for_populated_or_incomplete_fields() -> None:
    """Missing / needs_verify fields should NOT carry evidence references."""
    chart = ChartData(
        patient_id="P-1",
        procedure_requested="Cardiac PET",
        procedure_code="78492",
        # Most fields intentionally blank
    )
    graph = _populated_graph(chart, "")
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.5, approval_likelihood_label="MEDIUM",
        pa_narrative_draft="",
    )
    entries = populate_payer_form_entries(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
        evidence_graph=graph,
    )
    for e in entries:
        if e.status in ("missing", "needs_verify"):
            assert e.evidence.is_empty(), \
                f"{e.key} ({e.status}) should not carry evidence ref"


# ── Helper — _evidence_reference_for_form_field directly ──────────────

def test_evidence_helper_uses_ontology_for_clinical_fields() -> None:
    """Direct test of the helper — for ecg_findings field, the ontology
    binds to ECG criteria (evidence_type='ecg' → chart.ecg_findings),
    so spans at that path should be referenced."""
    g = EvidenceGraph()
    g.add(EvidenceSpan.new(
        source_id="raw_note", source_type="raw_note",
        field_path="chart.ecg_findings[0]",
        extracted_value="LBBB", extractor="claude",
    ))
    form = get_payer_form("UnitedHealthcare", "78492")
    ecg_field = next(f for f in form.fields if f.key == "ecg_findings")
    ref = _evidence_reference_for_form_field(
        ecg_field,
        ontology=default_ontology(),
        graph=g,
    )
    assert not ref.is_empty()
    assert ref.derivation == "ontology"


def test_evidence_helper_returns_empty_for_unbound_field() -> None:
    """A field not in the ontology AND with no chart_data.* path returns
    an empty reference."""
    g = EvidenceGraph()
    g.add(EvidenceSpan.new(
        source_id="x", source_type="raw_note",
        field_path="chart.something_else",
        extracted_value="x", extractor="x",
    ))
    form = get_payer_form("UnitedHealthcare", "78492")
    # Pick a field that has no chart_data.* path AND no ontology binding —
    # in_network_attestation uses "flagged_requires_verify" (no path) and
    # has no criterion bindings.
    attest = next(f for f in form.fields if f.key == "in_network_attestation")
    ref = _evidence_reference_for_form_field(
        attest, ontology=default_ontology(), graph=g,
    )
    assert ref.is_empty()


# ── Backwards compat: dict version still produces the right shape ─────

def test_dict_api_carries_structured_evidence_ref() -> None:
    """populate_payer_form (dict) now exposes evidence_ref alongside
    the legacy evidence text key."""
    chart = _populated_chart()
    graph = _populated_graph(chart, "")
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    result = populate_payer_form(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
        evidence_graph=graph,
    )
    rows = result["fields"]
    sample = next(r for r in rows if r["key"] == "ecg_findings")
    assert "evidence_ref" in sample
    assert "span_ids" in sample["evidence_ref"]


def test_dict_api_back_compat_when_no_graph() -> None:
    """Calling without evidence_graph still works with the legacy shape."""
    chart = _populated_chart()
    form = get_payer_form("UnitedHealthcare", "78492")
    reasoning = ReasoningResult(
        approval_likelihood_score=0.9, approval_likelihood_label="HIGH",
        pa_narrative_draft="",
    )
    result = populate_payer_form(
        form, chart_data=chart, policy_data=None, reasoning=reasoning,
    )
    assert "fields" in result
    assert result["counts"]["total"] == len(form.fields)
    sample = result["fields"][0]
    assert "evidence_ref" in sample
    # Empty when no graph supplied
    assert sample["evidence_ref"]["span_ids"] == []
