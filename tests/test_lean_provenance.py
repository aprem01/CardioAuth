"""Tests for State 5 — FHIR Provenance + freeze.

Locks in the CMS-0057-F audit-trail contract: every lean run produces
a Provenance resource that names who/what/when/why, the resource
validates as FHIR R4 in shape, the signature is deterministic for the
same input, and freezing writes the artifacts durably.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cardioauth.lean_pipeline import LeanRunResult
from cardioauth.lean_provenance import (
    emit_provenance,
    freeze_lean_run,
)


def _stub_result(**overrides) -> LeanRunResult:
    base = dict(
        case_id="LEAN-78452-ABC123",
        decision="transmit",
        decision_rationale="All checks passed.",
        request_cpt="78452",
        resolved_cpt="78452",
        payer="UnitedHealthcare",
        approval_score=0.85,
        approval_label="HIGH",
        findings=[],
        pipeline_errors=[],
        stages=[
            {"name": "State 2: unified call", "status": "ok",
             "duration_ms": 6000, "summary": "x",
             "detail": {"model": "claude-opus-4-7"}},
        ],
        started_at="2026-05-03T20:00:00+00:00",
        total_duration_ms=6500,
        state2_tokens=1500,
        state2_cost_usd=0.0525,
        state2_output={
            "criteria_evaluated": [
                {"code": "ECG-001", "status": "met"},
            ],
        },
    )
    base.update(overrides)
    return LeanRunResult(**base)


# ── Shape conformance ──────────────────────────────────────────────────


def test_provenance_resource_type_is_provenance() -> None:
    p = emit_provenance(_stub_result())
    assert p["resourceType"] == "Provenance"


def test_provenance_includes_required_fhir_fields() -> None:
    """FHIR R4 Provenance MUST have: id, target, recorded, agent."""
    p = emit_provenance(_stub_result())
    for field in ("id", "target", "recorded", "agent", "activity"):
        assert field in p, f"missing required field: {field}"


def test_provenance_target_references_the_request() -> None:
    p = emit_provenance(_stub_result(case_id="C-XYZ", resolved_cpt="78492"))
    targets = p["target"]
    assert len(targets) == 1
    assert "C-XYZ" in targets[0]["reference"]
    assert "78492" in targets[0]["display"]


def test_provenance_carries_three_agent_roles() -> None:
    """Author (pipeline) + performer (LLM) + enterer (operator)."""
    p = emit_provenance(_stub_result())
    agents = p["agent"]
    types = [
        a["type"]["coding"][0]["code"] for a in agents
    ]
    assert set(types) == {"author", "performer", "enterer"}


def test_provenance_performer_names_the_llm() -> None:
    p = emit_provenance(_stub_result())
    performer = next(
        a for a in p["agent"]
        if a["type"]["coding"][0]["code"] == "performer"
    )
    assert "claude-opus-4-7" in performer["who"]["identifier"]["value"]


def test_provenance_enterer_carries_operator_id() -> None:
    p = emit_provenance(_stub_result(), operator_id="dr-jcarter")
    enterer = next(
        a for a in p["agent"]
        if a["type"]["coding"][0]["code"] == "enterer"
    )
    assert enterer["who"]["identifier"]["value"] == "dr-jcarter"


def test_provenance_includes_cms_policy_reference() -> None:
    """The CMS-0057-F policy URL must be in the policy field — proves
    we know what we're complying with."""
    p = emit_provenance(_stub_result())
    assert any("cms-0057-f" in pol.lower() for pol in p["policy"])


# ── Activity + reason mapping ─────────────────────────────────────────


def test_activity_maps_decision_to_action() -> None:
    p_transmit = emit_provenance(_stub_result(decision="transmit"))
    p_hold = emit_provenance(_stub_result(decision="hold_for_review"))
    p_block = emit_provenance(_stub_result(decision="block"))

    assert p_transmit["activity"]["coding"][0]["code"] == "CREATE"
    assert p_hold["activity"]["coding"][0]["code"] == "HOLD"
    assert p_block["activity"]["coding"][0]["code"] == "DELETE"


def test_reason_carries_decision_rationale_text() -> None:
    p = emit_provenance(_stub_result(
        decision_rationale="LBBB present — SPECT meets ECG-001."
    ))
    assert p["reason"]
    assert p["reason"][0]["text"] == "LBBB present — SPECT meets ECG-001."


# ── Signature determinism (audit anchor) ─────────────────────────────


def test_signature_is_deterministic_for_same_input() -> None:
    """Same case → same signature digest. That's what makes the
    signature meaningful as a tamper check; if a stored Provenance
    was modified, the digest wouldn't match."""
    a = emit_provenance(_stub_result())
    b = emit_provenance(_stub_result())
    assert a["signature"][0]["data"] == b["signature"][0]["data"]


def test_signature_changes_when_decision_changes() -> None:
    a = emit_provenance(_stub_result(decision="transmit"))
    b = emit_provenance(_stub_result(decision="block"))
    assert a["signature"][0]["data"] != b["signature"][0]["data"]


def test_signature_is_sha256_hex_64_chars() -> None:
    p = emit_provenance(_stub_result())
    sig = p["signature"][0]["data"]
    assert len(sig) == 64
    int(sig, 16)  # pure hex


# ── Provenance ID is stable for replay ──────────────────────────────


def test_provenance_id_is_stable_across_calls() -> None:
    """Replay the same run → same Provenance.id. Idempotent freeze."""
    a = emit_provenance(_stub_result())
    b = emit_provenance(_stub_result())
    assert a["id"] == b["id"]


