import base64

from fastapi.testclient import TestClient

from ai_visual_agent.domain import AssetRef
from ai_visual_agent.main import app
from ai_visual_agent.services.image_analysis import classify_image_role, is_image_asset
from ai_visual_agent.services.memory_store import get_memory_store
from ai_visual_agent.services.ocr import MockOCRProvider, get_ocr_provider


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_classify_image_asset() -> None:
    assert classify_image_role(
        AssetRef(kind="product_image", filename="hero.png", uri="C:/tmp/hero.png", mime_type="image/png")
    ) == "product_image"
    assert classify_image_role(
        AssetRef(kind="other", filename="brand_logo.png", uri="C:/tmp/logo.png", mime_type="image/png")
    ) == "logo"


def test_analyze_uploaded_image_asset(tmp_path) -> None:
    image_path = tmp_path / "hero.png"
    image_path.write_bytes(PNG_1X1)

    client = TestClient(app)
    project_response = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": ["safe"],
                "value_proposition": "fun",
                "core_product_definition": "toy set",
            },
            "assets": [],
        },
    )
    project_id = project_response.json()["id"]

    with image_path.open("rb") as handle:
        upload_response = client.post(
            f"/api/projects/{project_id}/assets",
            data={"kind": "product_image"},
            files={"file": ("hero.png", handle, "image/png")},
        )

    asset = upload_response.json()
    assert is_image_asset(AssetRef(**asset))

    analyze_response = client.post(f"/api/projects/{project_id}/assets/{asset['id']}/analyze")
    assert analyze_response.status_code == 200
    analysis = analyze_response.json()
    assert analysis["image_role"] == "product_image"
    assert analysis["ocr"]["engine"] == "mock-paddleocr"
    assert analysis["understanding"]["engine"] == "mock-vlm"
    assert analysis["semantic_summary"]

    project = client.get(f"/api/projects/{project_id}").json()
    assert project["assets"][0]["metadata"]["image_analysis"]["image_role"] == "product_image"

    audit = client.get(f"/api/projects/{project_id}/audit?record_type=agent_output").json()
    assert any(record["stage"] == "analyze_image_asset" for record in audit)


def test_image_ocr_text_is_saved_to_memory(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "packaging.png"
    image_path.write_bytes(PNG_1X1)
    get_memory_store.cache_clear()
    get_ocr_provider.cache_clear()
    monkeypatch.setattr(
        "ai_visual_agent.tools.vision_tools.get_ocr_provider",
        lambda: MockOCRProvider(mock_text="安全材质 LOGO"),
    )

    client = TestClient(app)
    project_response = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": ["safe"],
                "value_proposition": "fun",
                "core_product_definition": "toy set",
            },
            "assets": [],
        },
    )
    project_id = project_response.json()["id"]

    with image_path.open("rb") as handle:
        upload_response = client.post(
            f"/api/projects/{project_id}/assets",
            data={"kind": "competitor_packaging"},
            files={"file": ("packaging.png", handle, "image/png")},
        )

    asset = upload_response.json()
    analysis = client.post(f"/api/projects/{project_id}/assets/{asset['id']}/analyze").json()
    assert analysis["understanding"]["competitor_visual_hooks"]
    assert analysis["ocr"]["full_text"] == "安全材质 LOGO"

    memory_results = client.post(
        "/api/memory/search",
        json={
            "query": "安全 LOGO",
            "project_id": project_id,
            "memory_type": "product_doc",
            "limit": 5,
        },
    ).json()
    assert memory_results
    assert "LOGO" in memory_results[0]["text"]
