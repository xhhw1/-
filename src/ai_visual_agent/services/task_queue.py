from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import socket
from threading import BoundedSemaphore, Thread, local
from typing import Any, Callable
from uuid import uuid4

from ai_visual_agent.config import get_settings
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_BACKGROUND_HANDLERS: dict[str, Callable[..., None]] = {}
_CURRENT_JOB = local()


class TaskCancelledError(RuntimeError):
    """Raised inside a worker when the current background job has been cancelled."""


def current_background_job_id() -> str:
    return str(getattr(_CURRENT_JOB, "job_id", "") or "")


def is_current_background_job_cancelled() -> bool:
    job_id = current_background_job_id()
    if not job_id:
        return False
    try:
        queue = getattr(_CURRENT_JOB, "queue", None) or background_task_queue
        return queue.store.get(job_id).status == "cancelled"
    except Exception:
        return False


def raise_if_current_job_cancelled() -> None:
    if is_current_background_job_cancelled():
        raise TaskCancelledError("cancelled_by_user")


def register_background_handler(kind: str, handler: Callable[..., None]) -> None:
    _BACKGROUND_HANDLERS[kind] = handler


@dataclass
class BackgroundJob:
    id: str = field(default_factory=lambda: str(uuid4()))
    kind: str = ""
    project_id: str = ""
    owner_id: str = ""
    status: str = "queued"
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None


class InMemoryBackgroundJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, BackgroundJob] = {}

    def setup(self) -> None:
        return None

    def create(self, job: BackgroundJob) -> BackgroundJob:
        self._jobs[job.id] = job
        return job

    def mark(self, job_id: str, status: str, *, error: str = "") -> None:
        job = self._jobs[job_id]
        job.status = status
        job.heartbeat_at = _now()
        if status == "running":
            job.started_at = _now()
        if status in {"succeeded", "failed", "cancelled"}:
            job.finished_at = _now()
        if error:
            job.error = error
        self._jobs[job_id] = job

    def get(self, job_id: str) -> BackgroundJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"Background job not found: {job_id}") from exc

    def heartbeat(self, job_id: str) -> None:
        job = self.get(job_id)
        job.heartbeat_at = _now()
        self._jobs[job_id] = job

    def cancel(self, job_id: str, *, reason: str = "cancelled") -> BackgroundJob:
        job = self.get(job_id)
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job
        job.status = "cancelled"
        job.error = reason
        job.finished_at = _now()
        job.heartbeat_at = job.finished_at
        self._jobs[job_id] = job
        return job

    def recover_interrupted(self, *, reason: str) -> int:
        count = 0
        for job in self._jobs.values():
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.error = reason
                job.finished_at = _now()
                job.heartbeat_at = job.finished_at
                count += 1
        return count

    def list(self, *, owner_id: str | None = None, project_id: str | None = None) -> list[BackgroundJob]:
        jobs = list(self._jobs.values())
        if owner_id:
            jobs = [job for job in jobs if job.owner_id == owner_id]
        if project_id:
            jobs = [job for job in jobs if job.project_id == project_id]
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)


