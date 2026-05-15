"""Synthetic Epic chart feeder.

Produces FHIR R4 Bundles from markdown case files. The output is shape-
identical to what FHIRClient.get_patient_bundle returns from Epic, so the
downstream pipeline (corpus mapping, retrieval, lean evaluation, packet
PDF generation) runs without a single conditional branch on real-vs-
synthetic. This is intentional: it means anything that works against
the synthetic feeder works against real Epic, and any regression in the
synthetic suite is a real regression in the production pipeline.
"""

from cardioauth.synthetic.loader import (
    SyntheticCase,
    load_case_markdown,
    load_case_by_id,
    list_available_cases,
    case_to_bundle,
)

__all__ = [
    "SyntheticCase",
    "load_case_markdown",
    "load_case_by_id",
    "list_available_cases",
    "case_to_bundle",
]
