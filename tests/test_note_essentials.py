"""Tests for the deterministic regex essentials extractor.

Peter May rerun: Claude failure on a real PDF cascaded into six
"missing essentials" findings even though the fields were sitting in
the note. This module proves the regex pre-pass catches those fields
without an LLM, so EssentialsChecker no longer false-flags them.
"""

from __future__ import annotations

from cardioauth.note_essentials import (
    EssentialMatch,
    extract_essentials_from_note,
    normalize_payer_name,
    overlay_essentials,
)


# ── Patient name ───────────────────────────────────────────────────────


def test_patient_name_label() -> None:
    note = "Patient: Jane Synthetic\nDOB: 01/15/1958\n"
    out = extract_essentials_from_note(note)
    assert "patient_name" in out
    assert out["patient_name"].value == "Jane Synthetic"


def test_patient_name_pt_label() -> None:
    note = "Pt: John Doe Jr\n"
    out = extract_essentials_from_note(note)
    assert out["patient_name"].value == "John Doe Jr"


def test_patient_name_re_label() -> None:
    """Letter-style PA notes often start with 'Re: <patient>'."""
    note = "Re: Mary Roberts\n\nThis 67-year-old female...\n"
    out = extract_essentials_from_note(note)
    assert out["patient_name"].value == "Mary Roberts"


def test_patient_name_lowercase_label_still_matched() -> None:
    note = "patient: Sarah Connor\n"
    out = extract_essentials_from_note(note)
    assert out["patient_name"].value == "Sarah Connor"


def test_patient_name_skips_lowercase_value() -> None:
    """Don't match 'Patient: presents with chest pain' as the name."""
    note = "Patient: presents with chest pain on exertion\n"
    out = extract_essentials_from_note(note)
    assert "patient_name" not in out


# ── Date of birth ──────────────────────────────────────────────────────


def test_dob_slash_format() -> None:
    note = "DOB: 01/15/1958\n"
    out = extract_essentials_from_note(note)
    assert out["date_of_birth"].value == "01/15/1958"


def test_dob_iso_format() -> None:
    note = "DOB: 1958-01-15\n"
    out = extract_essentials_from_note(note)
    assert out["date_of_birth"].value == "1958-01-15"


def test_dob_with_periods() -> None:
    note = "D.O.B.: 1/15/1958\n"
    out = extract_essentials_from_note(note)
    assert out["date_of_birth"].value == "1/15/1958"


def test_dob_born_phrase() -> None:
    note = "67-year-old male, born on 01/15/1958, presents with...\n"
    out = extract_essentials_from_note(note)
    assert out["date_of_birth"].value == "01/15/1958"


def test_dob_date_of_birth_long_form() -> None:
    note = "Date of Birth: 03-22-1960\n"
    out = extract_essentials_from_note(note)
    assert out["date_of_birth"].value == "03-22-1960"


# ── Insurance / member ID ──────────────────────────────────────────────


def test_member_id_label() -> None:
    note = "Member ID: UHC123456789\n"
    out = extract_essentials_from_note(note)
    assert out["insurance_id"].value == "UHC123456789"


def test_subscriber_id_label() -> None:
    note = "Subscriber ID: ABC-987654\n"
    out = extract_essentials_from_note(note)
    assert out["insurance_id"].value == "ABC-987654"


def test_member_id_skips_short_garbage() -> None:
    """Member IDs are at least 4 characters."""
    note = "Member ID: AB\n"
    out = extract_essentials_from_note(note)
    assert "insurance_id" not in out


# ── Payer name ─────────────────────────────────────────────────────────


def test_payer_label_uhc() -> None:
    note = "Payer: UnitedHealthcare\n"
    out = extract_essentials_from_note(note)
    assert "payer_name" in out
    assert "UnitedHealthcare" in out["payer_name"].value


def test_payer_label_aetna() -> None:
    note = "Insurance: Aetna PPO\n"
    out = extract_essentials_from_note(note)
    assert "Aetna" in out["payer_name"].value


def test_normalize_payer_name_uhc() -> None:
    assert normalize_payer_name("UHC") == "UnitedHealthcare"
    assert normalize_payer_name("United Healthcare") == "UnitedHealthcare"
    assert normalize_payer_name("UnitedHealthcare") == "UnitedHealthcare"


def test_normalize_payer_name_passthrough() -> None:
    """Unknown payers come back unchanged (just trimmed)."""
    assert normalize_payer_name("  Unknown Plan  ") == "Unknown Plan"


# ── Attending physician + NPI ──────────────────────────────────────────


def test_ordering_md_label() -> None:
    note = "Ordering MD: Dr. Sarah Connor\n"
    out = extract_essentials_from_note(note)
    assert out["attending_physician"].value == "Dr. Sarah Connor"


def test_attending_physician_label() -> None:
    note = "Attending Physician: John Smith MD\n"
    out = extract_essentials_from_note(note)
    assert "John Smith" in out["attending_physician"].value


