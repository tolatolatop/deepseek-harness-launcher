import pytest
from sqlmodel import Session, select

from launcher.db import engine, init_db
from launcher.models import AuditEvent, Operation, User, Workspace, WorkspaceRuntime, now
from launcher.runtime import RuntimeFailure, build_runtime_spec, workspace_root
from launcher.settings import settings
from launcher.worker import process_one, reconcile
from tests.conftest import login, member
from tests.test_worker import FakeRuntime

IMAGE = "runzhliu/deepseek-harness@sha256:" + "b" * 64


def template(client, version="1.0.0", group="deepseek-harness-0.2.1"):
    result = client.post(
        "/launcher/api/admin/templates",
        json={
            "slug": "python-dev",
            "name": "Python 环境",
            "version": version,
            "image": IMAGE,
            "spec": {
                "cpu": 1,
                "memory_mb": 1024,
                "environment": {"TZ": "Asia/Shanghai"},
                "compatibility_group": group,
                "startup_patches": ["/opt/team/python.yml"],
            },
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def publish(client, value):
    result = client.post(f"/launcher/api/admin/templates/{value['id']}/publish")
    assert result.status_code == 200


def test_template_publish_snapshot_and_retire(client):
    user = member(client)
    t = template(client)
    assert t["id"] not in str(client.get("/launcher/api/catalog").json())
    edited = {key: t[key] for key in ("slug", "name", "version", "description", "image", "spec")}
    edited["description"] = "发布前可编辑草稿"
    assert client.put(f"/launcher/api/admin/templates/{t['id']}", json=edited).status_code == 200
    publish(client, t)
    assert client.put(f"/launcher/api/admin/templates/{t['id']}", json=edited).status_code == 409
    login(client, user["username"], "member-password-long")
    assert client.get("/launcher/api/admin/templates").status_code == 403
    ws = client.post("/launcher/api/workspaces", json={"template_id": t["id"]}).json()
    assert ws["runtime"]["template_version"] == "1.0.0"
    assert ws["runtime"]["resources"] == {"cpu": 1, "memory_mb": 1024}
    login(client)
    assert (
        client.patch(f"/launcher/api/admin/templates/{t['id']}", json={"enabled": False}).status_code == 200
    )
    assert (
        client.patch(
            f"/launcher/api/admin/templates/{t['id']}", json={"enabled": True, "image": "changed"}
        ).status_code
        == 422
    )
    assert t["id"] not in str(client.get("/launcher/api/catalog").json())
    assert client.post("/launcher/api/workspaces", json={"template_id": t["id"]}).status_code == 409
    login(client, user["username"], "member-password-long")
    assert client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start").status_code == 202
    runtime = FakeRuntime()
    assert process_one(runtime, "test")
    spec = runtime.running[ws["id"]]
    assert spec.image_ref == IMAGE and spec.cpu == 1 and spec.memory_mb == 1024
    assert spec.environment["TZ"] == "Asia/Shanghai"
    assert spec.startup_patches == ("/opt/team/python.yml",)


@pytest.mark.parametrize(
    "change",
    [
        {"image": "image:latest"},
        {"spec": {"environment": {"LAUNCHER_MODEL_TOKEN": "override"}}},
        {"spec": {"startup_patches": ["/opt/../etc/secret.yml"]}},
        {"spec": {"contract_version": "unknown/v2"}},
        {"spec": {"environment": {"NODE_OPTIONS": "--require=/tmp/inject.js"}}},
    ],
)
def test_template_rejects_invalid_contract(client, change):
    login(client)
    result = client.post(
        "/launcher/api/admin/templates",
        json={
            "slug": "test",
            "name": "Test",
            "version": "1",
            "image": IMAGE,
            **change,
        },
    )
    assert result.status_code == 422


def test_explicit_upgrade_keeps_data_and_rejects_incompatible_group(client):
    login(client)
    first = template(client)
    publish(client, first)
    ws = client.post("/launcher/api/workspaces", json={"template_id": first["id"]}).json()
    project = workspace_root(ws["id"]) / "project" / "keep.txt"
    project.write_text("keep")
    runtime = FakeRuntime()
    client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start")
    process_one(runtime, "test")
    second = template(client, "2.0.0")
    publish(client, second)
    # Publishing a new version does not change existing workspaces.
    assert client.get("/launcher/api/workspace").json()["runtime"]["template_id"] == first["id"]
    result = client.post(
        f"/launcher/api/admin/workspaces/{ws['id']}/upgrade",
        json={
            "template_id": second["id"],
            "expected_version": ws["config_version"],
        },
    )
    assert result.status_code == 202, result.text
    process_one(runtime, "test")
    current = client.get("/launcher/api/workspace").json()
    assert current["runtime"]["applied_template"]["id"] == second["id"]
    assert current["config_version"] == current["applied_version"]
    assert project.read_text() == "keep"
    third = template(client, "3.0.0", "different-data-format")
    publish(client, third)
    assert (
        client.put(
            f"/launcher/api/workspaces/{ws['id']}/template",
            json={
                "template_id": third["id"],
                "expected_version": current["config_version"],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/launcher/api/admin/workspaces/{ws['id']}/upgrade",
            json={
                "template_id": first["id"],
                "expected_version": ws["config_version"],
            },
        ).status_code
        == 409
    )


def test_runtime_recovery_keeps_container_and_does_not_apply_pending_config(client):
    login(client)
    ws = client.post("/launcher/api/workspaces", json={}).json()
    client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start")
    runtime = FakeRuntime()
    process_one(runtime, "test")
    original = runtime.running[ws["id"]]
    with Session(engine()) as db:
        w = db.get(Workspace, ws["id"])
        w.runtime_address = w.runtime_cookie = ""
        w.config_version += 1
        db.add(w)
        db.commit()
    reconcile(runtime)
    result = client.get("/launcher/api/workspace").json()
    assert result["status"] == "running"
    assert result["applied_version"] == original.config_version < result["config_version"]
    assert runtime.running[ws["id"]] is original
    settings().runtime_image = "unrelated:new"
    with Session(engine()) as db:
        w = db.get(Workspace, ws["id"])
        spec = build_runtime_spec(db, w, db.get(User, w.user_id))
        assert spec.image.startswith("sha256:")


def test_expiry_queues_stop_and_preserves_files(client):
    login(client)
    ws = client.post("/launcher/api/workspaces", json={}).json()
    base = f"/launcher/api/workspaces/{ws['id']}"
    assert client.put(base + "/maintenance", json={"max_runtime_minutes": 15}).status_code == 200
    runtime = FakeRuntime()
    client.post(base + "/actions/start")
    process_one(runtime, "test")
    project = workspace_root(ws["id"]) / "project" / "saved.txt"
    project.write_text("data")
    with Session(engine()) as db:
        binding = db.get(WorkspaceRuntime, ws["id"])
        assert binding.stop_at > now()
        binding.stop_at = now() - 1
        db.add(binding)
        db.commit()
    reconcile(runtime)
    process_one(runtime, "test")
    result = client.get("/launcher/api/workspace").json()
    assert result["status"] == "stopped" and result["runtime"]["stop_at"] is None
    assert project.read_text() == "data"
    with Session(engine()) as db:
        assert db.exec(select(AuditEvent).where(AuditEvent.action == "workspace.expired")).one()


def test_batch_reports_each_result_and_respects_capacity(client):
    alice = member(client)
    first = client.post("/launcher/api/workspaces", json={}).json()
    login(client, alice["username"], "member-password-long")
    second = client.post("/launcher/api/workspaces", json={}).json()
    assert (
        client.post(
            "/launcher/api/admin/workspaces/actions",
            json={"workspace_ids": [second["id"]], "action": "start"},
        ).status_code
        == 403
    )
    login(client)
    result = client.post(
        "/launcher/api/admin/workspaces/actions",
        json={
            "workspace_ids": [first["id"], second["id"], "missing"],
            "action": "start",
        },
    ).json()["results"]
    assert [r["ok"] for r in result] == [True, False, False]
    with Session(engine()) as db:
        assert len(db.exec(select(Operation)).all()) == 1


def test_existing_workspace_backfill_is_repeatable(client):
    admin = login(client)
    ws = client.post("/launcher/api/workspaces", json={}).json()
    with Session(engine()) as db:
        db.delete(db.get(WorkspaceRuntime, ws["id"]))
        user = db.get(User, admin["id"])
        user.cpu = 4
        db.add(user)
        db.commit()
    init_db()
    init_db()
    result = client.get("/launcher/api/workspace").json()
    assert result["runtime"]["resources"]["cpu"] == 4
    assert result["id"] == ws["id"]


def test_recovery_failure_retries_without_replacing_container(client, monkeypatch):
    login(client)
    ws = client.post("/launcher/api/workspaces", json={}).json()
    client.post(f"/launcher/api/workspaces/{ws['id']}/actions/start")
    runtime = FakeRuntime()
    process_one(runtime, "test")
    original = runtime.running[ws["id"]]
    with Session(engine()) as db:
        binding = db.get(WorkspaceRuntime, ws["id"])
        binding.checked_at = 0
        db.add(binding)
        db.commit()

    def unavailable(*args):
        raise RuntimeFailure("代理网络正在恢复")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "recover", unavailable)
        reconcile(runtime)
    current = client.get("/launcher/api/workspace").json()
    assert current["status"] == "recovering" and current["desired_status"] == "running"
    assert runtime.running[ws["id"]] is original
    reconcile(runtime)
    assert client.get("/launcher/api/workspace").json()["status"] == "running"
    assert runtime.running[ws["id"]] is original
