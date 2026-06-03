from fastapi.testclient import TestClient

from ai_visual_agent.main import app
from ai_visual_agent.services.storage import asset_storage


def test_structured_usp_and_strategy_edits_drive_downstream_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = TestClient(app)
    project = client.post(
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
    ).json()

    client.post(f"/api/workflows/{project['id']}/start")
    edited_usps = {
        "core": [
            {
                "title": "Edited hero play proof",
                "description": "Reviewer-approved core selling point.",
                "aligned_expectations": ["fun"],
                "product_evidence": ["manual review"],
                "competitor_comparison": "clearer than competitor",
                "confidence": 0.91,
            }
        ],
        "secondary": [],
        "notes": ["edited in review console"],
    }
    strategy_response = client.post(
        f"/api/workflows/{project['id']}/resume",
        json={
            "action": "edit",
            "reviewer": "tester",
            "comment": "use edited USP",
            "selected_usps": edited_usps,
        },
    )

    assert strategy_response.status_code == 200
    strategy_state = strategy_response.json()["state"]
    assert strategy_state["selected_usps"]["core"][0]["title"] == "Edited hero play proof"
    assert strategy_state["packaging_strategy"]["required_copy"] == ["Edited hero play proof"]

    edited_strategy = strategy_state["packaging_strategy"]
    edited_strategy["front_layout"] = "CUSTOM REVIEW FRONT LAYOUT"
    final_response = client.post(
        f"/api/workflows/{project['id']}/resume",
        json={
            "action": "edit",
            "reviewer": "tester",
            "comment": "use edited strategy",
            "packaging_strategy": edited_strategy,
        },
    )

    assert final_response.status_code == 200
    final_state = final_response.json()["state"]
    front = next(item for item in final_state["generated_outputs"]["items"] if item["name"] == "front")
    assert front["prompt"] == "CUSTOM REVIEW FRONT LAYOUT"
    assert final_state["packaging_strategy"]["front_layout"] == "CUSTOM REVIEW FRONT LAYOUT"
