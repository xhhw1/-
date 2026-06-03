from __future__ import annotations

from fastapi import Header, HTTPException

from ai_visual_agent.domain import AuthUser
from ai_visual_agent.services.auth import auth_enabled, default_user, verify_access_token


def require_current_user(authorization: str | None = Header(default=None)) -> AuthUser:
    if not auth_enabled():
        return default_user()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return verify_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_admin_dependency(authorization: str | None = Header(default=None)) -> AuthUser:
    user = require_current_user(authorization)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required.")
    return user