def test_signed_by() -> None:
    note = "Electronically signed by: Dr. Linda Kim\n"
    out = extract_essentials_from_note(note)
    assert out["attending_physician"].value == "Dr. Linda Kim"


def test_npi_label() -> None:
    note = "NPI: 1234567890\n"
    out = extract_essentials_from_note(note)
    assert out["attending_npi"].value == "1234567890"


def test_npi_must_be_10_digits() -> None:
    """9 digits is not an NPI."""
    note = "NPI: 123456789\n"
    out = extract_essentials_from_note(note)
    assert "attending_npi" not in out


# ── Full Peter-style note ──────────────────────────────────────────────


def test_full_referral_letter_extracts_all_six() -> None:
    """A typical PDF-export note with all six essentials should
    produce a complete EssentialMatch dict — no LLM required."""
    note = """\
Re: Margaret Synthetic
DOB: 01/15/1958
Member ID: UHC987654321
Insurance: UnitedHealthcare PPO

This 67-year-old female presents with intermittent chest pain on
moderate exertion. ECG shows new LBBB. Stress test was nondiagnostic.

Ordering MD: Dr. James Carter
NPI: 1306939693
"""
    out = extract_essentials_from_note(note)
    assert out["patient_name"].value == "Margaret Synthetic"
    assert out["date_of_birth"].value == "01/15/1958"
    assert out["insurance_id"].value == "UHC987654321"
    assert "UnitedHealthcare" in out["payer_name"].value
    assert "James Carter" in out["attending_physician"].value
    assert out["attending_npi"].value == "1306939693"


def test_empty_note_returns_empty_dict() -> None:
    assert extract_essentials_from_note("") == {}


def test_unrelated_note_returns_empty_dict() -> None:
    """A note with no header lines and no NPI/DOB/etc should not
    invent matches."""
    note = "patient describes substernal pressure radiating to jaw."
    out = extract_essentials_from_note(note)
    # patient_name might fire on capitalized garbage — but the note
    # here is all lowercase so no false positives expected
    assert "patient_name" not in out
    assert "date_of_birth" not in out
    assert "attending_npi" not in out


# ── Char-span audit ────────────────────────────────────────────────────


def test_essential_match_carries_char_offsets() -> None:
    note = "Patient: Jane Synthetic\nDOB: 01/15/1958\n"
    out = extract_essentials_from_note(note)
    pm = out["patient_name"]
    # The captured group starts AFTER "Patient: "
    assert note[pm.char_start:pm.char_end] == "Jane Synthetic"


def test_essential_match_records_rule_name() -> None:
    note = "DOB: 01/15/1958\n"
    out = extract_essentials_from_note(note)
    assert out["date_of_birth"].rule == "dob_label"


# ── overlay_essentials ─────────────────────────────────────────────────


def test_overlay_fills_blank_slots() -> None:
    """Claude returned an empty patient_name; regex fills it in."""
    chart = {"patient_name": "", "date_of_birth": "", "insurance_id": "X"}
    essentials = {
        "patient_name": EssentialMatch(
            field="patient_name", value="Jane", char_start=0, char_end=4, rule="t"
        ),
        "date_of_birth": EssentialMatch(
            field="date_of_birth", value="1958-01-15", char_start=10, char_end=20, rule="t"
        ),
    }
    out = overlay_essentials(chart, essentials)
    assert out["patient_name"] == "Jane"
    assert out["date_of_birth"] == "1958-01-15"
    assert out["insurance_id"] == "X"  # unchanged — already populated


def test_overlay_doesnt_clobber_claude_value() -> None:
    """Claude wins when both have a value."""
    chart = {"patient_name": "Claude's Pick"}
    essentials = {
        "patient_name": EssentialMatch(
            field="patient_name", value="Regex Pick", char_start=0, char_end=10, rule="t"
        ),
    }
    out = overlay_essentials(chart, essentials)
    assert out["patient_name"] == "Claude's Pick"


def test_overlay_normalizes_payer() -> None:
    """Regex match on 'UHC' upgrades to 'UnitedHealthcare'."""
    chart = {"payer_name": ""}
    essentials = {
        "payer_name": EssentialMatch(
            field="payer_name", value="UHC", char_start=0, char_end=3, rule="t"
        ),
    }
    out = overlay_essentials(chart, essentials)
    assert out["payer_name"] == "UnitedHealthcare"


def test_overlay_returns_new_dict() -> None:
    """Doesn't mutate input."""
    chart = {"patient_name": ""}
    essentials = {
        "patient_name": EssentialMatch(
            field="patient_name", value="X", char_start=0, char_end=1, rule="t"
        ),
    }
    out = overlay_essentials(chart, essentials)
    assert chart["patient_name"] == ""  # original unchanged
    assert out["patient_name"] == "X"
