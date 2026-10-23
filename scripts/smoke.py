#!/usr/bin/env python3
"""Real Docker / HTTP / persistence smoke test. Uses only synthetic gateway credentials.

Runs an isolated API and worker. Removes only its own containers and temp files.
The model gateway fixture validates wiring; it is not a real NEW API deployment.
"""

import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import docker
import httpx
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description="Isolated real Docker/Harness integration test")
    parser.add_argument(
        "--desktop-only",
        action="store_true",
        help="Test native UI and browser desktop without the model fixture",
    )
    args = parser.parse_args()
    suffix = secrets.token_hex(4)
    prefix = f"launcher-smoke-{suffix}"
    port = free_port()
    password = secrets.token_urlsafe(20)
    client = docker.from_env()
    repo_image = os.environ.get("LAUNCHER_RUNTIME_IMAGE", "runzhliu/deepseek-harness:0.2.1-rc.2-r1")
    client.images.get(repo_image)  # fail early without creating resources
    with tempfile.TemporaryDirectory(prefix="launcher-smoke-") as tmp:
        data = Path(tmp)
        data.chmod(0o755)
        env = dict(
            os.environ,
            LAUNCHER_DATABASE_URL=f"sqlite:///{tmp}/test.db",
            LAUNCHER_DATA_ROOT=tmp,
            LAUNCHER_SECRET_KEY=Fernet.generate_key().decode(),
            LAUNCHER_BOOTSTRAP_USERNAME="admin",
            LAUNCHER_BOOTSTRAP_PASSWORD=password,
            LAUNCHER_PUBLIC_URL=f"http://localhost:{port}/launcher",
            LAUNCHER_DOCKER_PREFIX=prefix,
            LAUNCHER_CONTROL_CONTAINERS="",
            LAUNCHER_RUNTIME_IMAGE=repo_image,
            LAUNCHER_MIN_FREE_DISK_MB="0",
        )
        processes = []
        http = httpx.Client(base_url=f"http://localhost:{port}", trust_env=False, timeout=30)

        gateway = None
        try:
            log = open(data / "services.log", "w")
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "launcher.app:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
            )
            for _ in range(100):
                try:
                    if http.get("/launcher/api/health").is_success:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("Isolated API did not start")
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-m", "launcher.worker"], cwd=ROOT, env=env, stdout=log, stderr=log
                )
            )
            auth = http.post("/launcher/api/auth/login", json={"username": "admin", "password": password})
            auth.raise_for_status()
            http.headers["X-CSRF-Token"] = auth.json()["csrf_token"]
            ws = http.post("/launcher/api/workspaces", json={"name": "Docker smoke"}).json()
            wid = ws["id"]
            http.post(
                f"/launcher/api/workspaces/{wid}/files",
                files={"file": ("persist.txt", b"persistent workspace content")},
            ).raise_for_status()

            def operation(action, template_id=None):
                if template_id:
                    current = http.get("/launcher/api/workspace").json()
                    response = http.post(
                        f"/launcher/api/admin/workspaces/{wid}/upgrade",
                        json={
                            "template_id": template_id,
                            "expected_version": current["config_version"],
                        },
                    )
                else:
                    response = http.post(f"/launcher/api/workspaces/{wid}/actions/{action}")
                response.raise_for_status()
                operation_id = response.json()["id"]
                deadline = time.monotonic() + 160
                while time.monotonic() < deadline:
                    operations = http.get(f"/launcher/api/workspaces/{wid}/operations").json()
                    op = next(o for o in operations if o["id"] == operation_id)
                    if op["status"] == "failed":
                        print((data / "services.log").read_text()[-3000:], flush=True)
                        # Preserve only sanitized diagnostic lines for a failed test.
                        c = client.containers.list(all=True, filters={"label": f"launcher.managed={prefix}"})
                        for container in c:
                            import re

                            raw = container.logs(tail=45).decode(errors="replace")
                            print(re.sub(r"token=[^\s]+", "token=[REDACTED]", raw))
                        raise RuntimeError(op["message"])
                    if op["status"] == "succeeded":
                        print(f"PASS: {action}", flush=True)
                        return
                    time.sleep(1)
                raise RuntimeError(f"{action} timed out")

            def native_browser(desktop=False):
                browser = subprocess.run(
                    ["node", "scripts/runtime-browser.mjs"],
                    cwd=ROOT,
                    input=json.dumps(
                        {
                            "origin": f"http://localhost:{port}",
                            "workspace": wid,
                            "session": http.cookies.get("launcher_session"),
                            "desktop": desktop,
                        }
                    ),
                    text=True,
                    capture_output=True,
                    timeout=40,
                )
                print(browser.stdout, end="", flush=True)
                assert browser.returncode == 0, browser.stderr[-3000:]

            if not args.desktop_only:
                operation("start")
                native_browser()
                print("PASS: enter real Harness without gateway, model or API token", flush=True)
                operation("stop")
            ws = http.get("/launcher/api/workspace").json()
            http.put(
                f"/launcher/api/workspaces/{wid}/config",
                json={
                    "expected_version": ws["config_version"],
                    "plugins": ["browser-desktop", "browser-use"],
                },
            ).raise_for_status()
            operation("start")
            desktop_ws = http.get("/launcher/api/workspace").json()
            assert desktop_ws["desktop_url"]
            assert http.get(desktop_ws["desktop_url"]).status_code == 200
            assert (
                httpx.get(f"http://localhost:{port}" + desktop_ws["desktop_url"], trust_env=False).status_code
                == 401
            )
            desktop_container = client.containers.get(f"{prefix}-{wid}")
            desktop_ports = desktop_container.attrs["NetworkSettings"]["Ports"]["6080/tcp"]
            assert desktop_ports and all(p["HostIp"] == "127.0.0.1" for p in desktop_ports)
            cdp_check = desktop_container.exec_run(
                [
                    "node",
                    "-e",
                    "fetch('http://127.0.0.1:9222/json/version').then(r=>r.json()).then(j=>{if(!j.webSocketDebuggerUrl)process.exit(1)}).catch(()=>process.exit(1))",
                ]
            )
            assert cdp_check.exit_code == 0, "Chromium CDP endpoint is not ready"
            native_browser(desktop=True)
            interaction = desktop_container.exec_run(
                [
                    "node",
                    "-e",
                    "fetch('http://127.0.0.1:9222/json/list').then(r=>r.json()).then(t=>{if(!t.some(p=>p.url.startsWith('data:text/html,') && p.title === 'Launcher browser desktop'))process.exit(1)}).catch(()=>process.exit(1))",
                ]
            )
            assert interaction.exit_code == 0, "VNC keyboard input did not reach Chromium"
            print("PASS: real Chromium, embedded noVNC desktop and authenticated RFB WebSocket", flush=True)
            operation("stop")
            assert http.get("/launcher/api/workspace").json()["desktop_url"] is None
            if args.desktop_only:
                return
            platform = http.put(
                "/launcher/api/admin/platform",
                json={
                    "gateway_url": f"http://{prefix}-gateway:8000/v1",
                    "models": ["smoke-model"],
                },
            )
            platform.raise_for_status()
            ws = http.get("/launcher/api/workspace").json()
            result = http.put(
                f"/launcher/api/workspaces/{wid}/config",
                json={
                    "model": "smoke-model",
                    "api_token": "synthetic-smoke-token",
                    "instructions": "Smoke test preferences",
                    "plugins": [],
                    "expected_version": ws["config_version"],
                },
            )
            result.raise_for_status()

            operation("start")
            page = http.get(f"/launcher/w/{wid}/")
            assert page.status_code == 200, page.text[:200]
            assert "text/html" in page.headers["content-type"]
            assert "set-cookie" not in page.headers
            assert "synthetic-smoke-token" not in page.text
            print("PASS: authenticated Harness subpath entry", flush=True)
            unauthenticated = httpx.get(f"http://localhost:{port}/launcher/w/{wid}/", trust_env=False)
            assert unauthenticated.status_code == 401
            container = client.containers.get(f"{prefix}-{wid}")
            gateway = client.containers.run(
                "python:3.12-slim",
                ["python", "-u", "/gateway.py"],
                name=f"{prefix}-gateway",
                detach=True,
                network=f"{prefix}-{wid}-net",
                user=container.attrs["Config"]["User"],
                read_only=True,
                cap_drop=["ALL"],
                volumes={str(ROOT / "scripts/fixtures/gateway.py"): {"bind": "/gateway.py", "mode": "ro"}},
                labels={"launcher.managed": prefix},
            )
            # Exercise actual Python and filesystem operations under the runtime UID.
            execution = container.exec_run(
                [
                    "python3",
                    "-c",
                    "from pathlib import Path; Path('/workspace/result.txt').write_text('executed in container')",
                ],
                user=container.attrs["Config"]["User"],
            )
            assert execution.exit_code == 0, "Container command failed"
            payload = container.exec_run(
                [
                    "node",
                    "-e",
                    "fetch('"
                    + f"http://{prefix}-gateway:8000/v1/models"
                    + "',{signal:AbortSignal.timeout(5000)}).then(r=>r.json()).then(d=>{if(d.data[0].id!=='smoke-model')process.exit(2)}).catch(()=>process.exit(3))",
                ]
            )
            assert payload.exit_code == 0, "Runtime cannot reach gateway fixture"
            print("PASS: container command and gateway connectivity", flush=True)
            # Headless uses the same provider adapter and persistent home as Web.
            model_run = container.exec_run(
                [
                    "timeout",
                    "45",
                    "dsh",
                    "headless",
                    "--patch",
                    "/run/launcher/launcher.patch.yml",
                    "Create /workspace/model-result.txt for the smoke test.",
                ]
            )
            if model_run.exit_code != 0:
                print(model_run.output.decode(errors="replace")[-3500:])
                print(gateway.logs(tail=5).decode(errors="replace"))
            assert model_run.exit_code == 0, "Harness model/tool run failed"
            assert (
                http.get(
                    f"/launcher/api/workspaces/{wid}/download", params={"path": "model-result.txt"}
                ).content
                == b"created via model tool call\n"
            )
            print("PASS: streaming model response and native tool call (synthetic gateway)", flush=True)
            native_browser()
            home = data / "workspaces" / wid / "home"
            sentinel = home / "persistence-smoke.txt"
            sentinel.write_text("persist home")
            before_id = container.id
            # Restart both control processes and remove cached connection data. The
            # worker must reattach/authenticate without replacing the runtime.
            for index in (0, 1):
                previous = processes[index]
                previous.terminate()
                previous.wait(timeout=15)
                processes[index] = subprocess.Popen(previous.args, cwd=ROOT, env=env, stdout=log, stderr=log)
                if index == 0:
                    for _ in range(80):
                        try:
                            if http.get("/launcher/api/health").is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.2)
            with sqlite3.connect(data / "test.db") as connection:
                connection.execute(
                    "UPDATE workspace SET runtime_address='', runtime_cookie='', status='recovering' WHERE id=?",
                    (wid,),
                )
            recovery_deadline = time.monotonic() + 40
            while time.monotonic() < recovery_deadline:
                current = http.get("/launcher/api/workspace").json()
                if current["status"] == "running" and http.get(f"/launcher/w/{wid}/").status_code == 200:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Worker failed to recover existing runtime")
            assert client.containers.get(f"{prefix}-{wid}").id == before_id
            print(
                "PASS: API/Worker restart and lost-session recovery preserve the running container",
                flush=True,
            )
            template = http.post(
                "/launcher/api/admin/templates",
                json={
                    "slug": "smoke-runtime",
                    "name": "Smoke custom environment",
                    "version": "2.0.0",
                    "image": client.images.get(repo_image).attrs["RepoDigests"][0],
                    "spec": {"cpu": 1, "memory_mb": 1024, "environment": {"WORKSPACE_PROFILE": "smoke"}},
                },
            )
            template.raise_for_status()
            template_id = template.json()["id"]
            http.post(f"/launcher/api/admin/templates/{template_id}/publish").raise_for_status()
            operation("upgrade", template_id)
            upgraded = client.containers.get(f"{prefix}-{wid}")
            assert upgraded.id != before_id
            assert upgraded.attrs["HostConfig"]["NanoCpus"] == 10**9
            assert "WORKSPACE_PROFILE=smoke" in upgraded.attrs["Config"]["Env"]
            current = http.get("/launcher/api/workspace").json()
            assert current["runtime"]["applied_template"]["id"] == template_id
            assert current["runtime"]["actual_image_id"] == upgraded.attrs["Image"]
            assert sentinel.read_text() == "persist home"
            print(
                "PASS: published template controls real image, resources, environment and persistent upgrade",
                flush=True,
            )
            operation("stop")
            assert http.get(f"/launcher/w/{wid}/").status_code == 409
            operation("start")
            operation("rebuild")
            assert client.containers.get(f"{prefix}-{wid}").id != before_id
            assert sentinel.read_text() == "persist home"
            assert (
                http.get(f"/launcher/api/workspaces/{wid}/download", params={"path": "persist.txt"}).content
                == b"persistent workspace content"
            )
            assert (
                http.get(f"/launcher/api/workspaces/{wid}/download", params={"path": "result.txt"}).content
                == b"executed in container"
            )
            print("PASS: project and Harness home survive stop/start/rebuild", flush=True)
            subnet = client.networks.get(f"{prefix}-{wid}-net").attrs["IPAM"]["Config"][0]["Subnet"]
            operation("stop")
            # A stopped workspace may have lost its container while leaving a network behind.
            stale = client.networks.create(
                f"{prefix}-{wid}-net",
                driver="bridge",
                labels={"launcher.managed": prefix},
                ipam=docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=subnet)]),
            )
            cleanup_deadline = time.monotonic() + 15
            while time.monotonic() < cleanup_deadline:
                if not client.networks.list(filters={"id": stale.id}):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Stopped workspace left a stale network")
            print("PASS: reconciliation removes residual networks without a container", flush=True)
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            http.close()
            for c in client.containers.list(all=True, filters={"label": f"launcher.managed={prefix}"}):
                c.remove(force=True)
            for network in client.networks.list(filters={"label": f"launcher.managed={prefix}"}):
                network.remove()
            if "log" in locals():
                log.close()


if __name__ == "__main__":
    main()
