from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import math
from threading import BoundedSemaphore, Lock
import time
from typing import Any, Callable, Iterator

from ai_visual_agent.config import get_settings


@dataclass(frozen=True)
class RateLimitDecision:
    scope: str
    limit: int
    window_seconds: int
    retry_after_seconds: int
    backend: str

    def to_detail(self) -> dict[str, object]:
        return {
            "message": "Rate limit exceeded. Please retry later.",
            "scope": self.scope,
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "retry_after_seconds": self.retry_after_seconds,
            "backend": self.backend,
        }


class RateLimitExceeded(Exception):
    def __init__(self, decision: RateLimitDecision) -> None:
        super().__init__(str(decision.to_detail()["message"]))
        self.decision = decision


class RateLimiterUnavailable(RuntimeError):
    def __init__(self, backend: str, cause: Exception) -> None:
        super().__init__(f"Rate limit backend '{backend}' is unavailable: {type(cause).__name__}: {cause}")
        self.backend = backend
        self.cause = cause


class FixedWindowRateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._memory_counts: dict[tuple[str, str, int], int] = {}
        self._redis_client: Any | None = None
        self._redis_disabled = False

    def check(
        self,
        *,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int = 60,
    ) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled or limit <= 0:
            return
        backend = settings.rate_limit_backend.lower()
        client = self._get_redis_client(backend)
        if client is not None:
            self._check_redis(
                client=client,
                scope=scope,
                identity=identity,
                limit=limit,
                window_seconds=window_seconds,
            )
            return
        self._check_memory(
            scope=scope,
            identity=identity,
            limit=limit,
            window_seconds=window_seconds,
            backend="memory",
        )

    def _check_memory(
        self,
        *,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int,
        backend: str,
    ) -> None:
        now = time.time()
        bucket = int(now // window_seconds)
        retry_after = max(1, math.ceil((bucket + 1) * window_seconds - now))
        key = (scope, _safe_identity(identity), bucket)
        with self._lock:
            if len(self._memory_counts) > 5000:
                oldest_allowed = bucket - 2
                self._memory_counts = {
                    item_key: count
                    for item_key, count in self._memory_counts.items()
                    if item_key[2] >= oldest_allowed
                }
            count = self._memory_counts.get(key, 0) + 1
            self._memory_counts[key] = count
        if count > limit:
            raise RateLimitExceeded(
                RateLimitDecision(
                    scope=scope,
                    limit=limit,
                    window_seconds=window_seconds,
                    retry_after_seconds=retry_after,
                    backend=backend,
                )
            )

    def _check_redis(
        self,
        *,
        client: Any,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        now = time.time()
        bucket = int(now // window_seconds)
        retry_after = max(1, math.ceil((bucket + 1) * window_seconds - now))
        key = f"rate_limit:{scope}:{_safe_identity(identity)}:{bucket}"
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, window_seconds + 5)
        if count > limit:
            ttl = client.ttl(key)
            if isinstance(ttl, int) and ttl > 0:
                retry_after = ttl
            raise RateLimitExceeded(
                RateLimitDecision(
                    scope=scope,
                    limit=limit,
                    window_seconds=window_seconds,
                    retry_after_seconds=retry_after,
                    backend="redis",
                )
            )

    def _get_redis_client(self, backend: str) -> Any | None:
        if backend not in {"redis", "auto"} or self._redis_disabled:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            import redis

            client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
            client.ping()
        except Exception as exc:
            if backend == "redis":
                raise RateLimiterUnavailable(backend, exc) from exc
            self._redis_disabled = True
            return None
        self._redis_client = client
        return client


class _ConcurrencyLease:
    def __init__(self, release: Callable[[], None]) -> None:
        self._release = release
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._release()

    def __enter__(self) -> _ConcurrencyLease:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()


class ConcurrencyLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._memory_slots: dict[str, BoundedSemaphore] = {}
        self._redis_client: Any | None = None
        self._redis_disabled = False

    def acquire(
        self,
        *,
        scope: str,
        max_concurrent: int,
        timeout_seconds: float,
        lease_ttl_seconds: int = 900,
    ) -> _ConcurrencyLease:
        settings = get_settings()
        if max_concurrent <= 0:
            return _ConcurrencyLease(lambda: None)
        backend = settings.rate_limit_backend.lower()
        client = self._get_redis_client(backend)
        if client is not None:
            return self._acquire_redis(
                client=client,
                scope=scope,
                max_concurrent=max_concurrent,
                timeout_seconds=timeout_seconds,
                lease_ttl_seconds=lease_ttl_seconds,
            )
        return self._acquire_memory(
            scope=scope,
            max_concurrent=max_concurrent,
            timeout_seconds=timeout_seconds,
        )

    def _acquire_memory(
        self,
        *,
        scope: str,
        max_concurrent: int,
        timeout_seconds: float,
    ) -> _ConcurrencyLease:
        with self._lock:
            semaphore = self._memory_slots.get(scope)
            if semaphore is None:
                semaphore = BoundedSemaphore(max_concurrent)
                self._memory_slots[scope] = semaphore
        acquired = (
            semaphore.acquire(blocking=False)
            if timeout_seconds <= 0
            else semaphore.acquire(timeout=timeout_seconds)
        )
        if not acquired:
            raise RateLimitExceeded(
                RateLimitDecision(
                    scope=f"{scope}_concurrency",
                    limit=max_concurrent,
                    window_seconds=max(1, int(timeout_seconds)),
                    retry_after_seconds=max(1, int(timeout_seconds)),
                    backend="memory",
                )
            )
        return _ConcurrencyLease(semaphore.release)

    def _acquire_redis(
        self,
        *,
        client: Any,
        scope: str,
        max_concurrent: int,
        timeout_seconds: float,
        lease_ttl_seconds: int,
    ) -> _ConcurrencyLease:
        key = f"concurrency_limit:{scope}"
        deadline = time.monotonic() + max(0, timeout_seconds)
        while True:
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, lease_ttl_seconds)
            if count <= max_concurrent:
                return _ConcurrencyLease(lambda: _release_redis_concurrency(client, key))
            client.decr(key)
            if timeout_seconds <= 0 or time.monotonic() >= deadline:
                ttl = client.ttl(key)
                raise RateLimitExceeded(
                    RateLimitDecision(
                        scope=f"{scope}_concurrency",
                        limit=max_concurrent,
                        window_seconds=lease_ttl_seconds,
                        retry_after_seconds=ttl if isinstance(ttl, int) and ttl > 0 else 1,
                        backend="redis",
                    )
                )
            time.sleep(0.2)

    def _get_redis_client(self, backend: str) -> Any | None:
        if backend not in {"redis", "auto"} or self._redis_disabled:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            import redis

            client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
            client.ping()
        except Exception as exc:
            if backend == "redis":
                raise RateLimiterUnavailable(backend, exc) from exc
            self._redis_disabled = True
            return None
        self._redis_client = client
        return client


def _release_redis_concurrency(client: Any, key: str) -> None:
    try:
        value = int(client.decr(key))
        if value <= 0:
            client.delete(key)
    except Exception:
        return


def _safe_identity(identity: str) -> str:
    return sha256(identity.encode("utf-8")).hexdigest()[:24]


rate_limiter = FixedWindowRateLimiter()
concurrency_limiter = ConcurrencyLimiter()


def enforce_rate_limit(*, scope: str, identity: str, limit: int, window_seconds: int = 60) -> None:
    rate_limiter.check(scope=scope, identity=identity, limit=limit, window_seconds=window_seconds)


@contextmanager
def image_generation_budget(*, identity: str) -> Iterator[None]:
    settings = get_settings()
    enforce_rate_limit(
        scope="image_generation",
        identity=identity,
        limit=settings.rate_limit_image_generation_per_minute,
    )
    enforce_rate_limit(
        scope="image_generation_global",
        identity="global",
        limit=settings.rate_limit_image_generation_global_per_minute,
    )
    with concurrency_limiter.acquire(
        scope="image_generation",
        max_concurrent=settings.image_generation_max_concurrent,
        timeout_seconds=settings.image_generation_acquire_timeout_seconds,
    ):
        yield
