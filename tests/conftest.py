import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from launcher import db
from launcher.settings import settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LAUNCHER_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
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
