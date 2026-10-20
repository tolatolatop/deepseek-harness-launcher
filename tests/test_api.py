import os

from sqlmodel import Session, select

from launcher.db import engine
from launcher.models import LoginSession, Workspace
from launcher.runtime import workspace_root
from tests.conftest import configured_workspace, login, member


def test_login_csrf_and_logout(client):
    assert client.get("/launcher/api/workspace").status_code == 401
    login(client)
    saved = client.headers.pop("X-CSRF-Token")
    assert client.post("/launcher/api/workspaces", json={}).status_code == 403
    client.headers["X-CSRF-Token"] = saved
    assert (
        client.post(
            "/launcher/api/workspaces", json={}, headers={"Origin": "http://evil.example"}
        ).status_code
        == 403
    )
    assert client.post("/launcher/api/auth/logout").status_code == 200
    assert client.get("/launcher/api/auth/me").status_code == 401


def test_wrong_login_and_rate_limit(client):
    for _ in range(10):
        assert (
            client.post(
                "/launcher/api/auth/login", json={"username": "admin", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post("/launcher/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code
        == 429
    )


def test_owner_only_even_for_admin(client):
    alice = member(client)
    login(client, "alice", "member-password-long")
    ws = client.post("/launcher/api/workspaces", json={}).json()
    assert client.get("/launcher/api/admin/users").status_code == 403
    assert client.post("/launcher/api/workspaces", json={}).json()["id"] == ws["id"]
    login(client)
    for path in (f"/launcher/api/workspaces/{ws['id']}/files", f"/launcher/w/{ws['id']}/"):
        assert client.get(path).status_code == 404
    assert client.patch(f"/launcher/api/admin/users/{alice['id']}", json={"active": False}).status_code == 200
    assert (
        client.post(
            "/launcher/api/auth/login", json={"username": "alice", "password": "member-password-long"}
        ).status_code
        == 401
    )


def test_disable_revokes_existing_session(client):
    alice = member(client)
    login(client, "alice", "member-password-long")
    token = client.cookies.get("launcher_session")
    login(client)
    client.patch(f"/launcher/api/admin/users/{alice['id']}", json={"active": False})
    assert (
        client.get("/launcher/api/auth/me", headers={"Cookie": f"launcher_session={token}"}).status_code
        == 401
    )
    with Session(engine()) as db:
        assert not db.exec(select(LoginSession).where(LoginSession.user_id == alice["id"])).all()


def test_config_optimistic_lock_and_no_credential_echo(client):
    ws = configured_workspace(client)
    assert ws["has_api_token"]
    assert "sk-secret" not in str(ws)
    assert "token_encrypted" not in ws
    bad = client.put(
        f"/launcher/api/workspaces/{ws['id']}/config",
        json={"model": "team-model", "expected_version": ws["config_version"] - 1},
    )
    assert bad.status_code == 409
    result = client.put(
        f"/launcher/api/workspaces/{ws['id']}/config",
        json={
            "model": "team-model",
            "expected_version": ws["config_version"],
            "plugins": ["arbitrary-package"],
        },
    )
    assert result.status_code == 400
    with Session(engine()) as db:
        encrypted = db.get(Workspace, ws["id"]).token_encrypted
        assert "sk-secret" not in encrypted and encrypted


def test_start_idempotency_and_mutual_exclusion(client):
    ws = configured_workspace(client)
    endpoint = f"/launcher/api/workspaces/{ws['id']}/actions/"
    first = client.post(endpoint + "start")
    assert first.status_code == 202, first.text
    again = client.post(endpoint + "start")
    assert again.json()["id"] == first.json()["id"]
    assert client.post(endpoint + "rebuild").status_code == 409


def test_admission_counts_starting_spaces(client):
    alice = member(client)
    ws = configured_workspace(client)
    assert client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start").status_code == 202
    login(client, alice["username"], "member-password-long")
    other = client.post("/launcher/api/workspaces", json={}).json()
    client.put(
        f"/launcher/api/workspaces/{other['id']}/config",
        json={
            "model": "team-model",
            "api_token": "key",
            "expected_version": other["config_version"],
        },
    )
    assert client.post(f"/launcher/api/workspaces/{other['id']}/actions/start").status_code == 409


def test_files_download_upload_and_no_overwrite(client):
    ws = configured_workspace(client)
    url = f"/launcher/api/workspaces/{ws['id']}"
    assert client.post(url + "/files", files={"file": ("report.csv", b"a,b\n1,2")}).status_code == 200
    assert client.post(url + "/files", files={"file": ("report.csv", b"overwrite")}).status_code == 409
    result = client.get(url + "/download", params={"path": "report.csv"})
    assert result.content == b"a,b\n1,2"
    assert "attachment" in result.headers["content-disposition"]
    assert "report.csv" in str(client.get(url + "/files").json())
    assert client.post(url + "/files", files={"file": ("../attack", b"x")}).status_code == 400


def test_traversal_symlinks_and_special_files(client, tmp_path):
    ws = configured_workspace(client)
    base = workspace_root(ws["id"]) / "project"
    secret = tmp_path / "outside"
    secret.write_text("secret")
    (base / "escape").symlink_to(secret)
    (base / "folder").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(base / "pipe")
    prefix = f"/launcher/api/workspaces/{ws['id']}"
    for path in ("../outside", "/etc/passwd", "escape", "folder/outside", "pipe"):
        assert client.get(prefix + "/download", params={"path": path}).status_code in (400, 404)
    assert client.get(prefix + "/files", params={"path": "folder"}).status_code == 404
    assert client.get(prefix + "/files").json()["entries"] == []


def test_password_change_revokes_sessions(client):
    login(client)
    assert (
        client.put(
            "/launcher/api/auth/password",
            json={
                "old_password": "a-test-password-very-long",
                "new_password": "a-new-test-password-long",
            },
        ).status_code
        == 200
    )
    assert client.get("/launcher/api/auth/me").status_code == 401
    login(client, password="a-new-test-password-long")


def test_gateway_rejects_embedded_credentials(client):
    login(client)
    assert (
        client.put(
            "/launcher/api/admin/platform",
            json={
                "gateway_url": "https://user:pass@host/v1",
                "models": ["x"],
            },
        ).status_code
        == 422
    )


def test_admin_cannot_disable_self(client):
    user = login(client)
    assert client.patch(f"/launcher/api/admin/users/{user['id']}", json={"active": False}).status_code == 400


def test_runtime_mount_redirect_requires_ownership(client):
    ws = configured_workspace(client)
    response = client.get(f"/launcher/w/{ws['id']}", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == f"/launcher/w/{ws['id']}/"


def test_root_mount_spa_has_no_redirect_loop(client, tmp_path):
    from fastapi.testclient import TestClient

    from launcher.app import create_app
    from launcher.settings import settings

    cfg = settings()
    cfg.public_url = "http://testserver"
    cfg.frontend_dir = tmp_path / "frontend"
    cfg.frontend_dir.mkdir()
    (cfg.frontend_dir / "index.html").write_text('<script>window.base="__LAUNCHER_BASE_JSON__";</script>')
    with TestClient(create_app()) as root:
        result = root.get("/", follow_redirects=False)
        assert result.status_code == 200
        assert 'window.base=""' in result.text
        assert root.get("/api/health").status_code == 200


def test_oversized_body_rejected_before_processing(client):
    result = client.post(
        "/launcher/api/auth/login",
        content=b"{}",
        headers={"Content-Length": str(100 * 1024**2), "Content-Type": "application/json"},
    )
    assert result.status_code == 413


def test_parallel_start_is_one_operation(client):
    from concurrent.futures import ThreadPoolExecutor

    ws = configured_workspace(client)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start"), range(4)
            )
        )
    assert all(r.status_code == 202 for r in results)
    assert len({r.json()["id"] for r in results}) == 1
