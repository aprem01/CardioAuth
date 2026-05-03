"""State 5 — FHIR Provenance + freeze.

CMS-0057-F (effective Jan 1, 2026 for impacted payers) requires
prior-authorization decisions to carry an auditable provenance
trail. Specifically it requires:

  - Who acted (the system, the operator, the LLM model+version)
  - What was done (decision + supporting reasoning)
  - When (UTC timestamp of decision)
  - On what (the source artifacts: note, policy chunks, taxonomy
    version, form schema version)
  - Why (the chain of evidence — quotes, criterion mappings)

FHIR's Provenance resource (R4) is the canonical shape for this.
Every other healthcare actor (Epic, Cerner, payers' UM platforms)
already speaks Provenance. By emitting our audit trail in this
shape, we plug into the existing interop standard rather than
inventing a parallel format.

This module emits Provenance from a LeanRunResult — a pure
function — so the same provenance can be regenerated from a
frozen run record. State 5 calls emit_provenance() then freezes
both the LeanRunResult and the Provenance to durable storage.

Reference:
  https://hl7.org/fhir/R4/provenance.html
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from cardioauth.lean_pipeline import LeanRunResult


# FHIR Provenance is a strict structure. We build it as nested dicts
# so the caller can json.dumps it directly without a dependency on
# fhir.resources.


def emit_provenance(
    result: LeanRunResult,
    *,
    operator_id: str = "demo",
    operator_role: str = "demo",
    site_organization: str = "CardioAuth Demo",
    pipeline_version: str = "lean-hybrid-1.0",
) -> dict[str, Any]:
    """Build a FHIR R4 Provenance resource from a lean run result.

    Pure function — no IO, no shared state. The output is a JSON-
    serializable dict suitable for either local archival or
    transmission to a payer endpoint.
    """
    occurred_at = result.started_at or datetime.now(timezone.utc).isoformat()

    # Stable identifier for this provenance record. Hash the case_id +
    # decision + start time so replays of the same run produce the
    # same identifier (idempotent freeze).
    digest = hashlib.sha256(
        f"{result.case_id}|{result.decision}|{occurred_at}".encode()
    ).hexdigest()[:16]
    provenance_id = f"prov-{digest}"

    # Targets — the artifacts this provenance attests to. Lean produces
    # one canonical artifact: the LeanRunResult itself, addressed by
    # case_id. Payer-side this would become a FHIR ServiceRequest +
    # Coverage + Claim chain.
    target = [
        {
            "reference": f"ServiceRequest/{result.case_id}",
            "display": (
                f"Prior-authorization request for CPT "
                f"{result.resolved_cpt or result.request_cpt} "
                f"({result.payer})"
            ),
        },
    ]

    # Agents — who acted. Three roles:
    #   1. Author: the AI pipeline (lean state machine)
    #   2. Performer: the LLM model that did State 2 reasoning
    #   3. Operator: the human user who initiated the run
    agents = [
        {
            "type": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        "code": "author",
                        "display": "Author",
                    },
                ],
            },
            "who": {
                "identifier": {
                    "system": "https://cardioauth.app/pipeline",
                    "value": pipeline_version,
                },
                "display": f"CardioAuth Lean Hybrid Pipeline ({pipeline_version})",
            },
        },
        {
            "type": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        "code": "performer",
                        "display": "Performer",
                    },
                ],
            },
            "who": {
                "identifier": {
                    "system": "https://anthropic.com/models",
                    "value": _model_from_run(result),
                },
                "display": f"Anthropic LLM ({_model_from_run(result)})",
            },
        },
        {
            "type": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        "code": "enterer",
                        "display": "Enterer",
                    },
                ],
            },
            "role": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/practitioner-role",
                            "code": operator_role,
                        },
                    ],
                },
            ],
            "who": {
                "identifier": {
                    "system": "https://cardioauth.app/users",
                    "value": operator_id,
                },
                "display": operator_id,
            },
            "onBehalfOf": {
                "display": site_organization,
            },
        },
    ]

    # Entity — the source artifacts that fed the decision. Each has a
    # role (source / quotation / derivation) and a reference. Lean
    # carries the raw note in state 2 output; we abstract it as
    # `source-note`.
    entities = []
    out = result.state2_output or {}
    if out:
        entities.extend([
            {
                "role": "source",
                "what": {
                    "display": "Deidentified clinical note (raw_note input to State 2)",
                },
            },
            {
                "role": "source",
                "what": {
                    "display": (
                        f"Applicable criterion taxonomy "
                        f"({len(out.get('criteria_evaluated') or [])} criteria evaluated)"
                    ),
                },
            },
            {
                "role": "derivation",
                "what": {
                    "display": (
                        f"State 2 unified call output: "
                        f"score {result.approval_score:.0%} ({result.approval_label})"
                    ),
                },
            },
        ])

    # Reason — the rationale the gate produced.
    reason = []
    if result.decision_rationale:
        reason.append({
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
                    "code": _decision_to_reason_code(result.decision),
                },
            ],
            "text": result.decision_rationale,
        })

    # Activity — the high-level action (created / approved / blocked)
    activity = {
        "coding": [
            {
                "system": "http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                "code": _decision_to_activity_code(result.decision),
            },
        ],
    }

    # Signature — for compliance, every Provenance must be signed by
    # the system. We attest with a deterministic-style signature:
    # SHA-256 of the canonical decision payload. Real production would
    # use a proper digital signature; this is the audit anchor.
    signature_bytes = _canonical_signature_payload(result).encode()
    signature_digest = hashlib.sha256(signature_bytes).hexdigest()

    return {
        "resourceType": "Provenance",
        "id": provenance_id,
        "meta": {
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
            "profile": [
                "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-provenance",
            ],
        },
        "target": target,
        "occurredDateTime": occurred_at,
        "recorded": datetime.now(timezone.utc).isoformat(),
        "policy": [
            "https://www.cms.gov/cms-interoperability-and-prior-authorization-final-rule-cms-0057-f",
        ],
        "activity": activity,
        "agent": agents,
        "entity": entities,
        "reason": reason,
        "signature": [
            {
                "type": [
                    {
                        "system": "urn:iso-astm:E1762-95:2013",
                        "code": "1.2.840.10065.1.12.1.5",
                        "display": "Verification Signature",
                    },
                ],
                "when": datetime.now(timezone.utc).isoformat(),
                "who": {
                    "identifier": {
                        "system": "https://cardioauth.app/pipeline",
                        "value": pipeline_version,
                    },
                },
                "targetFormat": "application/fhir+json",
                "sigFormat": "application/jose",
                "data": signature_digest,  # SHA-256 of canonical payload
            },
        ],
    }


def _decision_to_reason_code(decision: str) -> str:
    return {
        "transmit": "TREAT",
        "hold_for_review": "HRESCH",  # research/evaluation pending
        "block": "PATRQT",            # patient request blocked / data quality
    }.get(decision, "TREAT")


def _decision_to_activity_code(decision: str) -> str:
    return {
        "transmit": "CREATE",
        "hold_for_review": "HOLD",
        "block": "DELETE",
    }.get(decision, "CREATE")


def _model_from_run(result: LeanRunResult) -> str:
    """Extract the model identifier from the run's stages. Falls back
    to 'unknown' if not recorded — should never happen in production."""
    for stage in result.stages or []:
        detail = stage.get("detail") or {}
        if "model" in detail and detail["model"]:
            return str(detail["model"])
    return "unknown"


def _canonical_signature_payload(result: LeanRunResult) -> str:
    """Build the canonical payload that gets SHA-256 signed.

    Order-stable so the same run always produces the same digest —
    that's what makes the signature meaningful as a tamper check."""
    parts = [
        f"case_id={result.case_id}",
        f"decision={result.decision}",
        f"resolved_cpt={result.resolved_cpt}",
        f"approval_score={result.approval_score:.6f}",
        f"approval_label={result.approval_label}",
        f"started_at={result.started_at}",
        f"finding_count={len(result.findings or [])}",
    ]
    return "|".join(parts)


