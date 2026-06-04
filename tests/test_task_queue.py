from __future__ import annotations

import time
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_visual_agent.api.dependencies import require_current_user
from ai_visual_agent.domain import AuthUser
from ai_visual_agent.main import app
from ai_visual_agent.services.task_queue import (
    BackgroundJob,
    BackgroundTaskQueue,
    InMemoryBackgroundJobStore,
    SqlBackgroundJobStore,
    current_background_job_id,
    raise_if_current_job_cancelled,
    register_background_handler,
)
from ai_visual_agent.services.task_queue import background_task_queue


class FakeRedis:
    def __init__(self) -> None:
        self.items: list[str] = []

    def ping(self) -> bool:
        return True

    def rpush(self, _queue: str, job_id: str) -> int:
        self.items.append(job_id)
        return len(self.items)


def test_sql_background_job_store_recovers_interrupted_jobs(tmp_path) -> None:
    store = SqlBackgroundJobStore(f"sqlite:///{tmp_path / 'jobs.db'}")
    store.setup()
    queued = store.create(BackgroundJob(kind="agent_run", status="queued"))
    running = store.create(BackgroundJob(kind="agent_run", status="running"))
    done = store.create(BackgroundJob(kind="agent_run", status="succeeded"))

    recovered = store.recover_interrupted(reason="service restarted")

    assert recovered == 2
    assert store.get(queued.id).status == "failed"
    assert store.get(running.id).status == "failed"
    assert store.get(queued.id).error == "service restarted"
    assert store.get(running.id).finished_at is not None
    assert store.get(running.id).heartbeat_at is not None
    assert store.get(done.id).status == "succeeded"


def test_memory_background_job_store_cancel_marks_terminal_state() -> None:
    store = InMemoryBackgroundJobStore()
    job = store.create(BackgroundJob(kind="agent_run", status="running"))

    cancelled = store.cancel(job.id, reason="user cancelled")

    assert cancelled.status == "cancelled"
    assert cancelled.error == "user cancelled"
    assert cancelled.finished_at is not None
    assert cancelled.heartbeat_at == cancelled.finished_at


def test_running_handler_can_observe_current_job_cancellation() -> None:
    queue = BackgroundTaskQueue()
    queue.store = InMemoryBackgroundJobStore()
    job = queue.store.create(BackgroundJob(kind="agent_run"))
    observed_job_ids: list[str] = []

    def handler() -> None:
        observed_job_ids.append(current_background_job_id())
        queue.cancel(observed_job_ids[0], reason="cancelled_by_user")
        raise_if_current_job_cancelled()

    queue._execute(job_id=job.id, handler=handler, kwargs={})

    assert observed_job_ids == [job.id]
    assert queue.store.get(job.id).status == "cancelled"
    assert queue.store.get(job.id).error == "cancelled_by_user"


def test_cancel_background_task_api_for_owned_project() -> None:
    owner = f"task-test-user-{uuid4()}"
    app.dependency_overrides[require_current_user] = lambda: AuthUser(
        id=owner,
        email=f"{owner}@example.com",
        role="admin",
    )
    client = TestClient(app)
    project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": [],
                "value_proposition": "",
                "core_product_definition": "toy",
            },
            "assets": [],
        },
    ).json()
    job = background_task_queue.store.create(
        BackgroundJob(
            kind="agent_run",
            project_id=project["id"],
            owner_id=owner,
            status="queued",
        )
    )

    response = client.post(f"/api/tasks/{job.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["error"] == "cancelled_by_user"
    assert background_task_queue.store.get(job.id).status == "cancelled"


def test_retry_background_task_api_creates_new_owned_job() -> None:
    owner = f"task-retry-user-{uuid4()}"
    app.dependency_overrides[require_current_user] = lambda: AuthUser(
        id=owner,
        email=f"{owner}@example.com",
        role="admin",
    )
    client = TestClient(app)
    project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": [],
                "value_proposition": "",
                "core_product_definition": "toy",
            },
            "assets": [],
        },
    ).json()
    calls = []
    kind = f"retry_test_job_{uuid4()}"

    def handler(value: str) -> None:
        calls.append(value)

    register_background_handler(kind, handler)
    failed = background_task_queue.store.create(
        BackgroundJob(
            kind=kind,
            project_id=project["id"],
            owner_id=owner,
            status="failed",
            error="previous failure",
            payload={"value": "retry-payload"},
        )
    )

    try:
        response = client.post(f"/api/tasks/{failed.id}/retry")

        assert response.status_code == 200
        retried = response.json()
        assert retried["id"] != failed.id
        assert retried["kind"] == kind
        for _ in range(20):
            if background_task_queue.store.get(retried["id"]).status == "succeeded":
                break
            time.sleep(0.02)
        assert calls == ["retry-payload"]
        assert background_task_queue.store.get(failed.id).status == "failed"
        assert background_task_queue.store.get(retried["id"]).status == "succeeded"
    finally:
        app.dependency_overrides.pop(require_current_user, None)


def test_redis_background_queue_submit_enqueues_without_running() -> None:
    queue = BackgroundTaskQueue()
    queue.backend = "redis"
    fake_redis = FakeRedis()
    queue._redis_client = fake_redis
    called = []

    job = queue.submit(
        kind="redis_test_job",
        handler=lambda value: called.append(value),
        kwargs={"value": "should-not-run-in-api"},
        owner_id="owner",
        project_id="project",
    )

    assert fake_redis.items == [job.id]
    assert called == []
    assert queue.store.get(job.id).status == "queued"
    assert queue.store.get(job.id).payload == {"value": "should-not-run-in-api"}


def test_redis_worker_runs_registered_handler_from_payload() -> None:
    queue = BackgroundTaskQueue()
    called = []

    def handler(value: str) -> None:
        called.append(value)

    register_background_handler("registered_test_job", handler)
    job = queue.store.create(
        BackgroundJob(
            kind="registered_test_job",
            project_id="project",
            owner_id="owner",
            payload={"value": "from-db-payload"},
        )
    )

    queue.run_registered_job(job.id)

    assert called == ["from-db-payload"]
    assert queue.store.get(job.id).status == "succeeded"
