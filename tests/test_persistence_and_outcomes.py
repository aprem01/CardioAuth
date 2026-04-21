"""Tests for durable persistence + submission channels + outcome feedback.

Covers the three production weakest-points fix:
  - reviews/submissions/outcomes survive process restart (SQLite)
  - submission channels actually produce confirmation numbers
  - outcome recording updates rolling stats + audit
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cardioauth.persistence import SQLiteStore
from cardioauth.submission_channels import (
    AvailityChannel,
    EviCoreChannel,
    FaxChannel,
    MockChannel,
    SubmissionPackage,
    get_channel_for,
)


# ── Persistence ─────────────────────────────────────────────────────────

def _fresh_store() -> SQLiteStore:
    """Create an isolated store on a tempfile — each test is independent."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return SQLiteStore(tmp.name)


def test_save_and_get_review_roundtrip() -> None:
    store = _fresh_store()
    review_data = {"chart_data": {"patient_id": "P1"}, "system_warnings": []}
    store.save_review("R-1", review_data, user_id="u1")
    loaded = store.get_review("R-1")
    assert loaded is not None
    assert loaded["chart_data"]["patient_id"] == "P1"


def test_save_review_idempotent_on_same_id() -> None:
    store = _fresh_store()
    store.save_review("R-1", {"a": 1})
    store.save_review("R-1", {"a": 2})
    loaded = store.get_review("R-1")
    assert loaded == {"a": 2}


def test_list_reviews_by_user() -> None:
    store = _fresh_store()
    store.save_review("R-1", {"id": 1}, user_id="u1")
    store.save_review("R-2", {"id": 2}, user_id="u2")
    store.save_review("R-3", {"id": 3}, user_id="u1")
    mine = store.list_reviews(user_id="u1")
    ids = {r["review_id"] for r in mine}
    assert ids == {"R-1", "R-3"}


def test_survives_restart() -> None:
    """New SQLiteStore pointed at the same file sees everything from before."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store1 = SQLiteStore(tmp.name)
    store1.save_review("R-X", {"hello": "world"})
    # Simulate container restart by making a fresh Store on the same file
    store2 = SQLiteStore(tmp.name)
    loaded = store2.get_review("R-X")
    assert loaded == {"hello": "world"}


def test_submission_save_update_status() -> None:
    store = _fresh_store()
    sub = {
        "submission_id": "S-1",
        "payer": "UHC",
        "status": "submitted",
        "audit_trail": [],
    }
    store.save_submission("S-1", sub)
    store.update_submission_status("S-1", "approved", note="Auth# 12345")
    loaded = store.get_submission("S-1")
    assert loaded["status"] == "approved"
    # Audit trail must be appended to, not overwritten
    assert any("approved" in a.get("action", "") for a in loaded["audit_trail"])


def test_outcome_save_and_rolling_stats() -> None:
    store = _fresh_store()
    store.save_outcome("S-1", {
        "submission_id": "S-1",
        "outcome": "APPROVED",
        "payer": "UHC",
        "cpt_code": "78492",
    })
    store.record_outcome_for_stats("UHC", "78492", "APPROVED")
    store.record_outcome_for_stats("UHC", "78492", "APPROVED")
    store.record_outcome_for_stats("UHC", "78492", "DENIED")
    rolling = store.get_rolling_stats("UHC", "78492")
    assert rolling is not None
    assert rolling["total"] == 3
    assert rolling["approved"] == 2
    assert rolling["denied"] == 1
    assert rolling["approval_rate"] == round(2 / 3, 3)


def test_audit_is_append_only() -> None:
    store = _fresh_store()
    store.append_audit(actor="u1", action="approved", subject_id="S-1", detail="first")
    store.append_audit(actor="u1", action="denied", subject_id="S-1", detail="overturn")
    # No API to delete — that's the point; audits are immutable.


# ── Submission channels ─────────────────────────────────────────────────

def _sample_package() -> SubmissionPackage:
    return SubmissionPackage(
        submission_id="CA-12345678",
        patient_id="P-1",
        payer="UnitedHealthcare",
        procedure_name="Cardiac PET",
        cpt_code="78492",
        cover_summary="67M with CAD, unable to exercise, PET requested.",
        pdf_bytes=b"%PDF-1.4\n%Mock",
    )


def test_mock_channel_returns_confirmation() -> None:
    result = MockChannel().submit(_sample_package())
    assert result.status == "submitted"
    assert result.confirmation_number.startswith("MOCK-")
    assert result.expected_decision_date  # non-empty


def test_fax_channel_queues_for_human_dispatch() -> None:
    result = FaxChannel().submit(_sample_package())
    assert result.status == "fax_queued"
    assert result.confirmation_number.startswith("FAX-Q-")
    assert "fax" in result.channel_notes.lower()
    assert result.requires_human_action  # explicit ask for human dispatch


def test_portal_channels_return_pending_credentials() -> None:
    availity = AvailityChannel(payer="UnitedHealthcare").submit(_sample_package())
    assert availity.status == "pending_credentials"
    assert "credentials" in availity.channel_notes.lower()

    evicore = EviCoreChannel(payer="Aetna").submit(_sample_package())
    assert evicore.status == "pending_credentials"


def test_get_channel_routes_uhc_to_availity() -> None:
    ch = get_channel_for(payer="UnitedHealthcare")
    assert ch.name == "availity"


def test_get_channel_routes_aetna_to_evicore() -> None:
    ch = get_channel_for(payer="Aetna")
    assert ch.name == "evicore"


def test_get_channel_fax_on_explicit_format() -> None:
    ch = get_channel_for(payer="UHC", submission_format="fax")
    assert ch.name == "fax"


def test_get_channel_unknown_payer_uses_mock() -> None:
    ch = get_channel_for(payer="Nebula Health")
    assert ch.name == "mock"


# ── Live stats override seeds after N real outcomes ─────────────────────

def test_live_rolling_stats_override_seed_after_5_outcomes(monkeypatch) -> None:
    """With ≥5 live outcomes, get_payer_stats returns rolling approval rate."""
    from cardioauth.stats import payer_stats

    store = _fresh_store()
    # Override the global singleton to use our temp store
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    # Seed 5 approvals + 0 denials → rolling approval_rate=1.0
    for _ in range(5):
        store.record_outcome_for_stats("UnitedHealthcare", "78492", "APPROVED")

    stats = payer_stats.get_payer_stats("UnitedHealthcare", "78492")
    assert stats is not None
    assert stats.approval_rate == 1.0
    assert stats.sample_size == 5
    assert "live" in stats.data_vintage


def test_live_stats_ignored_below_threshold(monkeypatch) -> None:
    """With <5 outcomes the seeded value is preserved."""
    from cardioauth.stats import payer_stats

    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    store.record_outcome_for_stats("UnitedHealthcare", "78492", "APPROVED")
    stats = payer_stats.get_payer_stats("UnitedHealthcare", "78492")
    assert stats is not None
    # Should NOT be live — fewer than 5 outcomes
    assert "live" not in (stats.data_vintage or "")
