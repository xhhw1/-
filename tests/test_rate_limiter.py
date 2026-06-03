from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ai_visual_agent.api import routes
from ai_visual_agent.api.dependencies import require_current_user
from ai_visual_agent.domain import AuthUser
from ai_visual_agent.main import app
from ai_visual_agent.services import rate_limiter
from ai_visual_agent.services.rate_limiter import (
    ConcurrencyLimiter,
    FixedWindowRateLimiter,
    RateLimitDecision,
    RateLimitExceeded,
)


class _EnabledMemoryRateLimitSettings:
    rate_limit_enabled = True
    rate_limit_backend = "memory"
    redis_url = "redis://localhost:6379/0"


def test_fixed_window_limiter_blocks_after_limit(monkeypatch) -> None:
    monkeypatch.setattr(rate_limiter, "get_settings", lambda: _EnabledMemoryRateLimitSettings())
    limiter = FixedWindowRateLimiter()

    limiter.check(scope="agent_message", identity="user-1", limit=2)
    limiter.check(scope="agent_message", identity="user-1", limit=2)

    with pytest.raises(RateLimitExceeded) as exc:
        limiter.check(scope="agent_message", identity="user-1", limit=2)

    assert exc.value.decision.scope == "agent_message"
    assert exc.value.decision.limit == 2
    assert exc.value.decision.retry_after_seconds >= 1


def test_concurrency_limiter_releases_memory_slot(monkeypatch) -> None:
    monkeypatch.setattr(rate_limiter, "get_settings", lambda: _EnabledMemoryRateLimitSettings())
    limiter = ConcurrencyLimiter()
    lease = limiter.acquire(scope="image_generation", max_concurrent=1, timeout_seconds=0)

    with pytest.raises(RateLimitExceeded):
        limiter.acquire(scope="image_generation", max_concurrent=1, timeout_seconds=0)

    lease.release()
    limiter.acquire(scope="image_generation", max_concurrent=1, timeout_seconds=0).release()


def test_agent_message_endpoint_returns_429_when_rate_limited(monkeypatch) -> None:
    owner = f"rate-limit-user-{uuid4()}"
    app.dependency_overrides[require_current_user] = lambda: AuthUser(
        id=owner,
        email=f"{owner}@example.com",
        role="admin",
    )
    client = TestClient(app)
    session = client.post("/api/conversations", json={"title": "rate limit test"}).json()

    def fake_enforce_rate_limit(**_kwargs) -> None:
        raise RateLimitExceeded(
            RateLimitDecision(
                scope="agent_message",
                limit=1,
                window_seconds=60,
                retry_after_seconds=42,
                backend="memory",
            )
        )

    try:
        monkeypatch.setattr(routes, "enforce_rate_limit", fake_enforce_rate_limit)
        response = client.post(
            f"/api/conversations/{session['session']['id']}/messages",
            json={"content": "hello"},
        )

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "42"
        assert response.json()["detail"]["scope"] == "agent_message"
    finally:
        app.dependency_overrides.pop(require_current_user, None)
