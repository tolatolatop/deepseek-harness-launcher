from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import text
from sqlmodel import Session, select

from launcher.db import engine
from launcher.models import LoginSession, Operation, Platform, User, WorkerHeartbeat, Workspace, now
from launcher.security import decrypt
from launcher.services import enqueue
from tests.conftest import configured_workspace, login, member


def test_unicode_long_config_and_timestamp_precision(client):
    ws = configured_workspace(client)
    instructions = "团队偏好🚀" * 1600
    token = "x" * 4096
    response = client.put(
        f"/launcher/api/workspaces/{ws['id']}/config",
        json={
            "model": "team-model",
            "api_token": token,
            "instructions": instructions,
            "expected_version": ws["config_version"],
        },
    )
    assert response.status_code == 200, response.text
    timestamp = now()
    with Session(engine()) as db:
        db.add(WorkerHeartbeat(updated_at=timestamp))
        db.commit()
    with Session(engine()) as db:
        stored = db.get(Workspace, ws["id"])
        assert stored.instructions == instructions
        assert decrypt(stored.token_encrypted) == token
        assert abs(db.get(WorkerHeartbeat, "primary").updated_at - timestamp) < 0.001
        assert db.exec(select(LoginSession)).first().expires_at > timestamp
        if engine().dialect.name == "mysql":
            assert db.exec(text("SELECT @@transaction_isolation")).scalar_one() == "READ-COMMITTED"
            assert db.exec(text("SELECT @@character_set_connection")).scalar_one() == "utf8mb4"


def test_username_case_remains_significant(client):
    member(client, "Alice")
    response = client.post(
        "/launcher/api/auth/login",
        json={"username": "alice", "password": "member-password-long"},
    )
    assert response.status_code == 401
    login(client, "Alice", "member-password-long")


def test_parallel_different_workspaces_respect_global_limit(client):
    alice = member(client)
    first = configured_workspace(client)
    admin = login(client)
    login(client, "alice", "member-password-long")
    second = client.post("/launcher/api/workspaces", json={}).json()
    response = client.put(
        f"/launcher/api/workspaces/{second['id']}/config",
        json={"model": "team-model", "api_token": "test", "expected_version": second["config_version"]},
    )
    assert response.status_code == 200
    barrier = Barrier(2)

    def start(ids):
        from fastapi import HTTPException

        with Session(engine()) as db:
            user = db.get(User, ids[0])
            ws = db.get(Workspace, ids[1])
            # Establish a read snapshot before either admission transaction commits.
            db.get(Platform, 1)
            barrier.wait(timeout=10)
            try:
                enqueue(db, user, ws, "start")
                return 202
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(start, [(admin["id"], first["id"]), (alice["id"], second["id"])]))
    assert sorted(statuses) == [202, 409]
    with Session(engine()) as db:
        assert len(db.exec(select(Operation)).all()) == 1