class SqlBackgroundJobStore:
    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import Engine
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("SQLAlchemy is required for SqlBackgroundJobStore.") from exc

        if database_url.startswith("sqlite:///"):
            db_path = database_url.removeprefix("sqlite:///")
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._text = text
        self.engine: Engine = create_engine(database_url, future=True)

    def setup(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    CREATE TABLE IF NOT EXISTS background_jobs (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        error TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        heartbeat_at TEXT
                    )
                    """
                )
            )
            self._ensure_column(conn, "background_jobs", "heartbeat_at", "TEXT")
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_background_jobs_owner ON background_jobs(owner_id)"))
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_background_jobs_project ON background_jobs(project_id)"))

    def create(self, job: BackgroundJob) -> BackgroundJob:
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO background_jobs (
                        id, kind, project_id, owner_id, status, payload_json, error,
                        created_at, started_at, finished_at, heartbeat_at
                    ) VALUES (
                        :id, :kind, :project_id, :owner_id, :status, :payload_json, :error,
                        :created_at, :started_at, :finished_at, :heartbeat_at
                    )
                    """
                ),
                _params(job),
            )
        return job

    def mark(self, job_id: str, status: str, *, error: str = "") -> None:
        patch: dict[str, Any] = {"id": job_id, "status": status, "error": error}
        patch["started_at"] = _now() if status == "running" else None
        patch["finished_at"] = _now() if status in {"succeeded", "failed", "cancelled"} else None
        patch["heartbeat_at"] = _now()
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE background_jobs
                    SET status = :status,
                        error = CASE WHEN :error = '' THEN error ELSE :error END,
                        started_at = COALESCE(:started_at, started_at),
                        finished_at = COALESCE(:finished_at, finished_at),
                        heartbeat_at = :heartbeat_at
                    WHERE id = :id
                    """
                ),
                patch,
            )

    def get(self, job_id: str) -> BackgroundJob:
        with self.engine.begin() as conn:
            row = conn.execute(
                self._text("SELECT * FROM background_jobs WHERE id = :id"),
                {"id": job_id},
            ).mappings().first()
        if row is None:
            raise KeyError(f"Background job not found: {job_id}")
        return _job_from_row(row)

    def heartbeat(self, job_id: str) -> None:
        with self.engine.begin() as conn:
            result = conn.execute(
                self._text("UPDATE background_jobs SET heartbeat_at = :heartbeat_at WHERE id = :id"),
                {"id": job_id, "heartbeat_at": _now()},
            )
        if result.rowcount == 0:
            raise KeyError(f"Background job not found: {job_id}")

    def cancel(self, job_id: str, *, reason: str = "cancelled") -> BackgroundJob:
        now = _now()
        with self.engine.begin() as conn:
            row = conn.execute(
                self._text("SELECT * FROM background_jobs WHERE id = :id"),
                {"id": job_id},
            ).mappings().first()
            if row is None:
                raise KeyError(f"Background job not found: {job_id}")
            if row["status"] not in {"succeeded", "failed", "cancelled"}:
                conn.execute(
                    self._text(
                        """
                        UPDATE background_jobs
                        SET status = 'cancelled',
                            error = :reason,
                            finished_at = :finished_at,
                            heartbeat_at = :heartbeat_at
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": job_id,
                        "reason": reason,
                        "finished_at": now,
                        "heartbeat_at": now,
                    },
                )
        return self.get(job_id)

    def recover_interrupted(self, *, reason: str) -> int:
        now = _now()
        with self.engine.begin() as conn:
            result = conn.execute(
                self._text(
                    """
                    UPDATE background_jobs
                    SET status = 'failed',
                        error = :reason,
                        finished_at = :finished_at,
                        heartbeat_at = :heartbeat_at
                    WHERE status IN ('queued', 'running')
                    """
                ),
                {"reason": reason, "finished_at": now, "heartbeat_at": now},
            )
        return int(result.rowcount or 0)

    def list(self, *, owner_id: str | None = None, project_id: str | None = None) -> list[BackgroundJob]:
        where = []
        params: dict[str, str] = {}
        if owner_id:
            where.append("owner_id = :owner_id")
            params["owner_id"] = owner_id
        if project_id:
            where.append("project_id = :project_id")
            params["project_id"] = project_id
        sql = "SELECT * FROM background_jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"
        with self.engine.begin() as conn:
            rows = conn.execute(self._text(sql), params).mappings().all()
        return [_job_from_row(row) for row in rows]

    def _ensure_column(self, conn, table: str, column: str, definition: str) -> None:
        if self.engine.dialect.name == "postgresql":
            conn.execute(
                self._text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
            )
            return
        try:
            conn.execute(self._text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
        except Exception:
            return


class BackgroundTaskQueue:
    def __init__(self) -> None:
        settings = get_settings()
        if project_store_uses_sql(settings):
            self.store: InMemoryBackgroundJobStore | SqlBackgroundJobStore = SqlBackgroundJobStore(
                resolved_project_database_url(settings)
            )
        else:
            self.store = InMemoryBackgroundJobStore()
        self.store.setup()
        self._semaphore = BoundedSemaphore(max(1, settings.background_worker_concurrency))
        self.backend = settings.task_queue_backend.lower()
        self.redis_queue_name = settings.task_queue_redis_queue_name
        self._redis_client: Any | None = None

    def recover_interrupted_jobs(self) -> int:
        settings = get_settings()
        if not settings.background_job_recovery_enabled:
            return 0
        if self.backend != "thread":
            return 0
        return self.store.recover_interrupted(
            reason="Interrupted by service restart; please retry this task."
        )

    def cancel(self, job_id: str, *, reason: str = "cancelled") -> BackgroundJob:
        return self.store.cancel(job_id, reason=reason)

    def retry(self, job_id: str) -> BackgroundJob:
        job = self.store.get(job_id)
        if job.status not in {"failed", "cancelled"}:
            raise ValueError(f"Only failed or cancelled jobs can be retried; current status is {job.status}.")
        handler = _BACKGROUND_HANDLERS.get(job.kind)
        if self.backend == "thread" and handler is None:
            raise RuntimeError(f"No handler registered for background job kind: {job.kind}")
        return self.submit(
            kind=job.kind,
            handler=handler or _missing_registered_handler,
            kwargs=job.payload,
            owner_id=job.owner_id,
            project_id=job.project_id,
        )

    def submit(
        self,
        *,
        kind: str,
        handler: Callable[..., None],
        kwargs: dict[str, Any],
        owner_id: str = "",
        project_id: str = "",
    ) -> BackgroundJob:
        job = self.store.create(
            BackgroundJob(
                kind=kind,
                project_id=project_id,
                owner_id=owner_id,
                payload={key: _jsonable(value) for key, value in kwargs.items()},
            )
        )
        if self.backend == "redis":
            try:
                self._enqueue_redis(job.id)
            except Exception as exc:
                self.store.mark(job.id, "failed", error=f"{type(exc).__name__}: {exc}")
                raise
            return job
        Thread(target=self._run, kwargs={"job_id": job.id, "handler": handler, "kwargs": kwargs}, daemon=True).start()
        return job

    def _run(self, *, job_id: str, handler: Callable[..., None], kwargs: dict[str, Any]) -> None:
        with self._semaphore:
            self._execute(job_id=job_id, handler=handler, kwargs=kwargs)

    def _execute(self, *, job_id: str, handler: Callable[..., None], kwargs: dict[str, Any]) -> None:
        if self.store.get(job_id).status == "cancelled":
            return
        _CURRENT_JOB.job_id = job_id
        _CURRENT_JOB.queue = self
        self.store.mark(job_id, "running")
        try:
            self.store.heartbeat(job_id)
            handler(**kwargs)
        except TaskCancelledError as exc:
            self.store.cancel(job_id, reason=str(exc) or "cancelled")
            return
        except Exception as exc:
            if self.store.get(job_id).status == "cancelled":
                return
            self.store.mark(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            if self.store.get(job_id).status != "cancelled":
                self.store.mark(job_id, "succeeded")
        finally:
            if getattr(_CURRENT_JOB, "job_id", "") == job_id:
                _CURRENT_JOB.job_id = ""
                _CURRENT_JOB.queue = None

    def run_registered_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        handler = _BACKGROUND_HANDLERS.get(job.kind)
        if handler is None:
            self.store.mark(job_id, "failed", error=f"No handler registered for background job kind: {job.kind}")
            return
        self._execute(job_id=job_id, handler=handler, kwargs=job.payload)

    def run_worker_forever(self, *, poll_timeout_seconds: int = 5) -> None:
        if self.backend != "redis":
            raise RuntimeError("Redis worker requires TASK_QUEUE_BACKEND=redis.")
        self.requeue_redis_jobs()
        client = self._redis()
        instance_id = _worker_instance_id()
        while True:
            self.record_worker_heartbeat(instance_id=instance_id)
            item = client.blpop(self.redis_queue_name, timeout=poll_timeout_seconds)
            if not item:
                continue
            self.record_worker_heartbeat(instance_id=instance_id)
            _queue_name, raw_job_id = item
            job_id = raw_job_id.decode("utf-8") if isinstance(raw_job_id, bytes) else str(raw_job_id)
            self._semaphore.acquire()
            Thread(target=self._run_registered_job_with_permit, kwargs={"job_id": job_id}, daemon=True).start()

    def record_worker_heartbeat(self, *, instance_id: str | None = None) -> str | None:
        if self.backend != "redis":
            return None
        settings = get_settings()
        instance_id = instance_id or _worker_instance_id()
        key = f"{settings.worker_heartbeat_key_prefix}:{instance_id}"
        payload = {
            "instance_id": instance_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "queue": self.redis_queue_name,
            "concurrency": settings.background_worker_concurrency,
            "heartbeat_at": _now(),
        }
        self._redis().set(
            key,
            json.dumps(payload, ensure_ascii=False),
            ex=max(5, int(settings.worker_heartbeat_ttl_seconds)),
        )
        return key

    def _run_registered_job_with_permit(self, *, job_id: str) -> None:
        try:
            self.run_registered_job(job_id)
        finally:
            self._semaphore.release()

    def requeue_redis_jobs(self) -> int:
        if self.backend != "redis":
            return 0
        count = 0
        client = self._redis()
        for job in self.store.list():
            if job.status == "running":
                self.store.mark(
                    job.id,
                    "failed",
                    error="Interrupted while running in Redis worker; please retry this task.",
                )
                continue
            if job.status == "queued":
                client.rpush(self.redis_queue_name, job.id)
                count += 1
        return count

    def _enqueue_redis(self, job_id: str) -> None:
        self._redis().rpush(self.redis_queue_name, job_id)

    def _redis(self) -> Any:
        if self._redis_client is not None:
            return self._redis_client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("Redis task queue requires the redis package.") from exc
        self._redis_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        self._redis_client.ping()
        return self._redis_client


def _params(job: BackgroundJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "project_id": job.project_id,
        "owner_id": job.owner_id,
        "status": job.status,
        "payload_json": json.dumps(job.payload, ensure_ascii=False, default=str),
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "heartbeat_at": job.heartbeat_at,
    }


def _job_from_row(row: Any) -> BackgroundJob:
    return BackgroundJob(
        id=row["id"],
        kind=row["kind"],
        project_id=row["project_id"],
        owner_id=row["owner_id"],
        status=row["status"],
        payload=json.loads(row["payload_json"] or "{}"),
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        heartbeat_at=row.get("heartbeat_at") if hasattr(row, "get") else row["heartbeat_at"],
    )


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _missing_registered_handler(**_kwargs: Any) -> None:
    raise RuntimeError("No handler registered for this background job kind.")


def _worker_instance_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def list_worker_heartbeats(settings: Any | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    try:
        import redis
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError("redis package is required to inspect worker heartbeats.") from exc

    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    client.ping()
    pattern = f"{settings.worker_heartbeat_key_prefix}:*"
    heartbeats: list[dict[str, Any]] = []
    for key in client.scan_iter(match=pattern, count=100):
        raw = client.get(key)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        payload["key"] = key
        heartbeats.append(payload)
    return heartbeats


background_task_queue = BackgroundTaskQueue()
