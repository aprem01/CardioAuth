"""Statistical calibration layer.

Gives the reasoner calibration signals — historical approval rates,
top denial reasons, payer-global rules, policy freshness — so approval
likelihood scores correlate with real outcomes and the reasoner can
preemptively address common denial patterns.

Seed data is hand-curated from public payer coverage policies and aggregate
industry data. Production deployment should replace seed data with live
telemetry from SUBMISSION_AGENT outcomes.
"""

from cardioauth.stats.payer_stats import (
    PayerStatistics,
    get_payer_stats,
    list_payer_stats,
)
from cardioauth.stats.payer_global_rules import (
    PayerGlobalRule,
    get_global_rules,
)
from cardioauth.stats.policy_freshness import check_policy_freshness

__all__ = [
    "PayerStatistics",
    "get_payer_stats",
    "list_payer_stats",
    "PayerGlobalRule",
    "get_global_rules",
    "check_policy_freshness",
]
