#!/usr/bin/env python3
"""Run the backend suite against a disposable PostgreSQL 17 container."""

import os
import secrets
import subprocess
import sys
import time
import uuid

import docker
import psycopg

client = docker.from_env()
password = secrets.token_urlsafe(24)
container = None
try:
    container = client.containers.run(
        "postgres:17-alpine",
        name=f"launcher-postgres-test-{uuid.uuid4().hex[:10]}",
        environment={"POSTGRES_PASSWORD": password, "POSTGRES_USER": "launcher", "POSTGRES_DB": "postgres"},
        ports={"5432/tcp": ("127.0.0.1", None)},
        network="bridge",
        detach=True,
    )
    container.reload()
    port = int(container.attrs["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
    deadline = time.monotonic() + 150
    while True:
        try:
            connection = psycopg.connect(
                host="127.0.0.1",
                port=port,
                user="launcher",
                password=password,
                dbname="postgres",
                connect_timeout=2,
            )
            connection.close()
            break
        except psycopg.Error:
            if time.monotonic() > deadline:
                raise RuntimeError("Temporary PostgreSQL did not become ready in 150 seconds") from None
            time.sleep(1)
    env = dict(os.environ)
    env["LAUNCHER_TEST_POSTGRES_URL"] = f"postgresql+psycopg://launcher:{password}@127.0.0.1:{port}"
    print("Running backend tests against isolated PostgreSQL 17", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *sys.argv[1:]], env=env)
    raise SystemExit(result.returncode)
finally:
    if container:
        container.remove(force=True, v=True)
    client.close()
