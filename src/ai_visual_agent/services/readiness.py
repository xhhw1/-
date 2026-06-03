from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from ai_visual_agent.config import Settings, get_settings
from ai_visual_agent.domain import ReadinessCheck, ReadinessReport
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)
from ai_visual_agent.services.task_queue import list_worker_heartbeats


def build_readiness_report(settings: Settings | Any | None = None) -> ReadinessReport:
    settings = settings or get_settings()
    checks = [
        _database_check(settings),
        _redis_check(settings),
        _worker_check(settings),
        _object_storage_check(settings),
        _qdrant_check(settings),
    ]
    failures = [check.message for check in checks if check.required and check.status == "failed"]
    return ReadinessReport(
        status="not_ready" if failures else "ready",
        checks=checks,
        failures=failures,
    )


def _timed_check(
    *,
    name: str,
    backend: str,
    required: bool,
    check: Callable[[], tuple[str, dict[str, Any] | None]],
) -> ReadinessCheck:
    started = perf_counter()
    try:
        message, details = check()
    except Exception as exc:
        return ReadinessCheck(
            name=name,
            backend=backend,
            status="failed",
            required=required,
            message=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
        )
    return ReadinessCheck(
        name=name,
        backend=backend,
        status="ready",
        required=required,
        message=message,
        latency_ms=_elapsed_ms(started),
        details=details or {},
    )


def _skipped_check(*, name: str, backend: str, message: str) -> ReadinessCheck:
    return ReadinessCheck(
        name=name,
        backend=backend,
        status="skipped",
        required=False,
        message=message,
    )


def _database_check(settings: Any) -> ReadinessCheck:
    uses_graph_postgres = str(getattr(settings, "graph_checkpoint_backend", "")).lower() in {
        "postgres",
        "postgresql",
        "sql",
    }
    required = project_store_uses_sql(settings) or uses_graph_postgres
    backend = str(getattr(settings, "project_store_backend", "memory")).lower()
    if not required:
        return _skipped_check(
            name="database",
            backend=backend,
            message="SQL persistence is not enabled for this runtime.",
        )

    database_url = resolved_project_database_url(settings)
    if not database_url:
        return ReadinessCheck(
            name="database",
            backend=backend,
            status="failed",
            required=True,
            message="DATABASE_URL or LOCAL_DATABASE_URL is required.",
        )

    def probe() -> tuple[str, dict[str, Any]]:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url, future=True, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()
        safe_url = _redact_database_url(database_url)
        return "Database connection is ready.", {"url": safe_url}

    return _timed_check(name="database", backend=backend, required=True, check=probe)


def _redis_check(settings: Any) -> ReadinessCheck:
    task_queue_backend = str(getattr(settings, "task_queue_backend", "thread")).lower()
    rate_limit_backend = str(getattr(settings, "rate_limit_backend", "memory")).lower()
    required = task_queue_backend == "redis" or rate_limit_backend == "redis"
    if not required:
        return _skipped_check(
            name="redis",
            backend="memory/thread",
            message="Redis is not required for this runtime.",
        )

    def probe() -> tuple[str, dict[str, Any]]:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        queue_name = getattr(settings, "task_queue_redis_queue_name", "ai_visual_agent:jobs")
        queue_length = int(client.llen(queue_name))
        return "Redis connection is ready.", {"queue": queue_name, "queue_length": queue_length}

    return _timed_check(name="redis", backend="redis", required=True, check=probe)


def _worker_check(settings: Any) -> ReadinessCheck:
    task_queue_backend = str(getattr(settings, "task_queue_backend", "thread")).lower()
    if task_queue_backend != "redis":
        return _skipped_check(
            name="worker",
            backend=task_queue_backend,
            message="External worker heartbeat is only required for Redis task queue mode.",
        )

    def probe() -> tuple[str, dict[str, Any]]:
        heartbeats = list_worker_heartbeats(settings)
        if not heartbeats:
            raise RuntimeError("No active worker heartbeat was found in Redis.")
        return "Redis worker heartbeat is active.", {"workers": heartbeats}

    return _timed_check(name="worker", backend="redis", required=True, check=probe)


def _object_storage_check(settings: Any) -> ReadinessCheck:
    backend = str(getattr(settings, "storage_backend", "local")).lower()
    if backend not in {"s3", "minio"}:
        return _skipped_check(
            name="object_storage",
            backend=backend,
            message="Object storage is using local filesystem mode.",
        )

    missing = [
        name
        for name, value in {
            "S3_BUCKET": getattr(settings, "s3_bucket", ""),
            "S3_ACCESS_KEY": getattr(settings, "s3_access_key", ""),
            "S3_SECRET_KEY": getattr(settings, "s3_secret_key", ""),
        }.items()
        if not value
    ]
    if missing:
        return ReadinessCheck(
            name="object_storage",
            backend=backend,
            status="failed",
            required=True,
            message=f"Missing object storage settings: {', '.join(missing)}.",
            details={"missing": missing},
        )

    def probe() -> tuple[str, dict[str, Any]]:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=getattr(settings, "s3_endpoint_url", None),
            aws_access_key_id=getattr(settings, "s3_access_key", None),
            aws_secret_access_key=getattr(settings, "s3_secret_key", None),
        )
        bucket = getattr(settings, "s3_bucket", "")
        client.head_bucket(Bucket=bucket)
        return "Object storage bucket is reachable.", {"bucket": bucket}

    return _timed_check(name="object_storage", backend=backend, required=True, check=probe)


def _qdrant_check(settings: Any) -> ReadinessCheck:
    backend = "qdrant"
    required = (
        str(getattr(settings, "app_env", "local")).lower() == "production"
        or not bool(getattr(settings, "mock_external_tools", True))
    )
    if not required:
        return _skipped_check(
            name="vector_memory",
            backend=backend,
            message="Qdrant is not required while external tools are mocked.",
        )
    if not getattr(settings, "qdrant_url", ""):
        return ReadinessCheck(
            name="vector_memory",
            backend=backend,
            status="failed",
            required=True,
            message="QDRANT_URL is required.",
        )

    def probe() -> tuple[str, dict[str, Any]]:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=settings.qdrant_url, timeout=3)
        collections = client.get_collections().collections
        names = [collection.name for collection in collections]
        target = getattr(settings, "qdrant_collection", "ai_visual_agent_memory")
        return (
            "Qdrant connection is ready.",
            {"collection": target, "collection_exists": target in names},
        )

    return _timed_check(name="vector_memory", backend=backend, required=True, check=probe)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _redact_database_url(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    scheme_and_credentials, host = database_url.rsplit("@", 1)
    scheme = scheme_and_credentials.split("://", 1)[0] if "://" in scheme_and_credentials else ""
    if scheme:
        return f"{scheme}://***:***@{host}"
    return f"***:***@{host}"
