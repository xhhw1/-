from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ai_visual_agent.config import get_settings

try:
    from langgraph.checkpoint.memory import InMemorySaver as MemorySaver
except ImportError:  # pragma: no cover - compatibility with older LangGraph versions
    from langgraph.checkpoint.memory import MemorySaver


@dataclass
class CheckpointerHandle:
    checkpointer: Any
    backend: str
    close_callback: Callable[[], None] | None = None

    def close(self) -> None:
        if self.close_callback:
            self.close_callback()


def _to_psycopg_conn_string(database_url: str) -> str:
    """Convert SQLAlchemy-style URLs into psycopg connection strings."""

    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if database_url.startswith("postgres+psycopg://"):
        return database_url.replace("postgres+psycopg://", "postgresql://", 1)
    return database_url


def create_checkpointer_handle() -> CheckpointerHandle:
    settings = get_settings()
    backend = settings.graph_checkpoint_backend.lower()
    if backend in {"postgres", "postgresql", "sql"}:
        return _create_postgres_checkpointer(settings.database_url)
    return CheckpointerHandle(checkpointer=MemorySaver(), backend="memory")


def _create_postgres_checkpointer(database_url: str) -> CheckpointerHandle:
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError(
            "GRAPH_CHECKPOINT_BACKEND=postgres requires langgraph-checkpoint-postgres. "
            "Install requirements-infra.txt or the [infra] extra."
        ) from exc

    conn_string = _to_psycopg_conn_string(database_url)
    created = PostgresSaver.from_conn_string(conn_string)

    # Some versions return a context manager; others return the saver directly.
    close_callback: Callable[[], None] | None = None
    if hasattr(created, "__enter__") and hasattr(created, "__exit__"):
        saver = created.__enter__()

        def close_callback() -> None:
            created.__exit__(None, None, None)

    else:
        saver = created

    saver.setup()
    return CheckpointerHandle(checkpointer=saver, backend="postgres", close_callback=close_callback)
