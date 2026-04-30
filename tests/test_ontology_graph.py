"""Tests for Phase B.4 — NetworkX knowledge-graph view over the ontology."""

from __future__ import annotations

import networkx as nx
import pytest

from cardioauth.ontology_graph import (
    NODE_TYPE_CHART_PATH,
    NODE_TYPE_CPT,
    NODE_TYPE_CRITERION,
    NODE_TYPE_EVIDENCE_TYPE,
    NODE_TYPE_FORM_FIELD,
    REL_APPLIES_TO,
    REL_CAPTURED_IN,
    REL_HAS_EVIDENCE_TYPE,
    REL_SUPPLIED_BY,
    build_graph,
    chart_paths_for_evidence_type,
    cpts_using_form_field,
    criteria_for_chart_path,
    criteria_for_cpt,
    evidence_chain_for_form_field,
    form_fields_for_criterion,
    get_default_graph,
    graph_stats,
    iter_nodes_of_kind,
    node_id,
    reset_default_graph,
    shortest_path,
    split_node,
)


# ── Node id helpers ────────────────────────────────────────────────────


def test_node_id_format() -> None:
    assert node_id("cpt", "78492") == "cpt::78492"


def test_split_node_round_trip() -> None:
    nid = node_id("criterion", "EX-001")
    kind, value = split_node(nid)
    assert kind == "criterion"
    assert value == "EX-001"


def test_split_node_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        split_node("no-separator")


# ── Graph build ────────────────────────────────────────────────────────


def test_build_graph_returns_directed() -> None:
    g = build_graph()
    assert isinstance(g, nx.DiGraph)


def test_build_graph_has_cpt_nodes() -> None:
    g = build_graph()
    cpt_node = node_id(NODE_TYPE_CPT, "78492")
    assert cpt_node in g
    assert g.nodes[cpt_node]["kind"] == NODE_TYPE_CPT


def test_build_graph_has_criterion_nodes() -> None:
    g = build_graph()
    crit_node = node_id(NODE_TYPE_CRITERION, "EX-001")
    assert crit_node in g
    assert g.nodes[crit_node]["evidence_type"] == "clinical_note"


def test_build_graph_has_form_field_nodes() -> None:
    g = build_graph()
    ff = node_id(NODE_TYPE_FORM_FIELD, "exercise_capacity")
    assert ff in g


def test_build_graph_evidence_type_to_chart_path_edges() -> None:
    g = build_graph()
    et = node_id(NODE_TYPE_EVIDENCE_TYPE, "ecg")
    cp = node_id(NODE_TYPE_CHART_PATH, "chart.ecg_findings")
    assert g.has_edge(et, cp)
    assert g.edges[et, cp]["relation"] == REL_SUPPLIED_BY


def test_build_graph_cpt_to_criterion_applies_to_edges() -> None:
    g = build_graph()
    cpt_node = node_id(NODE_TYPE_CPT, "78492")
    crit_node = node_id(NODE_TYPE_CRITERION, "BMI-001")
    assert g.has_edge(cpt_node, crit_node)
    assert g.edges[cpt_node, crit_node]["relation"] == REL_APPLIES_TO


def test_build_graph_criterion_to_form_field_captured_in() -> None:
    g = build_graph()
    crit_node = node_id(NODE_TYPE_CRITERION, "EX-001")
    ff_node = node_id(NODE_TYPE_FORM_FIELD, "exercise_capacity")
    assert g.has_edge(crit_node, ff_node)
    assert g.edges[crit_node, ff_node]["relation"] == REL_CAPTURED_IN


def test_build_graph_criterion_to_evidence_type() -> None:
    g = build_graph()
    crit_node = node_id(NODE_TYPE_CRITERION, "ECG-001")
    et_node = node_id(NODE_TYPE_EVIDENCE_TYPE, "ecg")
    assert g.has_edge(crit_node, et_node)
    assert g.edges[crit_node, et_node]["relation"] == REL_HAS_EVIDENCE_TYPE


# ── Forward queries ───────────────────────────────────────────────────


def test_criteria_for_pet_cpt() -> None:
    out = criteria_for_cpt("78492")
    assert "BMI-001" in out
    assert "ECG-001" in out
    assert "EX-001" in out


def test_criteria_for_unknown_cpt() -> None:
    assert criteria_for_cpt("99999") == []


def test_form_fields_for_ex_001() -> None:
    out = form_fields_for_criterion("EX-001")
    assert "exercise_capacity" in out
    assert "exercise_limitation" in out


