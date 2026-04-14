"""Policy freshness checker.

The POLICY_AGENT captures policy_last_updated from the retrieved chunks,
but nothing consumes it. Payers revise coverage policies frequently —
UHC's cardiology CPBs update roughly quarterly. A chunk retrieved from
an 18-month-old policy may be stale even though the retrieval score is
high.

This module surfaces freshness as a visible signal so the cardiologist
knows when they're looking at criteria that may no longer reflect the
current policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


STALE_DAYS_WARNING = 90
STALE_DAYS_CRITICAL = 365


@dataclass
class FreshnessResult:
    policy_last_updated: str
    age_days: int | None
    level: str   # fresh | stale_warning | stale_critical | unknown
    message: str


def check_policy_freshness(policy_last_updated: str | None, as_of: date | None = None) -> FreshnessResult:
    """Classify a policy's age and emit a structured message.

    Input format tolerated: ISO 8601 date (YYYY-MM-DD) or datetime. Unknown
    or unparseable → returned with level="unknown" so the caller can still
    surface the fact that freshness is unverified.
    """
    as_of = as_of or datetime.now(timezone.utc).date()

    if not policy_last_updated:
        return FreshnessResult(
            policy_last_updated="",
            age_days=None,
            level="unknown",
            message="Policy last-updated date not available; freshness cannot be verified.",
        )

    parsed: date | None = None
    s = policy_last_updated.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(s[: len(fmt.replace("%z", "+0000"))], fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = date.fromisoformat(s[:10])
        except ValueError:
            return FreshnessResult(
                policy_last_updated=policy_last_updated,
                age_days=None,
                level="unknown",
                message=f"Could not parse policy date '{policy_last_updated}'.",
            )

    age = (as_of - parsed).days

    if age < 0:
        return FreshnessResult(
            policy_last_updated=policy_last_updated,
            age_days=age,
            level="fresh",
            message="Policy last-updated date is in the future; treating as fresh.",
        )

    if age <= STALE_DAYS_WARNING:
        return FreshnessResult(
            policy_last_updated=policy_last_updated,
            age_days=age,
            level="fresh",
            message=f"Policy is {age} days old (fresh; threshold {STALE_DAYS_WARNING}d).",
        )

    if age <= STALE_DAYS_CRITICAL:
        return FreshnessResult(
            policy_last_updated=policy_last_updated,
            age_days=age,
            level="stale_warning",
            message=(
                f"Policy is {age} days old (> {STALE_DAYS_WARNING}d warning threshold). "
                f"Verify that current payer policy still matches these criteria before submission."
            ),
        )

    return FreshnessResult(
        policy_last_updated=policy_last_updated,
        age_days=age,
        level="stale_critical",
        message=(
            f"Policy is {age} days old (> {STALE_DAYS_CRITICAL}d critical threshold). "
            f"This policy is very likely out of date. Re-ingest current payer policy "
            f"before relying on these criteria."
        ),
    )
