"""Pinned adapter for runzhliu/deepseek-harness-docker 0.2.1-rc.2-r1."""

import hashlib
import ipaddress
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import docker
import httpx
import yaml
from docker.errors import NotFound

from launcher.models import Platform, User, Workspace
from launcher.security import decrypt, encrypt
from launcher.settings import settings

BASE_SOURCE = "https://github.com/runzhliu/deepseek-harness-docker"
BASE_COMMIT = "d482839b469562df36e450c6146ca7afea046a7e"


class RuntimeFailure(Exception):
    """Only controlled, credential-free messages may be returned to the UI."""


def workspace_root(workspace_id: str) -> Path:
    return settings().data_root / "workspaces" / workspace_id


def prepare_storage(workspace_id: str):
    root = workspace_root(workspace_id)
    for folder in ("project", "home", "managed"):
        path = root / folder
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.getuid() == 0:
            os.chown(path, 1000, 1000)
    return root


def atomic_write(path: Path, content: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    if os.getuid() == 0:
        os.chown(temporary, 1000, 1000)
    os.replace(temporary, path)


def render_config(ws: Workspace, platform: Platform):
    root = prepare_storage(ws.id)
    model_configured = bool(platform.gateway_url and ws.model in platform.models and ws.token_encrypted)
    patch = [
        {
            "id": "llm-pi-ai",
            "disabled": not model_configured,
            "config": {
                "providers": {
                    "team-gateway": {
                        "displayName": "团队 NEW API",
                        "api": "openai-completions",
                        "baseURL": platform.gateway_url,
                        "apiKeyEnv": "LAUNCHER_MODEL_TOKEN",
                        "models": [{"id": model} for model in platform.models],
                    }
                }
            },
        },
        {
            "id": "agent-default-model",
            "disabled": not model_configured,
            "config": {"provider": "team-gateway", "model": ws.model},
        },
        {
            "id": "session-title-llm",
            "disabled": not model_configured or "session-titles" not in ws.plugins,
        },
        # The launcher catalog controls the deployed composition. Native plugin
        # installation is deliberately unavailable in this managed template.
        {"id": "ui-plugin-manager", "disabled": True},
    ]
    atomic_write(root / "managed" / "launcher.patch.yml", yaml.safe_dump(patch, allow_unicode=True))
    atomic_write(
        root / "managed" / "AGENTS.md",
        f"# 用户偏好\n\n请优先使用 {ws.language} 回复。\n\n{ws.instructions}\n",
    )
    atomic_write(root / "managed" / "revision.json", json.dumps({"version": ws.config_version}))
    return root


class DockerRuntime:
    def __init__(self):
        self.cfg = settings()
        self.client = docker.from_env(timeout=20)

    def name(self, workspace_id: str) -> str:
        return f"{self.cfg.docker_prefix}-{workspace_id}"

    def identity(self) -> str:
        if self.cfg.control_containers or os.getuid() == 0:
            return "1000:1000"
        return f"{os.getuid()}:{os.getgid()}"

    def container(self, workspace_id: str):
        try:
            result = self.client.containers.get(self.name(workspace_id))
        except NotFound:
            return None
        if result.labels.get("launcher.managed") != self.cfg.docker_prefix:
            raise RuntimeFailure("同名容器不属于本平台，请管理员检查")
        return result

    def network(self, workspace_id: str):
        name = self.name(workspace_id) + "-net"
        try:
            network = self.client.networks.get(name)
            if network.attrs.get("Labels", {}).get("launcher.managed") != self.cfg.docker_prefix:
                raise RuntimeFailure("同名网络不属于本平台")
        except NotFound:
            used = []
            for existing in self.client.networks.list():
                for entry in existing.attrs.get("IPAM", {}).get("Config") or []:
                    if entry.get("Subnet"):
                        used.append(ipaddress.ip_network(entry["Subnet"]))
            pool = ipaddress.ip_network(self.cfg.runtime_subnet_pool)
            candidates = list(pool.subnets(new_prefix=28))
            start = int(hashlib.sha256(workspace_id.encode()).hexdigest()[:8], 16) % len(candidates)
            candidates = candidates[start:] + candidates[:start]
            subnet = next(
                (s for s in candidates if not any(n.version == s.version and n.overlaps(s) for n in used)),
                None,
            )
            if subnet is None:
                raise RuntimeFailure("实例网络地址池已耗尽，请管理员扩容")
            network = self.client.networks.create(
                name,
                driver="bridge",
                labels={"launcher.managed": self.cfg.docker_prefix},
                ipam=docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=str(subnet))]),
                options={"com.docker.network.bridge.enable_icc": "true"},
            )
        network.reload()
        attached = network.attrs.get("Containers", {})
        for name in self.cfg.control_containers.split(","):
            if name.strip():
                control = self.client.containers.get(name.strip())
                if control.id not in attached:
                    network.connect(control)
        return network

    def stop(self, workspace_id: str):
        container = self.container(workspace_id)
        if container:
            container.stop(timeout=15)
            container.remove()
        try:
            network = self.client.networks.get(self.name(workspace_id) + "-net")
            if network.attrs.get("Labels", {}).get("launcher.managed") != self.cfg.docker_prefix:
                return
            for container_id in list(network.attrs.get("Containers", {})):
                network.disconnect(container_id, force=True)
            network.remove()
        except NotFound:
            pass

    def inspect(self, workspace_id: str) -> str:
        container = self.container(workspace_id)
        return container.status if container else "missing"

    def start(self, ws: Workspace, user: User, platform: Platform, rebuild: bool = False):
        cfg = self.cfg
        cfg.data_root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(cfg.data_root).free < cfg.min_free_disk_mb * 1024**2:
            raise RuntimeFailure("宿主机可用磁盘空间不足")
        container = self.container(ws.id)
        if container and (
            rebuild
            or container.labels.get("launcher.config-version") != str(ws.config_version)
            or container.status != "running"
        ):
            self.stop(ws.id)
            container = None
        if not container:
            root = render_config(ws, platform)
            network = self.network(ws.id)
            try:
                self.client.images.get(cfg.runtime_image)
            except NotFound:
                raise RuntimeFailure(
                    "运行镜像尚未准备，请管理员执行 docker compose --profile images pull runtime-image"
                ) from None
            public_url = f"{cfg.public_url}/w/{ws.id}/"
            container = self.client.containers.create(
                cfg.runtime_image,
                name=self.name(ws.id),
                command=[
                    "web",
                    "--patch",
                    "/opt/deepseek-harness/web.cordis.patch.yml",
                    "--patch",
                    "/run/launcher/launcher.patch.yml",
                    "--no-open",
                    "--public-url",
                    public_url,
                    "--trusted-host",
                    urlsplit(cfg.public_url).netloc,
                ],
                user=self.identity(),
                working_dir="/workspace",
                environment={
                    "HOME": "/workspace",
                    "DSH_HOME": "/home/node/.dsh",
                    "DSH_DESKTOP_ENABLED": "0",
                    "DSH_BROWSER_USE_ENABLED": "0",
                    "DSH_TELEMETRY_DISABLED": "1",
                    "LAUNCHER_MODEL_TOKEN": decrypt(ws.token_encrypted),
                },
                volumes={
                    str(root / "project"): {"bind": "/workspace", "mode": "rw"},
                    str(root / "home"): {"bind": "/home/node/.dsh", "mode": "rw"},
                    str(root / "managed"): {"bind": "/run/launcher", "mode": "ro"},
                    str(root / "managed" / "AGENTS.md"): {"bind": "/home/node/.dsh/AGENTS.md", "mode": "ro"},
                },
                network=network.name,
                read_only=True,
                ports={"3080/tcp": ("127.0.0.1", None)} if not cfg.control_containers else None,
                tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=536870912"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=f"{user.memory_mb}m",
                memswap_limit=f"{user.memory_mb}m",
                nano_cpus=int(user.cpu * 1e9),
                pids_limit=256,
                labels={
                    "launcher.managed": cfg.docker_prefix,
                    "launcher.workspace": ws.id,
                    "launcher.config-version": str(ws.config_version),
                },
                log_config=docker.types.LogConfig(
                    type="json-file", config={"max-size": "10m", "max-file": "2"}
                ),
                restart_policy={"Name": "no"},
            )
            container.start()
        deadline = time.monotonic() + cfg.startup_timeout
        host = urlsplit(cfg.public_url).netloc
        with httpx.Client(trust_env=False, timeout=4, follow_redirects=False) as http:
            while time.monotonic() < deadline:
                container.reload()
                if container.status != "running":
                    raise RuntimeFailure("Harness 进程提前退出，请管理员检查镜像配置和容器日志")
                network_data = container.attrs["NetworkSettings"]["Networks"]
                if cfg.control_containers:
                    address = f"http://{network_data[self.name(ws.id) + '-net']['IPAddress']}:3080"
                else:
                    # Docker Desktop does not route container bridge IPs to the host.
                    binding = container.attrs["NetworkSettings"]["Ports"]["3080/tcp"][0]
                    address = f"http://127.0.0.1:{binding['HostPort']}"
                # Never persist or expose the launch token or raw container logs.
                logs = container.logs(tail=150).decode(errors="replace")
                urls = re.findall(r"dsh web:\s+(https?://\S+)", logs)
                if urls:
                    token = parse_qs(urlsplit(urls[-1]).query).get("token", [""])[0]
                    if token:
                        try:
                            response = http.get(
                                f"{address}/", params={"token": token}, headers={"Host": host}
                            )
                            cookie = "; ".join(f"{c.name}={c.value}" for c in http.cookies.jar)
                            if response.status_code in (200, 302, 303) and cookie:
                                ready = http.get(f"{address}/", headers={"Host": host, "Cookie": cookie})
                                if ready.status_code == 200:
                                    return address, encrypt(cookie)
                        except httpx.HTTPError:
                            pass
                time.sleep(1)
        raise RuntimeFailure("Harness 启动超时，可以重试或请管理员检查运行环境")
