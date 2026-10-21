import os
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from launcher import db
from launcher.settings import settings


@pytest.fixture
def database_url(tmp_path):
    mysql_url = os.environ.get("LAUNCHER_TEST_MYSQL_URL")
    postgres_url = os.environ.get("LAUNCHER_TEST_POSTGRES_URL")
    if mysql_url and postgres_url:
        raise ValueError("Choose one test database backend at a time")
    server_url = postgres_url or mysql_url
    if not server_url:
        yield f"sqlite:///{tmp_path}/test.db"
        return
    url = make_url(server_url)
    expected = "postgresql+psycopg" if postgres_url else "mysql+pymysql"
    if url.drivername != expected:
        raise ValueError(f"Test database URL must use {expected}")
    # Never clear a supplied database. Create and drop only our own random schema.
    name = "launcher_test_" + uuid.uuid4().hex
    admin = create_engine(
        url.set(database="postgres" if postgres_url else None), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as connection:
            statement = (
                f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0"
                if postgres_url
                else f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
            connection.execute(text(statement))
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(
                    f'DROP DATABASE IF EXISTS "{name}"'
                    if postgres_url
                    else f"DROP DATABASE IF EXISTS `{name}`"
                )
            )
        admin.dispose()


@pytest.fixture
def client(tmp_path, monkeypatch, database_url):
    monkeypatch.setenv("LAUNCHER_DATABASE_URL", database_url)
    monkeypatch.setenv("LAUNCHER_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("LAUNCHER_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LAUNCHER_BOOTSTRAP_PASSWORD", "a-test-password-very-long")
    monkeypatch.setenv("LAUNCHER_PUBLIC_URL", "http://testserver/launcher")
    monkeypatch.setenv("LAUNCHER_MAX_RUNNING", "1")
    monkeypatch.setenv("LAUNCHER_MIN_FREE_DISK_MB", "0")
    settings.cache_clear()
    if db._engine:
        db._engine.dispose()
    db._engine = None
    from launcher.app import create_app

    with TestClient(create_app()) as client:
        yield client
    db._engine.dispose()
    db._engine = None
    settings.cache_clear()


def login(client, username="admin", password="a-test-password-very-long"):
    result = client.post("/launcher/api/auth/login", json={"username": username, "password": password})
    assert result.status_code == 200, result.text
    assert result.json()["user"]["id"]
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result.json()["user"]


def member(client, username="alice"):
    login(client)
    response = client.post(
        "/launcher/api/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": "member-password-long",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def configured_workspace(client):
    login(client)
    r = client.put(
        "/launcher/api/admin/platform",
        json={
            "gateway_url": "http://gateway.example/v1",
            "models": ["team-model"],
        },
    )
    assert r.status_code == 200
    ws = client.post("/launcher/api/workspaces", json={"name": "Test"}).json()
    response = client.put(
        f"/launcher/api/workspaces/{ws['id']}/config",
        json={
            "model": "team-model",
            "api_token": "sk-secret-test-token",
            "expected_version": ws["config_version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()
