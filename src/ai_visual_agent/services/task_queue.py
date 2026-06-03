from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import BoundedSemaphore, Thread
from typing import Any, Callable
from uuid import uuid4

from ai_visual_agent.config import get_settings
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        if status == "running":
            job.started_at = _now()
        if status in {"succeeded", "failed", "cancelled"}:
            job.finished_at = _now()
        if error:
            job.error = error
        self._jobs[job_id] = job

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
                        finished_at TEXT
                    )
                    """
                )
            )
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_background_jobs_owner ON background_jobs(owner_id)"))
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_background_jobs_project ON background_jobs(project_id)"))

    def create(self, job: BackgroundJob) -> BackgroundJob:
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO background_jobs (
                        id, kind, project_id, owner_id, status, payload_json, error,
                        created_at, started_at, finished_at
                    ) VALUES (
                        :id, :kind, :project_id, :owner_id, :status, :payload_json, :error,
                        :created_at, :started_at, :finished_at
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
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE background_jobs
                    SET status = :status,
                        error = CASE WHEN :error = '' THEN error ELSE :error END,
                        started_at = COALESCE(:started_at, started_at),
                        finished_at = COALESCE(:finished_at, finished_at)
                    WHERE id = :id
                    """
                ),
                patch,
            )

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
        Thread(target=self._run, kwargs={"job_id": job.id, "handler": handler, "kwargs": kwargs}, daemon=True).start()
        return job

    def _run(self, *, job_id: str, handler: Callable[..., None], kwargs: dict[str, Any]) -> None:
        with self._semaphore:
            self.store.mark(job_id, "running")
            try:
                handler(**kwargs)
            except Exception as exc:
                self.store.mark(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
                raise
            else:
                self.store.mark(job_id, "succeeded")


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
    )


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)

background_task_queue = BackgroundTaskQueue()
