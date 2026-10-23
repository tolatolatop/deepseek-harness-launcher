"""Authenticated public routes and private addresses for optional runtime services."""

from urllib.parse import urlencode

from sqlmodel import Session

from launcher.models import RuntimeEndpoint
from launcher.runtime_types import RuntimeHandle
from launcher.settings import settings


def desktop_prefix(workspace_id: str) -> str:
    return f"{settings().base_path}/w/{workspace_id}/desktop/"


def desktop_url(workspace_id: str) -> str:
    prefix = desktop_prefix(workspace_id)
    query = urlencode(
        {
            "autoconnect": "1",
            "resize": "scale",
            "view_only": "0",
            "reconnect": "1",
            "path": f"{prefix}websockify",
        }
    )
    return f"{prefix}vnc.html?{query}"


def save_runtime_endpoints(db: Session, workspace_id: str, handle: RuntimeHandle | None = None):
    current = db.get(RuntimeEndpoint, (workspace_id, "desktop"))
    if handle and handle.desktop_address:
        db.merge(
            RuntimeEndpoint(
                workspace_id=workspace_id,
                address=handle.desktop_address,
                config_version=handle.config_version,
            )
        )
    elif current:
        db.delete(current)
