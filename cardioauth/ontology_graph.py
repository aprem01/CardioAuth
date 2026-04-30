"""Phase B.4 — NetworkX knowledge-graph view over the ontology.

Article alignment ("Knowledge Graph as Connective Tissue"): the
ontology lives in DEFAULT_*_BINDINGS today as flat lookup tables.
This module projects those bindings as a directed graph the reviewer
+ downstream callers can traverse without writing point-lookup code
each time:

  CPT    --[applies_to]-->     Criterion
  Criterion --[has_evidence_type]--> EvidenceType
  EvidenceType --[supplied_by]--> ChartPath
  Criterion --[captured_in]--> FormField
  Criterion --[expects_policy_chunk]--> PolicyChunkType

Node types: cpt, criterion, evidence_type, chart_path, form_field,
policy_chunk_type. Each node carries metadata useful at query time
(e.g. a criterion node has its short_name, severity, pathway_group).

Today's queries (used by the reviewer + future packet checks):
  criteria_satisfied_by_chart_path(path) -> [criterion_codes]
  form_fields_supporting_cpt(cpt)        -> [form_field_keys]
  cpts_using_form_field(field_key)       -> [cpts]
  evidence_chain_for_form_field(field_key) ->
        list of (criterion, evidence_type, chart_paths) tuples
  shortest_path(source, target)          -> list of nodes (or None)

The graph is built once from the ontology singleton; rebuilds are
explicit via `build_graph(ontology)`.
"""

from __future__ import annotations

import logging
from typing import Iterator

import networkx as nx

from cardioauth.ontology import SubmissionPacketOntology, get_default_ontology
from cardioauth.taxonomy.taxonomy import CRITERION_TAXONOMY

logger = logging.getLogger(__name__)


# Node types — used as a structured prefix to avoid id collisions
# (same string could be a CPT and a form_field key in principle).
NODE_TYPE_CPT = "cpt"
NODE_TYPE_CRITERION = "criterion"
NODE_TYPE_EVIDENCE_TYPE = "evidence_type"
NODE_TYPE_CHART_PATH = "chart_path"
NODE_TYPE_FORM_FIELD = "form_field"
NODE_TYPE_POLICY_CHUNK = "policy_chunk_type"


def node_id(kind: str, value: str) -> str:
    """Build a typed node identifier — e.g. node_id('cpt', '78492')."""
    return f"{kind}::{value}"


def split_node(nid: str) -> tuple[str, str]:
    """Return (kind, value) for a node id; raises if malformed."""
    if "::" not in nid:
        raise ValueError(f"malformed node id: {nid!r}")
    kind, value = nid.split("::", 1)
    return kind, value


# Edge relation labels
REL_APPLIES_TO = "applies_to"           # CPT      -> Criterion
REL_HAS_EVIDENCE_TYPE = "has_evidence_type"   # Criterion -> EvidenceType
REL_SUPPLIED_BY = "supplied_by"          # EvidenceType -> ChartPath
REL_CAPTURED_IN = "captured_in"          # Criterion -> FormField
REL_EXPECTS_POLICY = "expects_policy_chunk"  # Criterion -> PolicyChunkType


# ──────────────────────────────────────────────────────────────────────
# Build the graph
# ──────────────────────────────────────────────────────────────────────


