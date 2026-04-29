"""Tests for the recall-queue feature (Peter Apr 28 — Tier 1)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cardioauth.recall_queue import (
    apply_action,
    compute_expected_followup,
    compute_recall_state,
    follow_up_months_for,
    list_recall_queue,
    queue_kpis,
    seed_demo_recalls,
    upsert_recall_entry,
    value_estimate_for,
)


@pytest.fixture
def fresh_store(monkeypatch, tmp_path):
    """Point the store at an empty tmp DB so tests don't pollute prod data."""
    from cardioauth import persistence
    db = tmp_path / "test_recall.db"
    persistence._store_singleton = None
    monkeypatch.setenv("CARDIOAUTH_DB_PATH", str(db))
    yield
    persistence._store_singleton = None


# ── Lookup tables ───────────────────────────────────────────────────────

def test_pet_followup_window_six_months() -> None:
    assert follow_up_months_for("78492") == 6
    assert follow_up_months_for("78491") == 6


def test_tavr_tight_window() -> None:
    assert follow_up_months_for("33361") == 1


def test_unknown_cpt_default_window() -> None:
    assert follow_up_months_for("99999") == 6


def test_value_estimate_pet() -> None:
    assert value_estimate_for("78492") == 1500


def test_value_estimate_tavr_high() -> None:
    assert value_estimate_for("33361") >= 10000


def test_value_estimate_default_when_unknown() -> None:
    assert value_estimate_for("99999") == 500.0


# ── compute_expected_followup ──────────────────────────────────────────

def test_expected_followup_six_month_window() -> None:
    out = compute_expected_followup("2026-01-01", "78492")
    # 6 months ~= 180 days from 2026-01-01
    assert out == "2026-06-30"


def test_expected_followup_tavr_one_month() -> None:
    out = compute_expected_followup("2026-04-01", "33361")
    assert out == "2026-05-01"


# ── compute_recall_state ───────────────────────────────────────────────

def test_pending_when_followup_date_in_future() -> None:
    today = date(2026, 4, 1)
    assert compute_recall_state(today, "2026-08-01", "2026-04-01", "pending") == "pending"


def test_due_when_followup_date_recent_past() -> None:
    today = date(2026, 4, 15)
    assert compute_recall_state(today, "2026-04-01", "2026-04-01", "pending") == "due"


def test_overdue_when_30_days_past() -> None:
    today = date(2026, 5, 5)
    assert compute_recall_state(today, "2026-04-01", "2026-04-01", "pending") == "overdue"


def test_terminal_states_are_sticky() -> None:
    today = date(2026, 5, 5)
    for term in ("scheduled", "removed", "outreach_attempted"):
        assert compute_recall_state(today, "2026-04-01", None, term) == term


def test_recent_encounter_transitions_to_scheduled() -> None:
    """If patient has been seen since the followup date, no recall needed."""
    today = date(2026, 5, 1)
    out = compute_recall_state(today, "2026-04-01", "2026-04-15", "due")
    assert out == "scheduled"


# ── DB layer ───────────────────────────────────────────────────────────

def test_upsert_creates_row(fresh_store) -> None:
    upsert_recall_entry(
        submission_id="S-1", patient_id="P-1",
        patient_name="Test Patient", procedure_code="78492",
        procedure_name="Cardiac PET", payer="UHC",
        submission_date="2026-01-01",
    )
    entries = list_recall_queue(refresh_status=False)
    assert len(entries) == 1
    e = entries[0]
    assert e.submission_id == "S-1"
    assert e.expected_followup_date == "2026-06-30"
    assert e.practice_value_estimate == 1500


