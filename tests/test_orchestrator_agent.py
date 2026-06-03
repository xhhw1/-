from fastapi.testclient import TestClient

from ai_visual_agent.main import app


def _create_project(client: TestClient, *, with_required_assets: bool = True) -> str:
    assets = []
    if with_required_assets:
        assets = [
            {
                "kind": "product_ppt",
                "filename": "product.pptx",
                "uri": "data/test/product.pptx",
                "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "metadata": {},
            },
            {
                "kind": "product_image",
                "filename": "product.png",
                "uri": "data/test/product.png",
                "mime_type": "image/png",
                "metadata": {},
            },
        ]
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
            "assets": assets,
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_agent_chat_requires_packaging_materials_before_start() -> None:
    client = TestClient(app)
    project_id = _create_project(client, with_required_assets=False)

    response = client.post(
        f"/api/projects/{project_id}/agent/chat",
        json={"message": "start", "reviewer": "tester"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "materials_required"
    assert body["project_detail"]["project"]["status"] == "created"
    assert body["workflow_result"] is None
    assert body["messages"][-1]["payload"]["workflow_requirements"]["missing"]


def test_agent_chat_starts_workflow_and_surfaces_review_gate() -> None:
    client = TestClient(app)
    project_id = _create_project(client)

    response = client.post(
        f"/api/projects/{project_id}/agent/chat",
        json={"message": "start", "reviewer": "tester"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "workflow_started"
    assert body["project_detail"]["project"]["status"] == "waiting_review"
    assert body["project_detail"]["pending_review"]["type"] == "usp_review"
    assert [message["role"] for message in body["messages"]][-3:] == ["user", "tool", "agent"]
    assert body["messages"][-1]["message_type"] == "review_gate"


def test_agent_chat_approves_pending_review() -> None:
    client = TestClient(app)
    project_id = _create_project(client)
    client.post(f"/api/projects/{project_id}/agent/chat", json={"message": "start", "reviewer": "tester"})

    response = client.post(
        f"/api/projects/{project_id}/agent/chat",
        json={"message": "approve and continue", "reviewer": "tester"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "review_approved"
    assert body["project_detail"]["project"]["status"] == "waiting_review"
    assert body["project_detail"]["pending_review"]["type"] == "strategy_review"
    assert body["workflow_result"]["interrupts"][0]["value"]["type"] == "strategy_review"


def test_agent_chat_requests_manual_edit_without_resuming() -> None:
    client = TestClient(app)
    project_id = _create_project(client)
    client.post(f"/api/projects/{project_id}/agent/chat", json={"message": "start", "reviewer": "tester"})

    response = client.post(
        f"/api/projects/{project_id}/agent/chat",
        json={"message": "edit the core selling point to emphasize play", "reviewer": "tester"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "manual_edit_needed"
    assert body["workflow_result"] is None
    assert body["project_detail"]["pending_review"]["type"] == "usp_review"


def test_agent_messages_endpoint_returns_conversation() -> None:
    client = TestClient(app)
    project_id = _create_project(client, with_required_assets=False)
    client.post(f"/api/projects/{project_id}/agent/chat", json={"message": "project status", "reviewer": "tester"})

    response = client.get(f"/api/projects/{project_id}/agent/messages")

    assert response.status_code == 200
    messages = response.json()
    assert messages[0]["role"] == "user"
    assert messages[-1]["role"] == "agent"
