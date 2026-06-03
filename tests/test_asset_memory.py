import base64

from fastapi.testclient import TestClient

from ai_visual_agent.graph.nodes import _product_reference_asset_ids
from ai_visual_agent.main import app
from ai_visual_agent.services.asset_memory import project_file_memory_context
from ai_visual_agent.services.memory_store import get_memory_store
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.storage import asset_storage


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_upload_registers_asset_registry_memory(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    get_memory_store.cache_clear()
    client = TestClient(app)

    project_response = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": ["safe"],
                "value_proposition": "clear play proof",
                "core_product_definition": "rocket toy set",
            },
            "assets": [],
        },
    )
    project_id = project_response.json()["id"]

    upload_response = client.post(
        f"/api/projects/{project_id}/assets",
        data={"kind": "product_image"},
        files={"file": ("product.png", PNG_1X1, "image/png")},
    )

    assert upload_response.status_code == 200
    uploaded = upload_response.json()
    asset_memory = uploaded["metadata"]["asset_memory"]
    assert asset_memory["source_type"] == "asset_registry"
    assert asset_memory["role"] == "product_reference_image"
    assert "image_edit_reference" in asset_memory["agent_tool_hints"]

    results = get_memory_store().search(
        "product reference image edit",
        project_id=project_id,
        memory_type="asset_registry",
        source_type="asset_registry",
    )
    assert any(result.payload["asset_id"] == uploaded["id"] for result in results)

    project = project_store.get(project_id)
    context = project_file_memory_context(project)
    assert context[0]["asset_id"] == uploaded["id"]
    assert context[0]["candidate_reference"] is True


def test_product_reference_selection_prefers_manual_and_cutout() -> None:
    state = {
        "file_memory_context": [
            {"asset_id": "plain", "candidate_reference": True},
            {"asset_id": "manual", "candidate_reference": True, "preferred_product_reference": True},
        ],
        "assets": [
            {
                "id": "plain",
                "kind": "product_image",
                "metadata": {"asset_memory": {"candidate_reference": True}},
            },
            {
                "id": "cutout",
                "kind": "transparent_product_image",
                "metadata": {"asset_memory": {"candidate_reference": True}},
            },
            {
                "id": "logo",
                "kind": "logo",
                "metadata": {"asset_memory": {"candidate_reference": False}},
            },
            {
                "id": "manual",
                "kind": "product_image",
                "metadata": {
                    "preferred_product_reference": True,
                    "asset_memory": {"candidate_reference": True},
                },
            },
        ],
    }

    assert _product_reference_asset_ids(state) == ["manual", "cutout", "plain"]
