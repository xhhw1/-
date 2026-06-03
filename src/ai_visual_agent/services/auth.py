from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from typing import Any

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AuthUser
from ai_visual_agent.services.user_store import public_user, user_store, verify_password


def auth_enabled() -> bool:
    return bool(get_settings().auth_enabled)


def default_user() -> AuthUser:
    settings = get_settings()
    return AuthUser(id=_normalize_email(settings.admin_email), email=settings.admin_email, role="admin")


def authenticate_user(email: str, password: str) -> AuthUser:
    settings = get_settings()
    try:
        record = user_store.get_by_email(email)
    except KeyError as exc:
        if _normalize_email(email) == _normalize_email(settings.admin_email) and not settings.admin_password:
            raise RuntimeError("ADMIN_PASSWORD is required for first admin bootstrap.") from exc
        raise ValueError("Invalid email or password.") from exc
    if record.status != "active":
        raise ValueError("User is disabled.")
    if not verify_password(password, record.password_hash):
        raise ValueError("Invalid email or password.")
    return public_user(record)


def authenticate_admin(email: str, password: str) -> AuthUser:
    return authenticate_user(email, password)


def issue_access_token(user: AuthUser) -> str:
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.auth_token_ttl_minutes)
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "exp": int(expires_at.timestamp()),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64_json(header)}.{_b64_json(payload)}"
    signature = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64(signature)}"


def verify_access_token(token: str) -> AuthUser:
    settings = get_settings()
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid access token.")
    signing_input = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    supplied = _b64_decode(parts[2])
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("Invalid access token.")
    payload = json.loads(_b64_decode(parts[1]).decode("utf-8"))
    exp = int(payload.get("exp") or 0)
    if exp < int(datetime.now(UTC).timestamp()):
        raise ValueError("Access token expired.")
    email = str(payload.get("email") or "")
    return AuthUser(
        id=_normalize_email(str(payload.get("sub") or email)),
        email=email,
        role="admin" if payload.get("role") == "admin" else "member",
    )


def _normalize_email(value: str | None) -> str:
    return (value or "local-admin").strip().lower()


def _b64_json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
