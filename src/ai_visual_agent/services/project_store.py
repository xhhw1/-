from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Protocol

from ai_visual_agent.domain import (
    AssetRef,
    AssetUpdateRequest,
    ProjectCreateRequest,
    ProjectRecord,
    ProjectUpdateRequest,
)
from ai_visual_agent.config import get_settings
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)


def _owner_id(value: str | None = None) -> str:
    return (value or get_settings().admin_email or "local-admin").strip().lower()


class ProjectStore(Protocol):
    def setup(self) -> None: ...

    def create(self, request: ProjectCreateRequest) -> ProjectRecord: ...

    def get(self, project_id: str) -> ProjectRecord: ...

    def update(self, project_id: str, request: ProjectUpdateRequest) -> ProjectRecord: ...

    def delete(self, project_id: str) -> None: ...

    def update_status(self, project_id: str, status: str) -> ProjectRecord: ...

    def add_asset(self, project_id: str, asset: AssetRef) -> ProjectRecord: ...

    def update_asset_metadata(
        self,
        project_id: str,
        asset_id: str,
        metadata_patch: dict,
    ) -> AssetRef: ...

    def update_asset(
        self,
        project_id: str,
        asset_id: str,
        request: AssetUpdateRequest,
    ) -> AssetRef: ...

    def delete_asset(self, project_id: str, asset_id: str) -> AssetRef: ...

    def list(self, owner_id: str | None = None) -> list[ProjectRecord]: ...


class InMemoryProjectStore:
    """Small development store. Replace with Postgres repositories in production."""

    def __init__(self) -> None:
        self._projects: dict[str, ProjectRecord] = {}

    def setup(self) -> None:
        return None

    def create(self, request: ProjectCreateRequest) -> ProjectRecord:
        record = ProjectRecord(**{**request.model_dump(), "owner_id": _owner_id(request.owner_id)})
        self._projects[record.id] = record
        return record

    def get(self, project_id: str) -> ProjectRecord:
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise KeyError(f"Project not found: {project_id}") from exc

    def update(self, project_id: str, request: ProjectUpdateRequest) -> ProjectRecord:
        record = self.get(project_id)
        if request.workflow_type is not None:
            record.workflow_type = request.workflow_type
        if request.brief is not None:
            record.brief = request.brief
        record.updated_at = datetime.now(UTC)
        self._projects[project_id] = record
        return record

    def delete(self, project_id: str) -> None:
        self.get(project_id)
        del self._projects[project_id]

    def update_status(self, project_id: str, status: str) -> ProjectRecord:
        record = self.get(project_id)
        record.status = status
        record.updated_at = datetime.now(UTC)
        self._projects[project_id] = record
        return record

    def add_asset(self, project_id: str, asset: AssetRef) -> ProjectRecord:
        record = self.get(project_id)
        record.assets.append(asset)
        record.updated_at = datetime.now(UTC)
        self._projects[project_id] = record
        return record

    def update_asset_metadata(
        self,
        project_id: str,
        asset_id: str,
        metadata_patch: dict,
    ) -> AssetRef:
        record = self.get(project_id)
        for index, asset in enumerate(record.assets):
            if asset.id == asset_id:
                asset.metadata.update(metadata_patch)
                record.assets[index] = asset
                record.updated_at = datetime.now(UTC)
                self._projects[project_id] = record
                return asset
        raise KeyError(f"Asset not found: {asset_id}")

    def update_asset(
        self,
        project_id: str,
        asset_id: str,
        request: AssetUpdateRequest,
    ) -> AssetRef:
        record = self.get(project_id)
        for index, asset in enumerate(record.assets):
            if asset.id == asset_id:
                if request.kind is not None:
                    asset.kind = request.kind
                if request.filename is not None:
                    asset.filename = request.filename
                if request.metadata is not None:
                    asset.metadata.update(request.metadata)
                record.assets[index] = asset
                record.updated_at = datetime.now(UTC)
                self._projects[project_id] = record
                return asset
        raise KeyError(f"Asset not found: {asset_id}")

    def delete_asset(self, project_id: str, asset_id: str) -> AssetRef:
        record = self.get(project_id)
        for index, asset in enumerate(record.assets):
            if asset.id == asset_id:
                deleted = record.assets.pop(index)
                record.updated_at = datetime.now(UTC)
                self._projects[project_id] = record
                return deleted
        raise KeyError(f"Asset not found: {asset_id}")

    def list(self, owner_id: str | None = None) -> list[ProjectRecord]:
        records = list(self._projects.values())
        if owner_id:
            records = [record for record in records if _owner_id(record.owner_id) == owner_id]
        return records


