"""Payer-form routing: take a case context (payer, state, plan type,
CPT, diagnosis, test type) and identify the most likely correct PA form
or pathway. When no form matches, falls back to a portal-ready packet
recommendation.

Peter's 2026-05-18 ask: validate the payer-routing layer against UHC's
public forms page across MA, TX, and other states.
"""

from cardioauth.payer_routing.router import (
    CaseContext,
    FormCandidate,
    RoutingResult,
    route_case,
    list_payers,
    load_catalog,
)

__all__ = [
    "CaseContext",
    "FormCandidate",
    "RoutingResult",
    "route_case",
    "list_payers",
    "load_catalog",
]
