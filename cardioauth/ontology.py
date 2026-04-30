"""SubmissionPacketOntology — the unified domain ontology.

Article alignment ("Ontology-Driven Domain Modelling"): formally define
the relationships between business entities (criteria, form fields,
ChartData buckets, evidence types) in machine-readable form. Don't
let downstream code reason from column names; give it a queryable
ontology.

Peter alignment: "the system needs to reason inductively from the full
context and then normalize the result into payer-compatible outputs."
The ontology is the connective tissue that lets a reviewer mechanically
answer "is this form field's value supported by chart evidence and
cited policy?" — instead of relying on hand-coded callables.

Three declarative tables compose the ontology:

  EvidenceTypeBinding   evidence_type -> ChartData bucket paths
                        (e.g. "ecg" -> "chart.ecg_findings")
  CriterionFormBinding  criterion_code -> form field keys
                        (e.g. "EX-001" -> ("exercise_capacity",
                        "exercise_limitation"))
  CriterionPolicyBinding criterion_code -> policy chunk types
                        (which policy chunks are expected to define
                        each criterion — used by the reviewer to
                        verify citation completeness)

Today the ontology is a dict-backed lookup with reverse indexes.
Phase B.4 will project a NetworkX graph view over the same data so
the reviewer can ask multi-hop questions ("show me every CPT whose
criteria are satisfied by ChartData bucket X") without writing
imperative traversal code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from cardioauth.taxonomy.taxonomy import CRITERION_TAXONOMY


# ──────────────────────────────────────────────────────────────────────
# Binding records
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceTypeBinding:
    """Maps an evidence_type to the ChartData bucket paths that supply it."""

    evidence_type: str
    chart_paths: tuple[str, ...]
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "evidence_type": self.evidence_type,
            "chart_paths": list(self.chart_paths),
            "description": self.description,
        }


@dataclass(frozen=True)
class CriterionFormBinding:
    """Maps a criterion code to the form fields that capture its evidence.

    A criterion may map to several form fields when a payer asks about
    the same concept multiple ways (e.g., SX criteria map to
    "primary_symptoms" + "symptom_change" + "symptom_timeline").
    """

    criterion_code: str
    form_field_keys: tuple[str, ...]
    rationale: str = ""

    def __post_init__(self):
        if isinstance(self.form_field_keys, list):
            object.__setattr__(self, "form_field_keys", tuple(self.form_field_keys))

    def to_dict(self) -> dict:
        return {
            "criterion_code": self.criterion_code,
            "form_field_keys": list(self.form_field_keys),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class CriterionPolicyBinding:
    """Maps a criterion code to expected policy chunk types.

    Used by the reviewer to verify that a criterion's policy citation
    points at the right kind of source (NCD/LCD vs commercial vs
    guideline). When chunk_types is empty the criterion has no
    citation requirement.
    """

    criterion_code: str
    chunk_types: tuple[str, ...] = tuple()  # "ncd" | "lcd" | "policy" | "guideline" | "aha_acc"
    rationale: str = ""

    def __post_init__(self):
        if isinstance(self.chunk_types, list):
            object.__setattr__(self, "chunk_types", tuple(self.chunk_types))

    def to_dict(self) -> dict:
        return {
            "criterion_code": self.criterion_code,
            "chunk_types": list(self.chunk_types),
            "rationale": self.rationale,
        }


# ──────────────────────────────────────────────────────────────────────
# Default declarations
# ──────────────────────────────────────────────────────────────────────


# Each evidence_type used by criteria maps to one or more ChartData
# bucket paths (v2 schema). The reviewer reads this to know "if a
# criterion of type 'ecg' is claimed met, the supporting evidence
# must come from chart.ecg_findings." Field paths use a dotted
# convention rooted at "chart.*" matching the chart_data dict shape.
DEFAULT_EVIDENCE_TYPE_BINDINGS: tuple[EvidenceTypeBinding, ...] = (
    EvidenceTypeBinding(
        evidence_type="imaging",
        chart_paths=(
            "chart.relevant_imaging",
            "chart.prior_stress_tests",
        ),
        description="Imaging studies + cardiology stress tests. Both feed imaging-typed criteria.",
    ),
    EvidenceTypeBinding(
        evidence_type="ecg",
        chart_paths=("chart.ecg_findings",),
        description="Baseline ECG only. Stress test ECG belongs to imaging.",
    ),
    EvidenceTypeBinding(
        evidence_type="clinical_note",
        chart_paths=(
            "chart.current_symptoms",
            "chart.exam_findings",
            "chart.past_medical_history",
            "chart.family_history",
            "chart.additional_notes",
        ),
        description="Free-form clinical content the criterion may reference.",
    ),
    EvidenceTypeBinding(
        evidence_type="medication",
        chart_paths=(
            "chart.relevant_medications",
            "chart.additional_notes",
        ),
        description="Structured med list + note phrasings about duration / titration.",
    ),
    EvidenceTypeBinding(
        evidence_type="lab",
        chart_paths=("chart.relevant_labs",),
        description="Numeric lab values with source anchor.",
    ),
    EvidenceTypeBinding(
        evidence_type="score",
        chart_paths=(
            "chart.additional_notes",
            "chart.current_symptoms",  # NYHA/CCS classes carried on Symptom.severity
        ),
        description="Risk and functional class scores; usually in notes.",
    ),
    EvidenceTypeBinding(
        evidence_type="demographic",
        chart_paths=(
            "chart.age",
            "chart.sex",
            "chart.active_comorbidities",   # BMI lives here as a string entry
            "chart.additional_notes",
        ),
        description="Age, sex, BMI, and other demographic-typed features.",
    ),
)


# Criterion ↔ form field bindings for the UHC cardiac imaging form.
# Each row says "if this criterion's evidence is on the chart, these
# form fields are the place that evidence shows up on the worksheet."
# Many criteria contribute to multiple fields (the same SX criterion
# feeds chief complaint + symptom timeline + symptom change). Some
# fields aren't covered here because they're identification-only
# (patient_name, dob, npi, etc.).
DEFAULT_CRITERION_FORM_BINDINGS: tuple[CriterionFormBinding, ...] = (
    # Non-diagnostic prior testing — drives the "prior_testing" textarea
    CriterionFormBinding(
        criterion_code="NDX-001",
        form_field_keys=("prior_testing",),
        rationale="Non-diagnostic prior stress test populates prior_testing field.",
    ),
    CriterionFormBinding(
        criterion_code="NDX-002",
        form_field_keys=("prior_testing",),
        rationale="Inconclusive prior testing populates prior_testing field.",
    ),
    CriterionFormBinding(
        criterion_code="NDX-003",
        form_field_keys=("prior_testing",),
    ),
    CriterionFormBinding(
        criterion_code="NDX-004",
        form_field_keys=("prior_testing",),
    ),

    # Symptom criteria — three different views on the form
    CriterionFormBinding(
        criterion_code="SX-001",
        form_field_keys=("primary_symptoms", "symptom_change", "chief_complaint_symptoms"),
        rationale="New/worsening symptoms drive primary_symptoms + symptom_change.",
    ),
    CriterionFormBinding(
        criterion_code="SX-002",
        form_field_keys=("primary_symptoms", "symptom_timeline", "chief_complaint_symptoms"),
        rationale="Symptom timeline lives in primary_symptoms + symptom_timeline.",
    ),
    CriterionFormBinding(
        criterion_code="SX-003",
        form_field_keys=("primary_symptoms", "chief_complaint_symptoms"),
    ),
    CriterionFormBinding(
        criterion_code="SX-004",
        form_field_keys=("functional_class",),
        rationale="NYHA/CCS class populates functional_class field.",
    ),

    # Failed medical therapy
    CriterionFormBinding(
        criterion_code="MED-001",
        form_field_keys=("medical_therapy",),
    ),
    CriterionFormBinding(
        criterion_code="MED-002",
        form_field_keys=("medical_therapy",),
        rationale="Med name + dose + duration drives medical_therapy textarea.",
    ),
    CriterionFormBinding(
        criterion_code="MED-003",
        form_field_keys=("medical_therapy",),
    ),

    # BMI
    CriterionFormBinding(
        criterion_code="BMI-001",
        form_field_keys=("bmi",),
        rationale="BMI ≥ 35 supports PET-over-SPECT decision in bmi field.",
    ),
    CriterionFormBinding(
        criterion_code="BMI-002",
        form_field_keys=("prior_testing", "bmi"),
        rationale="Attenuation artifact ties BMI evidence to prior testing notes.",
    ),

    # ECG
    CriterionFormBinding(
        criterion_code="ECG-001",
        form_field_keys=("ecg_findings",),
        rationale="LBBB shows in baseline ecg_findings textarea.",
    ),
    CriterionFormBinding(
        criterion_code="ECG-002",
        form_field_keys=("ecg_findings",),
    ),
    CriterionFormBinding(
        criterion_code="ECG-003",
        form_field_keys=("ecg_findings",),
    ),
    CriterionFormBinding(
        criterion_code="ECG-004",
        form_field_keys=("ecg_findings",),
    ),

    # Exercise capacity
    CriterionFormBinding(
        criterion_code="EX-001",
        form_field_keys=("exercise_capacity", "exercise_limitation"),
        rationale="Specific functional limitation drives both fields together.",
    ),

    # Frequency / duplicate-imaging
    CriterionFormBinding(
        criterion_code="FREQ-001",
        form_field_keys=("no_duplicate_imaging_12mo",),
    ),

    # LVEF — drives clinical_rationale narrative when present
    CriterionFormBinding(
        criterion_code="LVEF-001",
        form_field_keys=("prior_testing", "clinical_rationale"),
    ),
    CriterionFormBinding(
        criterion_code="LVEF-002",
        form_field_keys=("prior_testing",),
    ),

    # Risk / pre-test probability
    CriterionFormBinding(
        criterion_code="RISK-001",
        form_field_keys=("risk_factors", "clinical_rationale"),
    ),
    CriterionFormBinding(
        criterion_code="RISK-002",
        form_field_keys=("risk_factors", "chief_complaint_symptoms"),
    ),
    CriterionFormBinding(
        criterion_code="RISK-003",
        form_field_keys=("risk_factors",),
    ),
)


# Criterion ↔ expected policy chunk type bindings. Today: most criteria
# expect a "policy" chunk (commercial payer policy) plus "ncd"/"lcd"
# for Medicare-applicable codes. This is intentionally sparse — only
# entries that meaningfully constrain the reviewer's citation check.
DEFAULT_CRITERION_POLICY_BINDINGS: tuple[CriterionPolicyBinding, ...] = (
    CriterionPolicyBinding(
        criterion_code="NDX-001",
        chunk_types=("policy", "ncd", "guideline"),
        rationale="Non-diagnostic prior testing is required by both UHC policy and ACC AUC guidelines.",
    ),
    CriterionPolicyBinding(
        criterion_code="BMI-001",
        chunk_types=("policy",),
        rationale="BMI threshold for PET vs SPECT is payer-specific.",
    ),
    CriterionPolicyBinding(
        criterion_code="EX-001",
        chunk_types=("policy", "guideline"),
    ),
    CriterionPolicyBinding(
        criterion_code="FREQ-001",
        chunk_types=("policy",),
        rationale="Frequency rules are payer-specific.",
    ),
)


# ──────────────────────────────────────────────────────────────────────
# SubmissionPacketOntology
# ──────────────────────────────────────────────────────────────────────


@dataclass
class SubmissionPacketOntology:
    """The unified ontology binding criteria, form fields, and chart paths.

    Today: dict-backed with reverse indexes. Phase B.4 layers a
    NetworkX graph view on top of the same data for multi-hop queries.
    """

    evidence_type_bindings: tuple[EvidenceTypeBinding, ...]
    criterion_form_bindings: tuple[CriterionFormBinding, ...]
    criterion_policy_bindings: tuple[CriterionPolicyBinding, ...]
    taxonomy_version: str = ""

    # Reverse indexes (built lazily)
    _criterion_to_form: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)
    _form_to_criteria: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)
    _evidence_to_paths: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)
    _criterion_to_policy: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        # criterion -> form
        self._criterion_to_form = {b.criterion_code: b.form_field_keys for b in self.criterion_form_bindings}

        # form -> criteria (reverse)
        form_to_crit: dict[str, list[str]] = {}
        for b in self.criterion_form_bindings:
            for fk in b.form_field_keys:
                form_to_crit.setdefault(fk, []).append(b.criterion_code)
        self._form_to_criteria = {k: tuple(v) for k, v in form_to_crit.items()}

        # evidence_type -> chart paths
        self._evidence_to_paths = {b.evidence_type: b.chart_paths for b in self.evidence_type_bindings}

        # criterion -> policy chunk types
        self._criterion_to_policy = {b.criterion_code: b.chunk_types for b in self.criterion_policy_bindings}

    # ── Queries ──

    def chart_paths_for_evidence_type(self, evidence_type: str) -> tuple[str, ...]:
        return self._evidence_to_paths.get(evidence_type, tuple())

    def form_fields_for_criterion(self, criterion_code: str) -> tuple[str, ...]:
        return self._criterion_to_form.get(criterion_code, tuple())

    def criteria_for_form_field(self, form_field_key: str) -> tuple[str, ...]:
        return self._form_to_criteria.get(form_field_key, tuple())

    def criteria_for_cpt(self, cpt: str) -> tuple[str, ...]:
        """Return every criterion code that applies to the given CPT.

        Sourced from the criterion taxonomy's `applies_to` field, not
        from the bindings — bindings are about form/policy mapping,
        applicability is about the criterion itself.
        """
        if not cpt:
            return tuple()
        out = [
            code for code, c in CRITERION_TAXONOMY.items()
            if cpt in (c.applies_to or [])
        ]
        return tuple(out)

    def chart_paths_for_form_field(self, form_field_key: str) -> tuple[str, ...]:
        """Indirect lookup: form field -> criteria -> evidence types
        -> chart paths. Returns the union of paths that could supply
        evidence for any criterion bound to this field.
        """
        criteria = self.criteria_for_form_field(form_field_key)
        seen: set[str] = set()
        out: list[str] = []
        for code in criteria:
            crit = CRITERION_TAXONOMY.get(code)
            if not crit:
                continue
            for path in self.chart_paths_for_evidence_type(crit.evidence_type):
                if path not in seen:
                    seen.add(path)
                    out.append(path)
        return tuple(out)

    def expected_policy_chunk_types(self, criterion_code: str) -> tuple[str, ...]:
        return self._criterion_to_policy.get(criterion_code, tuple())

    def evidence_type_for_criterion(self, criterion_code: str) -> str:
        crit = CRITERION_TAXONOMY.get(criterion_code)
        return crit.evidence_type if crit else ""

    def applies_to_for_criterion(self, criterion_code: str) -> tuple[str, ...]:
        crit = CRITERION_TAXONOMY.get(criterion_code)
        return tuple(crit.applies_to) if crit else tuple()

    def all_form_fields_in_ontology(self) -> tuple[str, ...]:
        """Every form field key referenced anywhere in the bindings."""
        return tuple(sorted(self._form_to_criteria.keys()))

    def all_criteria_in_ontology(self) -> tuple[str, ...]:
        """Every criterion code that has at least one form binding."""
        return tuple(sorted(self._criterion_to_form.keys()))

    # ── Validation ──

    def validate(self) -> list[str]:
        """Return a list of integrity problems with the ontology.

        Checks:
          - every criterion code in CriterionFormBinding exists in the
            taxonomy
          - every criterion code in CriterionPolicyBinding exists
          - every evidence_type in the taxonomy has a binding here
          - no duplicate criterion codes in the form bindings
        """
        problems: list[str] = []

        seen_codes: set[str] = set()
        for b in self.criterion_form_bindings:
            if b.criterion_code in seen_codes:
                problems.append(f"duplicate CriterionFormBinding for {b.criterion_code}")
            seen_codes.add(b.criterion_code)
            if b.criterion_code not in CRITERION_TAXONOMY:
                problems.append(
                    f"CriterionFormBinding for unknown criterion {b.criterion_code}"
                )

        for b in self.criterion_policy_bindings:
            if b.criterion_code not in CRITERION_TAXONOMY:
                problems.append(
                    f"CriterionPolicyBinding for unknown criterion {b.criterion_code}"
                )

        used_evidence_types = {c.evidence_type for c in CRITERION_TAXONOMY.values()}
        bound_evidence_types = {b.evidence_type for b in self.evidence_type_bindings}
        for et in used_evidence_types - bound_evidence_types:
            problems.append(
                f"evidence_type {et!r} is used by the taxonomy but not bound to any chart paths"
            )

        return problems

    # ── Serialization ──

    def to_dict(self) -> dict:
        return {
            "evidence_type_bindings": [b.to_dict() for b in self.evidence_type_bindings],
            "criterion_form_bindings": [b.to_dict() for b in self.criterion_form_bindings],
            "criterion_policy_bindings": [b.to_dict() for b in self.criterion_policy_bindings],
            "taxonomy_version": self.taxonomy_version,
        }


# ──────────────────────────────────────────────────────────────────────
# Module-level default ontology
# ──────────────────────────────────────────────────────────────────────


def default_ontology() -> SubmissionPacketOntology:
    """The default ontology compiled from CardioAuth's declarations."""
    from cardioauth.taxonomy.taxonomy import TAXONOMY_VERSION
    return SubmissionPacketOntology(
        evidence_type_bindings=DEFAULT_EVIDENCE_TYPE_BINDINGS,
        criterion_form_bindings=DEFAULT_CRITERION_FORM_BINDINGS,
        criterion_policy_bindings=DEFAULT_CRITERION_POLICY_BINDINGS,
        taxonomy_version=TAXONOMY_VERSION,
    )


_default_singleton: SubmissionPacketOntology | None = None


def get_default_ontology() -> SubmissionPacketOntology:
    """Process-wide default ontology singleton."""
    global _default_singleton
    if _default_singleton is None:
        _default_singleton = default_ontology()
    return _default_singleton
