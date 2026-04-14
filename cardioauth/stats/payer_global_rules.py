"""Payer-global rules — requirements that apply across every CPT for a payer.

These cross-CPT constraints aren't captured by our per-criterion taxonomy
because they aren't specific to any one procedure — they're conditions of
doing business with the payer. Missing them is a frequent cause of
administrative denial that the reasoner currently has no way to flag.

Examples:
  - UnitedHealthcare: ordering physician must be in-network
  - Aetna: auth void if patient coverage lapses before service date
  - Medicare: Advance Beneficiary Notice required for non-covered services

The rules live here as structured data so they feed into the reasoner
prompt and can be included in the PA narrative preemptively.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PayerGlobalRule:
    payer: str
    rule_id: str
    description: str
    kind: str  # eligibility | network | documentation | timing | notification
    denial_if_missed: bool = True
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "payer": self.payer,
            "rule_id": self.rule_id,
            "description": self.description,
            "kind": self.kind,
            "denial_if_missed": self.denial_if_missed,
            "source": self.source,
        }


_SEED_RULES: list[PayerGlobalRule] = [
    # ── UnitedHealthcare ──
    PayerGlobalRule(
        payer="UnitedHealthcare",
        rule_id="UHC-G-001",
        description="Ordering physician must be in-network. Out-of-network ordering physicians trigger automatic denial even if the service is covered.",
        kind="network",
        source="UHC Commercial Provider Manual 2025",
    ),
    PayerGlobalRule(
        payer="UnitedHealthcare",
        rule_id="UHC-G-002",
        description="Authorization becomes void if patient coverage lapses between approval and service date. Re-verify eligibility within 48 hours of service.",
        kind="eligibility",
        source="UHC Commercial Coverage Policy 2025",
    ),
    PayerGlobalRule(
        payer="UnitedHealthcare",
        rule_id="UHC-G-003",
        description="All PA requests require ICD-10 primary diagnosis code AND at least one documented symptom or clinical finding supporting medical necessity.",
        kind="documentation",
        source="UHC PA Requirement Guide 2025",
    ),

    # ── Aetna ──
    PayerGlobalRule(
        payer="Aetna",
        rule_id="AETNA-G-001",
        description="Prior authorization must be obtained before the service date. Retroactive authorization is not granted except in documented emergency circumstances.",
        kind="timing",
        source="Aetna Clinical Policy Bulletin Framework",
    ),
    PayerGlobalRule(
        payer="Aetna",
        rule_id="AETNA-G-002",
        description="For advanced imaging (PET, SPECT, stress echo), eviCore acts as the utilization management vendor. Submit through eviCore portal, not Aetna directly.",
        kind="notification",
        source="Aetna Provider Manual 2025",
    ),

    # ── Anthem / BCBS ──
    PayerGlobalRule(
        payer="Anthem",
        rule_id="ANTHEM-G-001",
        description="Clinical documentation must include the specific clinical question the study is intended to answer. Generic 'rule out CAD' is insufficient.",
        kind="documentation",
        source="Anthem Medical Policy 2025",
    ),

    # ── Medicare ──
    PayerGlobalRule(
        payer="Medicare",
        rule_id="MEDICARE-G-001",
        description="For services that may not meet medical necessity, an Advance Beneficiary Notice (ABN) must be signed before service.",
        kind="notification",
        source="Medicare Benefit Policy Manual",
    ),
    PayerGlobalRule(
        payer="Medicare",
        rule_id="MEDICARE-G-002",
        description="Local Coverage Determinations (LCDs) apply per MAC region. Verify that the ordering physician and service location fall under the correct MAC.",
        kind="eligibility",
        source="CMS LCD Framework",
    ),
]


def _canonicalize_payer(payer: str) -> str:
    s = (payer or "").lower().strip()
    aliases = {
        "uhc": "unitedhealthcare",
        "united": "unitedhealthcare",
        "united healthcare": "unitedhealthcare",
        "bcbs": "anthem",
        "blue cross": "anthem",
        "blue cross blue shield": "anthem",
        "cms": "medicare",
    }
    return aliases.get(s, s)


_RULES_INDEX: dict[str, list[PayerGlobalRule]] = {}
for _rule in _SEED_RULES:
    _RULES_INDEX.setdefault(_canonicalize_payer(_rule.payer), []).append(_rule)


def get_global_rules(payer: str) -> list[PayerGlobalRule]:
    """Return all cross-CPT rules for a payer. Empty list if none seeded."""
    return list(_RULES_INDEX.get(_canonicalize_payer(payer), []))