# ──────────────────────────────────────────────────────────────────────
# Freeze: durable archive of (LeanRunResult, Provenance)
# ──────────────────────────────────────────────────────────────────────


def freeze_lean_run(
    result: LeanRunResult,
    *,
    archive_dir: str | None = None,
) -> dict[str, str]:
    """Write a frozen snapshot of (LeanRunResult, Provenance) to the
    durable archive directory. Returns a dict of artifact paths.

    Idempotent: same case_id + decision + start time produces the
    same provenance_id, and the file write is overwrite-safe so
    replaying a run doesn't corrupt the archive.
    """
    import json
    import logging
    import os
    from pathlib import Path

    logger = logging.getLogger(__name__)

    archive_root = Path(
        archive_dir or os.environ.get("CARDIOAUTH_ARCHIVE_DIR")
        or "/tmp/cardioauth-archive"
    )
    archive_root.mkdir(parents=True, exist_ok=True)

    case_dir = archive_root / result.case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    provenance = emit_provenance(result)

    result_path = case_dir / "lean_run_result.json"
    provenance_path = case_dir / "provenance.fhir.json"

    try:
        result_path.write_text(json.dumps(result.to_dict(), indent=2, default=str))
        provenance_path.write_text(json.dumps(provenance, indent=2, default=str))
    except Exception as e:
        logger.warning("Lean freeze failed (non-fatal): %s", e)
        return {}

    return {
        "result_path": str(result_path),
        "provenance_path": str(provenance_path),
        "provenance_id": provenance.get("id", ""),
        "signature_digest": provenance["signature"][0]["data"],
    }
