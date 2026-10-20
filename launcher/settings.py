import ipaddress
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LAUNCHER_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://launcher@127.0.0.1:55432/launcher"
    public_url: str = "http://localhost:8080/launcher"
    data_root: Path = Path("/tmp/dsh-launcher")
    secret_key: str = ""
    bootstrap_username: str = "admin"
    bootstrap_password: str = ""
    runtime_image: str = "runzhliu/deepseek-harness:0.2.1-rc.2-r1"
    docker_prefix: str = "dsh-launcher"
    runtime_subnet_pool: str = "10.203.0.0/16"
    control_containers: str = ""
    max_running: int = 8
    min_free_disk_mb: int = 1024
    startup_timeout: int = 120
    session_hours: int = 12
    upload_limit_mb: int = 32
    frontend_dir: Path = Path("web/dist")

    @model_validator(mode="after")
    def validate_settings(self):
        parsed = urlsplit(self.public_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("LAUNCHER_PUBLIC_URL must be an http(s) URL without query or fragment")
        if parsed.username or parsed.password:
            raise ValueError("PUBLIC_URL must not contain credentials")
        self.public_url = self.public_url.rstrip("/")
        self.data_root = self.data_root.resolve()
        network = ipaddress.ip_network(self.runtime_subnet_pool)
        if network.version != 4 or network.prefixlen < 16 or network.prefixlen > 28:
            raise ValueError("LAUNCHER_RUNTIME_SUBNET_POOL must be an IPv4 /16 through /28")
        return self

    @property
    def base_path(self) -> str:
        return urlsplit(self.public_url).path

    @property
    def origin(self) -> str:
        p = urlsplit(self.public_url)
        return f"{p.scheme}://{p.netloc}"

    @property
    def secure_cookie(self) -> bool:
        return self.public_url.startswith("https:")


@lru_cache
def settings() -> Settings:
    return Settings()
