from ai_visual_agent.services.checkpoint import (
    CheckpointerHandle,
    _to_psycopg_conn_string,
    create_checkpointer_handle,
)


def test_default_checkpointer_is_memory() -> None:
    handle = create_checkpointer_handle()

    assert isinstance(handle, CheckpointerHandle)
    assert handle.backend == "memory"
    assert handle.checkpointer is not None


def test_sqlalchemy_postgres_url_is_converted_for_psycopg() -> None:
    assert (
        _to_psycopg_conn_string("postgresql+psycopg://user:pass@host:5432/db")
        == "postgresql://user:pass@host:5432/db"
    )
