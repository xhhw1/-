from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


DEFAULT_SQLITE_URL = "sqlite:///data/vision_agent.db"
DEFAULT_POSTGRES_URL = "postgresql+psycopg://vision_agent:vision_agent@localhost:5432/vision_agent"

TABLES_IN_COPY_ORDER = [
    "projects",
    "assets",
    "conversation_sessions",
    "conversation_messages",
    "conversation_review_gates",
    "audit_records",
    "auth_users",
    "background_jobs",
    "knowledge_entries",
]

SCHEMA_STATEMENTS = [
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
    """,
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
    """,
    "CREATE INDEX IF NOT EXISTS idx_assets_project_id ON assets(project_id)",
    """
    CREATE TABLE IF NOT EXISTS conversation_sessions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        owner_id TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL,
        workflow_type TEXT NOT NULL,
        status TEXT NOT NULL,
        current_stage TEXT NOT NULL,
        confirmed_context_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        message_type TEXT NOT NULL,
        content TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_review_gates (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        allowed_actions_json TEXT NOT NULL,
        next_step_on_approve TEXT NOT NULL,
        created_by_agent TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_sessions_project ON conversation_sessions(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_conv_messages_session ON conversation_messages(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_conv_gates_session ON conversation_review_gates(session_id)",
    """
    CREATE TABLE IF NOT EXISTS audit_records (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        record_type TEXT NOT NULL,
        stage TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_audit_project_type
    ON audit_records(project_id, record_type)
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auth_users_email ON auth_users(email)",
    """
    CREATE TABLE IF NOT EXISTS background_jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        project_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        status TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        error TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        heartbeat_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_background_jobs_owner ON background_jobs(owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_background_jobs_project ON background_jobs(project_id)",
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
    """,
    "CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_entries(status)",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge_entries(domain)",
    "CREATE INDEX IF NOT EXISTS idx_knowledge_workflow ON knowledge_entries(workflow_type)",
]


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw_path = url.removeprefix("sqlite:///")
    if raw_path == ":memory:":
        return None
    return Path(raw_path)


def _create_engine(url: str) -> Engine:
    sqlite_path = _sqlite_path_from_url(url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


def _ensure_target_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(text(statement))


def _table_exists(engine: Engine, table: str) -> bool:
    return table in inspect(engine).get_table_names()


def _copy_table(source: Engine, target: Engine, table: str) -> int:
    if not _table_exists(source, table):
        return 0

    with source.begin() as source_conn:
        rows = source_conn.execute(text(f"SELECT * FROM {table}")).mappings().all()
    if not rows:
        return 0

    columns = list(rows[0].keys())
    column_names = ", ".join(columns)
    bind_names = ", ".join(f":{column}" for column in columns)
    update_columns = [column for column in columns if column != "id"]
    if update_columns:
        update_clause = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
        conflict_clause = f"DO UPDATE SET {update_clause}"
    else:
        conflict_clause = "DO NOTHING"
    statement = text(
        f"""
        INSERT INTO {table} ({column_names})
        VALUES ({bind_names})
        ON CONFLICT (id) {conflict_clause}
        """
    )
    with target.begin() as target_conn:
        target_conn.execute(statement, [dict(row) for row in rows])
    return len(rows)


def migrate(source_url: str, target_url: str) -> dict[str, int]:
    if not target_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("Target URL must be PostgreSQL.")
    source_path = _sqlite_path_from_url(source_url)
    if source_path is not None and not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    source = _create_engine(source_url)
    target = _create_engine(target_url)
    _ensure_target_schema(target)
    return {table: _copy_table(source, target, table) for table in TABLES_IN_COPY_ORDER}


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy local SQLite app data into PostgreSQL.")
    parser.add_argument(
        "--source",
        default=os.getenv("LOCAL_DATABASE_URL", DEFAULT_SQLITE_URL),
        help="SQLite SQLAlchemy URL. Defaults to LOCAL_DATABASE_URL or data/vision_agent.db.",
    )
    parser.add_argument(
        "--target",
        default=os.getenv("DATABASE_URL", DEFAULT_POSTGRES_URL),
        help="PostgreSQL SQLAlchemy URL. Defaults to DATABASE_URL or local Docker Postgres.",
    )
    args = parser.parse_args()
    copied = migrate(args.source, args.target)
    total = sum(copied.values())
    print(f"Migrated {total} rows from SQLite to PostgreSQL.")
    for table, count in copied.items():
        print(f"- {table}: {count}")


if __name__ == "__main__":
    main()
