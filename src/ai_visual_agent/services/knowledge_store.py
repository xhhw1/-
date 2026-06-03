from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Protocol

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseEntry,
    KnowledgeBaseUpdateRequest,
    KnowledgeDomain,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeStatus,
    KnowledgeWorkflowType,
    ProjectRecord,
)
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)


DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "defaults"


class KnowledgeStore(Protocol):
    def setup(self) -> None: ...

    def seed_defaults(self) -> None: ...

    def create(self, request: KnowledgeBaseCreateRequest) -> KnowledgeBaseEntry: ...

    def get(self, entry_id: str) -> KnowledgeBaseEntry: ...

    def update(self, entry_id: str, request: KnowledgeBaseUpdateRequest) -> KnowledgeBaseEntry: ...

    def delete(self, entry_id: str) -> KnowledgeBaseEntry: ...

    def list(
        self,
        *,
        status: KnowledgeStatus | None = None,
        domain: KnowledgeDomain | None = None,
        workflow_type: KnowledgeWorkflowType | None = None,
    ) -> list[KnowledgeBaseEntry]: ...


class InMemoryKnowledgeStore:
    def __init__(self) -> None:
        self._entries: dict[str, KnowledgeBaseEntry] = {}

    def setup(self) -> None:
        return None

    def seed_defaults(self) -> None:
        for request in load_default_knowledge_requests():
            if request.id and request.id in self._entries:
                continue
            self.create(request)

    def create(self, request: KnowledgeBaseCreateRequest) -> KnowledgeBaseEntry:
        payload = request.model_dump()
        requested_id = payload.pop("id", None)
        entry = KnowledgeBaseEntry(**payload)
        if requested_id:
            entry.id = requested_id
        if entry.id in self._entries:
            raise ValueError(f"Knowledge entry already exists: {entry.id}")
        self._entries[entry.id] = entry
        return entry

    def get(self, entry_id: str) -> KnowledgeBaseEntry:
        try:
            return self._entries[entry_id]
        except KeyError as exc:
            raise KeyError(f"Knowledge entry not found: {entry_id}") from exc

    def update(self, entry_id: str, request: KnowledgeBaseUpdateRequest) -> KnowledgeBaseEntry:
        entry = self.get(entry_id)
        patch = request.model_dump(exclude_unset=True)
        for key, value in patch.items():
            if value is not None:
                setattr(entry, key, value)
        entry.updated_at = datetime.now(UTC)
        self._entries[entry.id] = entry
        return entry

    def delete(self, entry_id: str) -> KnowledgeBaseEntry:
        entry = self.get(entry_id)
        del self._entries[entry_id]
        return entry

    def list(
        self,
        *,
        status: KnowledgeStatus | None = None,
        domain: KnowledgeDomain | None = None,
        workflow_type: KnowledgeWorkflowType | None = None,
    ) -> list[KnowledgeBaseEntry]:
        entries = list(self._entries.values())
        return _filter_entries(entries, status=status, domain=domain, workflow_type=workflow_type)


