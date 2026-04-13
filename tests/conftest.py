"""Shared pytest fixtures and setup for CardioAuth unit tests."""

import os
import sys

# Add project root to path so tests can import cardioauth.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Install eval_type_backport if available (helps Python 3.9 handle `X | None` syntax)
try:
    import eval_type_backport  # noqa: F401
except ImportError:
    # Try to monkey-patch Python 3.9's typing to handle `|` unions
    try:
        import types as _types
        import typing as _typing
        # Python 3.10+ supports `int | None`; 3.9 does not — graceful in CI (uses 3.12)
    except Exception:
        pass

# Minimal stub for anthropic to avoid import-time dependency on API key
# (our tests cover pure-logic components that don't call the API)
if "anthropic" not in sys.modules:
    import types
    stub = types.ModuleType("anthropic")
    class _BadRequestError(Exception):
        pass
    class _RateLimitError(Exception):
        pass
    class _Anthropic:
        def __init__(self, *a, **kw):
            pass
    stub.BadRequestError = _BadRequestError
    stub.RateLimitError = _RateLimitError
    stub.Anthropic = _Anthropic
    sys.modules["anthropic"] = stub

import pytest
