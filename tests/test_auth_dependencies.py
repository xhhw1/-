from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ai_visual_agent.api import dependencies
from ai_visual_agent.api.auth_routes import auth_router, update_user
from ai_visual_agent.api.dependencies import AUTH_COOKIE_NAME, require_current_user
from ai_visual_agent.domain import AuthUser, AuthUserUpdateRequest


def test_require_current_user_accepts_auth_cookie(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "auth_enabled", lambda: True)

    def fake_verify(token: str) -> AuthUser:
        assert token == "cookie-token"
        return AuthUser(id="admin@example.com", email="admin@example.com", role="admin")

    monkeypatch.setattr(dependencies, "verify_access_token", fake_verify)

    app = FastAPI()

    @app.get("/protected")
    def protected(user: AuthUser = Depends(require_current_user)) -> dict[str, str]:
        return {"user": user.id}

    client = TestClient(app)
    response = client.get("/protected", cookies={AUTH_COOKIE_NAME: "cookie-token"})

    assert response.status_code == 200
    assert response.json() == {"user": "admin@example.com"}


def test_logout_clears_auth_cookie() -> None:
    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app)

    response = client.post("/api/auth/logout", cookies={AUTH_COOKIE_NAME: "old-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    set_cookie = response.headers["set-cookie"]
    assert AUTH_COOKIE_NAME in set_cookie
    assert "Max-Age=0" in set_cookie


def test_update_user_rejects_self_disable() -> None:
    admin = AuthUser(id="admin@example.com", email="admin@example.com", role="admin")

    with pytest.raises(HTTPException) as exc_info:
        update_user("admin@example.com", AuthUserUpdateRequest(status="disabled"), admin)

    assert exc_info.value.status_code == 400
