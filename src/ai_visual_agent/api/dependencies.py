from __future__ import annotations

from fastapi import Cookie, Depends, Header, HTTPException, Query

from ai_visual_agent.domain import AuthUser
from ai_visual_agent.services.auth import auth_enabled, default_user, verify_access_token

AUTH_COOKIE_NAME = "ai_visual_agent_auth_token"


def require_current_user(
    authorization: str | None = Header(default=None),
    auth_cookie: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    access_token: str | None = Query(default=None),
) -> AuthUser:
    if not auth_enabled():
        return default_user()
    token = _bearer_token(authorization) or auth_cookie or access_token
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        return verify_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_admin_dependency(user: AuthUser = Depends(require_current_user)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required.")
    return user


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization.split(" ", 1)[1].strip()
