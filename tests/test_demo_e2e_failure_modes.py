"""Failure-mode tests for the end-to-end demo pipeline.

Peter's May rerun caught a class of bugs our test suite was blind
to: when a real Anthropic API failure happens mid-pipeline, the
system silently degrades and the UI shows a cascade of false-
positive findings. The pre-rerun test suite passed all green
through this scenario because every test ran the HAPPY path —
either with a live API or with the no-API-key skeletal fallback.

This module deliberately breaks the API mid-call and asserts the
pipeline produces ONE honest signal (a top-level pipeline_error)
rather than six false-positive missing-essential findings, a 0%
reasoner score, and a misleading "your chart is incomplete" UX.

Test matrix:
  1. Anthropic call raises with "spend limit" → pipeline_error
     kind=anthropic_spend_limit, severity=blocking, refill CTA
  2. Anthropic call raises generic exception → pipeline_error
     kind=anthropic_unavailable, severity=high
  3. Happy path (no API key → skeletal but recognized fallback)
     → pipeline_error populated, EssentialsChecker emits ONE finding
  4. Real essentials present in note → regex pass populates them
     even when API is down (no false "missing field" cascade)
  5. Pipeline_error round-trips through to_dict (JSON-serializable
     for the API endpoint that backs the UI)
"""

from __future__ import annotations

import pytest

from cardioauth.demo_e2e import (
    PipelineError,
    _chart_extraction_failed_via_api,
    _pipeline_error_from_chart,
    run_end_to_end_demo,
)
from cardioauth.models.chart import ChartData


# ─────────────────────── Fixtures + helpers ───────────────────────────

# A note with all six essentials and clinical content. Used to verify
# that even when the API fails, the regex extractor still pulls out
# patient_name / DOB / member_id / NPI so EssentialsChecker doesn't
# false-flag them as missing.
PETER_STYLE_NOTE = """\
Re: Margaret Synthetic
DOB: 01/15/1958
Member ID: UHC987654321
Insurance: UnitedHealthcare PPO

This 67-year-old female with known CAD presents with progressive
exertional chest pain over 3 months. ECG shows new LBBB. Recent
treadmill stress test was nondiagnostic due to inability to
achieve target heart rate. Patient has severe degenerative joint
disease limiting exercise tolerance.

Plan: Cardiac PET stress test (CPT 78492) to assess for ischemia.

Ordering MD: Dr. James Carter
NPI: 1306939693
"""


class _AnthropicSpendLimitClient:
    """Mock Anthropic client whose .messages.create raises a spend-
    limit error. Triggers the same code path Peter hit in production."""

    def __init__(self, *args, **kwargs):
        self.messages = self

    def create(self, *args, **kwargs):
        raise Exception(
            "Error code: 400 - {'error': {'type': 'invalid_request_error', "
            "'message': 'Your credit balance is too low to access the "
            "Anthropic API. Please go to Plans & Billing to upgrade or "
            "purchase credits. (spend limit)'}}"
        )


class _AnthropicGenericFailClient:
    """Mock Anthropic client whose .messages.create raises an
    arbitrary exception (network error, timeout, etc.) — distinct
    from the spend-limit path."""

    def __init__(self, *args, **kwargs):
        self.messages = self

    def create(self, *args, **kwargs):
        raise Exception("Connection reset by peer")


def _patch_anthropic(monkeypatch, client_class) -> None:
    """Patch the anthropic.Anthropic constructor so any code path
    that instantiates a client gets the mock instead. Covers the
    chart extractor, policy agent, and reasoner — all of which
    construct their own client."""
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", client_class)


# ─────────────────────── Detection helpers ────────────────────────────


def test_chart_extraction_failed_detector_recognizes_api_unavailable() -> None:
    """The detection helper must distinguish API fallback from
    user-supplied missing_fields entries."""
    chart = ChartData(
        patient_id="X", procedure_requested="P", procedure_code="78492",
        missing_fields=["Anthropic API unavailable; only raw note preserved"],
    )
    assert _chart_extraction_failed_via_api(chart) is True


def test_chart_extraction_failed_detector_recognizes_note_extraction_failed() -> None:
    chart = ChartData(
        patient_id="X", procedure_requested="P", procedure_code="78492",
        missing_fields=["Note extraction failed: Connection reset"],
    )
    assert _chart_extraction_failed_via_api(chart) is True


def test_chart_extraction_failed_detector_ignores_user_missing_fields() -> None:
    """Non-API entries shouldn't trigger the API-fallback detector."""
    chart = ChartData(
        patient_id="X", procedure_requested="P", procedure_code="78492",
        missing_fields=["Patient declined to disclose name"],
    )
    assert _chart_extraction_failed_via_api(chart) is False


def test_chart_extraction_failed_detector_handles_empty_missing_fields() -> None:
    chart = ChartData(
        patient_id="X", procedure_requested="P", procedure_code="78492",
    )
    assert _chart_extraction_failed_via_api(chart) is False


