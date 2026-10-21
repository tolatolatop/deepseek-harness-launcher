import pytest
import yaml
from sqlmodel import Session, select

from launcher.db import engine
from launcher.models import Operation, Platform, Workspace
from launcher.runtime import RuntimeFailure, workspace_root
from launcher.runtime_types import RuntimeHandle
from launcher.security import encrypt
from launcher.worker import process_one, reconcile
from tests.conftest import configured_workspace, login


class FakeRuntime:
    def __init__(self):
        self.running = {}
        self.fail = False

    def start(self, spec, rebuild=False):
        if self.fail:
            raise RuntimeFailure("启动超时")
        self.running[spec.workspace_id] = spec
        return self.recover(spec, "")

    def recover(self, spec, encrypted_cookie):
        actual = self.running[spec.workspace_id]
        return RuntimeHandle(
            "http://127.0.0.1:3080",
            encrypt("session=example"),
            "sha256:" + "a" * 64,
            actual.image_ref,
            actual.config_version,
            actual.template_id,
            actual.template_version,
        )

    def cleanup_orphans(self, workspace_ids):
        for workspace_id in set(self.running) - workspace_ids:
            self.stop(workspace_id)

    def stop(self, workspace_id):
        self.running.pop(workspace_id, None)

    def inspect(self, workspace_id):
        return "running" if workspace_id in self.running else "missing"


@pytest.mark.parametrize("setup", ["none", "catalog_only"])
def test_enter_without_model_credentials_then_configure(client, setup):
    login(client)
    if setup == "catalog_only":
        client.put(
            "/launcher/api/admin/platform",
            json={"gateway_url": "http://gateway.example/v1", "models": ["team-model"]},
        ).raise_for_status()
    ws = client.post("/launcher/api/workspaces", json={}).json()
    result = client.put(
        f"/launcher/api/workspaces/{ws['id']}/config",
        json={"instructions": "稍后配置模型🚀", "expected_version": ws["config_version"]},
    )
    assert result.status_code == 200, result.text
    runtime = FakeRuntime()
    endpoint = f"/launcher/api/workspaces/{ws['id']}"
    patch_file = workspace_root(ws["id"]) / "managed" / "launcher.patch.yml"
    for action in ("start", "restart", "rebuild"):
        assert client.post(endpoint + "/actions/" + action).status_code == 202
        assert process_one(runtime, "test")
        current = client.get("/launcher/api/workspace").json()
        assert current["status"] == "running" and not current["has_api_token"]
        patch = {p["id"]: p for p in yaml.safe_load(patch_file.read_text())}
        assert patch["llm-pi-ai"]["disabled"]
        assert patch["agent-default-model"]["disabled"]
        assert patch["session-title-llm"]["disabled"]
    client.put(
        "/launcher/api/admin/platform",
        json={"gateway_url": "http://gateway.example/v1", "models": ["team-model"]},
    ).raise_for_status()
    current = client.get("/launcher/api/workspace").json()
    result = client.put(
        endpoint + "/config",
        json={
            "model": "team-model",
            "api_token": "synthetic-test-token",
            "expected_version": current["config_version"],
        },
    )
    assert result.status_code == 200
    assert client.post(endpoint + "/actions/restart").status_code == 202
    assert process_one(runtime, "test")
    patch = {p["id"]: p for p in yaml.safe_load(patch_file.read_text())}
    assert not patch["llm-pi-ai"]["disabled"]
    assert not patch["agent-default-model"]["disabled"]
    assert not patch["session-title-llm"]["disabled"]
    with Session(engine()) as db:
        assert db.get(Platform, 1).models == ["team-model"]


def test_start_stop_rebuild_preserve_files(client):
    ws = configured_workspace(client)
    runtime = FakeRuntime()
    project = workspace_root(ws["id"]) / "project"
    (project / "keep.txt").write_text("keep me")
    for action in ("start", "stop", "start", "rebuild"):
        assert client.post(f"/launcher/api/workspaces/{ws['id']}/actions/{action}").status_code == 202
        assert process_one(runtime, "test")
        assert (project / "keep.txt").read_text() == "keep me"
    current = client.get("/launcher/api/workspace").json()
    assert current["status"] == "running"
    assert current["applied_version"] == current["config_version"]


def test_failed_start_releases_slot_and_allows_retry(client):
    ws = configured_workspace(client)
    runtime = FakeRuntime()
    runtime.fail = True
    client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start")
    process_one(runtime, "test")
    with Session(engine()) as db:
        w = db.get(Workspace, ws["id"])
        assert w.status == "error" and w.desired_status == "stopped"
        op = db.exec(select(Operation)).one()
        assert op.active_workspace_id is None and op.status == "failed"
    runtime.fail = False
    assert client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start").status_code == 202
    process_one(runtime, "test")
    assert client.get("/launcher/api/workspace").json()["status"] == "running"


def test_expired_lease_recovers_operation(client):
    ws = configured_workspace(client)
    client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start")
    with Session(engine()) as db:
        op = db.exec(select(Operation)).one()
        op.status = "running"
        op.lease_until = 1
        db.add(op)
        db.commit()
    assert process_one(FakeRuntime(), "replacement-worker")
    assert client.get("/launcher/api/workspace").json()["status"] == "running"


def test_reconcile_detects_crashed_runtime(client):
    ws = configured_workspace(client)
    runtime = FakeRuntime()
    client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start")
    process_one(runtime, "test")
    runtime.running.clear()
    reconcile(runtime)
    assert client.get("/launcher/api/workspace").json()["status"] == "error"
