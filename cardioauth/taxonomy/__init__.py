"""Structured Criterion Taxonomy for CardioAuth.

A versioned, coded library of clinical criteria used to standardize
prior authorization scoring across cases. Each criterion has a stable
code so that case-to-case comparison and ML training become possible.

Architecture:
  Layer 1 (this module) — Fixed taxonomy of coded criteria
  Layer 2 (matcher.py) — Per-case mapping of chart data to taxonomy
  Layer 3 (emerging.py) — Capture queue for unrecognized payer criteria
"""

from cardioauth.taxonomy.taxonomy import (
    CRITERION_TAXONOMY,
    TAXONOMY_VERSION,
    get_criterion,
    get_criteria_for_procedure,
    get_categories,
)
from cardioauth.taxonomy.matcher import (
    CriterionMatch,
    CaseMatchResult,
    match_case_to_taxonomy,
    score_from_matches,
)
from cardioauth.taxonomy.emerging import (
    EmergingCriterion,
    record_emerging_criterion,
    get_emerging_queue,
    promote_to_taxonomy,
)

__all__ = [
    "CRITERION_TAXONOMY",
    "TAXONOMY_VERSION",
    "get_criterion",
    "get_criteria_for_procedure",
    "get_categories",
    "CriterionMatch",
    "CaseMatchResult",
    "match_case_to_taxonomy",
    "score_from_matches",
    "EmergingCriterion",
    "record_emerging_criterion",
    "get_emerging_queue",
    "promote_to_taxonomy",
]