def test_form_fields_for_unknown_criterion() -> None:
    assert form_fields_for_criterion("FAKE-999") == []


def test_chart_paths_for_evidence_type_ecg() -> None:
    out = chart_paths_for_evidence_type("ecg")
    assert out == ["chart.ecg_findings"]


def test_chart_paths_for_imaging_includes_stress() -> None:
    out = chart_paths_for_evidence_type("imaging")
    assert "chart.relevant_imaging" in out
    assert "chart.prior_stress_tests" in out


# ── Reverse queries ───────────────────────────────────────────────────


def test_cpts_using_ecg_findings_field() -> None:
    """ecg_findings is captured_in for ECG-001..004, all of which apply
    to PET (78492) and SPECT (78452)."""
    out = cpts_using_form_field("ecg_findings")
    assert "78492" in out
    assert "78452" in out


def test_cpts_using_unknown_form_field() -> None:
    assert cpts_using_form_field("not_a_field") == []


def test_criteria_for_chart_path_ecg() -> None:
    out = criteria_for_chart_path("chart.ecg_findings")
    # All ECG-* criteria should surface
    assert "ECG-001" in out
    assert "ECG-002" in out


def test_criteria_for_chart_path_strips_index_suffix() -> None:
    """List-item paths like 'chart.ecg_findings[0]' should resolve via
    their base path 'chart.ecg_findings'."""
    out = criteria_for_chart_path("chart.ecg_findings[0]")
    assert "ECG-001" in out


def test_criteria_for_unknown_chart_path() -> None:
    assert criteria_for_chart_path("chart.not_a_bucket") == []


# ── Multi-hop traversal ───────────────────────────────────────────────


def test_evidence_chain_for_exercise_capacity() -> None:
    chain = evidence_chain_for_form_field("exercise_capacity")
    assert len(chain) >= 1
    entry = chain[0]
    assert entry["criterion"] == "EX-001"
    assert entry["evidence_type"] == "clinical_note"
    # clinical_note → multiple chart paths including current_symptoms
    assert "chart.current_symptoms" in entry["chart_paths"]


def test_evidence_chain_for_ecg_field_lists_all_four_criteria() -> None:
    chain = evidence_chain_for_form_field("ecg_findings")
    crit_codes = {c["criterion"] for c in chain}
    assert {"ECG-001", "ECG-002", "ECG-003", "ECG-004"} <= crit_codes


def test_evidence_chain_for_unknown_field() -> None:
    assert evidence_chain_for_form_field("fake_field") == []


# ── shortest_path ─────────────────────────────────────────────────────


def test_shortest_path_cpt_to_form_field() -> None:
    path = shortest_path(
        node_id(NODE_TYPE_CPT, "78492"),
        node_id(NODE_TYPE_FORM_FIELD, "exercise_capacity"),
    )
    assert path is not None
    # Path must traverse via a Criterion
    assert any(p.startswith("criterion::") for p in path)


def test_shortest_path_returns_none_for_disconnected_nodes() -> None:
    g = build_graph()
    # form_field -> cpt is the wrong direction; no edge
    path = shortest_path(
        node_id(NODE_TYPE_FORM_FIELD, "exercise_capacity"),
        node_id(NODE_TYPE_CPT, "78492"),
        graph=g,
    )
    assert path is None


def test_shortest_path_unknown_node_returns_none() -> None:
    assert shortest_path("cpt::78492", "form_field::nonexistent") is None


# ── graph_stats / iter_nodes_of_kind ──────────────────────────────────


def test_graph_stats_reports_counts() -> None:
    s = graph_stats()
    assert s["node_count"] > 0
    assert s["edge_count"] > 0
    assert NODE_TYPE_CPT in s["nodes_by_kind"]
    assert REL_APPLIES_TO in s["edges_by_relation"]


def test_iter_nodes_of_kind_returns_only_matching() -> None:
    cpts = list(iter_nodes_of_kind(NODE_TYPE_CPT))
    assert len(cpts) > 0
    for n, attrs in cpts:
        assert attrs["kind"] == NODE_TYPE_CPT


# ── Singleton ─────────────────────────────────────────────────────────


def test_get_default_graph_caches() -> None:
    a = get_default_graph()
    b = get_default_graph()
    assert a is b


def test_reset_default_graph_forces_rebuild() -> None:
    a = get_default_graph()
    reset_default_graph()
    b = get_default_graph()
    assert a is not b
