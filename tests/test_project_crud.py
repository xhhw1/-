import base64

from fastapi.testclient import TestClient

from ai_visual_agent.api.dependencies import require_current_user
from ai_visual_agent.domain import AuthUser
from ai_visual_agent.main import app
from ai_visual_agent.services.storage import asset_storage


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _client() -> TestClient:
    app.dependency_overrides[require_current_user] = lambda: AuthUser(
        id="test-user",
        email="test@example.com",
        role="admin",
    )
    return TestClient(app)


def test_project_update_asset_crud_and_delete(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()

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
    assert project_response.status_code == 200
    project_id = project_response.json()["id"]

    update_response = client.patch(
        f"/api/projects/{project_id}",
        json={
            "workflow_type": "detail_page",
            "brief": {
                "category": "toy updated",
                "target_user": "gift buyer",
                "user_expectations": ["clear"],
                "value_proposition": "clear proof",
                "core_product_definition": "updated toy set",
            },
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["workflow_type"] == "detail_page"
    assert update_response.json()["brief"]["core_product_definition"] == "updated toy set"

    upload_response = client.post(
        f"/api/projects/{project_id}/assets",
        data={"kind": "product_image"},
        files={"file": ("product.png", PNG_1X1, "image/png")},
    )
    assert upload_response.status_code == 200
    uploaded = upload_response.json()
    asset_id = uploaded["id"]
    asset_path = asset_storage.root / project_id / next(item.name for item in (asset_storage.root / project_id).iterdir())
    assert asset_path.exists()

    content_response = client.get(f"/api/projects/{project_id}/assets/{asset_id}/content")
    assert content_response.status_code == 200
    assert content_response.content.startswith(b"\x89PNG\r\n\x1a\n")

    asset_update_response = client.patch(
        f"/api/projects/{project_id}/assets/{asset_id}",
        json={
            "kind": "logo",
            "filename": "brand-logo.png",
            "metadata": {"review_note": "renamed by CRUD test"},
        },
    )
    assert asset_update_response.status_code == 200
    assert asset_update_response.json()["kind"] == "logo"
    assert asset_update_response.json()["filename"] == "brand-logo.png"
    assert asset_update_response.json()["metadata"]["review_note"] == "renamed by CRUD test"

    detail_response = client.get(f"/api/projects/{project_id}/detail")
    assert detail_response.status_code == 200
    assert detail_response.json()["progress"]["asset_count"] == 1

    delete_asset_response = client.delete(f"/api/projects/{project_id}/assets/{asset_id}")
    assert delete_asset_response.status_code == 204
    assert client.get(f"/api/projects/{project_id}/detail").json()["progress"]["asset_count"] == 0
    assert not asset_path.exists()

    delete_project_response = client.delete(f"/api/projects/{project_id}")
    assert delete_project_response.status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_delete_asset_cancels_active_processing_job(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
    project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": [],
                "value_proposition": "",
                "core_product_definition": "toy set",
            },
            "assets": [],
        },
    ).json()
    uploaded = client.post(
        f"/api/projects/{project['id']}/assets",
        data={"kind": "product_image"},
        files={"file": ("product.png", PNG_1X1, "image/png")},
    ).json()
    asset_id = uploaded["id"]
    client.patch(
        f"/api/projects/{project['id']}/assets/{asset_id}",
        json={"metadata": {"processing": {"status": "running", "parser_name": "image_understanding", "parser_version": "image_understanding_v1", "progress": 20}}},
    )
    cancelled = {}

    def fake_cancel(project_id, cancelled_asset_id, *, reason):
        cancelled["project_id"] = project_id
        cancelled["asset_id"] = cancelled_asset_id
        cancelled["reason"] = reason

    monkeypatch.setattr("ai_visual_agent.api.routes.cancel_asset_processing", fake_cancel)

    response = client.delete(f"/api/projects/{project['id']}/assets/{asset_id}")

    assert response.status_code == 204
    assert cancelled == {"project_id": project["id"], "asset_id": asset_id, "reason": "asset_deleted"}


def test_upload_reuses_completed_asset_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
    source_project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {"category": "toy", "target_user": "", "user_expectations": [], "value_proposition": "", "core_product_definition": "toy"},
            "assets": [],
        },
    ).json()
    source_asset = client.post(
        f"/api/projects/{source_project['id']}/assets",
        data={"kind": "product_image"},
        files={"file": ("product.png", PNG_1X1, "image/png")},
    ).json()
    client.patch(
        f"/api/projects/{source_project['id']}/assets/{source_asset['id']}",
        json={
            "metadata": {
                "processing": {
                    "status": "completed",
                    "parser_name": "image_understanding",
                    "parser_version": "image_understanding_v1",
                    "progress": 100,
                    "result_ref": "metadata.image_analysis",
                },
                "image_analysis": {
                    "asset_id": source_asset["id"],
                    "image_uri": source_asset["uri"],
                    "image_role": "product_image",
                    "semantic_summary": "cached product image",
                },
            }
        },
    )
    target_project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {"category": "toy", "target_user": "", "user_expectations": [], "value_proposition": "", "core_product_definition": "toy"},
            "assets": [],
        },
    ).json()

    reused_asset = client.post(
        f"/api/projects/{target_project['id']}/assets",
        data={"kind": "product_image"},
        files={"file": ("product-copy.png", PNG_1X1, "image/png")},
    ).json()

    assert reused_asset["metadata"]["processing"]["status"] == "completed"
    assert reused_asset["metadata"]["processing"]["cache_hit"] is True
    assert reused_asset["metadata"]["image_analysis"]["semantic_summary"] == "cached product image"


def test_delete_project_removes_asset_directory_and_orphan_cleanup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
    project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": [],
                "value_proposition": "",
                "core_product_definition": "toy set",
            },
            "assets": [],
        },
    ).json()
    upload_response = client.post(
        f"/api/projects/{project['id']}/assets",
        data={"kind": "product_image"},
        files={"file": ("product.png", PNG_1X1, "image/png")},
    )
    assert upload_response.status_code == 200
    project_dir = asset_storage.root / project["id"]
    assert project_dir.exists()

    delete_response = client.delete(f"/api/projects/{project['id']}")

    assert delete_response.status_code == 204
    assert not project_dir.exists()

    orphan_dir = asset_storage.root / "orphan-project"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "old.png").write_bytes(PNG_1X1)
    summary = client.get("/api/assets/orphans").json()
    assert summary["orphan_count"] == 1
    cleanup = client.delete("/api/assets/orphans").json()
    assert cleanup["removed_count"] == 1
    assert not orphan_dir.exists()
