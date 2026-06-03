from typing import Any

from langchain_core.tools import tool

from ai_visual_agent.domain import MemoryUpsertRequest
from ai_visual_agent.services.memory_store import get_memory_store


@tool
def save_memory(text: str, memory_type: str = "other", payload: dict[str, Any] | None = None) -> dict[str, str]:
    """Save a semantic memory record for later Agent retrieval."""

    payload = payload or {}
    request = MemoryUpsertRequest(
        text=text,
        memory_type=memory_type,  # type: ignore[arg-type]
        project_id=payload.get("project_id"),
        brand_id=payload.get("brand_id"),
        category=payload.get("category"),
        workflow_type=payload.get("workflow_type"),
        asset_id=payload.get("asset_id"),
        source_type=payload.get("source_type"),
        metadata={key: value for key, value in payload.items() if key not in {
            "project_id",
            "brand_id",
            "category",
            "workflow_type",
            "asset_id",
            "source_type",
        }},
    )
    return {"memory_id": get_memory_store().upsert(request)}


@tool
def search_memory(query: str, limit: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Search semantic memory with optional metadata filters."""

    filters = filters or {}
    results = get_memory_store().search(query=query, limit=limit, **filters)
    return [result.model_dump(mode="json") for result in results]
