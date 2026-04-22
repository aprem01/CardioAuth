"""Cost-aware Claude helpers.

Two things this module provides:

  1. `system_with_cache_control(prompt)` — wraps a large stable system prompt
     in the list-of-blocks form with an `ephemeral` cache_control marker.
     Anthropic's prompt caching then serves that block from cache on
     subsequent calls within ~5 minutes — 90% cheaper on cached tokens.

  2. `track_usage(response, agent, ...)` — reads `response.usage` and
     appends a row to the cost_log table so we can see, per agent, how
     much we're spending and how much caching is actually saving us.

Both helpers are safe no-ops when Anthropic responses don't have usage
info (e.g., fallback paths that don't hit the API).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


MIN_CACHEABLE_TOKENS = 1024  # Anthropic's minimum for ephemeral caching


def system_with_cache_control(prompt: str) -> list[dict]:
    """Convert a system-prompt string into a cache-eligible blocks list.

    Anthropic only caches blocks with >=1024 tokens. Smaller prompts are
    returned as a single uncached block (caching would be a no-op anyway).
    """
    if not prompt:
        return []
    # Rough token heuristic: 4 chars/token. Cheap to be safe.
    rough_tokens = len(prompt) // 4
    if rough_tokens < MIN_CACHEABLE_TOKENS:
        return [{"type": "text", "text": prompt}]
    return [
        {
            "type": "text",
            "text": prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def track_usage(
    response: Any,
    *,
    agent: str,
    model: str = "",
    duration_ms: int = 0,
    endpoint: str = "",
    case_id: str = "",
) -> dict:
    """Read usage from an Anthropic response and log to cost_log.

    Returns a dict summary for the caller (useful for logging / traces).
    Silently no-ops if the response doesn't expose usage.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    # Newer SDK versions expose cache metrics when cache_control is used
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

    summary = {
        "agent": agent,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "duration_ms": duration_ms,
    }

    try:
        from cardioauth.persistence import get_store
        get_store().log_cost(
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            duration_ms=duration_ms,
            endpoint=endpoint,
            case_id=case_id,
        )
    except Exception as e:
        logger.warning("track_usage: failed to persist cost row: %s", e)

    return summary


class TimedCall:
    """Context manager to track duration around a Claude call.

    Usage:
        with TimedCall() as timer:
            response = client.messages.create(...)
        track_usage(response, agent="...", duration_ms=timer.ms)
    """

    def __init__(self) -> None:
        self._t0 = 0.0
        self.ms = 0

    def __enter__(self) -> "TimedCall":
        self._t0 = time.time()
        return self

    def __exit__(self, *exc) -> None:
        self.ms = int((time.time() - self._t0) * 1000)
