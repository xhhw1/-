from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ai_visual_agent.api.dependencies import require_admin_dependency, require_current_user
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
def login(request: AuthLoginRequest) -> AuthTokenResponse:
    try:
        user = authenticate_admin(request.email, request.password)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return AuthTokenResponse(access_token=issue_access_token(user), user=user)


@auth_router.get("/me", response_model=AuthUser)
def me(user: AuthUser = Depends(require_current_user)) -> AuthUser:
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
