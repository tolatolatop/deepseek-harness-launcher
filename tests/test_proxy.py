import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from launcher.db import engine
from launcher.models import User, Workspace
from launcher.security import encrypt
from tests.conftest import configured_workspace


def ready(workspace_id, port):
    with Session(engine()) as db:
        ws = db.get(Workspace, workspace_id)
        ws.status = "running"
        ws.runtime_address = f"http://127.0.0.1:{port}"
        ws.runtime_cookie = encrypt("harness=server-only")
        db.add(ws)
        db.commit()


def test_proxy_injects_only_runtime_cookie_and_rewrites_redirect(client):
    ws = configured_workspace(client)
    observed = {}

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            observed.update(dict(self.headers))
            self.send_response(302 if self.path == "/redirect" else 200)
            self.send_header("Set-Cookie", "harness=must-not-leak; Path=/")
            if self.path == "/redirect":
                self.send_header("Location", "/destination")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"body")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ready(ws["id"], server.server_port)
    try:
        response = client.get(f"/launcher/w/{ws['id']}/")
        assert response.status_code == 200 and response.content == b"body"
        assert observed["Cookie"] == "harness=server-only"
        assert observed["Host"] == "testserver"
        assert "set-cookie" not in response.headers
        redirected = client.get(f"/launcher/w/{ws['id']}/redirect", follow_redirects=False)
        assert redirected.headers["location"] == f"/launcher/w/{ws['id']}/destination"
        assert client.get(f"/launcher/w/{ws['id']}/?token=forbidden").status_code == 400
        assert (
            client.post(f"/launcher/w/{ws['id']}/", headers={"Origin": "http://other.example"}).status_code
            == 403
        )
    finally:
        server.shutdown()
        server.server_close()


def test_websocket_echo_and_disable_disconnect(client):
    ws = configured_workspace(client)
    started = threading.Event()
    state = {}

    def server_thread():
        async def run():
            state["loop"] = asyncio.get_running_loop()
            state["stop"] = asyncio.Event()

            async def echo(socket):
                state["cookie"] = socket.request.headers.get("Cookie")
                state["host"] = socket.request.headers.get("Host")
                async for message in socket:
                    await socket.send(message)

            async with serve(echo, "127.0.0.1", 0) as server:
                state["port"] = server.sockets[0].getsockname()[1]
                started.set()
                await state["stop"].wait()

        asyncio.run(run())

    thread = threading.Thread(target=server_thread, daemon=True)
    thread.start()
    assert started.wait(5)
    ready(ws["id"], state["port"])
    try:
        with client.websocket_connect(
            f"/launcher/w/{ws['id']}/socket", headers={"Origin": "http://testserver"}
        ) as socket:
            socket.send_text("hello")
            assert socket.receive_text() == "hello"
            assert state["cookie"] == "harness=server-only"
            assert state["host"] == "testserver"
            with Session(engine()) as db:
                owner = db.get(User, ws["user_id"])
                owner.active = False
                db.add(owner)
                db.commit()
            with pytest.raises(WebSocketDisconnect):
                socket.receive_text()
    finally:
        state["loop"].call_soon_threadsafe(state["stop"].set)
        thread.join(5)
