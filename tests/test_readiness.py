from types import SimpleNamespace

from fastapi.testclient import TestClient

from ai_visual_agent.domain import ReadinessCheck, ReadinessReport
from ai_visual_agent.main import app
from ai_visual_agent.services.readiness import build_readiness_report


def _settings(**overrides):
    defaults = {
        "app_env": "test",
        "project_store_backend": "memory",
        "graph_checkpoint_backend": "memory",
        "database_url": "",
        "local_database_url": "",
        "task_queue_backend": "thread",
        "task_queue_redis_queue_name": "ai_visual_agent:jobs",
        "rate_limit_backend": "memory",
        "redis_url": "redis://localhost:6379/0",
        "storage_backend": "local",
        "s3_endpoint_url": "http://localhost:9000",
        "s3_bucket": "vision-agent",
        "s3_access_key": None,
        "s3_secret_key": None,
        "mock_external_tools": True,
        "qdrant_url": "",
        "qdrant_collection": "ai_visual_agent_memory",
        "worker_heartbeat_key_prefix": "ai_visual_agent:workers",
        "worker_heartbeat_ttl_seconds": 30,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_readiness_skips_optional_local_dependencies() -> None:
    report = build_readiness_report(_settings())

    assert report.status == "ready"
    assert report.failures == []
    assert {check.name: check.status for check in report.checks} == {
        "database": "skipped",
        "redis": "skipped",
        "worker": "skipped",
        "object_storage": "skipped",
        "vector_memory": "skipped",
    }


def test_readiness_fails_when_minio_credentials_are_missing() -> None:
    report = build_readiness_report(
        _settings(storage_backend="minio", s3_access_key="", s3_secret_key="")
    )

    assert report.status == "not_ready"
    object_storage = next(check for check in report.checks if check.name == "object_storage")
    assert object_storage.status == "failed"
    assert "S3_ACCESS_KEY" in object_storage.message


def test_readiness_endpoint_uses_503_for_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_visual_agent.main.build_readiness_report",
        lambda _settings: ReadinessReport(
            status="not_ready",
            checks=[
                ReadinessCheck(
                    name="worker",
                    backend="redis",
                    status="failed",
                    required=True,
                    message="No active worker heartbeat.",
                )
            ],
            failures=["No active worker heartbeat."],
        ),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
