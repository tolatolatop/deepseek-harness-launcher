from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RuntimeSpec:
    workspace_id: str
    config_version: int
    image: str
    image_ref: str
    template_id: str
    template_version: str
    root: Path
    public_url: str
    cpu: float
    memory_mb: int
    tmp_mb: int
    startup_patches: tuple[str, ...]
    environment: dict[str, str] = field(repr=False)


@dataclass(frozen=True)
class RuntimeHandle:
    address: str
    encrypted_cookie: str = field(repr=False)
    image_id: str
    image_ref: str
    config_version: int
    template_id: str
    template_version: str
