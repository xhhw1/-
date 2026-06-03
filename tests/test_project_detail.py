from fastapi.testclient import TestClient

from ai_visual_agent.main import app


def _create_project(client: TestClient) -> str:
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


def test_project_detail_reports_pending_usp_review() -> None:
    client = TestClient(app)
    project_id = _create_project(client)

    start = client.post(f"/api/workflows/{project_id}/start")
    assert start.status_code == 200

    detail = client.get(f"/api/projects/{project_id}/detail")

    assert detail.status_code == 200
    body = detail.json()
    assert body["project"]["id"] == project_id
    assert body["project"]["status"] == "waiting_review"
    assert body["pending_review"]["type"] == "usp_review"
    assert body["pending_review"]["payload"]["core"]
    assert "generate_usps" in body["latest_agent_outputs"]
    assert body["progress"]["human_review_count"] == 0
    assert body["progress"]["audit_count"] >= 3


def test_project_detail_reports_final_design_review_outputs() -> None:
    client = TestClient(app)
    project_id = _create_project(client)

    client.post(f"/api/workflows/{project_id}/start")
    client.post(
        f"/api/workflows/{project_id}/resume",
        json={"action": "approve", "reviewer": "tester", "comment": "usp ok"},
    )
    client.post(
        f"/api/workflows/{project_id}/resume",
        json={"action": "approve", "reviewer": "tester", "comment": "strategy ok"},
    )

    detail = client.get(f"/api/projects/{project_id}/detail")

    assert detail.status_code == 200
    body = detail.json()
    assert body["pending_review"]["type"] == "final_design_review"
    assert body["latest_generated_outputs"]["items"]
    assert len(body["latest_generated_outputs"]["items"]) == 4
    assert body["latest_qc_report"]["passed"] is True
    assert body["progress"]["human_review_count"] == 2
