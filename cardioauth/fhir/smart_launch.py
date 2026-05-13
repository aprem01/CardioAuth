"""SMART on FHIR App Launch — the user-driven Epic integration.

Backend Services (cardioauth/fhir/client.py) is for headless workflows:
nightly batches, cross-patient analytics, denial prediction training.
This module is for the user-driven flow: physician clicks 'Launch
CardioAuth' inside their Epic chart, Epic fires our launch endpoint
with `iss` + `launch` params, we redirect through OAuth, then read
that patient's chart using the user's access token.

Two entry points:
  - EHR launch:        Epic redirects to /api/epic/launch with iss + launch
  - Standalone launch: we hit Epic's authorize endpoint directly,
                        user picks a patient (used in dev/sandbox)

Both land in /api/epic/callback after Epic redirects back with `code`.

Sessions are stored in-process (good for single-container Railway
deploys; promote to durable storage when we scale to multiple instances).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets as _secrets
import time
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import urlencode, urljoin

import requests

logger = logging.getLogger(__name__)


# ── In-process session store ────────────────────────────────────────────


@dataclass
class SmartSession:
    """One physician's authenticated SMART launch session.

    Holds the access token Epic issued, the patient context attached
    to the launch, and metadata for replay/audit. Expires when the
    token expires.
    """
    session_id: str
    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0                     # epoch seconds
    patient_id: str = ""                        # FHIR Patient/<id>
    encounter_id: str = ""                      # FHIR Encounter/<id> if attached
    fhir_user: str = ""                         # FHIR ref to launching user (Practitioner/<id>)
    scope: str = ""                             # granted scopes
    iss: str = ""                               # FHIR base URL the token is for
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 30  # 30s safety margin

    def to_dict(self) -> dict:
        d = asdict(self)
        # Don't ship the raw token to UI consumers — only the metadata
        d["access_token_preview"] = (self.access_token[:12] + "…") if self.access_token else ""
        d.pop("access_token", None)
        d.pop("refresh_token", None)
        d["expires_in_seconds"] = max(0, int(self.expires_at - time.time()))
        return d


@dataclass
class _LaunchAttempt:
    """Tracks state through a single OAuth round trip."""
    state: str
    iss: str                                    # FHIR base URL
    authorize_url: str
    token_url: str
    code_verifier: str = ""
    scopes: str = ""
    redirect_uri: str = ""


class SmartLaunchManager:
    """Process-wide manager for SMART launches and active sessions."""

    def __init__(self, *, client_id: str, redirect_uri: str, default_scopes: list[str]) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.default_scopes = default_scopes
        self._pending: dict[str, _LaunchAttempt] = {}
        self._sessions: dict[str, SmartSession] = {}

    # ── Launch initiation ──────────────────────────────────────────

    def begin_ehr_launch(self, iss: str, launch_token: str) -> str:
        """Build the authorize URL Epic redirects the user's browser to.

        EHR launch — Epic gave us iss (their FHIR base) and a launch
        token that proves the launch came from inside a chart. We send
        both back to Epic's authorize endpoint with our requested scopes.
        Returns the URL to redirect the browser to.
        """
        return self._begin_launch(iss=iss, launch_token=launch_token, aud=iss)

    def begin_standalone_launch(self, iss: str) -> str:
        """Standalone launch — no Epic-supplied launch token. Used for
        dev testing without a real EHR. Epic will let the user pick a
        test patient after they sign in."""
        return self._begin_launch(iss=iss, launch_token="", aud=iss)

    def _begin_launch(self, *, iss: str, launch_token: str, aud: str) -> str:
        smart_config = _fetch_smart_config(iss)
        authorize_url = smart_config["authorization_endpoint"]
        token_url = smart_config["token_endpoint"]

        state = _secrets.token_urlsafe(24)
        scopes = " ".join(self.default_scopes + (["launch", "online_access"] if launch_token else ["launch/patient", "online_access"]))

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scopes,
            "state": state,
            "aud": aud,
        }
        if launch_token:
            params["launch"] = launch_token

        self._pending[state] = _LaunchAttempt(
            state=state, iss=iss,
            authorize_url=authorize_url, token_url=token_url,
            scopes=scopes, redirect_uri=self.redirect_uri,
        )

        full_url = authorize_url + "?" + urlencode(params)
        logger.info("SMART launch initiated state=%s iss=%s launch=%s", state, iss, bool(launch_token))
        return full_url

    # ── Callback handling ──────────────────────────────────────────

    def complete_callback(self, *, code: str, state: str) -> SmartSession:
        """Exchange the auth code for an access token + patient context.

        Epic registers our app with a JWK Set URL (cardioauth-smart on the
        vendor portal), so even the SMART App Launch token exchange has
        to be authenticated via private_key_jwt — not just bare client_id.
        We sign the same shape of assertion as Backend Services but with
        grant_type=authorization_code.
        """
        attempt = self._pending.pop(state, None)
        if not attempt:
            raise ValueError(f"Unknown or expired state '{state[:8]}…' — replay or session lost")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": attempt.redirect_uri,
            "client_id": self.client_id,
        }

        # If a private key is available, sign a JWT assertion. Epic requires
        # this for clients registered with a JWK Set URL. Falls back to
        # public-client (client_id only) if no key configured.
        private_key = _load_private_key()
        if private_key:
            import jwt as _jwt
            import uuid as _uuid
            now = int(time.time())
            claims = {
                "iss": self.client_id,
                "sub": self.client_id,
                "aud": attempt.token_url,
                "jti": str(_uuid.uuid4()),
                "iat": now,
                "exp": now + 300,
            }
            assertion = _jwt.encode(claims, private_key, algorithm="RS384", headers={"kid": "cardioauth-1"})
            data["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            data["client_assertion"] = assertion

        resp = requests.post(
            attempt.token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
            timeout=30,
        )
        if not resp.ok:
            err = (resp.text or "")[:400]
            raise RuntimeError(f"Token exchange failed HTTP {resp.status_code}: {err}")
        td = resp.json()

        session_id = _secrets.token_urlsafe(24)
        expires_in = int(td.get("expires_in", 3600))
        session = SmartSession(
            session_id=session_id,
            access_token=td["access_token"],
            refresh_token=td.get("refresh_token", "") or "",
            token_type=td.get("token_type", "Bearer"),
            expires_at=time.time() + expires_in,
            patient_id=td.get("patient", "") or "",
            encounter_id=td.get("encounter", "") or "",
            fhir_user=td.get("fhirUser", "") or "",
            scope=td.get("scope", "") or attempt.scopes,
            iss=attempt.iss,
        )
        self._sessions[session_id] = session
        logger.info("SMART session opened session=%s patient=%s scope=%s",
                    session_id[:8], session.patient_id[:8] + "…" if session.patient_id else "(none)",
                    session.scope[:80])
        return session

    # ── Session access ─────────────────────────────────────────────

    def get_session(self, session_id: str) -> SmartSession | None:
        s = self._sessions.get(session_id)
        if s and s.is_expired():
            self._sessions.pop(session_id, None)
            return None
        return s

    def list_sessions(self) -> list[SmartSession]:
        # Drop expired ones on the way out
        now = time.time()
        keep = {k: v for k, v in self._sessions.items() if v.expires_at > now - 30}
        self._sessions = keep
        return list(keep.values())

    def end_session(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None


# ── Private-key loader (shared with Backend Services) ──────────────────


def _load_private_key() -> str:
    """Load the Epic private key from the same env var Backend Services uses.
    Returns empty string if no key is configured.
    """
    key = os.environ.get("EPIC_PRIVATE_KEY", "")
    if key:
        return key
    path = os.environ.get("EPIC_PRIVATE_KEY_PATH", "")
    if path:
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            return ""
    return ""


# ── SMART discovery ─────────────────────────────────────────────────────


_SMART_CONFIG_CACHE: dict[str, tuple[float, dict]] = {}
_SMART_CONFIG_TTL = 3600  # 1 hour


def _fetch_smart_config(iss: str) -> dict[str, Any]:
    """Fetch /.well-known/smart-configuration from the FHIR server.

    Cached for 1 hour — these endpoints don't change often, and Epic
    serves them from a single config so hitting them on every launch
    is wasteful.
    """
    now = time.time()
    cached = _SMART_CONFIG_CACHE.get(iss)
    if cached and now - cached[0] < _SMART_CONFIG_TTL:
        return cached[1]

    url = iss.rstrip("/") + "/.well-known/smart-configuration"
    resp = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
    resp.raise_for_status()
    config = resp.json()
    _SMART_CONFIG_CACHE[iss] = (now, config)
    return config


# ── Module-level singleton (created lazily) ─────────────────────────────


_manager: SmartLaunchManager | None = None


def get_manager(*, redirect_uri: str = "", client_id: str = "") -> SmartLaunchManager:
    """Lazy singleton. First caller supplies config; subsequent callers
    get the same instance regardless of args."""
    global _manager
    if _manager is None:
        if not client_id:
            # Prefer a SMART-specific client_id so the SMART App Launch
            # flow and the Backend Services flow can register as
            # separate Epic apps (different Application Audience,
            # different scopes). Fall back to EPIC_CLIENT_ID if
            # SMART-specific isn't set so single-app deployments still
            # work.
            client_id = (
                os.environ.get("EPIC_SMART_CLIENT_ID", "")
                or os.environ.get("EPIC_CLIENT_ID", "")
            )
        if not redirect_uri:
            redirect_uri = os.environ.get(
                "EPIC_SMART_REDIRECT_URI",
                "https://cardioauth2-production.up.railway.app/api/epic/callback",
            )
        # Default scopes for the SMART launch — read-only across the
        # resources the lean pipeline needs. patient/* scopes get
        # automatically narrowed to the patient context Epic attaches.
        default_scopes = [
            "patient/Patient.read",
            "patient/Encounter.read",
            "patient/Condition.read",
            "patient/Observation.read",
            "patient/MedicationRequest.read",
            "patient/DiagnosticReport.read",
            "patient/Procedure.read",
            "patient/Coverage.read",
            "patient/DocumentReference.read",
            "patient/Binary.read",
        ]
        _manager = SmartLaunchManager(
            client_id=client_id,
            redirect_uri=redirect_uri,
            default_scopes=default_scopes,
        )
    return _manager