def test_provenance_id_differs_across_cases() -> None:
    a = emit_provenance(_stub_result(case_id="C-A"))
    b = emit_provenance(_stub_result(case_id="C-B"))
    assert a["id"] != b["id"]


# ── JSON round-trip ──────────────────────────────────────────────────


def test_provenance_round_trips_through_json() -> None:
    p = emit_provenance(_stub_result())
    serialized = json.dumps(p, default=str)
    restored = json.loads(serialized)
    assert restored["resourceType"] == "Provenance"
    assert restored["id"] == p["id"]


# ── Freeze: durable archive ──────────────────────────────────────────


def test_freeze_writes_both_artifacts(tmp_path: Path) -> None:
    result = _stub_result(case_id="C-FREEZE-1")
    paths = freeze_lean_run(result, archive_dir=str(tmp_path))
    assert "result_path" in paths
    assert "provenance_path" in paths
    assert Path(paths["result_path"]).exists()
    assert Path(paths["provenance_path"]).exists()


def test_freeze_carries_signature_digest_in_paths(tmp_path: Path) -> None:
    """The archive paths return the signature digest so a caller can
    verify the on-disk Provenance hasn't been tampered with."""
    result = _stub_result()
    paths = freeze_lean_run(result, archive_dir=str(tmp_path))
    assert "signature_digest" in paths
    assert len(paths["signature_digest"]) == 64


def test_freeze_is_idempotent(tmp_path: Path) -> None:
    """Same input → same files (overwritten, not duplicated)."""
    result = _stub_result()
    a = freeze_lean_run(result, archive_dir=str(tmp_path))
    b = freeze_lean_run(result, archive_dir=str(tmp_path))
    assert a["result_path"] == b["result_path"]
    assert a["signature_digest"] == b["signature_digest"]


def test_freeze_writes_separate_dirs_per_case(tmp_path: Path) -> None:
    """Different case_ids land in different subdirs so we don't
    clobber each other's archives."""
    a = freeze_lean_run(_stub_result(case_id="C-A"), archive_dir=str(tmp_path))
    b = freeze_lean_run(_stub_result(case_id="C-B"), archive_dir=str(tmp_path))
    assert "C-A" in a["result_path"]
    assert "C-B" in b["result_path"]
    assert a["result_path"] != b["result_path"]


# ── Pipeline integration ─────────────────────────────────────────────


def test_lean_pipeline_emits_provenance_in_result(monkeypatch, tmp_path: Path) -> None:
    """End-to-end: run_lean_pipeline → result.provenance + result.archive_paths
    are populated."""
    from cardioauth.lean_pipeline import run_lean_pipeline

    monkeypatch.setenv("CARDIOAUTH_ARCHIVE_DIR", str(tmp_path))

    def fake_llm(system, user):
        payload = {
            "case_id": "TEST-1", "request_cpt": "78452", "payer": "UnitedHealthcare",
            "cpt_resolution": {"cpt": "78452", "procedure_name": "SPECT",
                               "source": "request", "request_cpt": "78452"},
            "patient_name": "X", "date_of_birth": "1950-01-01",
            "insurance_id": "X1", "payer_name": "UnitedHealthcare",
            "attending_physician": "Dr. Y", "attending_npi": "1234567890",
            "criteria_evaluated": [],
            "approval_verdict": {"score": 0.8, "label": "HIGH"},
            "narrative": {"text": "x", "cpt_referenced": "78452",
                          "procedure_referenced": "SPECT"},
            "documentation_quality": {"note_format_quality": "structured"},
        }
        return json.dumps(payload), {
            "input_tokens": 1000, "output_tokens": 500,
            "model": "claude-opus-4-7", "cost_usd": 0.05,
        }

    result = run_lean_pipeline(
        raw_note="Patient: X\nDOB: 01/01/1950\nMember ID: X1\nNPI: 1234567890",
        request_cpt="78452", payer="UnitedHealthcare",
        llm_caller=fake_llm,
    )
    assert result.provenance is not None
    assert result.provenance["resourceType"] == "Provenance"
    assert result.archive_paths is not None
    assert Path(result.archive_paths["result_path"]).exists()


def test_lean_pipeline_records_state5_stage(monkeypatch, tmp_path: Path) -> None:
    from cardioauth.lean_pipeline import run_lean_pipeline

    monkeypatch.setenv("CARDIOAUTH_ARCHIVE_DIR", str(tmp_path))

    def fake_llm(system, user):
        payload = {
            "case_id": "T", "request_cpt": "78452", "payer": "UHC",
            "cpt_resolution": {"cpt": "78452", "procedure_name": "X",
                               "source": "request", "request_cpt": "78452"},
            "patient_name": "X", "date_of_birth": "1950-01-01",
            "insurance_id": "X1", "payer_name": "UHC",
            "attending_physician": "Y", "attending_npi": "1234567890",
            "criteria_evaluated": [],
            "approval_verdict": {"score": 0.8, "label": "HIGH"},
            "narrative": {"text": "x", "cpt_referenced": "78452",
                          "procedure_referenced": "X"},
            "documentation_quality": {"note_format_quality": "structured"},
        }
        return json.dumps(payload), {"input_tokens": 100, "output_tokens": 50,
                                      "model": "claude-opus-4-7", "cost_usd": 0.01}

    result = run_lean_pipeline(
        raw_note="Patient: X\nDOB: 01/01/1950\nMember ID: X1\nNPI: 1234567890",
        request_cpt="78452", payer="UHC",
        llm_caller=fake_llm,
    )
    state5 = next(
        (s for s in result.stages if s.get("name", "").startswith("State 5")),
        None,
    )
    assert state5 is not None
    assert state5["status"] == "ok"