def build_graph(ontology: SubmissionPacketOntology | None = None) -> nx.DiGraph:
    """Project the ontology + criterion taxonomy into a DiGraph.

    Idempotent given the same ontology + taxonomy versions. Returns a
    fresh DiGraph each call; callers cache it via get_default_graph().
    """
    if ontology is None:
        ontology = get_default_ontology()

    G: nx.DiGraph = nx.DiGraph()

    # 1. Criteria + their CPT applicability + evidence type
    for code, criterion in CRITERION_TAXONOMY.items():
        crit_id = node_id(NODE_TYPE_CRITERION, code)
        G.add_node(
            crit_id,
            kind=NODE_TYPE_CRITERION,
            code=code,
            short_name=criterion.short_name,
            category=criterion.category,
            severity=criterion.severity,
            evidence_type=criterion.evidence_type,
            pathway_group=criterion.pathway_group or "",
        )

        # CPT --applies_to--> Criterion (one edge per applicable CPT)
        for cpt in criterion.applies_to or []:
            cpt_node = node_id(NODE_TYPE_CPT, cpt)
            if cpt_node not in G:
                G.add_node(cpt_node, kind=NODE_TYPE_CPT, code=cpt)
            G.add_edge(cpt_node, crit_id, relation=REL_APPLIES_TO)

        # Criterion --has_evidence_type--> EvidenceType
        et_node = node_id(NODE_TYPE_EVIDENCE_TYPE, criterion.evidence_type)
        if et_node not in G:
            G.add_node(et_node, kind=NODE_TYPE_EVIDENCE_TYPE, name=criterion.evidence_type)
        G.add_edge(crit_id, et_node, relation=REL_HAS_EVIDENCE_TYPE)

    # 2. EvidenceType --supplied_by--> ChartPath
    for binding in ontology.evidence_type_bindings:
        et_node = node_id(NODE_TYPE_EVIDENCE_TYPE, binding.evidence_type)
        if et_node not in G:
            G.add_node(et_node, kind=NODE_TYPE_EVIDENCE_TYPE, name=binding.evidence_type)
        for path in binding.chart_paths:
            cp_node = node_id(NODE_TYPE_CHART_PATH, path)
            if cp_node not in G:
                G.add_node(cp_node, kind=NODE_TYPE_CHART_PATH, path=path)
            G.add_edge(et_node, cp_node, relation=REL_SUPPLIED_BY)

    # 3. Criterion --captured_in--> FormField
    for binding in ontology.criterion_form_bindings:
        crit_id = node_id(NODE_TYPE_CRITERION, binding.criterion_code)
        if crit_id not in G:
            # Binding refers to an unknown criterion — skip silently;
            # `ontology.validate()` already surfaces this as an issue.
            continue
        for fk in binding.form_field_keys:
            ff_node = node_id(NODE_TYPE_FORM_FIELD, fk)
            if ff_node not in G:
                G.add_node(ff_node, kind=NODE_TYPE_FORM_FIELD, key=fk)
            G.add_edge(crit_id, ff_node, relation=REL_CAPTURED_IN)

    # 4. Criterion --expects_policy_chunk--> PolicyChunkType
    for binding in ontology.criterion_policy_bindings:
        crit_id = node_id(NODE_TYPE_CRITERION, binding.criterion_code)
        if crit_id not in G:
            continue
        for ct in binding.chunk_types:
            pc_node = node_id(NODE_TYPE_POLICY_CHUNK, ct)
            if pc_node not in G:
                G.add_node(pc_node, kind=NODE_TYPE_POLICY_CHUNK, chunk_type=ct)
            G.add_edge(crit_id, pc_node, relation=REL_EXPECTS_POLICY)

    return G


# ──────────────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────────────


_default_graph: nx.DiGraph | None = None


def get_default_graph() -> nx.DiGraph:
    """Process-wide default graph singleton (lazy)."""
    global _default_graph
    if _default_graph is None:
        _default_graph = build_graph()
    return _default_graph


def reset_default_graph() -> None:
    """Force the singleton to rebuild on next access. Useful in tests
    that mutate the ontology mid-process."""
    global _default_graph
    _default_graph = None


# ──────────────────────────────────────────────────────────────────────
# Queries
# ──────────────────────────────────────────────────────────────────────


def _neighbors_with_relation(
    graph: nx.DiGraph, source: str, relation: str,
) -> list[str]:
    return [
        n for n in graph.successors(source)
        if graph.edges[source, n].get("relation") == relation
    ]


def _predecessors_with_relation(
    graph: nx.DiGraph, target: str, relation: str,
) -> list[str]:
    return [
        n for n in graph.predecessors(target)
        if graph.edges[n, target].get("relation") == relation
    ]


def criteria_for_cpt(cpt: str, *, graph: nx.DiGraph | None = None) -> list[str]:
    """All criterion codes applicable to the given CPT."""
    g = graph if graph is not None else get_default_graph()
    cpt_node = node_id(NODE_TYPE_CPT, cpt)
    if cpt_node not in g:
        return []
    return [
        split_node(n)[1]
        for n in _neighbors_with_relation(g, cpt_node, REL_APPLIES_TO)
    ]


def form_fields_for_criterion(code: str, *, graph: nx.DiGraph | None = None) -> list[str]:
    """All form_field keys that capture this criterion."""
    g = graph if graph is not None else get_default_graph()
    crit_node = node_id(NODE_TYPE_CRITERION, code)
    if crit_node not in g:
        return []
    return [
        split_node(n)[1]
        for n in _neighbors_with_relation(g, crit_node, REL_CAPTURED_IN)
    ]


