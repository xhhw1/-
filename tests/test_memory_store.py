from ai_visual_agent.domain import MemoryUpsertRequest
from ai_visual_agent.services.memory_store import get_memory_store


def test_memory_store_filters_by_project() -> None:
    get_memory_store.cache_clear()
    store = get_memory_store()

    store.upsert(
        MemoryUpsertRequest(
            text="品牌色是红色和白色，包装需要保留 LOGO 安全区。",
            memory_type="brand_vi",
            project_id="project-a",
            brand_id="brand-1",
        )
    )
    store.upsert(
        MemoryUpsertRequest(
            text="竞品主打低价套装。",
            memory_type="competitor",
            project_id="project-b",
        )
    )

    results = store.search("LOGO 品牌色", project_id="project-a", memory_type="brand_vi")

    assert len(results) == 1
    assert results[0].payload["project_id"] == "project-a"
    assert "LOGO" in results[0].text
