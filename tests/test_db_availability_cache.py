"""Tests for the DB availability circuit breaker.

Peter May rerun (Railway logs): every request was burning ~500-800ms
timing out on a known-broken DB and logging a WARNING. The fix is a
TTL-based cache + log suppression. These tests pin the behavior so a
future refactor doesn't accidentally restore the per-request hammer.
"""

from __future__ import annotations

import logging

import pytest

from cardioauth import db as db_module


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the cache + global flag between every test."""
    db_module._DB_AVAILABILITY_CACHE.update({
        "last_check": 0.0,
        "available": None,
        "fail_logged": False,
    })
    db_module._DSN_NOT_SET_LOGGED = False
    yield


# ── No DSN configured ──────────────────────────────────────────────────


def test_no_dsn_returns_false_and_logs_once(monkeypatch, caplog) -> None:
    monkeypatch.setattr(db_module, "DATABASE_URL", "")
    caplog.set_level(logging.WARNING, logger="cardioauth.db")

    assert db_module.is_db_available() is False
    assert db_module.is_db_available() is False
    assert db_module.is_db_available() is False

    # Even three calls should produce only ONE warning
    msgs = [r.getMessage() for r in caplog.records if "DATABASE_URL not set" in r.getMessage()]
    assert len(msgs) == 1


# ── Probe failure (the Peter scenario) ─────────────────────────────────


def test_failure_caches_and_logs_only_once(monkeypatch, caplog) -> None:
    """When the DB is unreachable, the WARNING fires once and
    subsequent calls within FAILURE_TTL short-circuit silently."""
    monkeypatch.setattr(db_module, "DATABASE_URL", "postgres://fake:fake@fake:5432/fake")

    def boom():
        return False, "OperationalError: Tenant or user not found"
    monkeypatch.setattr(db_module, "_connection_probe", boom)
    caplog.set_level(logging.WARNING, logger="cardioauth.db")

    # 5 rapid calls → 1 probe + 4 cache hits → 1 WARNING total
    for _ in range(5):
        assert db_module.is_db_available() is False

    fail_msgs = [r.getMessage() for r in caplog.records if "DB connection failed" in r.getMessage()]
    assert len(fail_msgs) == 1, f"Expected 1 WARNING, got {len(fail_msgs)}: {fail_msgs}"


def test_failure_cache_short_circuits_probe(monkeypatch) -> None:
    """While the cache says 'unavailable', _connection_probe must not
    be called again. This is what kept Peter's runs from burning
    500-800ms per request on a known-broken DSN."""
    monkeypatch.setattr(db_module, "DATABASE_URL", "postgres://fake")

    probe_count = {"n": 0}
    def boom():
        probe_count["n"] += 1
        return False, "boom"
    monkeypatch.setattr(db_module, "_connection_probe", boom)

    for _ in range(10):
        db_module.is_db_available()
    assert probe_count["n"] == 1, f"Probe ran {probe_count['n']} times; expected 1"


def test_failure_cache_expires_after_ttl(monkeypatch) -> None:
    monkeypatch.setattr(db_module, "DATABASE_URL", "postgres://fake")
    monkeypatch.setattr(db_module, "_FAILURE_TTL", 0.01)  # 10ms

    probe_count = {"n": 0}
    def boom():
        probe_count["n"] += 1
        return False, "boom"
    monkeypatch.setattr(db_module, "_connection_probe", boom)

    db_module.is_db_available()
    import time
    time.sleep(0.02)  # past the TTL
    db_module.is_db_available()
    assert probe_count["n"] == 2  # second call re-probed


# ── Recovery path ──────────────────────────────────────────────────────


def test_db_recovery_logs_info_and_returns_true(monkeypatch, caplog) -> None:
    """When the DB comes back after a failure window, log INFO once
    and serve cached True afterwards."""
    monkeypatch.setattr(db_module, "DATABASE_URL", "postgres://fake")
    monkeypatch.setattr(db_module, "_FAILURE_TTL", 0.01)
    caplog.set_level(logging.INFO, logger="cardioauth.db")

    state = {"healthy": False}
    def probe():
        return (True, "") if state["healthy"] else (False, "fail")
    monkeypatch.setattr(db_module, "_connection_probe", probe)

    # First call: down
    assert db_module.is_db_available() is False
    # Flip to healthy + wait out the failure TTL
    state["healthy"] = True
    import time
    time.sleep(0.02)
    # Now should probe + report restored
    assert db_module.is_db_available() is True

    info_msgs = [r.getMessage() for r in caplog.records if "connection restored" in r.getMessage()]
    assert len(info_msgs) == 1


# ── Success path caches too ────────────────────────────────────────────


def test_success_caches_within_ttl(monkeypatch) -> None:
    monkeypatch.setattr(db_module, "DATABASE_URL", "postgres://fake")
    probe_count = {"n": 0}
    def probe():
        probe_count["n"] += 1
        return True, ""
    monkeypatch.setattr(db_module, "_connection_probe", probe)

    for _ in range(5):
        assert db_module.is_db_available() is True
    assert probe_count["n"] == 1