def chart_paths_for_evidence_type(et: str, *, graph: nx.DiGraph | None = None) -> list[str]:
    g = graph if graph is not None else get_default_graph()
    et_node = node_id(NODE_TYPE_EVIDENCE_TYPE, et)
    if et_node not in g:
        return []
    return [
        split_node(n)[1]
        for n in _neighbors_with_relation(g, et_node, REL_SUPPLIED_BY)
    ]


def cpts_using_form_field(field_key: str, *, graph: nx.DiGraph | None = None) -> list[str]:
    """Reverse traversal: for a given form field, which CPTs have at
    least one criterion that the field captures?

    field_field <-captured_in- criterion <-applies_to- cpt
    """
    g = graph if graph is not None else get_default_graph()
    ff_node = node_id(NODE_TYPE_FORM_FIELD, field_key)
    if ff_node not in g:
        return []
    cpts: set[str] = set()
    for crit_node in _predecessors_with_relation(g, ff_node, REL_CAPTURED_IN):
        for cpt_node in _predecessors_with_relation(g, crit_node, REL_APPLIES_TO):
            cpts.add(split_node(cpt_node)[1])
    return sorted(cpts)


def criteria_for_chart_path(path: str, *, graph: nx.DiGraph | None = None) -> list[str]:
    """Reverse-traverse: chart_path <-supplied_by- evidence_type
    <-has_evidence_type- criterion. Returns all criterion codes whose
    evidence type maps to (or whose path is itself) the given path
    (or a prefix of it for list items)."""
    g = graph if graph is not None else get_default_graph()
    base_path = path.split("[", 1)[0]  # strip [N] suffix if present
    cp_node = node_id(NODE_TYPE_CHART_PATH, base_path)
    if cp_node not in g:
        return []
    crits: set[str] = set()
    for et_node in _predecessors_with_relation(g, cp_node, REL_SUPPLIED_BY):
        for crit_node in _predecessors_with_relation(g, et_node, REL_HAS_EVIDENCE_TYPE):
            crits.add(split_node(crit_node)[1])
    return sorted(crits)


def evidence_chain_for_form_field(
    field_key: str, *, graph: nx.DiGraph | None = None,
) -> list[dict]:
    """Walk the chain form_field <-captured_in- criterion
    -has_evidence_type-> evidence_type -supplied_by-> chart_paths.

    Returns one entry per criterion that targets this field:
      {"criterion": code, "evidence_type": str, "chart_paths": [...]}.
    """
    g = graph if graph is not None else get_default_graph()
    ff_node = node_id(NODE_TYPE_FORM_FIELD, field_key)
    if ff_node not in g:
        return []
    out: list[dict] = []
    for crit_node in _predecessors_with_relation(g, ff_node, REL_CAPTURED_IN):
        crit_code = split_node(crit_node)[1]
        et_neighbors = _neighbors_with_relation(g, crit_node, REL_HAS_EVIDENCE_TYPE)
        for et_node in et_neighbors:
            et_value = split_node(et_node)[1]
            chart_paths = chart_paths_for_evidence_type(et_value, graph=g)
            out.append({
                "criterion": crit_code,
                "evidence_type": et_value,
                "chart_paths": chart_paths,
            })
    return out


def shortest_path(
    source_id: str, target_id: str, *, graph: nx.DiGraph | None = None,
) -> list[str] | None:
    """Shortest directed path between two typed node ids, or None."""
    g = graph if graph is not None else get_default_graph()
    if source_id not in g or target_id not in g:
        return None
    try:
        return nx.shortest_path(g, source=source_id, target=target_id)
    except nx.NetworkXNoPath:
        return None


def graph_stats(graph: nx.DiGraph | None = None) -> dict:
    """Summary of the graph for telemetry / sanity dashboards."""
    g = graph if graph is not None else get_default_graph()
    by_kind: dict[str, int] = {}
    for n, attrs in g.nodes(data=True):
        kind = attrs.get("kind", "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    by_relation: dict[str, int] = {}
    for _u, _v, attrs in g.edges(data=True):
        rel = attrs.get("relation", "unknown")
        by_relation[rel] = by_relation.get(rel, 0) + 1
    return {
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
        "nodes_by_kind": by_kind,
        "edges_by_relation": by_relation,
    }


def iter_nodes_of_kind(kind: str, *, graph: nx.DiGraph | None = None) -> Iterator[tuple[str, dict]]:
    g = graph if graph is not None else get_default_graph()
    for n, attrs in g.nodes(data=True):
        if attrs.get("kind") == kind:
            yield n, attrs
