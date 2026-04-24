"""Tests for the rule-based symptom backstop (Peter Apr 24 feedback)."""

from __future__ import annotations

from cardioauth.symptom_fallback import (
    backfill_symptoms_if_missing,
    extract_symptoms_from_text,
)


def test_extracts_dyspnea_on_exertion() -> None:
    syms = extract_symptoms_from_text(
        "67F with new-onset dyspnea on exertion x 3 weeks, progressively worse."
    )
    names = [s["name"] for s in syms]
    assert "dyspnea on exertion" in names
    # Should NOT also have bare "dyspnea" — dedupe rule
    assert "dyspnea" not in names


def test_captures_change_vs_baseline() -> None:
    syms = extract_symptoms_from_text(
        "New-onset chest pain, worsening over past 2 weeks."
    )
    cp = next(s for s in syms if s["name"] == "chest pain")
    assert cp.get("change_vs_baseline") == "new or worsening"


def test_skips_denied_symptoms() -> None:
    syms = extract_symptoms_from_text(
        "Patient with dyspnea on exertion. Denies chest pain, palpitations, or syncope."
    )
    names = [s["name"] for s in syms]
    assert "dyspnea on exertion" in names
    assert "chest pain" not in names
    assert "palpitations" not in names
    assert "syncope" not in names


def test_exertional_character_tagged() -> None:
    syms = extract_symptoms_from_text(
        "Reports chest tightness with exertion, no rest pain."
    )
    cp = next((s for s in syms if s["name"] == "chest pain"), None)
    assert cp is not None
    assert cp.get("character") == "exertional"


def test_onset_duration_extracted() -> None:
    syms = extract_symptoms_from_text(
        "Dyspnea on exertion x 3 weeks, progressively worse."
    )
    doe = next(s for s in syms if s["name"] == "dyspnea on exertion")
    assert "3 weeks" in (doe.get("onset") or "")


def test_backfill_only_when_empty() -> None:
    """Claude's extraction wins if it produced anything named."""
    chart = {
        "procedure_code": "78492",
        "current_symptoms": [{"name": "angina", "character": "typical"}],
    }
    note = "Patient has dyspnea on exertion."
    out = backfill_symptoms_if_missing(chart, note)
    # Unchanged — Claude already populated it
    assert len(out["current_symptoms"]) == 1
    assert out["current_symptoms"][0]["name"] == "angina"


def test_backfill_runs_when_empty() -> None:
    chart = {
        "procedure_code": "78492",
        "current_symptoms": [],
    }
    note = "67F with new-onset dyspnea on exertion x 3 weeks."
    out = backfill_symptoms_if_missing(chart, note)
    names = [s["name"] for s in out["current_symptoms"]]
    assert "dyspnea on exertion" in names
    # Backfill should leave a trace in missing_fields
    assert any("backfilled" in m.lower() for m in out.get("missing_fields", []))


def test_backfill_skipped_when_no_keywords() -> None:
    chart = {"procedure_code": "78492", "current_symptoms": []}
    note = "Routine follow-up, BP controlled, no acute issues."
    out = backfill_symptoms_if_missing(chart, note)
    assert out["current_symptoms"] == []


def test_backfill_uses_additional_notes_too() -> None:
    """If raw_note arg is empty but additional_notes has symptoms, backfill."""
    chart = {
        "procedure_code": "78492",
        "current_symptoms": [],
        "additional_notes": "Complaints: worsening dyspnea on exertion x 2 months.",
    }
    out = backfill_symptoms_if_missing(chart, "")
    names = [s["name"] for s in out["current_symptoms"]]
    assert "dyspnea on exertion" in names
