import time
import uuid

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def uid() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


class User(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    username: str = Field(unique=True, index=True)
    display_name: str
    password_hash: str
    role: str = "member"
    active: bool = True
    cpu: float = 2
    memory_mb: int = 2048
    storage_mb: int = 10240
    created_at: float = Field(default_factory=now)


class LoginSession(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    expires_at: float


class LoginAttempt(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    key: str = Field(index=True)
    created_at: float = Field(default_factory=now, index=True)


class Platform(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    gateway_url: str = ""
    models: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    template_name: str = "标准开发环境"
    updated_at: float = Field(default_factory=now)


class Workspace(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    name: str
    status: str = "stopped"
    desired_status: str = "stopped"
    error: str = ""
    runtime_address: str = ""
    runtime_cookie: str = ""
    model: str = ""
    token_encrypted: str = ""
    language: str = "中文"
    instructions: str = ""
    plugins: list[str] = Field(
        default_factory=lambda: ["session-titles"], sa_column=Column(JSON, nullable=False)
    )
    config_version: int = 1
    applied_version: int = 0
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)


class Operation(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("active_workspace_id"),)
    id: str = Field(default_factory=uid, primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    active_workspace_id: str | None = Field(default=None, nullable=True)
    actor_id: str
    action: str
    status: str = "queued"
    message: str = "等待执行"
    attempts: int = 0
    lease_until: float = 0
    worker_id: str = ""
    created_at: float = Field(default_factory=now)
    finished_at: float | None = None


class AuditEvent(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    actor_id: str
    action: str
    target_id: str
    created_at: float = Field(default_factory=now, index=True)


class WorkerHeartbeat(SQLModel, table=True):
    id: str = Field(default="primary", primary_key=True)
    updated_at: float = Field(default_factory=now)
