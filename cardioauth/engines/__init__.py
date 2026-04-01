"""CardioAuth Engines — clinical decision support modules for prior authorization."""

from cardioauth.engines.payer_rules import (
    check_auth_required,
    get_payer_matrix,
    flag_at_order_time,
    PAYER_AUTH_MATRIX,
    CPT_DESCRIPTIONS,
)
from cardioauth.engines.icd10_checker import (
    check_code_pairing,
    suggest_stronger_codes,
    estimate_clean_claim_impact,
    CODE_PAIRINGS,
    ICD10_DESCRIPTIONS as ENGINE_ICD10_DESCRIPTIONS,
)
from cardioauth.engines.medical_necessity import (
    analyze_documentation,
    generate_recommendations,
    score_documentation_strength,
    PROCEDURE_REQUIREMENTS,
)

__all__ = [
    # Payer rules
    "check_auth_required",
    "get_payer_matrix",
    "flag_at_order_time",
    "PAYER_AUTH_MATRIX",
    "CPT_DESCRIPTIONS",
    # ICD-10 checker
    "check_code_pairing",
    "suggest_stronger_codes",
    "estimate_clean_claim_impact",
    "CODE_PAIRINGS",
    "ENGINE_ICD10_DESCRIPTIONS",
    # Medical necessity
    "analyze_documentation",
    "generate_recommendations",
    "score_documentation_strength",
    "PROCEDURE_REQUIREMENTS",
]
