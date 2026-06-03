import base64
from pathlib import Path

from fastapi.testclient import TestClient

from ai_visual_agent.domain import AssetRef
from ai_visual_agent.main import app
from ai_visual_agent.services.segmentation import MockSegmentationProvider


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_mock_segmentation_provider_outputs_assets(tmp_path) -> None:
    image_path = tmp_path / "product.png"
    image_path.write_bytes(PNG_1X1)
    asset = AssetRef(kind="product_image", filename="product.png", uri=str(image_path), mime_type="image/png")

    result = MockSegmentationProvider().run(project_id="project-1", asset=asset)

    assert result.engine == "mock-sam2"
    assert result.mask_asset.kind == "mask_image"
    assert result.transparent_asset.kind == "transparent_product_image"
    assert Path(result.mask_asset.uri).exists()
    assert Path(result.transparent_asset.uri).exists()
    assert result.quality.needs_manual_trim is True


def test_segment_uploaded_image_asset() -> None:
    client = TestClient(app)
    project = client.post(
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
    ).json()

    upload = client.post(
        f"/api/projects/{project['id']}/assets",
        data={"kind": "product_image"},
        files={"file": ("product.png", PNG_1X1, "image/png")},
    ).json()

    response = client.post(f"/api/projects/{project['id']}/assets/{upload['id']}/segment")

    assert response.status_code == 200
    result = response.json()
    assert result["engine"] == "mock-sam2"
    assert result["mask_asset"]["kind"] == "mask_image"
    assert result["transparent_asset"]["kind"] == "transparent_product_image"

    loaded = client.get(f"/api/projects/{project['id']}").json()
    assert len(loaded["assets"]) == 3
    source_asset = next(asset for asset in loaded["assets"] if asset["id"] == upload["id"])
    assert source_asset["metadata"]["segmentation"]["transparent_asset"]["id"]

    audit = client.get(f"/api/projects/{project['id']}/audit?record_type=agent_output").json()
    assert any(record["stage"] == "segment_image_asset" for record in audit)
