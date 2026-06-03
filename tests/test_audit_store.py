import importlib.util

import pytest

from ai_visual_agent.services.audit_store import SqlAuditStore


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlalchemy") is None,
    reason="SQLAlchemy optional dependency is not installed",
)


def test_sql_audit_store_records_and_filters(tmp_path) -> None:
    store = SqlAuditStore(f"sqlite:///{tmp_path / 'audit.db'}")
    store.setup()

    store.record(
        project_id="project-1",
        record_type="human_review",
        stage="usp_review",
        payload={"action": "approve", "reviewer": "tester"},
    )
    store.record(
        project_id="project-1",
        record_type="agent_output",
        stage="generate_usps",
        payload={"items": 1},
    )
    store.record(
        project_id="project-1",
        record_type="agent_run",
        stage="generate_usps",
        payload={"prompt_version": "marketer@abc123", "fallback_used": True},
    )

    all_records = store.list_records("project-1")
    review_records = store.list_records("project-1", record_type="human_review")
    agent_run_records = store.list_records("project-1", record_type="agent_run")

    assert len(all_records) == 3
    assert len(review_records) == 1
    assert len(agent_run_records) == 1
    assert review_records[0].payload["action"] == "approve"
    assert agent_run_records[0].payload["prompt_version"] == "marketer@abc123"
