from fastapi.testclient import TestClient

from ai_visual_agent.main import app


def test_workflow_uses_image_understanding_metadata() -> None:
    client = TestClient(app)
    project_response = client.post(
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
            "assets": [
                {
                    "id": "product-hero",
                    "kind": "product_image",
                    "filename": "hero.png",
                    "uri": "C:/tmp/hero.png",
                    "mime_type": "image/png",
                    "metadata": {
                        "image_analysis": {
                            "image_role": "product_image",
                            "semantic_summary": "Hero product image.",
                            "understanding": {
                                "summary": "Bright hero product on clean background.",
                                "product_appearance": ["round blue toy body"],
                                "visible_accessories": ["card accessories"],
                                "play_clues": ["stacking play motion"],
                            },
                        }
                    },
                },
                {
                    "id": "competitor-pack",
                    "kind": "competitor_packaging",
                    "filename": "competitor.png",
                    "uri": "C:/tmp/competitor.png",
                    "mime_type": "image/png",
                    "metadata": {
                        "image_analysis": {
                            "image_role": "competitor_packaging",
                            "semantic_summary": "Competitor pack reference.",
                            "understanding": {
                                "summary": "Competitor uses a large front hero image.",
                                "competitor_visual_hooks": ["large front hero with red callout"],
                                "risks": ["avoid copying red callout layout"],
                            },
                        }
                    },
                },
            ],
        },
    )
    project_id = project_response.json()["id"]

    start_response = client.post(f"/api/workflows/{project_id}/start")

    assert start_response.status_code == 200
    state = start_response.json()["state"]
    assert "round blue toy body" in state["parsed_product"]["visual_features"]
    assert "card accessories" in state["parsed_product"]["visual_features"]
    assert state["competitor_insights"]["competitors"][0]["visual_hooks"] == [
        "large front hero with red callout"
    ]

    audit = client.get(f"/api/projects/{project_id}/audit?record_type=agent_output").json()
    generate_usps = next(record for record in audit if record["stage"] == "generate_usps")
    assert generate_usps["payload"]["llm"]["backend"] == "mock"

    runs = client.get(f"/api/projects/{project_id}/audit?record_type=agent_run").json()
    marketer_run = next(record for record in runs if record["stage"] == "generate_usps")
    payload = marketer_run["payload"]
    assert payload["agent_name"] == "Marketer Agent"
    assert payload["prompt_version"].startswith("marketer@")
    assert payload["input_context"]["parsed_product"]["visual_features"]
    assert payload["output_summary"].startswith("core=")
