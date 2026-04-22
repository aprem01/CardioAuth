"""Tests for the criterion-outcome correlation report.

Seeds the Store with synthetic submissions + outcomes and verifies
compute_criterion_correlation() produces the expected per-criterion
predictive weights.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from cardioauth.persistence import SQLiteStore
from cardioauth.stats.criterion_correlation import compute_criterion_correlation


def _fresh_store() -> SQLiteStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return SQLiteStore(tmp.name)


def _seed_case(store: SQLiteStore, *, submission_id: str, payer: str, cpt: str,
               outcome: str, criteria: dict[str, str]) -> None:
    """Write a submission + outcome with per-criterion matches."""
    store.save_submission(submission_id, {
        "submission_id": submission_id,
        "payer": payer,
        "cpt_code": cpt,
        "criterion_matches_snapshot": [
            {"code": code, "status": status, "gap_class": ""}
            for code, status in criteria.items()
        ],
    })
    store.save_outcome(submission_id, {
        "submission_id": submission_id,
        "outcome": outcome,
        "payer": payer,
        "cpt_code": cpt,
    })


def test_empty_store_returns_zero_counts(monkeypatch) -> None:
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    report = compute_criterion_correlation()
    assert report["total_cases"] == 0
    assert report["criteria"] == []
    # Low-data note should warn the caller
    assert report["note"]
    assert "decisive" in report["note"].lower() or "0" in report["note"]


def test_load_bearing_criterion_identified(monkeypatch) -> None:
    """If MED-002 correlates strongly with approval, report flags it load_bearing."""
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    # 10 cases where MED-002 met → all approved
    for i in range(10):
        _seed_case(
            store, submission_id=f"S-met-{i}", payer="UHC", cpt="78492",
            outcome="APPROVED",
            criteria={"MED-002": "met", "EX-001": "met"},
        )
    # 10 cases where MED-002 NOT met → all denied
    for i in range(10):
        _seed_case(
            store, submission_id=f"S-notmet-{i}", payer="UHC", cpt="78492",
            outcome="DENIED",
            criteria={"MED-002": "not_met", "EX-001": "met"},
        )

    report = compute_criterion_correlation(payer="UHC", cpt_code="78492")
    assert report["total_cases"] == 20
    assert report["total_approved"] == 10
    assert report["total_denied"] == 10

    med = next(c for c in report["criteria"] if c["code"] == "MED-002")
    assert med["approval_rate_when_met"] == 1.0
    assert med["approval_rate_when_not_met"] == 0.0
    assert med["predictive_weight"] == 1.0
    assert med["interpretation"] == "load_bearing"
    assert "MED-002" in report["load_bearing_criteria"]


def test_neutral_criterion_not_load_bearing(monkeypatch) -> None:
    """A criterion that's always met but doesn't predict outcome gets 'insufficient_data'."""
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    # Same approval rate regardless: EX-001 always met, outcomes split
    for i in range(5):
        _seed_case(
            store, submission_id=f"S-a-{i}", payer="UHC", cpt="78492",
            outcome="APPROVED",
            criteria={"EX-001": "met"},
        )
    for i in range(5):
        _seed_case(
            store, submission_id=f"S-d-{i}", payer="UHC", cpt="78492",
            outcome="DENIED",
            criteria={"EX-001": "met"},
        )

    report = compute_criterion_correlation(payer="UHC", cpt_code="78492")
    ex = next(c for c in report["criteria"] if c["code"] == "EX-001")
    # Can't compute predictive weight if we never saw it not_met
    assert ex["approval_rate_when_not_met"] is None
    assert ex["predictive_weight"] is None
    assert ex["interpretation"] == "insufficient_data"


def test_pending_outcomes_excluded_from_correlation(monkeypatch) -> None:
    """Pending / info_requested outcomes shouldn't pollute approval rates."""
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    _seed_case(store, submission_id="S-1", payer="UHC", cpt="78492",
               outcome="PENDING", criteria={"EX-001": "met"})
    _seed_case(store, submission_id="S-2", payer="UHC", cpt="78492",
               outcome="APPROVED", criteria={"EX-001": "met"})

    report = compute_criterion_correlation(payer="UHC", cpt_code="78492")
    # Only the APPROVED one counts; PENDING ignored
    assert report["total_cases"] == 1
    assert report["total_approved"] == 1


def test_filter_by_payer_and_cpt(monkeypatch) -> None:
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    _seed_case(store, submission_id="uhc-1", payer="UHC", cpt="78492",
               outcome="APPROVED", criteria={"EX-001": "met"})
    _seed_case(store, submission_id="aetna-1", payer="Aetna", cpt="78492",
               outcome="DENIED", criteria={"EX-001": "not_met"})

    uhc_report = compute_criterion_correlation(payer="UHC")
    aetna_report = compute_criterion_correlation(payer="Aetna")

    assert uhc_report["total_cases"] == 1
    assert aetna_report["total_cases"] == 1
    assert uhc_report["total_approved"] == 1
    assert aetna_report["total_denied"] == 1
