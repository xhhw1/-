from __future__ import annotations

from typing import Any


POSTGRES_BACKENDS = {"postgres", "postgresql", "sql"}
SQLITE_BACKENDS = {"sqlite", "local", "local_sqlite", "file"}
SQL_PROJECT_BACKENDS = POSTGRES_BACKENDS | SQLITE_BACKENDS


def normalized_project_store_backend(settings: Any) -> str:
    backend = str(getattr(settings, "project_store_backend", "memory") or "memory").lower()
    if backend in SQLITE_BACKENDS:
        return "sqlite"
    if backend in POSTGRES_BACKENDS:
        return "postgres"
    return backend


def project_store_uses_sql(settings: Any) -> bool:
    backend = str(getattr(settings, "project_store_backend", "memory") or "memory").lower()
    return backend in SQL_PROJECT_BACKENDS


def resolved_project_database_url(settings: Any) -> str:
    backend = str(getattr(settings, "project_store_backend", "memory") or "memory").lower()
    if backend in SQLITE_BACKENDS:
        return str(getattr(settings, "local_database_url", "sqlite:///data/vision_agent.db"))
    return str(getattr(settings, "database_url", ""))
