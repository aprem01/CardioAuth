"""Resilient JSON parser for LLM responses.

Claude sometimes returns JSON wrapped in markdown fences, or truncated
mid-string when the response hits max_tokens. This module recovers as
much structure as possible instead of raising on the first error.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def strip_markdown_fences(raw: str) -> str:
    """Remove ```json ... ``` wrapping if present."""
    if "```json" in raw:
        return raw.split("```json", 1)[1].rsplit("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].rsplit("```", 1)[0].strip()
    return raw.strip()


def _find_json_object(s: str) -> str:
    """Return the outermost JSON object substring, or empty string if none."""
    start = s.find("{")
    if start == -1:
        return ""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    # Unterminated — return what we have
    return s[start:]


def _repair_truncated_json(s: str) -> str:
    """Attempt to fix common truncation patterns:
    - Unterminated string: close it
    - Trailing comma: remove
    - Missing closing brackets/braces: add them
    """
    # Count open brackets/braces in context (ignoring those inside strings)
    depth_brace = 0
    depth_bracket = 0
    in_str = False
    esc = False
    last_valid = len(s)
    for i, c in enumerate(s):
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth_brace += 1
        elif c == "}":
            depth_brace -= 1
        elif c == "[":
            depth_bracket += 1
        elif c == "]":
            depth_bracket -= 1

    repaired = s
    # If we ended inside a string, close it
    if in_str:
        repaired += '"'
    # Remove trailing commas
    repaired = re.sub(r",(\s*[\]}])", r"\1", repaired)
    # Close any remaining brackets/braces
    repaired += "]" * max(0, depth_bracket)
    repaired += "}" * max(0, depth_brace)
    return repaired


def parse_llm_json(raw: str, fallback: dict | None = None) -> dict:
    """Parse JSON from an LLM response, recovering from truncation if needed.

    Returns the parsed dict, or ``fallback`` (default empty dict) if
    parsing fails entirely.
    """
    if fallback is None:
        fallback = {}

    # Step 1: strip markdown fences
    s = strip_markdown_fences(raw)

    # Step 2: try direct parse
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        logger.warning("Direct JSON parse failed (%s); attempting recovery", e)

    # Step 3: extract outermost object
    extracted = _find_json_object(s)
    if not extracted:
        logger.error("No JSON object found in response")
        return fallback

    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        pass

    # Step 4: repair truncated JSON
    repaired = _repair_truncated_json(extracted)
    try:
        result = json.loads(repaired)
        logger.info("Recovered partial JSON from truncated response (%d → %d chars)", len(raw), len(repaired))
        return result
    except json.JSONDecodeError as e:
        logger.error("JSON recovery failed: %s", e)
        return fallback
