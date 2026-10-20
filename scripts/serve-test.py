"""Isolated browser test API. No Docker worker and no live model calls."""

import os
import sys
import tempfile
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="launcher-browser-") as tmp:
    os.environ.update(
        LAUNCHER_DATABASE_URL=f"sqlite:///{tmp}/test.db",
        LAUNCHER_DATA_ROOT=tmp,
        LAUNCHER_SECRET_KEY=Fernet.generate_key().decode(),
        LAUNCHER_BOOTSTRAP_USERNAME="admin",
        LAUNCHER_BOOTSTRAP_PASSWORD="browser-test-password",
        LAUNCHER_PUBLIC_URL="http://localhost:18180/launcher",
        LAUNCHER_CONTROL_CONTAINERS="",
    )
    uvicorn.run("launcher.app:create_app", factory=True, host="127.0.0.1", port=18180, access_log=False)