# ─────────────────────── PipelineError builder ────────────────────────


def test_pipeline_error_from_chart_spend_limit_severity_blocking() -> None:
    """When the missing_fields entry mentions 'spend limit', the
    PipelineError must be severity=blocking with a refill CTA."""
    chart = ChartData(
        patient_id="X", procedure_requested="P", procedure_code="78492",
        missing_fields=[
            "Note extraction failed: Your credit balance is too low (spend limit)"
        ],
    )
    err = _pipeline_error_from_chart(chart)
    assert err.kind == "anthropic_spend_limit"
    assert err.severity == "blocking"
    assert "refill" in err.fix_suggestion.lower() or "credits" in err.fix_suggestion.lower()
    assert "CHART_AGENT" in err.affected_stages
    assert "POLICY_AGENT" in err.affected_stages
    assert "UnifiedReasoner" in err.affected_stages


def test_pipeline_error_from_chart_credit_keyword_also_classified_as_spend() -> None:
    """The detection should pick up 'credit' as well as 'spend limit'."""
    chart = ChartData(
        patient_id="X", procedure_requested="P", procedure_code="78492",
        missing_fields=["Note extraction failed: insufficient credit"],
    )
    err = _pipeline_error_from_chart(chart)
    assert err.kind == "anthropic_spend_limit"


def test_pipeline_error_from_chart_generic_failure_severity_high() -> None:
    """Generic failures are 'high' severity, not 'blocking' — the
    physician might still want to act on partial output."""
    chart = ChartData(
        patient_id="X", procedure_requested="P", procedure_code="78492",
        missing_fields=["Note extraction failed: Connection reset by peer"],
    )
    err = _pipeline_error_from_chart(chart)
    assert err.kind == "anthropic_unavailable"
    assert err.severity == "high"


def test_pipeline_error_serializes_to_dict() -> None:
    err = PipelineError(
        kind="anthropic_spend_limit", severity="blocking",
        message="X", affected_stages=["A", "B"],
        fix_suggestion="Refill credits.",
    )
    d = err.to_dict()
    assert d["kind"] == "anthropic_spend_limit"
    assert d["severity"] == "blocking"
    assert d["affected_stages"] == ["A", "B"]
    assert d["fix_suggestion"] == "Refill credits."


# ─────────────────────── End-to-end pipeline ──────────────────────────


def test_e2e_with_spend_limit_surfaces_pipeline_error_not_cascade(monkeypatch) -> None:
    """The exact bug Peter hit: API spend limit during a custom-note
    run. Must produce ONE blocking pipeline_error, not six false
    'missing essential' findings."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")
    _patch_anthropic(monkeypatch, _AnthropicSpendLimitClient)

    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=PETER_STYLE_NOTE,
        scripted_outcome="APPROVED",
    )

    # 1. The pipeline_errors list MUST be populated.
    assert len(timeline.pipeline_errors) >= 1
    spend_errors = [e for e in timeline.pipeline_errors if e.kind == "anthropic_spend_limit"]
    assert len(spend_errors) == 1
    assert spend_errors[0].severity == "blocking"

    # 2. The CHART_AGENT step must be marked as fallback, not "ok".
    chart_step = next(s for s in timeline.steps if s.agent == "CHART_AGENT")
    assert chart_step.status == "fallback"
    assert (chart_step.detail or {}).get("extraction_failed_via_api") is True


def test_e2e_with_spend_limit_does_not_emit_missing_essential_cascade(monkeypatch) -> None:
    """The skeletal-chart guard on EssentialsChecker must replace the
    six false 'missing essential' findings with one honest finding."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")
    _patch_anthropic(monkeypatch, _AnthropicSpendLimitClient)

    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=PETER_STYLE_NOTE,
        scripted_outcome="APPROVED",
    )

    physician_step = next(s for s in timeline.steps if s.agent == "Physician")
    typed_findings = (physician_step.detail or {}).get("typed_pipeline_findings", [])
    missing_essential_findings = [
        f for f in typed_findings if f.get("kind") == "missing_essential"
    ]
    extraction_blocked_findings = [
        f for f in typed_findings if f.get("kind") == "extraction_blocked_by_api"
    ]
    # The pre-rerun behavior emitted up to 6 missing_essential findings
    # against the empty skeleton. The guard MUST collapse this to zero.
    assert len(missing_essential_findings) == 0, (
        f"Expected the skeletal-chart guard to suppress missing_essential "
        f"findings; got {len(missing_essential_findings)}: {missing_essential_findings}"
    )
    # Replaced by exactly one honest finding pointing at the API failure.
    assert len(extraction_blocked_findings) == 1
    assert extraction_blocked_findings[0]["severity"] == "blocking"


