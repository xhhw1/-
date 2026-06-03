from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from ai_visual_agent.api.dependencies import require_current_user
from ai_visual_agent.domain import AuthUser
from ai_visual_agent.main import app
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.storage import asset_storage


def _client() -> TestClient:
    app.dependency_overrides[require_current_user] = lambda: AuthUser(
        id="review-console-test-user",
        email="review-console-test@example.com",
        role="admin",
    )
    return TestClient(app)


def test_review_console_static_files_are_served() -> None:
    client = _client()

    index = client.get("/app/")
    script = client.get("/app/app.js")

    assert index.status_code == 200
    assert "PackVision Agent" in index.text
    assert "PackVision" in index.text
    assert "conversationList" in index.text
    assert "messageStream" in index.text
    assert "conversationProgressBadge" in index.text
    assert "memory-panel" not in index.text
    assert "uploadFileBtn" in index.text
    assert "fileChipsArea" in index.text
    assert "assetMentionMenu" in index.text
    assert "attachmentDropzone" in index.text
    assert script.status_code == 200
    assert "loadConversations" in script.text
    assert "submitReviewGate" in script.text
    assert "renderStructuredResultCard" in script.text
    assert "renderPackagingStrategyReport" in script.text
    assert "renderConfirmedReviewGate" in script.text
    assert "startAutoRefresh" in script.text
    assert "scheduleMessageScroll" in script.text
    assert "editingGates" in script.text
    assert "image_prompt_review" in script.text
    assert "/api/conversations" in script.text
    assert "/api/knowledge" in script.text


def test_review_console_can_display_generated_asset_content(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
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
    client.post(
        f"/api/workflows/{project['id']}/resume",
        json={"action": "approve", "reviewer": "console-test", "comment": "usp ok"},
    )
    client.post(
        f"/api/workflows/{project['id']}/resume",
        json={"action": "approve", "reviewer": "console-test", "comment": "strategy ok"},
    )

    detail = client.get(f"/api/projects/{project['id']}/detail").json()
    first_output = detail["latest_generated_outputs"]["items"][0]
    content = client.get(f"/api/projects/{project['id']}/assets/{first_output['asset_id']}/content")

    assert detail["pending_review"]["type"] == "final_design_review"
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("image/png")
    assert content.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_asset_content_falls_back_to_generated_file_scan(monkeypatch, tmp_path) -> None:
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
                "core_product_definition": "toy",
            },
            "assets": [],
        },
    ).json()
    asset_id = "generated-scan-asset"
    project_dir = asset_storage.root / project["id"]
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / f"{asset_id}_packaging_front_base_r0.png").write_bytes(b"\x89PNG\r\n\x1a\nscan")

    response = client.get(f"/api/projects/{project['id']}/assets/{asset_id}/content")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\nscan")


def test_asset_content_falls_back_from_missing_composed_to_base_uri(monkeypatch, tmp_path) -> None:
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
                "core_product_definition": "toy",
            },
            "assets": [],
        },
    ).json()
    project_dir = asset_storage.root / project["id"]
    project_dir.mkdir(parents=True, exist_ok=True)
    base_path = project_dir / "real-base_packaging_front_base_r0.png"
    missing_composed_path = project_dir / "old-composed_packaging_front_composed_r0.png"
    base_path.write_bytes(b"\x89PNG\r\n\x1a\nbase")
    audit_store.record(
        project_id=project["id"],
        record_type="agent_output",
        stage="generate_design_assets",
        payload={
            "items": [
                {
                    "name": "front",
                    "asset_id": "old-composed",
                    "uri": str(missing_composed_path),
                    "layout_spec": {
                        "base_asset_id": "real-base",
                        "base_asset_uri": str(base_path),
                    },
                }
            ]
        },
    )

    response = client.get(f"/api/projects/{project['id']}/assets/old-composed/content")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\nbase")


def test_generated_composed_asset_content_can_download(monkeypatch, tmp_path) -> None:
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
                "core_product_definition": "toy",
            },
            "assets": [],
        },
    ).json()
    project_dir = asset_storage.root / project["id"]
    project_dir.mkdir(parents=True, exist_ok=True)
    composed_path = project_dir / "composed-real_packaging_front_composed_r0.png"
    composed_path.write_bytes(b"\x89PNG\r\n\x1a\ncomposed")
    audit_store.record(
        project_id=project["id"],
        record_type="agent_output",
        stage="generate_design_assets",
        payload={
            "generated_outputs": {
                "items": [
                    {
                        "name": "front",
                        "asset_id": "base-real",
                        "uri": str(project_dir / "base-real_packaging_front_base_r0.png"),
                        "layout_spec": {
                            "composed_asset_id": "composed-real",
                            "composed_asset_uri": str(composed_path),
                        },
                    }
                ]
            }
        },
    )

    response = client.get(f"/api/projects/{project['id']}/assets/composed-real/content?download=true")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"\x89PNG\r\n\x1a\ncomposed")


def test_review_console_archive_download_contains_manifest_and_outputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
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
    client.post(
        f"/api/workflows/{project['id']}/resume",
        json={"action": "approve", "reviewer": "console-test", "comment": "usp ok"},
    )
    client.post(
        f"/api/workflows/{project['id']}/resume",
        json={"action": "approve", "reviewer": "console-test", "comment": "strategy ok"},
    )

    response = client.get(f"/api/projects/{project['id']}/archive/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert "audit_records.json" in names
        assert any(name.startswith("outputs/") and name.endswith(".png") for name in names)


def test_project_backup_create_list_and_download(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
    project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "toy",
                "target_user": "family",
                "user_expectations": ["safe"],
                "value_proposition": "clear play proof",
                "core_product_definition": "interactive toy set",
            },
            "assets": [],
        },
    ).json()

    created = client.post(f"/api/projects/{project['id']}/backups")
    assert created.status_code == 200
    backup = created.json()
    assert backup["project_id"] == project["id"]
    assert backup["size_bytes"] > 0

    backups = client.get(f"/api/projects/{project['id']}/backups")
    assert backups.status_code == 200
    assert backups.json()[0]["id"] == backup["id"]

    downloaded = client.get(backup["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(downloaded.content)) as archive:
        assert "manifest.json" in archive.namelist()
