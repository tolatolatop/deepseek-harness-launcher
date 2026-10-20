from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,40}$")
    display_name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=12, max_length=256)
    cpu: float = Field(default=2, ge=0.5, le=16)
    memory_mb: int = Field(default=2048, ge=512, le=32768)
    storage_mb: int = Field(default=10240, ge=128, le=1048576)


class UserUpdate(BaseModel):
    active: bool | None = None
    cpu: float | None = Field(default=None, ge=0.5, le=16)
    memory_mb: int | None = Field(default=None, ge=512, le=32768)
    storage_mb: int | None = Field(default=None, ge=128, le=1048576)


class PasswordUpdate(BaseModel):
    old_password: str
    new_password: str = Field(min_length=12, max_length=256)


class PlatformUpdate(BaseModel):
    gateway_url: str = ""
    models: list[str] = Field(default_factory=list, max_length=50)
    template_name: str = Field(default="标准开发环境", min_length=1, max_length=60)

    @field_validator("gateway_url")
    @classmethod
    def validate_url(cls, value):
        value = value.strip().rstrip("/")
        if not value:
            return value
        u = urlsplit(value)
        if (
            u.scheme not in ("https", "http")
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise ValueError("请输入不含凭证、查询参数的 http(s) 网关地址")
        return value

    @field_validator("models")
    @classmethod
    def validate_models(cls, value):
        result = list(dict.fromkeys(m.strip() for m in value if m.strip()))
        if any(len(m) > 120 or any(c.isspace() for c in m) for m in result):
            raise ValueError("模型 ID 不能包含空白，长度不能超过 120")
        return result


class WorkspaceCreate(BaseModel):
    name: str = Field(default="我的工作空间", min_length=1, max_length=60)


class WorkspaceConfig(BaseModel):
    model: str = Field(default="", max_length=120)
    api_token: str | None = Field(default=None, max_length=4096)
    language: str = Field(default="中文", pattern=r"^(中文|English)$")
    instructions: str = Field(default="", max_length=8000)
    plugins: list[str] = Field(default_factory=lambda: ["session-titles"], max_length=10)
    expected_version: int
