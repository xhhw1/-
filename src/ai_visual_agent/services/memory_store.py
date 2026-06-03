import hashlib
import math
from functools import lru_cache
from typing import Any
from uuid import uuid4

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import MemorySearchResult, MemoryUpsertRequest


def _embed_text(text: str, dimensions: int) -> list[float]:
    """Deterministic local embedding for dev/test fallback.

    Production should replace this with a real embedding model, but keeping a stable local
    embedder lets the graph and API be developed without API keys.
    """

    vector = [0.0] * dimensions
    tokens = [token for token in text.lower().split() if token]
    if not tokens:
        tokens = [text.lower()]

    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def _matches_filters(payload: dict[str, Any], filters: dict[str, Any]) -> bool:
    return all(value is None or payload.get(key) == value for key, value in filters.items())


class InMemoryMemoryStore:
    """Development memory store with vector-like retrieval."""

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self._records: dict[str, tuple[list[float], str, dict[str, Any]]] = {}

    def upsert(self, request: MemoryUpsertRequest) -> str:
        record_id = str(uuid4())
        payload = request.model_dump(mode="json", exclude={"text"})
        payload.update(request.metadata)
        self._records[record_id] = (_embed_text(request.text, self.dimensions), request.text, payload)
        return record_id

    def search(self, query: str, limit: int = 5, **filters: Any) -> list[MemorySearchResult]:
        query_vector = _embed_text(query, self.dimensions)
        scored: list[MemorySearchResult] = []
        for record_id, (vector, text, payload) in self._records.items():
            if not _matches_filters(payload, filters):
                continue
            scored.append(
                MemorySearchResult(
                    id=record_id,
                    text=text,
                    score=max(0.0, min(1.0, (_cosine(query_vector, vector) + 1.0) / 2.0)),
                    payload=payload,
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    def delete_project(self, project_id: str) -> int:
        target_ids = [
            record_id
            for record_id, (_, _, payload) in self._records.items()
            if payload.get("project_id") == project_id
        ]
        for record_id in target_ids:
            del self._records[record_id]
        return len(target_ids)


class QdrantMemoryStore:
    """Qdrant-backed semantic memory store.

    This store still uses the deterministic local embedder until a production embedding
    provider is wired. That keeps Qdrant integration independent from model credentials.
    """

    def __init__(self, url: str, collection_name: str, dimensions: int) -> None:
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("qdrant-client is required for QdrantMemoryStore.") from exc

        self.models = models
        self.client = QdrantClient(url=url)
        self.collection_name = collection_name
        self.dimensions = dimensions
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        collections = self.client.get_collections().collections
        if any(collection.name == self.collection_name for collection in collections):
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=self.models.VectorParams(
                size=self.dimensions,
                distance=self.models.Distance.COSINE,
            ),
        )

    def upsert(self, request: MemoryUpsertRequest) -> str:
        record_id = str(uuid4())
        payload = request.model_dump(mode="json", exclude={"text"})
        payload.update(request.metadata)
        payload["text"] = request.text
        point = self.models.PointStruct(
            id=record_id,
            vector=_embed_text(request.text, self.dimensions),
            payload=payload,
        )
        self.client.upsert(collection_name=self.collection_name, points=[point])
        return record_id

    def search(self, query: str, limit: int = 5, **filters: Any) -> list[MemorySearchResult]:
        conditions = [
            self.models.FieldCondition(key=key, match=self.models.MatchValue(value=value))
            for key, value in filters.items()
            if value is not None
        ]
        query_filter = self.models.Filter(must=conditions) if conditions else None
        query_vector = _embed_text(query, self.dimensions)

        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            points = response.points
        else:  # pragma: no cover - compatibility with older qdrant-client versions
            points = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )

        results: list[MemorySearchResult] = []
        for point in points:
            payload = dict(point.payload or {})
            text = str(payload.pop("text", ""))
            results.append(
                MemorySearchResult(
                    id=str(point.id),
                    text=text,
                    score=float(point.score),
                    payload=payload,
                )
            )
        return results

    def delete_project(self, project_id: str) -> int:
        delete_filter = self.models.Filter(
            must=[
                self.models.FieldCondition(
                    key="project_id",
                    match=self.models.MatchValue(value=project_id),
                )
            ]
        )
        result = self.client.delete(
            collection_name=self.collection_name,
            points_selector=self.models.FilterSelector(filter=delete_filter),
        )
        return int(getattr(result, "operation_id", 0) or 0)


@lru_cache
def get_memory_store() -> InMemoryMemoryStore | QdrantMemoryStore:
    settings = get_settings()
    if settings.mock_external_tools:
        return InMemoryMemoryStore(dimensions=settings.memory_embedding_dim)
    try:
        return QdrantMemoryStore(
            url=settings.qdrant_url,
            collection_name=settings.qdrant_collection,
            dimensions=settings.memory_embedding_dim,
        )
    except Exception:
        return InMemoryMemoryStore(dimensions=settings.memory_embedding_dim)
