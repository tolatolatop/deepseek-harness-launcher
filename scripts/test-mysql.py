#!/usr/bin/env python3
"""Run the backend suite against a disposable MySQL 8.4 container."""

import os
import secrets
import subprocess
import sys
import time
import uuid

import docker
import pymysql

client = docker.from_env()
password = secrets.token_urlsafe(24)
container = None
try:
    container = client.containers.run(
        "mysql:8.4",
        name=f"launcher-mysql-test-{uuid.uuid4().hex[:10]}",
        environment={"MYSQL_ROOT_PASSWORD": password, "MYSQL_ROOT_HOST": "%"},
        ports={"3306/tcp": ("127.0.0.1", None)},
        network="bridge",
        detach=True,
    )
    container.reload()
    port = int(container.attrs["NetworkSettings"]["Ports"]["3306/tcp"][0]["HostPort"])
    deadline = time.monotonic() + 150
    while True:
        try:
            connection = pymysql.connect(
                host="127.0.0.1", port=port, user="root", password=password, connect_timeout=2
            )
            connection.close()
            break
        except pymysql.Error:
            if time.monotonic() > deadline:
                raise RuntimeError("Temporary MySQL did not become ready in 150 seconds") from None
            time.sleep(1)
    env = dict(os.environ)
    env["LAUNCHER_TEST_MYSQL_URL"] = f"mysql+pymysql://root:{password}@127.0.0.1:{port}"
    print("Running backend tests against isolated MySQL 8.4", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", *sys.argv[1:]], env=env)
    raise SystemExit(result.returncode)
finally:
    if container:
        container.remove(force=True, v=True)
    client.close()
