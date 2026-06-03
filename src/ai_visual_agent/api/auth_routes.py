from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from ai_visual_agent.api.dependencies import AUTH_COOKIE_NAME, require_admin_dependency, require_current_user
from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import (
    AuthLoginRequest,
    AuthTokenResponse,
    AuthUser,
    AuthUserCreateRequest,
    AuthUserUpdateRequest,
)
from ai_visual_agent.services.auth import authenticate_admin, issue_access_token
from ai_visual_agent.services.user_store import public_user, user_store


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/login", response_model=AuthTokenResponse)
def login(request: AuthLoginRequest, response: Response) -> AuthTokenResponse:
    try:
        user = authenticate_admin(request.email, request.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    token = issue_access_token(user)
    _set_auth_cookie(response, token)
    return AuthTokenResponse(access_token=token, user=user)


@auth_router.get("/me", response_model=AuthUser)
def me(
    response: Response,
    user: AuthUser = Depends(require_current_user),
    authorization: str | None = Header(default=None),
) -> AuthUser:
    token = _bearer_token(authorization)
    if token:
        _set_auth_cookie(response, token)
    return user


@auth_router.get("/users", response_model=list[AuthUser])
def list_users(_admin: AuthUser = Depends(require_admin_dependency)) -> list[AuthUser]:
    return [public_user(record) for record in user_store.list()]


@auth_router.post("/users", response_model=AuthUser)
def create_user(
    request: AuthUserCreateRequest,
    _admin: AuthUser = Depends(require_admin_dependency),
) -> AuthUser:
    try:
        return public_user(user_store.create(request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@auth_router.patch("/users/{user_id}", response_model=AuthUser)
def update_user(
    user_id: str,
    request: AuthUserUpdateRequest,
    _admin: AuthUser = Depends(require_admin_dependency),
) -> AuthUser:
    try:
        return public_user(user_store.update(user_id, request))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=settings.auth_token_ttl_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization.split(" ", 1)[1].strip()
