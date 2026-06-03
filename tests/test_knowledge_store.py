from fastapi.testclient import TestClient

from ai_visual_agent.domain import KnowledgeBaseCreateRequest, KnowledgeBaseUpdateRequest
from ai_visual_agent.main import app
from ai_visual_agent.services.knowledge_store import SqlKnowledgeStore, load_default_knowledge_requests


def test_sql_knowledge_store_crud_and_seed(tmp_path) -> None:
    store = SqlKnowledgeStore(f"sqlite:///{tmp_path / 'knowledge.db'}")
    store.setup()
    store.seed_defaults()

    seeded = store.get("toy_entertainment_offline_packaging_v1")
    assert seeded.title == "娱乐/玩具类线下包装知识"

    created = store.create(
        KnowledgeBaseCreateRequest(
            id="kb_test_cosmetics_packaging",
            title="美妆包装知识",
            domain="packaging",
            workflow_type="packaging",
            category="美妆",
            tags=["美妆", "礼盒"],
            keywords=["口红", "护肤"],
            priority=66,
            content={"principles": ["质感、信任和成分证据优先"]},
        )
    )
    assert created.id == "kb_test_cosmetics_packaging"
    assert any(entry.id == created.id for entry in store.list(domain="packaging", workflow_type="packaging"))

    updated = store.update(created.id, KnowledgeBaseUpdateRequest(status="inactive", priority=40))
    assert updated.status == "inactive"
    assert updated.priority == 40

    deleted = store.delete(created.id)
    assert deleted.id == created.id


def test_default_knowledge_lives_in_seed_files() -> None:
    requests = load_default_knowledge_requests()

    assert any(request.id == "toy_entertainment_offline_packaging_v1" for request in requests)
    assert all(request.source == "seed" for request in requests)


def test_knowledge_api_crud_search_and_project_preview() -> None:
    client = TestClient(app)
    entry_id = "kb_api_test_packaging_rules"
    client.delete(f"/api/knowledge/{entry_id}")

    create_response = client.post(
        "/api/knowledge",
        json={
            "id": entry_id,
            "title": "API 测试包装知识",
            "domain": "packaging",
            "workflow_type": "packaging",
            "category": "测试品类",
            "tags": ["测试标签"],
            "keywords": ["测试关键词"],
            "priority": 70,
            "content": {"principles": ["测试原则"]},
            "source": "test",
        },
    )
    assert create_response.status_code == 200
    duplicate_response = client.post(
        "/api/knowledge",
        json={
            "id": entry_id,
            "title": "API 测试包装知识",
            "domain": "packaging",
            "workflow_type": "packaging",
            "category": "测试品类",
            "content": {},
        },
    )
    assert duplicate_response.status_code == 409

    search_response = client.post(
        "/api/knowledge/search",
        json={
            "query": "这是测试关键词项目",
            "workflow_type": "packaging",
            "domain": "packaging",
            "status": "active",
            "limit": 5,
        },
    )
    assert search_response.status_code == 200
    assert any(item["entry"]["id"] == entry_id for item in search_response.json())

    project_response = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "测试品类",
                "target_user": "测试用户",
                "user_expectations": ["测试关键词"],
                "user_metrics": [],
                "value_proposition": "测试价值",
                "core_product_definition": "测试产品",
                "raw_text": "测试关键词包装项目",
            },
            "assets": [],
        },
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["id"]

    preview_response = client.post(f"/api/projects/{project_id}/knowledge/preview")
    assert preview_response.status_code == 200
    assert any(item["entry"]["id"] == entry_id for item in preview_response.json()["results"])

    delete_response = client.delete(f"/api/knowledge/{entry_id}")
    assert delete_response.status_code == 200