def test_upsert_is_idempotent_on_status(fresh_store) -> None:
    """Re-upserting an existing row must not clobber recall_status."""
    upsert_recall_entry(
        submission_id="S-1", patient_id="P-1", patient_name="A",
        procedure_code="78492", procedure_name="PET", payer="UHC",
        submission_date="2026-01-01",
    )
    apply_action("S-1", "mark_outreach", note="left voicemail")

    # Re-upsert with new values — status should still be outreach_attempted
    upsert_recall_entry(
        submission_id="S-1", patient_id="P-1", patient_name="A renamed",
        procedure_code="78492", procedure_name="PET", payer="UHC",
        submission_date="2026-01-01",
    )
    entries = list_recall_queue(refresh_status=False)
    assert entries[0].recall_status == "outreach_attempted"
    assert entries[0].patient_name == "A renamed"


def test_apply_action_records_actor_and_note(fresh_store) -> None:
    upsert_recall_entry(
        submission_id="S-1", patient_id="P-1", patient_name="A",
        procedure_code="78492", procedure_name="PET", payer="UHC",
        submission_date="2026-01-01",
    )
    apply_action("S-1", "mark_outreach", actor="alice", note="LMOP, retry Tue")
    entries = list_recall_queue(refresh_status=False)
    assert entries[0].recall_status == "outreach_attempted"
    assert entries[0].last_action_by == "alice"
    assert "LMOP" in entries[0].notes


def test_apply_remove(fresh_store) -> None:
    upsert_recall_entry(
        submission_id="S-1", patient_id="P-1", patient_name="A",
        procedure_code="78492", procedure_name="PET", payer="UHC",
        submission_date="2026-01-01",
    )
    apply_action("S-1", "remove", note="patient transferred care")
    entries = list_recall_queue(refresh_status=False)
    assert entries[0].recall_status == "removed"


def test_apply_action_unknown_raises(fresh_store) -> None:
    upsert_recall_entry(
        submission_id="S-1", patient_id="P-1", patient_name="A",
        procedure_code="78492", procedure_name="PET", payer="UHC",
        submission_date="2026-01-01",
    )
    with pytest.raises(ValueError):
        apply_action("S-1", "BOGUS_ACTION")


def test_filters_by_status(fresh_store) -> None:
    # One overdue, one pending
    long_ago = (date.today() - timedelta(days=400)).isoformat()
    near_now = (date.today() - timedelta(days=10)).isoformat()
    upsert_recall_entry(
        submission_id="S-OLD", patient_id="P-1", patient_name="Old",
        procedure_code="78492", procedure_name="PET", payer="UHC",
        submission_date=long_ago,
    )
    upsert_recall_entry(
        submission_id="S-NEW", patient_id="P-2", patient_name="New",
        procedure_code="78492", procedure_name="PET", payer="UHC",
        submission_date=near_now,
    )
    overdue_only = list_recall_queue(status="overdue")
    assert all(e.recall_status == "overdue" for e in overdue_only)
    assert any(e.submission_id == "S-OLD" for e in overdue_only)


def test_kpis_count_revenue_at_risk(fresh_store) -> None:
    long_ago = (date.today() - timedelta(days=400)).isoformat()
    upsert_recall_entry(
        submission_id="S-1", patient_id="P-1", patient_name="A",
        procedure_code="78492",  # $1500
        procedure_name="PET", payer="UHC",
        submission_date=long_ago,
    )
    entries = list_recall_queue()
    k = queue_kpis(entries)
    assert k["actionable"] >= 1
    assert k["revenue_at_risk_usd"] >= 1500


def test_seed_demo_recalls_idempotent(fresh_store) -> None:
    n1 = seed_demo_recalls()
    assert n1 > 0
    # Second call should add 0 new rows
    n2 = seed_demo_recalls()
    assert n2 == 0


def test_seed_demo_recalls_includes_overdue_and_pending(fresh_store) -> None:
    seed_demo_recalls()
    entries = list_recall_queue()
    statuses = {e.recall_status for e in entries}
    # Demo seed should produce at least one of each major bucket
    assert "overdue" in statuses
    assert "pending" in statuses
