import time
import uuid

from sqlalchemy import JSON, Column, Double, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def uid() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


class TableModel(SQLModel):
    __table_args__ = TABLE_OPTIONS


class User(TableModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True, max_length=32)
    username: str = Field(unique=True, index=True, max_length=64)
    display_name: str = Field(max_length=60)
    password_hash: str = Field(sa_type=Text)
    role: str = Field(default="member", max_length=16)
    active: bool = True
    cpu: float = 2
    memory_mb: int = 2048
    storage_mb: int = 10240
    created_at: float = Field(default_factory=now, sa_type=Double)


class LoginSession(TableModel, table=True):
    token_hash: str = Field(primary_key=True, max_length=64)
    user_id: str = Field(foreign_key="user.id", index=True, max_length=32)
    expires_at: float = Field(sa_type=Double)


class LoginAttempt(TableModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True, max_length=32)
    key: str = Field(index=True, max_length=64)
    created_at: float = Field(default_factory=now, index=True, sa_type=Double)


class Platform(TableModel, table=True):
    id: int = Field(default=1, primary_key=True)
    gateway_url: str = Field(default="", sa_type=Text)
    models: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    template_name: str = Field(default="标准开发环境", max_length=60)
    updated_at: float = Field(default_factory=now, sa_type=Double)


class Workspace(TableModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True, max_length=32)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True, max_length=32)
    name: str = Field(max_length=60)
    status: str = Field(default="stopped", max_length=16)
    desired_status: str = Field(default="stopped", max_length=16)
    error: str = Field(default="", sa_type=Text)
    runtime_address: str = Field(default="", sa_type=Text)
    runtime_cookie: str = Field(default="", sa_type=Text)
    model: str = Field(default="", max_length=120)
    token_encrypted: str = Field(default="", sa_type=Text)
    language: str = Field(default="中文", max_length=16)
    instructions: str = Field(default="", sa_type=Text)
    plugins: list[str] = Field(
        default_factory=lambda: ["session-titles"], sa_column=Column(JSON, nullable=False)
    )
    config_version: int = 1
    applied_version: int = 0
    created_at: float = Field(default_factory=now, sa_type=Double)
    updated_at: float = Field(default_factory=now, sa_type=Double)


class Operation(TableModel, table=True):
    __table_args__ = (UniqueConstraint("active_workspace_id"), TABLE_OPTIONS)
    id: str = Field(default_factory=uid, primary_key=True, max_length=32)
    workspace_id: str = Field(foreign_key="workspace.id", index=True, max_length=32)
    active_workspace_id: str | None = Field(default=None, nullable=True, max_length=32)
    actor_id: str = Field(max_length=32)
    action: str = Field(max_length=32)
    status: str = Field(default="queued", max_length=16)
    message: str = Field(default="等待执行", sa_type=Text)
    attempts: int = 0
    lease_until: float = Field(default=0, sa_type=Double)
    worker_id: str = Field(default="", sa_type=Text)
    created_at: float = Field(default_factory=now, sa_type=Double)
    finished_at: float | None = Field(default=None, sa_type=Double)


class AuditEvent(TableModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True, max_length=32)
    actor_id: str = Field(max_length=32)
    action: str = Field(max_length=64)
    target_id: str = Field(max_length=64)
    created_at: float = Field(default_factory=now, index=True, sa_type=Double)


class WorkerHeartbeat(TableModel, table=True):
    id: str = Field(default="primary", primary_key=True, max_length=32)
    updated_at: float = Field(default_factory=now, sa_type=Double)
