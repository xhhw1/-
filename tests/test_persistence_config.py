from types import SimpleNamespace

from ai_visual_agent.services.persistence_config import (
    normalized_project_store_backend,
    project_store_uses_sql,
    resolved_project_database_url,
)


def test_sqlite_backend_uses_local_database_url() -> None:
    settings = SimpleNamespace(
        project_store_backend="sqlite",
        database_url="postgresql+psycopg://user:pass@localhost/db",
        local_database_url="sqlite:///data/local.db",
    )

    assert normalized_project_store_backend(settings) == "sqlite"
    assert project_store_uses_sql(settings) is True
    assert resolved_project_database_url(settings) == "sqlite:///data/local.db"


def test_postgres_backend_uses_database_url() -> None:
    settings = SimpleNamespace(
        project_store_backend="postgres",
        database_url="postgresql+psycopg://user:pass@localhost/db",
        local_database_url="sqlite:///data/local.db",
    )

    assert normalized_project_store_backend(settings) == "postgres"
    assert project_store_uses_sql(settings) is True
    assert resolved_project_database_url(settings) == "postgresql+psycopg://user:pass@localhost/db"
