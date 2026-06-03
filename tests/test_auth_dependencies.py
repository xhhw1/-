from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ai_visual_agent.api import dependencies
from ai_visual_agent.api.dependencies import AUTH_COOKIE_NAME, require_current_user
from ai_visual_agent.domain import AuthUser


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
