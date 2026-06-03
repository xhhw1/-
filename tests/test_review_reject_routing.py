from fastapi.testclient import TestClient

from ai_visual_agent.main import app


def _create_packaging_project(client: TestClient) -> str:
    response = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": ["safe", "fun"],
                "value_proposition": "clear play proof",
                "core_product_definition": "interactive toy set",
            },
            "assets": [],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_rejecting_usp_review_regenerates_usps_and_waits_again() -> None:
    client = TestClient(app)
    project_id = _create_packaging_project(client)

    start = client.post(f"/api/workflows/{project_id}/start")
    assert start.status_code == 200
    assert start.json()["interrupts"][0]["value"]["type"] == "usp_review"

    rejected = client.post(
        f"/api/workflows/{project_id}/resume",
        json={
            "action": "reject",
            "reviewer": "tester",
            "comment": "core USP is not sharp enough",
            "requested_changes": ["make the play proof more specific"],
        },
    )

    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "waiting_review"
    assert body["interrupts"][0]["value"]["type"] == "usp_review"

    audit = client.get(f"/api/projects/{project_id}/audit").json()
    generate_usps_count = sum(1 for record in audit if record["stage"] == "generate_usps")
    assert generate_usps_count >= 2
    assert any(
        record["record_type"] == "human_review"
        and record["stage"] == "usp_review"
        and record["payload"]["action"] == "reject"
        for record in audit
    )

    detail = client.get(f"/api/projects/{project_id}/detail").json()
    assert detail["pending_review"]["type"] == "usp_review"
    assert detail["pending_review"]["payload"]["core"]


def test_rejecting_strategy_review_regenerates_strategy_and_waits_again() -> None:
    client = TestClient(app)
    project_id = _create_packaging_project(client)

    client.post(f"/api/workflows/{project_id}/start")
    strategy_review = client.post(
        f"/api/workflows/{project_id}/resume",
        json={"action": "approve", "reviewer": "tester", "comment": "usp ok"},
    )
    assert strategy_review.status_code == 200
    assert strategy_review.json()["interrupts"][0]["value"]["type"] == "strategy_review"

    rejected = client.post(
        f"/api/workflows/{project_id}/resume",
        json={
            "action": "reject",
            "reviewer": "tester",
            "comment": "front layout needs a clearer product hierarchy",
            "requested_changes": ["make the front layout more product-led"],
        },
    )

    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "waiting_review"
    assert body["interrupts"][0]["value"]["type"] == "strategy_review"

    audit = client.get(f"/api/projects/{project_id}/audit").json()
    strategy_count = sum(1 for record in audit if record["stage"] == "packaging_strategy")
    assert strategy_count >= 2
    assert any(
        record["record_type"] == "human_review"
        and record["stage"] == "strategy_review"
        and record["payload"]["action"] == "reject"
        for record in audit
    )

    detail = client.get(f"/api/projects/{project_id}/detail").json()
    assert detail["pending_review"]["type"] == "strategy_review"
    assert detail["pending_review"]["payload"]["front_layout"]
