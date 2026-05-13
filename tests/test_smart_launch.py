"""Tests for the SMART on FHIR App Launch manager.

The manager handles the user-driven side of the Epic integration —
authorize-redirect URLs, state validation, code-for-token exchange,
and session storage. We verify the pieces we control without touching
the network (Epic's authorize / token endpoints are stubbed via the
SMART config fetch).
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from cardioauth.fhir.smart_launch import (
    SmartLaunchManager,
    SmartSession,
    _SMART_CONFIG_CACHE,
)


_FAKE_SMART_CONFIG = {
    "authorization_endpoint": "https://fhir.epic.com/oauth2/authorize",
    "token_endpoint": "https://fhir.epic.com/oauth2/token",
}


def _mgr() -> SmartLaunchManager:
    return SmartLaunchManager(
        client_id="test-client-id",
        redirect_uri="https://example.com/api/epic/callback",
        default_scopes=["patient/Patient.read", "patient/Encounter.read"],
    )


def _stub_smart_config():
    _SMART_CONFIG_CACHE.clear()
    return patch("cardioauth.fhir.smart_launch._fetch_smart_config", return_value=_FAKE_SMART_CONFIG)


# ── Authorize URL construction ─────────────────────────────────────────


def test_ehr_launch_includes_launch_token_and_aud() -> None:
    """EHR-context launches must pass through Epic's launch token and
    set aud to the FHIR base URL so Epic binds the token correctly."""
    with _stub_smart_config():
        m = _mgr()
        url = m.begin_ehr_launch(
            iss="https://fhir.epic.com/api/FHIR/R4",
            launch_token="launch-abc-123",
        )
    assert url.startswith("https://fhir.epic.com/oauth2/authorize?")
    assert "launch=launch-abc-123" in url
    assert "aud=https" in url
    assert "client_id=test-client-id" in url
    # State must be opaque and unguessable
    state = [p.split("=")[1] for p in url.split("?")[1].split("&") if p.startswith("state=")][0]
    assert len(state) >= 20


def test_standalone_launch_uses_patient_scope() -> None:
    """Standalone launches have no launch token; the scope list must
    include launch/patient so Epic prompts the user to pick a patient."""
    with _stub_smart_config():
        m = _mgr()
        url = m.begin_standalone_launch(iss="https://fhir.epic.com/api/FHIR/R4")
    assert "launch=" not in url
    assert "launch%2Fpatient" in url or "launch/patient" in url


def test_state_round_trip_required_for_callback() -> None:
    """The state generated in begin_*_launch must be presented back at
    callback — otherwise the request is rejected (replay/CSRF guard)."""
    with _stub_smart_config():
        m = _mgr()
        m.begin_standalone_launch(iss="https://fhir.epic.com/api/FHIR/R4")
        with pytest.raises(ValueError, match="Unknown or expired state"):
            m.complete_callback(code="x", state="not-the-real-state")


# ── Callback / token exchange ──────────────────────────────────────────


def test_callback_exchange_creates_session_with_patient_context() -> None:
    """When Epic returns a token with patient/encounter/scope claims,
    those propagate into the session object the manager hands out."""
    fake_token_response = {
        "access_token": "eyJtoken",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "patient/Patient.read patient/Encounter.read",
        "patient": "erXuFYU-test-patient",
        "encounter": "enc-2024-09-22",
        "fhirUser": "Practitioner/prac-123",
    }

    with _stub_smart_config(), patch("cardioauth.fhir.smart_launch.requests.post") as mock_post:
        mock_post.return_value.ok = True
        mock_post.return_value.json = lambda: fake_token_response

        m = _mgr()
        url = m.begin_ehr_launch(iss="https://fhir.epic.com/api/FHIR/R4", launch_token="abc")
        state = [p.split("=")[1] for p in url.split("?")[1].split("&") if p.startswith("state=")][0]
        session = m.complete_callback(code="auth-code-xyz", state=state)

    assert session.patient_id == "erXuFYU-test-patient"
    assert session.encounter_id == "enc-2024-09-22"
    assert session.fhir_user == "Practitioner/prac-123"
    assert session.access_token == "eyJtoken"
    assert session.expires_at > time.time() + 3000  # ~1 hour from now


def test_callback_token_error_surfaces() -> None:
    """A non-2xx response from Epic's token endpoint must throw with the
    body, not swallow it — that's how we diagnose Epic-side issues."""
    with _stub_smart_config(), patch("cardioauth.fhir.smart_launch.requests.post") as mock_post:
        mock_post.return_value.ok = False
        mock_post.return_value.status_code = 400
        mock_post.return_value.text = '{"error":"invalid_grant"}'

        m = _mgr()
        url = m.begin_standalone_launch(iss="https://fhir.epic.com/api/FHIR/R4")
        state = [p.split("=")[1] for p in url.split("?")[1].split("&") if p.startswith("state=")][0]
        with pytest.raises(RuntimeError, match="invalid_grant"):
            m.complete_callback(code="x", state=state)


# ── Session lifecycle ──────────────────────────────────────────────────


def test_expired_session_is_swept_from_get() -> None:
    """get_session must drop expired sessions so callers never act on
    a token Epic would reject anyway."""
    m = _mgr()
    s = SmartSession(
        session_id="expired-1",
        access_token="tok",
        expires_at=time.time() - 60,  # already expired
    )
    m._sessions[s.session_id] = s
    assert m.get_session(s.session_id) is None
    assert "expired-1" not in m._sessions


def test_session_to_dict_redacts_access_token() -> None:
    """Session metadata shipped to the UI must NOT include the raw
    access token — only a preview suitable for display."""
    s = SmartSession(
        session_id="s1", access_token="this-is-secret-do-not-leak",
        expires_at=time.time() + 600,
    )
    d = s.to_dict()
    assert "access_token" not in d
    assert "refresh_token" not in d
    assert "access_token_preview" in d
    assert "this-is-secret-do-not-leak" not in str(d)


def test_end_session_returns_whether_present() -> None:
    m = _mgr()
    m._sessions["s1"] = SmartSession(session_id="s1", access_token="t", expires_at=time.time() + 60)
    assert m.end_session("s1") is True
    assert m.end_session("s1") is False  # already gone


def test_list_sessions_drops_expired() -> None:
    m = _mgr()
    fresh = SmartSession(session_id="fresh", access_token="t", expires_at=time.time() + 600)
    stale = SmartSession(session_id="stale", access_token="t", expires_at=time.time() - 600)
    m._sessions[fresh.session_id] = fresh
    m._sessions[stale.session_id] = stale
    listed = m.list_sessions()
    ids = {s.session_id for s in listed}
    assert ids == {"fresh"}
