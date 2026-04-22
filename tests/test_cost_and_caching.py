"""Tests for cost-aware Claude helpers: token logging, TTL cache, summary endpoint."""

from __future__ import annotations

import tempfile
import time

import pytest

from cardioauth.claude_cost import (
    MIN_CACHEABLE_TOKENS,
    TimedCall,
    system_with_cache_control,
    track_usage,
)
from cardioauth.persistence import SQLiteStore


def _fresh_store() -> SQLiteStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return SQLiteStore(tmp.name)


# ── system_with_cache_control ───────────────────────────────────────────

def test_short_prompt_has_no_cache_control() -> None:
    """Prompts below Anthropic's 1024-token minimum aren't tagged for caching."""
    short = "You are a helpful assistant."
    blocks = system_with_cache_control(short)
    assert len(blocks) == 1
    assert "cache_control" not in blocks[0]


def test_long_prompt_gets_ephemeral_cache_control() -> None:
    """Long stable system prompts get tagged cache_control=ephemeral."""
    # ~1200 tokens worth (≈4800 chars)
    long = "x" * (MIN_CACHEABLE_TOKENS * 4 + 200)
    blocks = system_with_cache_control(long)
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["type"] == "text"


def test_empty_prompt_returns_empty() -> None:
    assert system_with_cache_control("") == []


# ── TimedCall ───────────────────────────────────────────────────────────

def test_timed_call_measures_elapsed() -> None:
    with TimedCall() as t:
        time.sleep(0.01)
    assert t.ms >= 5  # at least 5ms (allow for timer resolution)


# ── track_usage + cost_log ──────────────────────────────────────────────

class _FakeUsage:
    input_tokens = 1500
    output_tokens = 800
    cache_read_input_tokens = 1200
    cache_creation_input_tokens = 0


class _FakeResponse:
    usage = _FakeUsage()


def test_track_usage_persists_to_cost_log(monkeypatch) -> None:
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    summary = track_usage(
        _FakeResponse(),
        agent="TEST_AGENT",
        model="claude-sonnet-4-6",
        duration_ms=350,
        case_id="CASE-1",
    )
    assert summary["input_tokens"] == 1500
    assert summary["cache_read_tokens"] == 1200

    rollup = store.summarize_cost(window_hours=1)
    assert rollup["total_calls"] == 1
    assert rollup["total_input_tokens"] == 1500
    assert rollup["total_cache_read_tokens"] == 1200
    assert len(rollup["per_agent"]) == 1
    assert rollup["per_agent"][0]["agent"] == "TEST_AGENT"


def test_track_usage_safe_when_no_usage() -> None:
    """Responses without .usage shouldn't crash."""
    class NoUsageResponse:
        pass
    summary = track_usage(NoUsageResponse(), agent="TEST")
    assert summary == {}


def test_cache_hit_rate_computed_correctly(monkeypatch) -> None:
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    # One call with high cache hit: 200 fresh, 1800 from cache
    class HitUsage:
        input_tokens = 200
        output_tokens = 100
        cache_read_input_tokens = 1800
        cache_creation_input_tokens = 0
    class HitResp:
        usage = HitUsage()

    track_usage(HitResp(), agent="CACHED_AGENT")
    rollup = store.summarize_cost(window_hours=1)
    agent_row = rollup["per_agent"][0]
    # 1800 / (200 + 1800) = 0.9
    assert agent_row["cache_hit_rate"] == 0.9


def test_summarize_cost_filters_by_agent(monkeypatch) -> None:
    store = _fresh_store()
    import cardioauth.persistence as persistence
    monkeypatch.setattr(persistence, "_store_singleton", store)

    track_usage(_FakeResponse(), agent="AGENT_A")
    track_usage(_FakeResponse(), agent="AGENT_B")

    only_a = store.summarize_cost(window_hours=1, agent="AGENT_A")
    assert only_a["total_calls"] == 1
    assert only_a["per_agent"][0]["agent"] == "AGENT_A"


# ── TTL response cache ──────────────────────────────────────────────────

def test_cache_set_then_get_returns_value() -> None:
    store = _fresh_store()
    store.cache_set("test-key", {"hello": "world"}, ttl_seconds=60)
    got = store.cache_get("test-key")
    assert got == {"hello": "world"}


def test_cache_get_returns_none_on_miss() -> None:
    store = _fresh_store()
    assert store.cache_get("never-set") is None


def test_cache_expires_after_ttl() -> None:
    """TTL=0 means entry is already expired."""
    store = _fresh_store()
    # Set with an already-past expiry via manual DB write
    store.cache_set("expiring-key", {"a": 1}, ttl_seconds=-10)
    assert store.cache_get("expiring-key") is None


def test_cache_overwrite_updates_value() -> None:
    store = _fresh_store()
    store.cache_set("k", {"v": 1}, ttl_seconds=60)
    store.cache_set("k", {"v": 2}, ttl_seconds=60)
    assert store.cache_get("k") == {"v": 2}


# ── Integration: POLICY_AGENT-like flow uses cache ──────────────────────

def test_policy_cache_key_reuse_simulates_second_call() -> None:
    """Two identical (payer, cpt) calls should reuse the cached value."""
    store = _fresh_store()
    # Simulate first call writing the cache
    payload = {
        "policy": {"payer": "UHC", "cpt_code": "78492", "procedure": "Cardiac PET",
                   "auth_required": True, "clinical_criteria": [],
                   "documentation_required": [], "submission_format": "portal",
                   "typical_turnaround_days": 5, "common_denial_reasons": [],
                   "appeal_success_factors": [], "policy_source": "",
                   "policy_last_updated": ""},
        "_retrieved_chunks": [],
        "_criterion_citations": [],
        "_payer_stats": None,
        "_payer_global_rules": [],
        "_freshness": None,
    }
    cache_key = "policy_agent:v2:UHC:78492"
    store.cache_set(cache_key, payload, ttl_seconds=3600)

    hit = store.cache_get(cache_key)
    assert hit is not None
    assert hit["policy"]["cpt_code"] == "78492"