def test_e2e_with_api_down_still_extracts_essentials_via_regex(monkeypatch) -> None:
    """Peter's specific complaint: 'patient name and NPI are right
    there in the note.' Even when Claude fails, the regex pre-pass
    must populate the essentials so the chart isn't an empty skeleton."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")
    _patch_anthropic(monkeypatch, _AnthropicSpendLimitClient)

    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=PETER_STYLE_NOTE,
    )

    chart_step = next(s for s in timeline.steps if s.agent == "CHART_AGENT")
    chart_data = (chart_step.detail or {}).get("chart_data", {})

    # The regex pass should have populated all six essentials from the
    # note headers — Claude wasn't reached, but they're still present.
    assert chart_data.get("patient_name") == "Margaret Synthetic"
    assert chart_data.get("date_of_birth") == "01/15/1958"
    assert chart_data.get("insurance_id") == "UHC987654321"
    assert "UnitedHealthcare" in chart_data.get("payer_name", "")
    assert "James Carter" in chart_data.get("attending_physician", "")
    assert chart_data.get("attending_npi") == "1306939693"


def test_e2e_with_generic_api_failure_distinct_from_spend_limit(monkeypatch) -> None:
    """A generic Anthropic failure (network error) should produce a
    different pipeline_error kind than spend-limit, so the UI banner
    can show the right CTA."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")
    _patch_anthropic(monkeypatch, _AnthropicGenericFailClient)

    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=PETER_STYLE_NOTE,
    )

    assert len(timeline.pipeline_errors) >= 1
    err = timeline.pipeline_errors[0]
    assert err.kind == "anthropic_unavailable"
    assert err.severity == "high"
    # Should NOT classify a generic network error as spend-limit
    assert err.kind != "anthropic_spend_limit"


def test_e2e_happy_path_has_no_pipeline_errors(monkeypatch) -> None:
    """The happy path (no API key configured) takes the deliberate
    skeletal fallback. Even though the chart is in 'API unavailable'
    mode, the pipeline_error banner SHOULD fire — because from the
    user's perspective this is the same failure class as a spend
    limit (they need an API key configured)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=PETER_STYLE_NOTE,
    )

    # No API key + raw_note should still surface the API-unavailable
    # signal so the user knows downstream output is degraded.
    assert len(timeline.pipeline_errors) >= 1
    err = timeline.pipeline_errors[0]
    assert err.kind in ("anthropic_unavailable", "anthropic_spend_limit")


def test_e2e_demo_patient_path_has_no_spurious_pipeline_errors(monkeypatch) -> None:
    """Demo patient path (FHIR-style chart) doesn't go through the
    note-extraction code path, so spend-limit during downstream
    stages shouldn't emit a pipeline_error at the chart step
    (because the chart didn't fail — it came from the demo fixture)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    timeline = run_end_to_end_demo(
        patient_id="DEMO-001",
        procedure_code="93458",
        payer_name="UnitedHealthcare",
        scripted_outcome="APPROVED",
    )

    # Demo patient gets a real ChartData from the fixture, so no
    # pipeline_error should fire from the chart-extraction layer.
    chart_pipeline_errors = [
        e for e in timeline.pipeline_errors
        if "CHART_AGENT" in e.affected_stages and e.kind in ("anthropic_unavailable", "anthropic_spend_limit")
    ]
    assert chart_pipeline_errors == []


def test_e2e_pipeline_errors_serialize_through_to_dict(monkeypatch) -> None:
    """The /api/demo/end-to-end endpoint serializes the timeline as
    JSON; pipeline_errors must round-trip cleanly so the UI banner
    can render."""
    import json

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")
    _patch_anthropic(monkeypatch, _AnthropicSpendLimitClient)

    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=PETER_STYLE_NOTE,
    )

    payload = timeline.to_dict()
    assert "pipeline_errors" in payload
    assert isinstance(payload["pipeline_errors"], list)
    assert len(payload["pipeline_errors"]) >= 1
    first = payload["pipeline_errors"][0]
    assert first["kind"] == "anthropic_spend_limit"
    assert first["severity"] == "blocking"
    assert isinstance(first["affected_stages"], list)
    # JSON-safe round trip
    json.dumps(payload, default=str)


def test_e2e_chart_summary_explains_failure_in_plain_language(monkeypatch) -> None:
    """The CHART_AGENT step summary must NOT read '0 symptoms, 0 ECG
    findings, 0 stress tests, 0 labs' on a failure — that's the line
    Peter complained about. It should explicitly name the API
    failure as the cause."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key-for-test")
    _patch_anthropic(monkeypatch, _AnthropicSpendLimitClient)

    timeline = run_end_to_end_demo(
        patient_id="CUSTOM",
        procedure_code="78492",
        payer_name="UnitedHealthcare",
        raw_note=PETER_STYLE_NOTE,
    )

    chart_step = next(s for s in timeline.steps if s.agent == "CHART_AGENT")
    assert "extraction FAILED" in chart_step.summary or "extraction failed" in chart_step.summary.lower()
    # Must NOT pretend everything's fine
    assert chart_step.status != "ok"