class SqlProjectStore:
    """SQL-backed project store for PostgreSQL production usage.

    The table definitions use conservative text JSON columns so the same class can be
    smoke-tested against SQLite while production runs on PostgreSQL 16.
    """

    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import Engine
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("SQLAlchemy is required for SqlProjectStore.") from exc

        self._text = text
        connect_args = {}
        if database_url.startswith("sqlite:///"):
            db_path = database_url.removeprefix("sqlite:///")
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(database_url, future=True, connect_args=connect_args)

    def setup(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL DEFAULT '',
                        workflow_type TEXT NOT NULL,
                        brief_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )
            self._ensure_column(conn, "projects", "owner_id", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                self._text(
                    """
                    CREATE TABLE IF NOT EXISTS assets (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        uri TEXT NOT NULL,
                        mime_type TEXT,
                        metadata_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                self._text(
                    "CREATE INDEX IF NOT EXISTS idx_assets_project_id ON assets(project_id)"
                )
            )

    def create(self, request: ProjectCreateRequest) -> ProjectRecord:
        record = ProjectRecord(**{**request.model_dump(), "owner_id": _owner_id(request.owner_id)})
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO projects (
                        id, owner_id, workflow_type, brief_json, status, created_at, updated_at
                    ) VALUES (
                        :id, :owner_id, :workflow_type, :brief_json, :status, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": record.id,
                    "owner_id": _owner_id(record.owner_id),
                    "workflow_type": record.workflow_type,
                    "brief_json": json.dumps(record.brief.model_dump(mode="json"), ensure_ascii=False),
                    "status": record.status,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                },
            )
            for asset in record.assets:
                self._insert_asset(conn, record.id, asset)
        return record

    def get(self, project_id: str) -> ProjectRecord:
        with self.engine.begin() as conn:
            project_row = (
                conn.execute(
                    self._text("SELECT * FROM projects WHERE id = :id"),
                    {"id": project_id},
                )
                .mappings()
                .first()
            )
            if not project_row:
                raise KeyError(f"Project not found: {project_id}")

            asset_rows = (
                conn.execute(
                    self._text("SELECT * FROM assets WHERE project_id = :project_id ORDER BY created_at"),
                    {"project_id": project_id},
                )
                .mappings()
                .all()
            )

        return self._record_from_rows(project_row, asset_rows)

    def update(self, project_id: str, request: ProjectUpdateRequest) -> ProjectRecord:
        record = self.get(project_id)
        workflow_type = request.workflow_type or record.workflow_type
        brief = request.brief or record.brief
        updated_at = datetime.now(UTC).isoformat()
        with self.engine.begin() as conn:
            result = conn.execute(
                self._text(
                    """
                    UPDATE projects
                    SET workflow_type = :workflow_type,
                        brief_json = :brief_json,
                        updated_at = :updated_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": project_id,
                    "workflow_type": workflow_type,
                    "brief_json": json.dumps(brief.model_dump(mode="json"), ensure_ascii=False),
                    "updated_at": updated_at,
                },
            )
            if result.rowcount == 0:
                raise KeyError(f"Project not found: {project_id}")
        return self.get(project_id)

    def delete(self, project_id: str) -> None:
        self.get(project_id)
        with self.engine.begin() as conn:
            conn.execute(self._text("DELETE FROM assets WHERE project_id = :project_id"), {"project_id": project_id})
            result = conn.execute(self._text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
            if result.rowcount == 0:
                raise KeyError(f"Project not found: {project_id}")

    def update_status(self, project_id: str, status: str) -> ProjectRecord:
        updated_at = datetime.now(UTC).isoformat()
        with self.engine.begin() as conn:
            result = conn.execute(
                self._text(
                    "UPDATE projects SET status = :status, updated_at = :updated_at WHERE id = :id"
                ),
                {"id": project_id, "status": status, "updated_at": updated_at},
            )
            if result.rowcount == 0:
                raise KeyError(f"Project not found: {project_id}")
        return self.get(project_id)

    def add_asset(self, project_id: str, asset: AssetRef) -> ProjectRecord:
        with self.engine.begin() as conn:
            exists = conn.execute(
                self._text("SELECT 1 FROM projects WHERE id = :id"),
                {"id": project_id},
            ).first()
            if not exists:
                raise KeyError(f"Project not found: {project_id}")
            self._insert_asset(conn, project_id, asset)
            conn.execute(
                self._text("UPDATE projects SET updated_at = :updated_at WHERE id = :id"),
                {"id": project_id, "updated_at": datetime.now(UTC).isoformat()},
            )
        return self.get(project_id)

    def update_asset_metadata(
        self,
        project_id: str,
        asset_id: str,
        metadata_patch: dict,
    ) -> AssetRef:
        record = self.get(project_id)
        asset = next((item for item in record.assets if item.id == asset_id), None)
        if not asset:
            raise KeyError(f"Asset not found: {asset_id}")

        asset.metadata.update(metadata_patch)
        with self.engine.begin() as conn:
            result = conn.execute(
                self._text(
                    """
                    UPDATE assets
                    SET metadata_json = :metadata_json
                    WHERE id = :id AND project_id = :project_id
                    """
                ),
                {
                    "id": asset_id,
                    "project_id": project_id,
                    "metadata_json": json.dumps(asset.metadata, ensure_ascii=False),
                },
            )
            if result.rowcount == 0:
                raise KeyError(f"Asset not found: {asset_id}")
            conn.execute(
                self._text("UPDATE projects SET updated_at = :updated_at WHERE id = :project_id"),
                {"project_id": project_id, "updated_at": datetime.now(UTC).isoformat()},
            )
        return asset

    def update_asset(
        self,
        project_id: str,
        asset_id: str,
        request: AssetUpdateRequest,
    ) -> AssetRef:
        record = self.get(project_id)
        asset = next((item for item in record.assets if item.id == asset_id), None)
        if not asset:
            raise KeyError(f"Asset not found: {asset_id}")

        if request.kind is not None:
            asset.kind = request.kind
        if request.filename is not None:
            asset.filename = request.filename
        if request.metadata is not None:
            asset.metadata.update(request.metadata)

        with self.engine.begin() as conn:
            result = conn.execute(
                self._text(
                    """
                    UPDATE assets
                    SET kind = :kind,
                        filename = :filename,
                        metadata_json = :metadata_json
                    WHERE id = :id AND project_id = :project_id
                    """
                ),
                {
                    "id": asset_id,
                    "project_id": project_id,
                    "kind": asset.kind,
                    "filename": asset.filename,
                    "metadata_json": json.dumps(asset.metadata, ensure_ascii=False),
                },
            )
            if result.rowcount == 0:
                raise KeyError(f"Asset not found: {asset_id}")
            conn.execute(
                self._text("UPDATE projects SET updated_at = :updated_at WHERE id = :project_id"),
                {"project_id": project_id, "updated_at": datetime.now(UTC).isoformat()},
            )
        return asset

    def delete_asset(self, project_id: str, asset_id: str) -> AssetRef:
        record = self.get(project_id)
        asset = next((item for item in record.assets if item.id == asset_id), None)
        if not asset:
            raise KeyError(f"Asset not found: {asset_id}")

        with self.engine.begin() as conn:
            result = conn.execute(
                self._text("DELETE FROM assets WHERE id = :id AND project_id = :project_id"),
                {"id": asset_id, "project_id": project_id},
            )
            if result.rowcount == 0:
                raise KeyError(f"Asset not found: {asset_id}")
            conn.execute(
                self._text("UPDATE projects SET updated_at = :updated_at WHERE id = :project_id"),
                {"project_id": project_id, "updated_at": datetime.now(UTC).isoformat()},
            )
        return asset

    def list(self, owner_id: str | None = None) -> list[ProjectRecord]:
        with self.engine.begin() as conn:
            if owner_id:
                owner = _owner_id(owner_id)
                if owner == _owner_id():
                    project_rows = conn.execute(
                        self._text(
                            """
                            SELECT * FROM projects
                            WHERE owner_id = :owner_id OR owner_id = ''
                            ORDER BY created_at DESC
                            """
                        ),
                        {"owner_id": owner},
                    ).mappings().all()
                else:
                    project_rows = conn.execute(
                        self._text(
                            "SELECT * FROM projects WHERE owner_id = :owner_id ORDER BY created_at DESC"
                        ),
                        {"owner_id": owner},
                    ).mappings().all()
            else:
                project_rows = conn.execute(
                    self._text("SELECT * FROM projects ORDER BY created_at DESC")
                ).mappings().all()
            asset_rows = conn.execute(self._text("SELECT * FROM assets ORDER BY created_at")).mappings().all()

        assets_by_project: dict[str, list[dict]] = {}
        for row in asset_rows:
            assets_by_project.setdefault(str(row["project_id"]), []).append(row)
        return [
            self._record_from_rows(row, assets_by_project.get(str(row["id"]), []))
            for row in project_rows
        ]

    def _insert_asset(self, conn, project_id: str, asset: AssetRef) -> None:
        conn.execute(
            self._text(
                """
                INSERT INTO assets (
                    id, project_id, kind, filename, uri, mime_type, metadata_json, created_at
                ) VALUES (
                    :id, :project_id, :kind, :filename, :uri, :mime_type, :metadata_json, :created_at
                )
                """
            ),
            {
                "id": asset.id,
                "project_id": project_id,
                "kind": asset.kind,
                "filename": asset.filename,
                "uri": asset.uri,
                "mime_type": asset.mime_type,
                "metadata_json": json.dumps(asset.metadata, ensure_ascii=False),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    def _ensure_column(self, conn, table: str, column: str, definition: str) -> None:
        try:
            conn.execute(self._text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
        except Exception:
            return

    @staticmethod
    def _record_from_rows(project_row, asset_rows) -> ProjectRecord:
        return ProjectRecord(
            id=project_row["id"],
            owner_id=_owner_id(str(project_row.get("owner_id") or "")),
            workflow_type=project_row["workflow_type"],
            brief=json.loads(project_row["brief_json"]),
            assets=[
                AssetRef(
                    id=row["id"],
                    kind=row["kind"],
                    filename=row["filename"],
                    uri=row["uri"],
                    mime_type=row["mime_type"],
                    metadata=json.loads(row["metadata_json"]),
                )
                for row in asset_rows
            ],
            status=project_row["status"],
            created_at=project_row["created_at"],
            updated_at=project_row["updated_at"],
        )


def create_project_store() -> ProjectStore:
    settings = get_settings()
    if project_store_uses_sql(settings):
        store = SqlProjectStore(resolved_project_database_url(settings))
    else:
        store = InMemoryProjectStore()
    store.setup()
    return store


project_store = create_project_store()
