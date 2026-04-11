"""Supabase Auth — JWT verification and role-based access control.

Architecture:
  1. Frontend calls Supabase JS client for login/signup (email+password or SSO)
  2. Supabase returns a JWT access token
  3. Frontend sends JWT in Authorization: Bearer <token> header
  4. This module verifies the JWT against Supabase's JWKS public key
  5. Extracts user ID, email, role from the token claims
  6. FastAPI Depends() gates enforce auth on protected endpoints

Environment variables required:
  SUPABASE_URL        — e.g., https://xxxxx.supabase.co
  SUPABASE_ANON_KEY   — public anon key (safe to expose in frontend)
  SUPABASE_JWT_SECRET — JWT secret from Supabase dashboard → Settings → API → JWT Secret

Roles:
  - provider: can run PA pipeline, view own cases
  - admin: can manage policy library, view all cases, access analytics
  - demo: limited access, no real PHI (default for unauthenticated in demo mode)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Supabase config
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

# When True, endpoints work without auth (for demo/development)
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "true").lower() in ("1", "true", "yes")

# Bearer token extractor — auto_error=False so we can handle missing tokens gracefully
_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    """Authenticated user extracted from JWT claims."""
    id: str                    # Supabase user UUID
    email: str
    role: str = "provider"     # provider | admin | demo
    name: str = ""
    is_authenticated: bool = True

    @staticmethod
    def demo_user() -> "AuthUser":
        """Return a demo user for unauthenticated access."""
        return AuthUser(
            id="demo",
            email="demo@cardioauth.app",
            role="demo",
            name="Dr. Demo",
            is_authenticated=False,
        )


def _decode_supabase_jwt(token: str) -> dict:
    """Verify and decode a Supabase JWT token.

    Supabase JWTs are signed with the project's JWT secret (HS256).
    The secret is found in: Supabase Dashboard → Settings → API → JWT Secret.
    """
    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Authentication not configured (SUPABASE_JWT_SECRET missing)",
        )

    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired — please log in again")
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid JWT: %s", e)
        raise HTTPException(status_code=401, detail="Invalid authentication token")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthUser:
    """Extract and verify the current user from the request.

    When AUTH_DISABLED=true (default for demo), returns a demo user
    if no token is provided. When AUTH_DISABLED=false, missing or
    invalid tokens result in 401.
    """
    # If no credentials provided
    if not credentials:
        if AUTH_DISABLED:
            return AuthUser.demo_user()
        raise HTTPException(status_code=401, detail="Authentication required")

    token = credentials.credentials
    payload = _decode_supabase_jwt(token)

    # Extract user info from Supabase JWT claims
    user_id = payload.get("sub", "")
    email = payload.get("email", "")
    user_metadata = payload.get("user_metadata", {})
    app_metadata = payload.get("app_metadata", {})

    # Role from app_metadata (set via Supabase admin) or default to provider
    role = app_metadata.get("role", "provider")
    name = user_metadata.get("full_name", "") or user_metadata.get("name", "") or email.split("@")[0]

    return AuthUser(
        id=user_id,
        email=email,
        role=role,
        name=name,
        is_authenticated=True,
    )


async def require_auth(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Strict auth — rejects demo users even when AUTH_DISABLED=true.

    Use this for endpoints that should NEVER be accessible without login,
    even in demo mode (e.g., real PHI endpoints, admin actions).
    """
    if not user.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required for this action")
    return user


async def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Require admin role."""
    if not user.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_provider_or_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Require provider or admin role (not demo)."""
    if not user.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.role not in ("provider", "admin"):
        raise HTTPException(status_code=403, detail="Provider or admin access required")
    return user


# ────────────────────────────────────────────────────────────────────────
# Audit logging
# ────────────────���─────────────────���─────────────────────────────────────

_AUDIT_LOG_PATH = os.environ.get(
    "AUDIT_LOG_PATH",
    str(__import__("pathlib").Path(__file__).parent.parent / "data" / "audit.log"),
)


def log_audit(
    user: AuthUser,
    action: str,
    resource: str = "",
    detail: str = "",
) -> None:
    """Write a HIPAA-compliant audit log entry.

    Format: timestamp | user_id | email | role | action | resource | detail
    """
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry = f"{ts} | {user.id} | {user.email} | {user.role} | {action} | {resource} | {detail}"

    # Log to application logger (goes to Railway logs)
    logger.info("AUDIT: %s", entry)

    # Also append to file for persistence
    try:
        from pathlib import Path
        p = Path(_AUDIT_LOG_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(entry + "\n")
    except Exception as e:
        logger.warning("Failed to write audit log file: %s", e)


def is_auth_configured() -> bool:
    """Check if Supabase auth credentials are set."""
    return bool(SUPABASE_URL and SUPABASE_JWT_SECRET)
