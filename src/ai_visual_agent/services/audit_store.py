from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Protocol

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AuditRecord, AuditRecordType
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)


class AuditStore(Protocol):
    def setup(self) -> None: ...

    def record(
        self,
        project_id: str,
        record_type: AuditRecordType,
        stage: str,
        payload: dict,
    ) -> AuditRecord: ...

    def list_records(
        self,
        project_id: str,
        record_type: AuditRecordType | None = None,
    ) -> list[AuditRecord]: ...

    def delete_project_records(self, project_id: str) -> int: ...


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def setup(self) -> None:
        return None

    def record(
        self,
        project_id: str,
        record_type: AuditRecordType,
        stage: str,
        payload: dict,
    ) -> AuditRecord:
        record = AuditRecord(
            project_id=project_id,
            record_type=record_type,
            stage=stage,
            payload=payload,
        )
        self._records.append(record)
        return record

    def list_records(
        self,
        project_id: str,
        record_type: AuditRecordType | None = None,
    ) -> list[AuditRecord]:
        records = [record for record in self._records if record.project_id == project_id]
        if record_type:
            records = [record for record in records if record.record_type == record_type]
        return sorted(records, key=lambda record: record.created_at)

    def delete_project_records(self, project_id: str) -> int:
        before = len(self._records)
        self._records = [record for record in self._records if record.project_id != project_id]
        return before - len(self._records)


class SqlAuditStore:
    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import Engine
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("SQLAlchemy is required for SqlAuditStore.") from exc

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
                    CREATE TABLE IF NOT EXISTS audit_records (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        record_type TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                self._text(
                    "CREATE INDEX IF NOT EXISTS idx_audit_project_type "
                    "ON audit_records(project_id, record_type)"
                )
            )

    def record(
        self,
        project_id: str,
        record_type: AuditRecordType,
        stage: str,
        payload: dict,
    ) -> AuditRecord:
        record = AuditRecord(
            project_id=project_id,
            record_type=record_type,
            stage=stage,
            payload=payload,
        )
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO audit_records (
                        id, project_id, record_type, stage, payload_json, created_at
                    ) VALUES (
                        :id, :project_id, :record_type, :stage, :payload_json, :created_at
                    )
                    """
                ),
                {
                    "id": record.id,
                    "project_id": record.project_id,
                    "record_type": record.record_type,
                    "stage": record.stage,
                    "payload_json": json.dumps(record.payload, ensure_ascii=False, default=str),
                    "created_at": record.created_at.isoformat(),
                },
            )
        return record

    def list_records(
        self,
        project_id: str,
        record_type: AuditRecordType | None = None,
    ) -> list[AuditRecord]:
        query = "SELECT * FROM audit_records WHERE project_id = :project_id"
        params = {"project_id": project_id}
        if record_type:
            query += " AND record_type = :record_type"
            params["record_type"] = record_type
        query += " ORDER BY created_at"

        with self.engine.begin() as conn:
            rows = conn.execute(self._text(query), params).mappings().all()

        return [
            AuditRecord(
                id=row["id"],
                project_id=row["project_id"],
                record_type=row["record_type"],
                stage=row["stage"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_project_records(self, project_id: str) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                self._text("DELETE FROM audit_records WHERE project_id = :project_id"),
                {"project_id": project_id},
            )
        return int(result.rowcount or 0)


def create_audit_store() -> AuditStore:
    settings = get_settings()
    if project_store_uses_sql(settings):
        store = SqlAuditStore(resolved_project_database_url(settings))
    else:
        store = InMemoryAuditStore()
    store.setup()
    return store


audit_store = create_audit_store()
