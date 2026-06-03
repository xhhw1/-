from __future__ import annotations

from datetime import UTC, datetime
import base64
import hashlib
import hmac
from pathlib import Path
import secrets
from typing import Any, Protocol

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AuthUser, AuthUserCreateRequest, AuthUserRecord, AuthUserUpdateRequest
from ai_visual_agent.services.persistence_config import (
    project_store_uses_sql,
    resolved_project_database_url,
)


PBKDF2_ITERATIONS = 210_000


class UserStore(Protocol):
    def setup(self) -> None: ...

    def bootstrap_admin(self) -> AuthUserRecord | None: ...

    def create(self, request: AuthUserCreateRequest) -> AuthUserRecord: ...

    def get(self, user_id: str) -> AuthUserRecord: ...

    def get_by_email(self, email: str) -> AuthUserRecord: ...

    def list(self) -> list[AuthUserRecord]: ...

    def update(self, user_id: str, request: AuthUserUpdateRequest) -> AuthUserRecord: ...


class InMemoryUserStore:
    def __init__(self) -> None:
        self._users: dict[str, AuthUserRecord] = {}

    def setup(self) -> None:
        self.bootstrap_admin()

    def bootstrap_admin(self) -> AuthUserRecord | None:
        settings = get_settings()
        email = _normalize_email(settings.admin_email)
        existing = self._users.get(email)
        if existing:
            return existing
        if not settings.admin_password:
            return None
        record = _record_from_create(
            AuthUserCreateRequest(email=settings.admin_email, password=settings.admin_password, role="admin")
        )
        self._users[record.id] = record
        return record

    def create(self, request: AuthUserCreateRequest) -> AuthUserRecord:
        record = _record_from_create(request)
        if record.id in self._users:
            raise ValueError(f"User already exists: {record.email}")
        self._users[record.id] = record
        return record

    def get(self, user_id: str) -> AuthUserRecord:
        normalized = _normalize_email(user_id)
        try:
            return self._users[normalized]
        except KeyError as exc:
            raise KeyError(f"User not found: {user_id}") from exc

    def get_by_email(self, email: str) -> AuthUserRecord:
        return self.get(email)

    def list(self) -> list[AuthUserRecord]:
        return sorted(self._users.values(), key=lambda item: item.created_at, reverse=True)

    def update(self, user_id: str, request: AuthUserUpdateRequest) -> AuthUserRecord:
        record = self.get(user_id)
        if request.password:
            record.password_hash = hash_password(request.password)
        if request.role is not None:
            record.role = request.role
        if request.status is not None:
            record.status = request.status
        record.updated_at = datetime.now(UTC)
        self._users[record.id] = record
        return record


class SqlUserStore:
    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import create_engine, text
            from sqlalchemy.engine import Engine
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("SQLAlchemy is required for SqlUserStore.") from exc

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
                    CREATE TABLE IF NOT EXISTS auth_users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(self._text("CREATE INDEX IF NOT EXISTS idx_auth_users_email ON auth_users(email)"))
        self.bootstrap_admin()

    def bootstrap_admin(self) -> AuthUserRecord | None:
        settings = get_settings()
        email = _normalize_email(settings.admin_email)
        try:
            return self.get_by_email(email)
        except KeyError:
            pass
        if not settings.admin_password:
            return None
        try:
            return self.create(
                AuthUserCreateRequest(email=settings.admin_email, password=settings.admin_password, role="admin")
            )
        except ValueError:
            return self.get_by_email(email)

    def create(self, request: AuthUserCreateRequest) -> AuthUserRecord:
        record = _record_from_create(request)
        with self.engine.begin() as conn:
            try:
                conn.execute(
                    self._text(
                        """
                        INSERT INTO auth_users (
                            id, email, password_hash, role, status, created_at, updated_at
                        ) VALUES (
                            :id, :email, :password_hash, :role, :status, :created_at, :updated_at
                        )
                        """
                    ),
                    _params(record),
                )
            except Exception as exc:
                raise ValueError(f"User already exists: {record.email}") from exc
        return record

    def get(self, user_id: str) -> AuthUserRecord:
        with self.engine.begin() as conn:
            row = conn.execute(
                self._text("SELECT * FROM auth_users WHERE id = :id"),
                {"id": _normalize_email(user_id)},
            ).mappings().first()
        if not row:
            raise KeyError(f"User not found: {user_id}")
        return _record_from_row(row)

    def get_by_email(self, email: str) -> AuthUserRecord:
        with self.engine.begin() as conn:
            row = conn.execute(
                self._text("SELECT * FROM auth_users WHERE email = :email"),
                {"email": _normalize_email(email)},
            ).mappings().first()
        if not row:
            raise KeyError(f"User not found: {email}")
        return _record_from_row(row)

    def list(self) -> list[AuthUserRecord]:
        with self.engine.begin() as conn:
            rows = conn.execute(self._text("SELECT * FROM auth_users ORDER BY created_at DESC")).mappings().all()
        return [_record_from_row(row) for row in rows]

    def update(self, user_id: str, request: AuthUserUpdateRequest) -> AuthUserRecord:
        existing = self.get(user_id)
        password_hash = hash_password(request.password) if request.password else existing.password_hash
        role = request.role or existing.role
        status = request.status or existing.status
        updated_at = datetime.now(UTC)
        with self.engine.begin() as conn:
            conn.execute(
                self._text(
                    """
                    UPDATE auth_users
                    SET password_hash = :password_hash,
                        role = :role,
                        status = :status,
                        updated_at = :updated_at
                    WHERE id = :id
                    """
                ),
                {
                    "id": existing.id,
                    "password_hash": password_hash,
                    "role": role,
                    "status": status,
                    "updated_at": updated_at.isoformat(),
                },
            )
        return self.get(existing.id)


def public_user(record: AuthUserRecord) -> AuthUser:
    return AuthUser(id=record.id, email=record.email, role=record.role, status=record.status)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        scheme, iterations_raw, salt, expected = password_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
    except ValueError:
        return False
    actual = _pbkdf2(password=password, salt=salt, iterations=iterations)
    return hmac.compare_digest(actual, expected)


def hash_password(password: str) -> str:
    salt = secrets.token_urlsafe(18)
    digest = _pbkdf2(password=password, salt=salt, iterations=PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def create_user_store() -> UserStore:
    settings = get_settings()
    if project_store_uses_sql(settings):
        store: UserStore = SqlUserStore(resolved_project_database_url(settings))
    else:
        store = InMemoryUserStore()
    store.setup()
    return store


def _record_from_create(request: AuthUserCreateRequest) -> AuthUserRecord:
    email = _normalize_email(request.email)
    now = datetime.now(UTC)
    return AuthUserRecord(
        id=email,
        email=email,
        password_hash=hash_password(request.password),
        role=request.role,
        status="active",
        created_at=now,
        updated_at=now,
    )


def _record_from_row(row: Any) -> AuthUserRecord:
    return AuthUserRecord(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        role=row["role"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _params(record: AuthUserRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "email": record.email,
        "password_hash": record.password_hash,
        "role": record.role,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _pbkdf2(*, password: str, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


user_store = create_user_store()
