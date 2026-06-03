from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from ai_visual_agent.api.dependencies import require_current_user
from ai_visual_agent.domain import AuthUser
from ai_visual_agent.main import app
from ai_visual_agent.services.task_queue import BackgroundJob, InMemoryBackgroundJobStore, SqlBackgroundJobStore
from ai_visual_agent.services.task_queue import background_task_queue


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
