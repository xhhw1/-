from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import (
    ConversationMessage,
    ConversationReviewAction,
    ConversationReviewGate,
    ConversationSession,
    ConversationWorkflowType,
)
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)


def _owner_id(value: str | None = None) -> str:
    return (value or get_settings().admin_email or "local-admin").strip().lower()


class InMemoryConversationStore:
    """Conversation store for the first dialogue-workbench slice.

    The service is intentionally small and can later be replaced by a SQL repository
    without changing the API layer.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationSession] = {}
        self._messages: dict[str, list[ConversationMessage]] = {}
        self._review_gates: dict[str, list[ConversationReviewGate]] = {}

    def create_session(
        self,
        *,
        project_id: str,
        title: str,
        workflow_type: ConversationWorkflowType = "unknown",
        owner_id: str = "",
    ) -> ConversationSession:
        session = ConversationSession(
            project_id=project_id,
            owner_id=_owner_id(owner_id),
            title=title or "未命名对话",
            workflow_type=workflow_type,
        )
        self._sessions[session.id] = session
        self._messages[session.id] = []
        self._review_gates[session.id] = []
        return session

    def list_sessions(self, owner_id: str | None = None) -> list[ConversationSession]:
        sessions = list(self._sessions.values())
        if owner_id:
            sessions = [session for session in sessions if _owner_id(session.owner_id) == owner_id]
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def get_session(self, session_id: str) -> ConversationSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Conversation not found: {session_id}") from exc

    def delete_session(self, session_id: str) -> ConversationSession:
        session = self.get_session(session_id)
        del self._sessions[session_id]
        self._messages.pop(session_id, None)
        self._review_gates.pop(session_id, None)
        return session

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        workflow_type: ConversationWorkflowType | None = None,
        current_stage: str | None = None,
        status: str | None = None,
        confirmed_context_patch: dict[str, Any] | None = None,
    ) -> ConversationSession:
        session = self.get_session(session_id)
        if title is not None and title.strip():
            session.title = title.strip()
        if workflow_type is not None:
            session.workflow_type = workflow_type
        if current_stage is not None:
            session.current_stage = current_stage
        if status is not None:
            session.status = status  # type: ignore[assignment]
        if confirmed_context_patch:
            session.confirmed_context.update(confirmed_context_patch)
        session.updated_at = datetime.now(UTC)
        self._sessions[session_id] = session
        return session

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        message_type: str = "text",
        content: str = "",
        payload: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        self.get_session(session_id)
        message = ConversationMessage(
            session_id=session_id,
            role=role,  # type: ignore[arg-type]
            message_type=message_type,  # type: ignore[arg-type]
            content=content,
            payload=payload or {},
        )
        self._messages.setdefault(session_id, []).append(message)
        self.touch(session_id)
        return message

    def list_messages(self, session_id: str) -> list[ConversationMessage]:
        self.get_session(session_id)
        return sorted(self._messages.get(session_id, []), key=lambda item: item.created_at)

    def create_review_gate(
        self,
        *,
        session_id: str,
        gate_type: str,
        title: str,
        payload: dict[str, Any],
        summary: str = "",
        next_step_on_approve: str = "",
        created_by_agent: str = "",
        allowed_actions: list[ConversationReviewAction] | None = None,
    ) -> ConversationReviewGate:
        self.get_session(session_id)
        for gate in self._review_gates.get(session_id, []):
            if gate.status == "pending":
                gate.status = "needs_more_info"
                gate.resolved_at = datetime.now(UTC)
        gate = ConversationReviewGate(
            session_id=session_id,
            type=gate_type,
            title=title,
            summary=summary,
            payload=payload,
            next_step_on_approve=next_step_on_approve,
            created_by_agent=created_by_agent,
            allowed_actions=allowed_actions or ["approve", "edit", "reject"],
        )
        self._review_gates.setdefault(session_id, []).append(gate)
        self.touch(session_id)
        return gate

    def get_review_gate(self, session_id: str, gate_id: str) -> ConversationReviewGate:
        self.get_session(session_id)
        for gate in self._review_gates.get(session_id, []):
            if gate.id == gate_id:
                return gate
        raise KeyError(f"Review gate not found: {gate_id}")

    def update_review_gate_payload(
        self,
        *,
        session_id: str,
        gate_id: str,
        payload: dict[str, Any],
        title: str | None = None,
        summary: str | None = None,
    ) -> ConversationReviewGate:
        gate = self.get_review_gate(session_id, gate_id)
        gate.payload = payload
        if title is not None:
            gate.title = title
        if summary is not None:
            gate.summary = summary
        self.touch(session_id)
        return gate

    def resolve_review_gate(
        self,
        *,
        session_id: str,
        gate_id: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> ConversationReviewGate:
        gate = self.get_review_gate(session_id, gate_id)
        gate.status = status  # type: ignore[assignment]
        if payload is not None:
            gate.payload = payload
        gate.resolved_at = datetime.now(UTC)
        self.touch(session_id)
        return gate

    def list_review_gates(self, session_id: str) -> list[ConversationReviewGate]:
        self.get_session(session_id)
        return sorted(self._review_gates.get(session_id, []), key=lambda item: item.created_at)

    def pending_review_gate(self, session_id: str) -> ConversationReviewGate | None:
        gates = [gate for gate in self.list_review_gates(session_id) if gate.status == "pending"]
        return gates[-1] if gates else None

    def touch(self, session_id: str) -> None:
        session = self.get_session(session_id)
        session.updated_at = datetime.now(UTC)
        self._sessions[session_id] = session


class SqlConversationStore:
    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import Engine
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("SQLAlchemy is required for SqlConversationStore.") from exc

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
                    """
                )
            )
            self._ensure_column(conn, "conversation_sessions", "owner_id", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                self._text(
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
                    """
                )
            )
            conn.execute(
                self._text(
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
                    """
                )
            )
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_conv_sessions_project ON conversation_sessions(project_id)"))
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_conv_messages_session ON conversation_messages(session_id)"))
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_conv_gates_session ON conversation_review_gates(session_id)"))

    def create_session(
        self,
        *,
        project_id: str,
        title: str,
        workflow_type: ConversationWorkflowType = "unknown",
        owner_id: str = "",
    ) -> ConversationSession:
        session = ConversationSession(
            project_id=project_id,
            owner_id=_owner_id(owner_id),
            title=title or "未命名对话",
            workflow_type=workflow_type,
        )
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO conversation_sessions (
                        id, project_id, owner_id, title, workflow_type, status, current_stage,
                        confirmed_context_json, created_at, updated_at
                    ) VALUES (
                        :id, :project_id, :owner_id, :title, :workflow_type, :status, :current_stage,
                        :confirmed_context_json, :created_at, :updated_at
                    )
                    """
                ),
                self._session_params(session),
            )
        return session

    def list_sessions(self, owner_id: str | None = None) -> list[ConversationSession]:
        with self.engine.begin() as conn:
            if owner_id:
                owner = _owner_id(owner_id)
                if owner == _owner_id():
                    rows = conn.execute(
                        self._text(
                            """
                            SELECT * FROM conversation_sessions
                            WHERE owner_id = :owner_id OR owner_id = ''
                            ORDER BY updated_at DESC
                            """
                        ),
                        {"owner_id": owner},
                    ).mappings().all()
                else:
                    rows = conn.execute(
                        self._text(
                            "SELECT * FROM conversation_sessions WHERE owner_id = :owner_id ORDER BY updated_at DESC"
                        ),
                        {"owner_id": owner},
                    ).mappings().all()
            else:
                rows = conn.execute(
                    self._text("SELECT * FROM conversation_sessions ORDER BY updated_at DESC")
                ).mappings().all()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> ConversationSession:
        with self.engine.begin() as conn:
            row = conn.execute(
                self._text("SELECT * FROM conversation_sessions WHERE id = :id"),
                {"id": session_id},
            ).mappings().first()
        if row is None:
            raise KeyError(f"Conversation not found: {session_id}")
        return self._session_from_row(row)

    def delete_session(self, session_id: str) -> ConversationSession:
        session = self.get_session(session_id)
        with self.engine.begin() as conn:
            conn.execute(self._text("DELETE FROM conversation_messages WHERE session_id = :session_id"), {"session_id": session_id})
            conn.execute(self._text("DELETE FROM conversation_review_gates WHERE session_id = :session_id"), {"session_id": session_id})
            result = conn.execute(self._text("DELETE FROM conversation_sessions WHERE id = :id"), {"id": session_id})
            if result.rowcount == 0:
                raise KeyError(f"Conversation not found: {session_id}")
        return session

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        workflow_type: ConversationWorkflowType | None = None,
        current_stage: str | None = None,
        status: str | None = None,
        confirmed_context_patch: dict[str, Any] | None = None,
    ) -> ConversationSession:
        session = self.get_session(session_id)
        if title is not None and title.strip():
            session.title = title.strip()
        if workflow_type is not None:
            session.workflow_type = workflow_type
        if current_stage is not None:
            session.current_stage = current_stage
        if status is not None:
            session.status = status  # type: ignore[assignment]
        if confirmed_context_patch:
            session.confirmed_context.update(confirmed_context_patch)
        session.updated_at = datetime.now(UTC)
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE conversation_sessions
                    SET title = :title,
                        workflow_type = :workflow_type,
                        status = :status,
                        current_stage = :current_stage,
                        confirmed_context_json = :confirmed_context_json,
                        updated_at = :updated_at
                    WHERE id = :id
                    """
                ),
                self._session_params(session),
            )
        return session

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        message_type: str = "text",
        content: str = "",
        payload: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        self.get_session(session_id)
        message = ConversationMessage(
            session_id=session_id,
            role=role,  # type: ignore[arg-type]
            message_type=message_type,  # type: ignore[arg-type]
            content=content,
            payload=payload or {},
        )
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    INSERT INTO conversation_messages (
                        id, session_id, role, message_type, content, payload_json, created_at
                    ) VALUES (
                        :id, :session_id, :role, :message_type, :content, :payload_json, :created_at
                    )
                    """
                ),
                {
                    "id": message.id,
                    "session_id": message.session_id,
                    "role": message.role,
                    "message_type": message.message_type,
                    "content": message.content,
                    "payload_json": json.dumps(message.payload, ensure_ascii=False, default=str),
                    "created_at": message.created_at.isoformat(),
                },
            )
        self.touch(session_id)
        return message

    def list_messages(self, session_id: str) -> list[ConversationMessage]:
        self.get_session(session_id)
        with self.engine.begin() as conn:
            rows = conn.execute(
                self._text(
                    "SELECT * FROM conversation_messages WHERE session_id = :session_id ORDER BY created_at"
                ),
                {"session_id": session_id},
            ).mappings().all()
        return [self._message_from_row(row) for row in rows]

    def create_review_gate(
        self,
        *,
        session_id: str,
        gate_type: str,
        title: str,
        payload: dict[str, Any],
        summary: str = "",
        next_step_on_approve: str = "",
        created_by_agent: str = "",
        allowed_actions: list[ConversationReviewAction] | None = None,
    ) -> ConversationReviewGate:
        self.get_session(session_id)
        now = datetime.now(UTC).isoformat()
        gate = ConversationReviewGate(
            session_id=session_id,
            type=gate_type,
            title=title,
            summary=summary,
            payload=payload,
            next_step_on_approve=next_step_on_approve,
            created_by_agent=created_by_agent,
            allowed_actions=allowed_actions or ["approve", "edit", "reject"],
        )
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE conversation_review_gates
                    SET status = 'needs_more_info', resolved_at = :resolved_at
                    WHERE session_id = :session_id AND status = 'pending'
                    """
                ),
                {"session_id": session_id, "resolved_at": now},
            )
            conn.execute(
                self._text(
                    """
                    INSERT INTO conversation_review_gates (
                        id, session_id, type, title, summary, payload_json, status,
                        allowed_actions_json, next_step_on_approve, created_by_agent,
                        created_at, resolved_at
                    ) VALUES (
                        :id, :session_id, :type, :title, :summary, :payload_json, :status,
                        :allowed_actions_json, :next_step_on_approve, :created_by_agent,
                        :created_at, :resolved_at
                    )
                    """
                ),
                self._gate_params(gate),
            )
        self.touch(session_id)
        return gate

    def get_review_gate(self, session_id: str, gate_id: str) -> ConversationReviewGate:
        self.get_session(session_id)
        with self.engine.begin() as conn:
            row = conn.execute(
                self._text(
                    "SELECT * FROM conversation_review_gates WHERE session_id = :session_id AND id = :id"
                ),
                {"session_id": session_id, "id": gate_id},
            ).mappings().first()
        if row is None:
            raise KeyError(f"Review gate not found: {gate_id}")
        return self._gate_from_row(row)

    def update_review_gate_payload(
        self,
        *,
        session_id: str,
        gate_id: str,
        payload: dict[str, Any],
        title: str | None = None,
        summary: str | None = None,
    ) -> ConversationReviewGate:
        gate = self.get_review_gate(session_id, gate_id)
        gate.payload = payload
        if title is not None:
            gate.title = title
        if summary is not None:
            gate.summary = summary
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE conversation_review_gates
                    SET title = :title, summary = :summary, payload_json = :payload_json
                    WHERE session_id = :session_id AND id = :id
                    """
                ),
                {
                    "session_id": session_id,
                    "id": gate_id,
                    "title": gate.title,
                    "summary": gate.summary,
                    "payload_json": json.dumps(gate.payload, ensure_ascii=False, default=str),
                },
            )
        self.touch(session_id)
        return gate

    def resolve_review_gate(
        self,
        *,
        session_id: str,
        gate_id: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> ConversationReviewGate:
        gate = self.get_review_gate(session_id, gate_id)
        gate.status = status  # type: ignore[assignment]
        if payload is not None:
            gate.payload = payload
        gate.resolved_at = datetime.now(UTC)
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE conversation_review_gates
                    SET status = :status, payload_json = :payload_json, resolved_at = :resolved_at
                    WHERE session_id = :session_id AND id = :id
                    """
                ),
                {
                    "session_id": session_id,
                    "id": gate_id,
                    "status": gate.status,
                    "payload_json": json.dumps(gate.payload, ensure_ascii=False, default=str),
                    "resolved_at": gate.resolved_at.isoformat() if gate.resolved_at else None,
                },
            )
        self.touch(session_id)
        return gate

    def list_review_gates(self, session_id: str) -> list[ConversationReviewGate]:
        self.get_session(session_id)
        with self.engine.begin() as conn:
            rows = conn.execute(
                self._text(
                    "SELECT * FROM conversation_review_gates WHERE session_id = :session_id ORDER BY created_at"
                ),
                {"session_id": session_id},
            ).mappings().all()
        return [self._gate_from_row(row) for row in rows]

    def pending_review_gate(self, session_id: str) -> ConversationReviewGate | None:
        gates = [gate for gate in self.list_review_gates(session_id) if gate.status == "pending"]
        return gates[-1] if gates else None

    def touch(self, session_id: str) -> None:
        updated_at = datetime.now(UTC).isoformat()
        with self.engine.begin() as conn:
            result = conn.execute(
                self._text("UPDATE conversation_sessions SET updated_at = :updated_at WHERE id = :id"),
                {"id": session_id, "updated_at": updated_at},
            )
            if result.rowcount == 0:
                raise KeyError(f"Conversation not found: {session_id}")

    @staticmethod
    def _session_params(session: ConversationSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "project_id": session.project_id,
            "owner_id": _owner_id(session.owner_id),
            "title": session.title,
            "workflow_type": session.workflow_type,
            "status": session.status,
            "current_stage": session.current_stage,
            "confirmed_context_json": json.dumps(session.confirmed_context, ensure_ascii=False, default=str),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }

    @staticmethod
    def _gate_params(gate: ConversationReviewGate) -> dict[str, Any]:
        return {
            "id": gate.id,
            "session_id": gate.session_id,
            "type": gate.type,
            "title": gate.title,
            "summary": gate.summary,
            "payload_json": json.dumps(gate.payload, ensure_ascii=False, default=str),
            "status": gate.status,
            "allowed_actions_json": json.dumps(gate.allowed_actions, ensure_ascii=False),
            "next_step_on_approve": gate.next_step_on_approve,
            "created_by_agent": gate.created_by_agent,
            "created_at": gate.created_at.isoformat(),
            "resolved_at": gate.resolved_at.isoformat() if gate.resolved_at else None,
        }

    @staticmethod
    def _session_from_row(row: Any) -> ConversationSession:
        return ConversationSession(
            id=row["id"],
            project_id=row["project_id"],
            owner_id=_owner_id(str(row.get("owner_id") or "")),
            title=row["title"],
            workflow_type=row["workflow_type"],
            status=row["status"],
            current_stage=row["current_stage"],
            confirmed_context=json.loads(row["confirmed_context_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _ensure_column(self, conn, table: str, column: str, definition: str) -> None:
        try:
            conn.execute(self._text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
        except Exception:
            return

    @staticmethod
    def _message_from_row(row: Any) -> ConversationMessage:
        return ConversationMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            message_type=row["message_type"],
            content=row["content"],
            payload=json.loads(row["payload_json"] or "{}"),
            created_at=row["created_at"],
        )

    @staticmethod
    def _gate_from_row(row: Any) -> ConversationReviewGate:
        return ConversationReviewGate(
            id=row["id"],
            session_id=row["session_id"],
            type=row["type"],
            title=row["title"],
            summary=row["summary"],
            payload=json.loads(row["payload_json"] or "{}"),
            status=row["status"],
            allowed_actions=json.loads(row["allowed_actions_json"] or "[]"),
            next_step_on_approve=row["next_step_on_approve"],
            created_by_agent=row["created_by_agent"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )


def create_conversation_store() -> InMemoryConversationStore | SqlConversationStore:
    settings = get_settings()
    if project_store_uses_sql(settings):
        store = SqlConversationStore(resolved_project_database_url(settings))
    else:
        store = InMemoryConversationStore()
    if hasattr(store, "setup"):
        store.setup()
    return store


conversation_store = create_conversation_store()
