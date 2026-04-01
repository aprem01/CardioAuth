"""Cardiology Modifier Checker Engine.

Validates CPT code combinations against NCCI edit pairs, suggests required
modifiers, and enforces PCI-specific billing rules per CMS guidelines.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# NCCI Edit Pairs — Cardiology
# Column 1 = comprehensive code, Column 2 = component code
# modifier_allowed: True = modifier can unbundle; False = always bundled
# ---------------------------------------------------------------------------

NCCI_EDITS: list[dict[str, Any]] = [
    # Cardiac catheterization pairs
    {"column1": "93458", "column2": "93452", "modifier_allowed": True,
     "note": "Left heart cath with right heart cath — use modifier 59 for separate procedural service"},
    {"column1": "93458", "column2": "93453", "modifier_allowed": True,
     "note": "Left heart cath with combined right/left cath — mutually exclusive, bill higher RVU code"},
    {"column1": "93458", "column2": "93571", "modifier_allowed": True,
     "note": "Cath with intravascular flow reserve (FFR) — modifier 26 if interpretation only"},
    {"column1": "93459", "column2": "93452", "modifier_allowed": True,
     "note": "Left cath + angio with right heart cath — modifier 59 if clinically distinct indication"},
    {"column1": "93460", "column2": "93458", "modifier_allowed": False,
     "note": "Combined L/R cath with angio bundles left cath with angio — cannot unbundle"},
    {"column1": "93461", "column2": "93459", "modifier_allowed": False,
     "note": "Combined cath bundles with left cath + ventriculography — always bundled"},

    # Echocardiography pairs
    {"column1": "93306", "column2": "93320", "modifier_allowed": False,
     "note": "Complete TTE with Doppler — Doppler (93320) is bundled into 93306, cannot bill separately"},
    {"column1": "93306", "column2": "93325", "modifier_allowed": False,
     "note": "Complete TTE with color flow Doppler — color flow bundled into comprehensive echo"},
    {"column1": "93312", "column2": "93320", "modifier_allowed": False,
     "note": "TEE with Doppler — Doppler bundled into transesophageal echo"},
    {"column1": "93312", "column2": "93325", "modifier_allowed": False,
     "note": "TEE with color flow — color flow bundled into TEE"},
    {"column1": "93350", "column2": "93306", "modifier_allowed": True,
     "note": "Stress echo with resting echo — modifier 59 if resting echo done separately on different date"},
    {"column1": "93350", "column2": "93320", "modifier_allowed": False,
     "note": "Stress echo with Doppler — Doppler bundled into stress echo evaluation"},

    # PCI pairs
    {"column1": "92928", "column2": "92921", "modifier_allowed": True,
     "note": "PCI stent single vessel with additional vessel PCI — use modifier XS for separate structure (different vessel)"},
    {"column1": "92928", "column2": "92920", "modifier_allowed": False,
     "note": "PCI stent bundles with balloon angioplasty same vessel — angioplasty included in stent placement"},
    {"column1": "92929", "column2": "92921", "modifier_allowed": True,
     "note": "PCI stent add-on vessel with additional vessel — use modifier XS for distinct vessel"},
    {"column1": "92941", "column2": "92928", "modifier_allowed": True,
     "note": "Acute MI PCI with elective PCI — modifier 59 if treating separate vessel from infarct-related artery"},
    {"column1": "92943", "column2": "92928", "modifier_allowed": False,
     "note": "Chronic total occlusion PCI bundles with standard PCI same vessel"},

    # Electrophysiology pairs
    {"column1": "93619", "column2": "93600", "modifier_allowed": False,
     "note": "Comprehensive EP study bundles bundle of His recording — His recording included"},
    {"column1": "93619", "column2": "93602", "modifier_allowed": False,
     "note": "Comprehensive EP study bundles intra-atrial recording"},
    {"column1": "93619", "column2": "93603", "modifier_allowed": False,
     "note": "Comprehensive EP study bundles right ventricular recording"},
    {"column1": "93653", "column2": "93619", "modifier_allowed": False,
     "note": "SVT ablation bundles comprehensive EP study — EP study included in ablation"},
    {"column1": "93656", "column2": "93621", "modifier_allowed": False,
     "note": "AFib ablation bundles LA pacing/recording — included in ablation procedure"},

    # Nuclear cardiology / stress testing pairs
    {"column1": "78452", "column2": "78451", "modifier_allowed": False,
     "note": "SPECT MPI rest+stress bundles SPECT MPI stress only — cannot bill both"},
    {"column1": "78452", "column2": "93015", "modifier_allowed": True,
     "note": "SPECT MPI with exercise stress — modifier 26/TC applicable for split billing"},
    {"column1": "93018", "column2": "93015", "modifier_allowed": False,
     "note": "Stress test interpretation only with global stress test — mutually exclusive"},
    {"column1": "93017", "column2": "93015", "modifier_allowed": False,
     "note": "Stress test tracing only with global stress test — mutually exclusive"},
]

# ---------------------------------------------------------------------------
# PCI Billing Rules (per CMS / ACC guidelines)
# ---------------------------------------------------------------------------

PCI_RULES: dict[str, Any] = {
    "per_vessel_not_per_lesion": True,
    "max_vessels": 3,
    "base_code": "92928",       # PCI with stent — single vessel
    "addon_code": "92929",      # Each additional vessel (max 2 add-on units)
    "acute_mi_code": "92941",   # PCI for acute ST-elevation MI
    "cto_code": "92943",        # Chronic total occlusion PCI
    "balloon_only_code": "92920",  # Balloon angioplasty without stent — single vessel
    "balloon_addon_code": "92921", # Balloon angioplasty add-on vessel
    "required_documentation": [
        "vessel treated (LAD, LCx, RCA, etc.)",
        "number of lesions per vessel",
        "stent type (DES or BMS) and dimensions",
        "pre-intervention stenosis percentage",
        "post-intervention stenosis percentage",
        "TIMI flow grade pre and post",
        "complications or lack thereof",
    ],
    "notes": [
        "Bill per vessel, not per lesion — multiple stents in same vessel = one code",
        "92928 is base; 92929 is add-on for each additional vessel (max 2)",
        "Acute MI PCI (92941) replaces 92928 for infarct-related artery",
        "If stent placed, do not separately bill balloon angioplasty (92920) same vessel",
        "Diagnostic cath (93458) on same date as PCI requires modifier 59 and separate indication",
    ],
}

# ---------------------------------------------------------------------------
# Modifier Reference
# ---------------------------------------------------------------------------

MODIFIERS: dict[str, dict[str, str]] = {
    "26": {
        "description": "Professional component",
        "use": "Interpretation only — physician did not own/operate the equipment",
    },
    "59": {
        "description": "Distinct procedural service",
        "use": "Separate and distinct procedure performed in the same session; requires documentation of distinct indication, site, or purpose",
    },
    "XS": {
        "description": "Separate structure",
        "use": "Service on a separate organ or anatomical structure (e.g., PCI on different coronary vessel)",
    },
    "XE": {
        "description": "Separate encounter",
        "use": "Different encounter on the same date of service",
    },
    "XP": {
        "description": "Separate practitioner",
        "use": "Service performed by a different practitioner in same group",
    },
    "XU": {
        "description": "Unusual non-overlapping service",
        "use": "Use when the service is distinct but does not meet XS/XE/XP criteria",
    },
    "LT": {
        "description": "Left side",
        "use": "Procedure performed on left side of body",
    },
    "RT": {
        "description": "Right side",
        "use": "Procedure performed on right side of body",
    },
    "76": {
        "description": "Repeat procedure, same physician",
        "use": "Same procedure repeated on the same day by the same provider",
    },
    "77": {
        "description": "Repeat procedure, different physician",
        "use": "Same procedure repeated on the same day by a different provider",
    },
    "TC": {
        "description": "Technical component",
        "use": "Facility billing for technical component only (equipment, tech staff)",
    },
    "25": {
        "description": "Significant, separately identifiable E/M service",
        "use": "E/M on same day as procedure — must be separately documented and beyond pre/post work",
    },
    "50": {
        "description": "Bilateral procedure",
        "use": "Procedure performed bilaterally during same session",
    },
}

# ---------------------------------------------------------------------------
# CPT descriptions for readable output
# ---------------------------------------------------------------------------

_CPT_DESCRIPTIONS: dict[str, str] = {
    "92920": "Balloon angioplasty, single vessel",
    "92921": "Balloon angioplasty, each additional vessel",
    "92928": "PCI with stent, single vessel",
    "92929": "PCI with stent, each additional vessel",
    "92941": "PCI for acute ST-elevation MI",
    "92943": "PCI for chronic total occlusion",
    "93015": "Cardiovascular stress test (global)",
    "93017": "Cardiovascular stress test, tracing only",
    "93018": "Cardiovascular stress test, interpretation only",
    "93306": "Transthoracic echocardiography, complete",
    "93312": "Transesophageal echocardiography",
    "93320": "Doppler echocardiography",
    "93325": "Color flow Doppler",
    "93350": "Stress echocardiography",
    "93452": "Right heart catheterization",
    "93453": "Combined right and left heart catheterization",
    "93458": "Left heart catheterization with coronary angiography",
    "93459": "Left heart catheterization with angio and ventriculography",
    "93460": "Combined R/L heart cath with coronary angiography",
    "93461": "Combined R/L heart cath with angio and ventriculography",
    "93571": "Intravascular Doppler flow velocity (FFR/iFR)",
    "93600": "Bundle of His recording",
    "93602": "Intra-atrial recording",
    "93603": "Right ventricular recording",
    "93619": "Comprehensive EP study without ablation",
    "93621": "Left atrial pacing/recording",
    "93653": "SVT ablation",
    "93656": "Atrial fibrillation ablation",
    "78451": "SPECT MPI, stress only",
    "78452": "SPECT MPI, rest and stress",
}


# ---------------------------------------------------------------------------
# Public Functions
# ---------------------------------------------------------------------------


def check_modifiers(cpt_codes: list[str], modifiers: list[str] | None = None) -> dict[str, Any]:
    """Validate code + modifier combinations. Flags conflicts and missing modifiers.

    Args:
        cpt_codes: List of CPT codes being billed together.
        modifiers: List of modifiers already applied (e.g. ["59", "XS"]).

    Returns:
        dict with keys: valid (bool), issues (list[str]), warnings (list[str]),
        applied_modifiers (list), suggestions (list).
    """
    modifiers = modifiers or []
    issues: list[str] = []
    warnings: list[str] = []
    suggestions: list[dict[str, str]] = []

    code_set = set(cpt_codes)
    modifier_set = set(modifiers)

    for edit in NCCI_EDITS:
        col1, col2 = edit["column1"], edit["column2"]
        if col1 in code_set and col2 in code_set:
            if not edit["modifier_allowed"]:
                issues.append(
                    f"NCCI conflict: {col1} ({_cpt_desc(col1)}) and {col2} ({_cpt_desc(col2)}) "
                    f"are bundled and CANNOT be billed together. {edit['note']}"
                )
            else:
                # Modifier is allowed — check if one is present
                needed = _suggested_modifier_for_edit(edit)
                if needed and needed not in modifier_set:
                    warnings.append(
                        f"Codes {col1} and {col2} require modifier {needed} to unbundle. "
                        f"{edit['note']}"
                    )
                    suggestions.append({
                        "code_pair": f"{col1}/{col2}",
                        "modifier": needed,
                        "reason": edit["note"],
                    })

    # Check for mutually exclusive component billing (26 + TC on same code)
    if "26" in modifier_set and "TC" in modifier_set:
        warnings.append(
            "Modifiers 26 (professional) and TC (technical) should not both appear "
            "on the same claim line — bill globally or split components to separate lines."
        )

    valid = len(issues) == 0
    return {
        "valid": valid,
        "issues": issues,
        "warnings": warnings,
        "applied_modifiers": modifiers,
        "suggestions": suggestions,
    }


def suggest_modifiers(cpt_codes: list[str]) -> list[dict[str, str]]:
    """Suggest required modifiers for a set of CPT codes billed together.

    Returns a list of dicts with keys: code_pair, modifier, reason.
    """
    suggestions: list[dict[str, str]] = []
    code_set = set(cpt_codes)

    for edit in NCCI_EDITS:
        col1, col2 = edit["column1"], edit["column2"]
        if col1 in code_set and col2 in code_set and edit["modifier_allowed"]:
            needed = _suggested_modifier_for_edit(edit)
            if needed:
                suggestions.append({
                    "code_pair": f"{col1}/{col2}",
                    "modifier": needed,
                    "description": MODIFIERS.get(needed, {}).get("description", ""),
                    "reason": edit["note"],
                })

    return suggestions


def validate_pci_billing(
    vessels_treated: list[str],
    codes_billed: list[str],
    is_acute_mi: bool = False,
) -> dict[str, Any]:
    """Validate PCI billing against CMS per-vessel rules.

    Args:
        vessels_treated: e.g. ["LAD", "RCA", "LCx"]
        codes_billed: CPT codes submitted for the PCI session.
        is_acute_mi: True if one vessel is the acute MI infarct-related artery.

    Returns:
        dict with valid, issues, expected_codes, documentation_required.
    """
    issues: list[str] = []
    n_vessels = len(vessels_treated)
    rules = PCI_RULES

    if n_vessels > rules["max_vessels"]:
        issues.append(
            f"Billing {n_vessels} vessels exceeds CMS maximum of {rules['max_vessels']} "
            f"reportable coronary territories per session."
        )

    if n_vessels == 0:
        issues.append("No vessels documented — at least one vessel must be specified.")
        return {"valid": False, "issues": issues, "expected_codes": [], "documentation_required": rules["required_documentation"]}

    # Build expected code set
    expected: list[str] = []
    if is_acute_mi:
        expected.append(rules["acute_mi_code"])
        addon_count = min(n_vessels - 1, 2)
    else:
        expected.append(rules["base_code"])
        addon_count = min(n_vessels - 1, 2)

    for _ in range(addon_count):
        expected.append(rules["addon_code"])

    billed_set = sorted(codes_billed)
    expected_set = sorted(expected)

    # Check for balloon-only same-vessel conflict
    if rules["base_code"] in codes_billed and rules["balloon_only_code"] in codes_billed:
        issues.append(
            f"Cannot bill {rules['balloon_only_code']} (balloon angioplasty) with "
            f"{rules['base_code']} (stent) for the same vessel — angioplasty is bundled into stent placement."
        )

    # Check vessel count vs code count
    base_codes_billed = [c for c in codes_billed if c in (rules["base_code"], rules["acute_mi_code"])]
    addon_codes_billed = [c for c in codes_billed if c == rules["addon_code"]]

    if len(base_codes_billed) > 1:
        issues.append(
            "Multiple base PCI codes billed — only one base code (92928 or 92941) per session."
        )

    if len(addon_codes_billed) > 2:
        issues.append(
            f"Billed {len(addon_codes_billed)} add-on units of {rules['addon_code']} — "
            f"maximum is 2 additional vessels."
        )

    if len(addon_codes_billed) + 1 != n_vessels and n_vessels <= rules["max_vessels"]:
        issues.append(
            f"Vessel count ({n_vessels}) does not match code count: "
            f"{len(base_codes_billed)} base + {len(addon_codes_billed)} add-on = "
            f"{len(base_codes_billed) + len(addon_codes_billed)} coded vessels."
        )

    # Duplicate vessel check
    if len(set(vessels_treated)) != len(vessels_treated):
        issues.append(
            "Duplicate vessel names detected — PCI is billed per vessel, not per lesion. "
            "Multiple lesions in the same vessel = one code."
        )

    valid = len(issues) == 0
    return {
        "valid": valid,
        "issues": issues,
        "expected_codes": expected,
        "billed_codes": codes_billed,
        "vessels": vessels_treated,
        "documentation_required": rules["required_documentation"],
    }


def check_bundling(cpt_codes: list[str]) -> dict[str, Any]:
    """Identify all bundled (non-separable) pairs in the submitted code set.

    Returns dict with bundled_pairs list and separable_pairs list.
    """
    code_set = set(cpt_codes)
    bundled: list[dict[str, str]] = []
    separable: list[dict[str, str]] = []

    for edit in NCCI_EDITS:
        col1, col2 = edit["column1"], edit["column2"]
        if col1 in code_set and col2 in code_set:
            entry = {
                "column1": col1,
                "column1_desc": _cpt_desc(col1),
                "column2": col2,
                "column2_desc": _cpt_desc(col2),
                "note": edit["note"],
            }
            if edit["modifier_allowed"]:
                separable.append(entry)
            else:
                bundled.append(entry)

    return {
        "total_codes": len(cpt_codes),
        "bundled_pairs": bundled,
        "separable_with_modifier": separable,
        "has_conflicts": len(bundled) > 0,
        "action_required": (
            "Remove bundled codes or revise billing"
            if bundled
            else "No hard conflicts — apply suggested modifiers for separable pairs"
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cpt_desc(code: str) -> str:
    return _CPT_DESCRIPTIONS.get(code, code)


def _suggested_modifier_for_edit(edit: dict[str, Any]) -> str | None:
    """Infer the best modifier to use from the edit note text."""
    note_lower = edit["note"].lower()
    if "modifier xs" in note_lower or "separate structure" in note_lower or "different vessel" in note_lower:
        return "XS"
    if "modifier 59" in note_lower or "distinct" in note_lower:
        return "59"
    if "modifier 26" in note_lower or "interpretation" in note_lower or "interp only" in note_lower:
        return "26"
    if "modifier xe" in note_lower or "separate encounter" in note_lower:
        return "XE"
    return "59"  # Default safe suggestion
