#!/usr/bin/env python3
"""Create local secrets without printing them or overwriting existing settings."""

import argparse
import base64
import os
import secrets
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=18080)
parser.add_argument("--public-url")
args = parser.parse_args()
path = Path(".env")
if path.exists():
    raise SystemExit(".env already exists; it has not been changed.")
root = Path("data").resolve()
root.mkdir(exist_ok=True)
content = f"""LAUNCHER_SECRET_KEY={base64.urlsafe_b64encode(os.urandom(32)).decode()}
LAUNCHER_BOOTSTRAP_USERNAME=admin
LAUNCHER_BOOTSTRAP_PASSWORD={secrets.token_urlsafe(20)}
LAUNCHER_PUBLIC_URL={args.public_url or f"http://localhost:{args.port}/launcher"}
LAUNCHER_DATA_ROOT={root}
LAUNCHER_DATABASE_URL=sqlite:///{root}/launcher.db
LAUNCHER_RUNTIME_IMAGE=runzhliu/deepseek-harness:0.2.1-rc.2-r1@sha256:ca8dd52ed949e3cbecc91a0c441ea627336ff23c68a640ca28db6e67a4337f73
LAUNCHER_DOCKER_PREFIX=dsh-launcher
LAUNCHER_CONTROL_CONTAINERS=
LAUNCHER_MAX_RUNNING=8
LAUNCHER_MIN_FREE_DISK_MB=1024
PORT={args.port}
POSTGRES_PASSWORD={secrets.token_urlsafe(24)}
"""
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(content)
print("Created .env (mode 0600). Initial administrator credentials are stored there.")
