import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from launcher.db import engine
from launcher.models import RuntimeEndpoint, User, Workspace
from launcher.runtime import workspace_root
from launcher.worker import process_one, reconcile
from tests.conftest import configured_workspace, login, member
from tests.test_proxy import ready
from tests.test_worker import FakeRuntime


def endpoint(workspace_id, port):
    ready(workspace_id, port)
    with Session(engine()) as db:
        ws = db.get(Workspace, workspace_id)
        db.merge(
            RuntimeEndpoint(
                workspace_id=workspace_id,
                address=f"http://127.0.0.1:{port}",
                config_version=ws.applied_version,
            )
        )
        db.commit()


def test_browser_plugins_and_endpoint_follow_applied_config(client):
    login(client)
    ws = client.post("/launcher/api/workspaces", json={}).json()
    base = f"/launcher/api/workspaces/{ws['id']}"
    assert ws["desktop_url"] is None
    assert (
        client.put(
            base + "/config", json={"expected_version": ws["config_version"], "plugins": ["browser-use"]}
        ).status_code
        == 422
    )
    ws = client.put(
        base + "/config",
        json={"expected_version": ws["config_version"], "plugins": ["browser-desktop", "browser-use"]},
    ).json()
    runtime = FakeRuntime()
    client.post(base + "/actions/start").raise_for_status()
    process_one(runtime, "desktop-test")
    ws = client.get("/launcher/api/workspace").json()
    assert ws["desktop_url"].startswith(f"/launcher/w/{ws['id']}/desktop/vnc.html?")
    assert parse_qs(urlsplit(ws["desktop_url"]).query)["path"] == [
        f"/launcher/w/{ws['id']}/desktop/websockify"
    ]
    spec = runtime.running[ws["id"]]
    assert spec.desktop_enabled and spec.browser_use_enabled
    assert spec.desktop_width == 1440 and spec.desktop_height == 900
    patch = {
        row["id"]: row
        for row in yaml.safe_load((workspace_root(ws["id"]) / "managed/launcher.patch.yml").read_text())
    }
    assert not patch["browser-desktop"]["disabled"]
    assert patch["browser-desktop"]["config"]["desktopPort"] == 80
    assert patch["browser-desktop"]["config"]["desktopPath"] == ws["desktop_url"]
    assert not patch["browser-use-playwright-mcp"]["disabled"]
    saved = client.put(
        base + "/config", json={"expected_version": ws["config_version"], "plugins": []}
    ).json()
    assert saved["desktop_url"] == ws["desktop_url"]  # pending change does not alter the running process
    reconcile(runtime)
    assert client.get("/launcher/api/workspace").json()["desktop_url"] == ws["desktop_url"]
    client.post(base + "/actions/restart").raise_for_status()
    process_one(runtime, "desktop-test")
    assert client.get("/launcher/api/workspace").json()["desktop_url"] is None
    with Session(engine()) as db:
        assert db.get(RuntimeEndpoint, (ws["id"], "desktop")) is None


def test_desktop_http_requires_owner_and_strips_credentials(client):
    member(client)
    ws = configured_workspace(client)
    observed = {}

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            observed.update(dict(self.headers))
            self.send_response(302 if self.path == "/redirect" else 200)
            self.send_header("Content-Length", "4")
            self.send_header("Set-Cookie", "untrusted=not-forwarded")
            if self.path == "/redirect":
                self.send_header("Location", "/vnc.html")
            self.end_headers()
            self.wfile.write(b"body")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint(ws["id"], server.server_port)
    path = f"/launcher/w/{ws['id']}/desktop/"
    try:
        response = client.get(path + "vnc.html", headers={"Authorization": "Bearer user-secret"})
        assert response.status_code == 200 and response.content == b"body"
        assert "Cookie" not in observed and "Authorization" not in observed
        assert "Transfer-Encoding" not in observed
        assert "set-cookie" not in response.headers
        assert response.headers["cache-control"] == "no-store"
        response = client.get(path + "redirect", follow_redirects=False)
        assert response.headers["location"] == path + "vnc.html"
        assert client.get(path + "vnc.html?token=forbidden").status_code == 400
        assert client.get(path + "vnc.html", headers={"Origin": "http://other.example"}).status_code == 403
        login(client, "alice", "member-password-long")
        assert client.get(path + "vnc.html").status_code == 404
        client.cookies.clear()
        assert client.get(path + "vnc.html").status_code == 401
        login(client)
        with Session(engine()) as db:
            current = db.get(RuntimeEndpoint, (ws["id"], "desktop"))
            current.config_version += 1
            db.add(current)
            db.commit()
        assert client.get(path + "vnc.html").status_code == 409
    finally:
        server.shutdown()
        server.server_close()


def test_desktop_websocket_relays_binary_and_revokes_access(client):
    ws = configured_workspace(client)
    started = threading.Event()
    state = {}

    def server_thread():
        async def run():
            state["loop"] = asyncio.get_running_loop()
            state["stop"] = asyncio.Event()

            async def echo(socket):
                state["cookie"] = socket.request.headers.get("Cookie")
                state["path"] = socket.request.path
                async for message in socket:
                    await socket.send(message)

            async with serve(echo, "127.0.0.1", 0, subprotocols=["binary"]) as server:
                state["port"] = server.sockets[0].getsockname()[1]
                started.set()
                await state["stop"].wait()

        asyncio.run(run())

    thread = threading.Thread(target=server_thread, daemon=True)
    thread.start()
    assert started.wait(5)
    endpoint(ws["id"], state["port"])
    path = f"/launcher/w/{ws['id']}/desktop/websockify"
    try:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers={"Origin": "http://other.example"}):
                pass
        with client.websocket_connect(
            path, headers={"Origin": "http://testserver"}, subprotocols=["binary"]
        ) as socket:
            socket.send_bytes(b"RFB 003.008\n")
            assert socket.receive_bytes() == b"RFB 003.008\n"
            assert state["cookie"] is None
            assert state["path"] == "/websockify"
            with Session(engine()) as db:
                owner = db.get(User, ws["user_id"])
                owner.active = False
                db.add(owner)
                db.commit()
            with pytest.raises(WebSocketDisconnect):
                socket.receive_bytes()
    finally:
        state["loop"].call_soon_threadsafe(state["stop"].set)
        thread.join(5)
