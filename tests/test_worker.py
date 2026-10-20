from sqlmodel import Session, select

from launcher.db import engine
from launcher.models import Operation, Workspace
from launcher.runtime import RuntimeFailure, render_config, workspace_root
from launcher.security import encrypt
from launcher.worker import process_one, reconcile
from tests.conftest import configured_workspace


class FakeRuntime:
    def __init__(self):
        self.running = {}
        self.fail = False

    def start(self, ws, user, platform, rebuild=False):
        if self.fail:
            raise RuntimeFailure("启动超时")
        render_config(ws, platform)
        self.running[ws.id] = True
        return "http://127.0.0.1:3080", encrypt("session=example")

    def stop(self, workspace_id):
        self.running.pop(workspace_id, None)

    def inspect(self, workspace_id):
        return "running" if workspace_id in self.running else "missing"


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