class SqlKnowledgeStore:
    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import Engine
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("SQLAlchemy is required for SqlKnowledgeStore.") from exc

        if database_url.startswith("sqlite:///"):
            db_path = database_url.removeprefix("sqlite:///")
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._text = text
        self.engine: Engine = create_engine(database_url, future=True)

    def setup(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_entries (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        workflow_type TEXT NOT NULL,
                        category TEXT NOT NULL,
                        tags_json TEXT NOT NULL,
                        keywords_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        priority INTEGER NOT NULL,
                        content_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_entries(status)"))
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge_entries(domain)"))
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_knowledge_workflow ON knowledge_entries(workflow_type)"))

    def seed_defaults(self) -> None:
        for request in load_default_knowledge_requests():
            if not request.id:
                continue
            try:
                self.get(request.id)
                continue
            except KeyError:
                self.create(request)

    def create(self, request: KnowledgeBaseCreateRequest) -> KnowledgeBaseEntry:
        payload = request.model_dump()
        requested_id = payload.pop("id", None)
        entry = KnowledgeBaseEntry(**payload)
        if requested_id:
            entry.id = requested_id
        try:
            self.get(entry.id)
            raise ValueError(f"Knowledge entry already exists: {entry.id}")
        except KeyError:
            pass
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO knowledge_entries (
                        id, title, domain, workflow_type, category, tags_json, keywords_json,
                        status, priority, content_json, source, created_at, updated_at
                    ) VALUES (
                        :id, :title, :domain, :workflow_type, :category, :tags_json, :keywords_json,
                        :status, :priority, :content_json, :source, :created_at, :updated_at
                    )
                    """
                ),
                _entry_params(entry),
            )
        return entry

    def get(self, entry_id: str) -> KnowledgeBaseEntry:
        with self.engine.begin() as conn:
            row = (
                conn.execute(self._text("SELECT * FROM knowledge_entries WHERE id = :id"), {"id": entry_id})
                .mappings()
                .first()
            )
        if not row:
            raise KeyError(f"Knowledge entry not found: {entry_id}")
        return _entry_from_row(row)

    def update(self, entry_id: str, request: KnowledgeBaseUpdateRequest) -> KnowledgeBaseEntry:
        entry = self.get(entry_id)
        patch = request.model_dump(exclude_unset=True)
        for key, value in patch.items():
            if value is not None:
                setattr(entry, key, value)
        entry.updated_at = datetime.now(UTC)
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE knowledge_entries SET
                        title = :title,
                        domain = :domain,
                        workflow_type = :workflow_type,
                        category = :category,
                        tags_json = :tags_json,
                        keywords_json = :keywords_json,
                        status = :status,
                        priority = :priority,
                        content_json = :content_json,
                        source = :source,
                        updated_at = :updated_at
                    WHERE id = :id
                    """
                ),
                _entry_params(entry),
            )
        return entry

    def delete(self, entry_id: str) -> KnowledgeBaseEntry:
        entry = self.get(entry_id)
        with self.engine.begin() as conn:
            conn.execute(self._text("DELETE FROM knowledge_entries WHERE id = :id"), {"id": entry_id})
        return entry

    def list(
        self,
        *,
        status: KnowledgeStatus | None = None,
        domain: KnowledgeDomain | None = None,
        workflow_type: KnowledgeWorkflowType | None = None,
    ) -> list[KnowledgeBaseEntry]:
        clauses: list[str] = []
        params: dict[str, str] = {}
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status
        if domain is not None:
            clauses.append("domain = :domain")
            params["domain"] = domain
        if workflow_type is not None:
            clauses.append("(workflow_type = :workflow_type OR workflow_type = 'all')")
            params["workflow_type"] = workflow_type
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.engine.begin() as conn:
            rows = (
                conn.execute(
                    self._text(
                        f"""
                        SELECT * FROM knowledge_entries
                        {where}
                        ORDER BY priority DESC, updated_at DESC
                        """
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return [_entry_from_row(row) for row in rows]


def load_default_knowledge_requests() -> list[KnowledgeBaseCreateRequest]:
    requests: list[KnowledgeBaseCreateRequest] = []
    if not DEFAULT_KNOWLEDGE_DIR.exists():
        return requests
    for path in sorted(DEFAULT_KNOWLEDGE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        requests.append(KnowledgeBaseCreateRequest(**payload))
    return requests


def search_knowledge(request: KnowledgeSearchRequest) -> list[KnowledgeSearchResult]:
    entries = knowledge_store.list(
        status=request.status,
        domain=request.domain,
        workflow_type=request.workflow_type,
    )
    scored = [
        _score_entry(entry, request.query, category=request.category)
        for entry in entries
    ]
    return [item for item in sorted(scored, key=lambda result: result.score, reverse=True) if item.score > 0][: request.limit]


def project_knowledge_query(project: ProjectRecord, confirmed_usps: dict | None = None) -> str:
    brief = project.brief
    parts: list[str] = [
        brief.raw_text,
        brief.category,
        brief.target_user,
        brief.value_proposition,
        brief.core_product_definition,
        " ".join(brief.user_expectations or []),
        " ".join(brief.user_metrics or []),
    ]
    for group in ("core", "secondary"):
        for item in (confirmed_usps or {}).get(group, []) or []:
            if not isinstance(item, dict):
                continue
            parts.extend(
                str(item.get(key) or "")
                for key in ("title", "headline", "angle", "content", "description", "product_visual_evidence")
            )
    return " ".join(part for part in parts if part)


def search_project_knowledge(
    project: ProjectRecord,
    confirmed_usps: dict | None = None,
    *,
    domain: KnowledgeDomain = "packaging",
    limit: int = 5,
) -> list[KnowledgeSearchResult]:
    return search_knowledge(
        KnowledgeSearchRequest(
            query=project_knowledge_query(project, confirmed_usps),
            workflow_type=project.workflow_type,
            domain=domain,
            status="active",
            category=project.brief.category,
            limit=limit,
        )
    )


def build_project_knowledge_context(
    project: ProjectRecord,
    confirmed_usps: dict | None = None,
    *,
    domain: KnowledgeDomain = "packaging",
    limit: int = 5,
) -> dict:
    results = search_project_knowledge(project, confirmed_usps, domain=domain, limit=limit)
    return {
        "matched": bool(results),
        "query": project_knowledge_query(project, confirmed_usps),
        "results": [
            {
                "id": result.entry.id,
                "title": result.entry.title,
                "domain": result.entry.domain,
                "workflow_type": result.entry.workflow_type,
                "category": result.entry.category,
                "priority": result.entry.priority,
                "matched_keywords": result.matched_keywords,
                "score": result.score,
                "content": result.entry.content,
            }
            for result in results
        ],
        "instruction": _knowledge_instruction(results),
    }


def _knowledge_instruction(results: list[KnowledgeSearchResult]) -> str:
    if not results:
        return "未命中特定知识库条目；按通用包装策略方法输出。"
    titles = "、".join(result.entry.title for result in results[:3])
    return (
        f"已命中知识库：{titles}。Agent 必须自主判断哪些原则适用于当前产品，"
        "把知识转化为具体包装策略；知识只能作为设计方法，不能覆盖产品资料和用户确认事实。"
    )


def _score_entry(entry: KnowledgeBaseEntry, query: str, category: str | None = None) -> KnowledgeSearchResult:
    normalized_query = _normalize(query)
    normalized_category = _normalize(category or "")
    matched: list[str] = []
    score = float(entry.priority) / 100.0
    for keyword in entry.keywords + entry.tags + ([entry.category] if entry.category else []):
        normalized = _normalize(keyword)
        if not normalized:
            continue
        if normalized and normalized in normalized_query:
            matched.append(keyword)
            score += 8
        elif normalized_category and normalized in normalized_category:
            matched.append(keyword)
            score += 5
    title_norm = _normalize(entry.title)
    if title_norm and title_norm in normalized_query:
        score += 4
    if entry.workflow_type != "all":
        score += 1
    if not matched and query.strip():
        content_text = _normalize(json.dumps(entry.content, ensure_ascii=False))
        for token in _query_tokens(normalized_query):
            if len(token) >= 2 and token in content_text:
                matched.append(token)
                score += 1
                if len(matched) >= 3:
                    break
    if not matched and query.strip():
        score = 0
    return KnowledgeSearchResult(
        entry=entry,
        score=round(score, 3),
        matched_keywords=list(dict.fromkeys(matched))[:12],
        reason="、".join(list(dict.fromkeys(matched))[:6]) if matched else "",
    )


def _query_tokens(normalized_query: str) -> list[str]:
    return [token for token in normalized_query.split() if token]


def _normalize(value: str) -> str:
    return (
        str(value or "")
        .lower()
        .replace("（", " ")
        .replace("）", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace("/", " ")
        .replace("_", " ")
        .replace("-", " ")
        .replace("，", " ")
        .replace(",", " ")
        .replace("。", " ")
    )


def _filter_entries(
    entries: list[KnowledgeBaseEntry],
    *,
    status: KnowledgeStatus | None = None,
    domain: KnowledgeDomain | None = None,
    workflow_type: KnowledgeWorkflowType | None = None,
) -> list[KnowledgeBaseEntry]:
    filtered = entries
    if status is not None:
        filtered = [entry for entry in filtered if entry.status == status]
    if domain is not None:
        filtered = [entry for entry in filtered if entry.domain == domain]
    if workflow_type is not None:
        filtered = [entry for entry in filtered if entry.workflow_type in {workflow_type, "all"}]
    return sorted(filtered, key=lambda entry: (entry.priority, entry.updated_at), reverse=True)


def _entry_params(entry: KnowledgeBaseEntry) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "domain": entry.domain,
        "workflow_type": entry.workflow_type,
        "category": entry.category,
        "tags_json": json.dumps(entry.tags, ensure_ascii=False),
        "keywords_json": json.dumps(entry.keywords, ensure_ascii=False),
        "status": entry.status,
        "priority": entry.priority,
        "content_json": json.dumps(entry.content, ensure_ascii=False),
        "source": entry.source,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _entry_from_row(row) -> KnowledgeBaseEntry:
    return KnowledgeBaseEntry(
        id=row["id"],
        title=row["title"],
        domain=row["domain"],
        workflow_type=row["workflow_type"],
        category=row["category"],
        tags=json.loads(row["tags_json"]),
        keywords=json.loads(row["keywords_json"]),
        status=row["status"],
        priority=row["priority"],
        content=json.loads(row["content_json"]),
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_knowledge_store() -> KnowledgeStore:
    settings = get_settings()
    if project_store_uses_sql(settings):
        store: KnowledgeStore = SqlKnowledgeStore(resolved_project_database_url(settings))
    else:
        store = InMemoryKnowledgeStore()
    store.setup()
    store.seed_defaults()
    return store


knowledge_store = create_knowledge_store()
