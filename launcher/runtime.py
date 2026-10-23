"""Pinned adapter for runzhliu/deepseek-harness-docker 0.2.1-rc.2-r1."""

import hashlib
import io
import ipaddress
import json
import os
import re
import shutil
import tarfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import docker
import httpx
import yaml
from docker.errors import NotFound
from sqlmodel import Session

from launcher.endpoints import desktop_url
from launcher.models import Platform, User, Workspace
from launcher.runtime_types import RuntimeHandle, RuntimeSpec
from launcher.schemas import TemplateSpec
from launcher.security import decrypt, encrypt
from launcher.settings import settings
from launcher.templates import effective_resources, runtime_binding

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
        {
            "id": "browser-desktop",
            "disabled": "browser-desktop" not in ws.plugins,
            "config": {
                "desktopPort": urlsplit(settings().public_url).port
                or (443 if urlsplit(settings().public_url).scheme == "https" else 80),
                "desktopPath": desktop_url(ws.id),
            },
        },
        {"id": "browser-use", "disabled": "browser-use" not in ws.plugins},
        {"id": "browser-use-playwright-mcp", "disabled": "browser-use" not in ws.plugins},
    ]
    atomic_write(root / "managed" / "launcher.patch.yml", yaml.safe_dump(patch, allow_unicode=True))
    atomic_write(
        root / "managed" / "AGENTS.md",
        f"# 用户偏好\n\n请优先使用 {ws.language} 回复。\n\n{ws.instructions}\n",
    )
    atomic_write(root / "managed" / "revision.json", json.dumps({"version": ws.config_version}))
    return root


def build_runtime_spec(db: Session, ws: Workspace, user: User) -> RuntimeSpec:
    binding = runtime_binding(db, ws)
    snapshot = binding.snapshot
    template = TemplateSpec.model_validate(snapshot["spec"])
    resources = effective_resources(snapshot, user)
    return RuntimeSpec(
        workspace_id=ws.id,
        config_version=ws.config_version,
        image=binding.pinned_image_id or snapshot["image"],
        image_ref=snapshot["image"],
        template_id=snapshot["id"],
        template_version=snapshot["version"],
        root=workspace_root(ws.id),
        public_url=f"{settings().public_url}/w/{ws.id}/",
        cpu=resources["cpu"],
        memory_mb=resources["memory_mb"],
        tmp_mb=template.tmp_mb,
        startup_patches=tuple(template.startup_patches),
        environment={**template.environment, "LAUNCHER_MODEL_TOKEN": decrypt(ws.token_encrypted)},
        desktop_enabled="browser-desktop" in ws.plugins,
        browser_use_enabled="browser-use" in ws.plugins and "browser-desktop" in ws.plugins,
        desktop_width=template.desktop_width,
        desktop_height=template.desktop_height,
    )


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
        if (
            result.labels.get("launcher.managed") != self.cfg.docker_prefix
            or result.labels.get("launcher.workspace") != workspace_id
        ):
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

    def prepare_browser_client(self, spec: RuntimeSpec, image_id: str) -> Path:
        """Adapt the pinned community plugin's root-only URL to this workspace mount.

        Read the shipped asset from a never-started, networkless container. The
        patched file is mounted read-only; the image and user's profile stay intact.
        """
        reader = self.client.containers.create(
            image_id,
            entrypoint=["true"],
            network_mode="none",
            labels={
                "launcher.managed": self.cfg.docker_prefix,
                "launcher.workspace": spec.workspace_id,
                "launcher.asset-reader": "1",
            },
        )
        try:
            chunks, _ = reader.get_archive("/opt/deepseek-harness/plugins/dsh-browser-desktop/client.js")
            with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as archive:
                member = next(m for m in archive.getmembers() if m.name == "client.js" and m.isfile())
                if member.size > 256 * 1024:
                    raise ValueError("unexpected browser asset size")
                source = archive.extractfile(member).read().decode()
            needle = "fetch('/browser-desktop/state',"
            if source.count(needle) != 1:
                raise ValueError("unsupported browser desktop client")
            source = source.replace(needle, "fetch(new URL('browser-desktop/state', window.location.href),")
            # Safe initial values also cover a click before the first state poll.
            source = source.replace(
                "desktopPort: 6080,",
                f"desktopPort: {urlsplit(spec.public_url).port or (443 if urlsplit(spec.public_url).scheme == 'https' else 80)},",
                1,
            )
            source = source.replace(
                "desktopPath: '/vnc.html?autoconnect=1&resize=scale&view_only=0&reconnect=1',",
                f"desktopPath: {json.dumps(desktop_url(spec.workspace_id))},",
                1,
            )
            path = spec.root / "managed" / "browser-desktop-client.js"
            atomic_write(path, source)
            return path
        except Exception:
            raise RuntimeFailure("镜像内浏览器桌面插件不兼容，请管理员检查固定底座版本") from None
        finally:
            reader.remove(force=True)

    def start(self, spec: RuntimeSpec, rebuild: bool = False) -> RuntimeHandle:
        cfg = self.cfg
        cfg.data_root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(cfg.data_root).free < cfg.min_free_disk_mb * 1024**2:
            raise RuntimeFailure("宿主机可用磁盘空间不足")
        container = self.container(spec.workspace_id)
        if container and (
            rebuild
            or container.labels.get("launcher.config-version") != str(spec.config_version)
            or container.status != "running"
        ):
            self.stop(spec.workspace_id)
            container = None
        if not container:
            root = spec.root
            network = self.network(spec.workspace_id)
            try:
                image = self.client.images.get(spec.image)
            except NotFound:
                raise RuntimeFailure("模板镜像尚未准备，请管理员先拉取模板指定的镜像") from None
            browser_client = self.prepare_browser_client(spec, image.id) if spec.desktop_enabled else None
            container = self.client.containers.create(
                image.id,
                name=self.name(spec.workspace_id),
                command=[
                    "web",
                    "--patch",
                    "/opt/deepseek-harness/web.cordis.patch.yml",
                    *[arg for path in spec.startup_patches for arg in ("--patch", path)],
                    "--patch",
                    "/run/launcher/launcher.patch.yml",
                    "--no-open",
                    "--public-url",
                    spec.public_url,
                    "--trusted-host",
                    urlsplit(cfg.public_url).netloc,
                ],
                user=self.identity(),
                working_dir="/workspace",
                environment={
                    "HOME": "/workspace",
                    "DSH_HOME": "/home/node/.dsh",
                    "DSH_DESKTOP_ENABLED": "1" if spec.desktop_enabled else "0",
                    "DSH_BROWSER_USE_ENABLED": "1" if spec.browser_use_enabled else "0",
                    "DSH_DESKTOP_WIDTH": str(spec.desktop_width),
                    "DSH_DESKTOP_HEIGHT": str(spec.desktop_height),
                    "DSH_TELEMETRY_DISABLED": "1",
                    **spec.environment,
                },
                volumes={
                    **(
                        {
                            str(browser_client): {
                                "bind": "/opt/deepseek-harness/plugins/dsh-browser-desktop/client.js",
                                "mode": "ro",
                            }
                        }
                        if browser_client
                        else {}
                    ),
                    str(root / "project"): {"bind": "/workspace", "mode": "rw"},
                    str(root / "home"): {"bind": "/home/node/.dsh", "mode": "rw"},
                    str(root / "managed"): {"bind": "/run/launcher", "mode": "ro"},
                    str(root / "managed" / "AGENTS.md"): {"bind": "/home/node/.dsh/AGENTS.md", "mode": "ro"},
                },
                network=network.name,
                read_only=True,
                ports=(
                    {
                        "3080/tcp": ("127.0.0.1", None),
                        **({"6080/tcp": ("127.0.0.1", None)} if spec.desktop_enabled else {}),
                    }
                    if not cfg.control_containers
                    else None
                ),
                shm_size="1g" if spec.desktop_enabled else "64m",
                tmpfs={"/tmp": f"rw,noexec,nosuid,nodev,size={spec.tmp_mb * 1024**2}"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit=f"{spec.memory_mb}m",
                memswap_limit=f"{spec.memory_mb}m",
                nano_cpus=int(spec.cpu * 1e9),
                pids_limit=512 if spec.desktop_enabled else 256,
                labels={
                    "launcher.managed": cfg.docker_prefix,
                    "launcher.workspace": spec.workspace_id,
                    "launcher.config-version": str(spec.config_version),
                    "launcher.template": spec.template_id,
                    "launcher.template-version": spec.template_version,
                    "launcher.image-ref": spec.image_ref,
                    "launcher.browser-desktop": "1" if spec.desktop_enabled else "0",
                },
                log_config=docker.types.LogConfig(
                    type="json-file", config={"max-size": "10m", "max-file": "2"}
                ),
                restart_policy={"Name": "no"},
            )
            container.start()
        self.network(spec.workspace_id)
        return self._connect(container, spec, "", cfg.startup_timeout)

    def recover(self, spec: RuntimeSpec, encrypted_cookie: str) -> RuntimeHandle:
        container = self.container(spec.workspace_id)
        if not container or container.status != "running":
            raise RuntimeFailure("运行容器已退出")
        # Reconnect newly-created API/Worker containers without replacing the user's container.
        self.network(spec.workspace_id)
        return self._connect(container, spec, encrypted_cookie, 8)

    def _connect(self, container, spec: RuntimeSpec, encrypted_cookie: str, timeout: int) -> RuntimeHandle:
        cfg = self.cfg
        deadline = time.monotonic() + timeout
        host = urlsplit(spec.public_url).netloc
        existing_cookie = decrypt(encrypted_cookie)
        with httpx.Client(trust_env=False, timeout=4, follow_redirects=False) as http:
            while time.monotonic() < deadline:
                container.reload()
                if container.status != "running":
                    raise RuntimeFailure("Harness 进程提前退出，请管理员检查镜像配置和容器日志")
                network_data = container.attrs["NetworkSettings"]["Networks"]
                if cfg.control_containers:
                    address = (
                        f"http://{network_data[self.name(spec.workspace_id) + '-net']['IPAddress']}:3080"
                    )
                else:
                    # Docker Desktop does not route container bridge IPs to the host.
                    binding = container.attrs["NetworkSettings"]["Ports"]["3080/tcp"][0]
                    address = f"http://127.0.0.1:{binding['HostPort']}"

                desktop_address = ""
                if container.labels.get("launcher.browser-desktop") == "1":
                    if cfg.control_containers:
                        desktop_address = f"http://{urlsplit(address).hostname}:6080"
                    else:
                        desktop_binding = container.attrs["NetworkSettings"]["Ports"].get("6080/tcp")
                        if not desktop_binding:
                            raise RuntimeFailure("浏览器桌面端口未准备，请重启工作空间")
                        desktop_address = f"http://127.0.0.1:{desktop_binding[0]['HostPort']}"
                    try:
                        desktop_ready = http.get(f"{desktop_address}/vnc.html").status_code == 200
                    except httpx.HTTPError:
                        desktop_ready = False
                    if not desktop_ready:
                        time.sleep(0.2)
                        continue

                def handle(cookie):
                    return RuntimeHandle(
                        address=address,
                        encrypted_cookie=encrypt(cookie),
                        image_id=container.attrs["Image"],
                        image_ref=container.labels.get(
                            "launcher.image-ref", container.attrs["Config"]["Image"]
                        ),
                        config_version=int(container.labels.get("launcher.config-version", "0")),
                        template_id=container.labels.get("launcher.template", ""),
                        template_version=container.labels.get("launcher.template-version", ""),
                        desktop_address=desktop_address,
                    )

                if existing_cookie:
                    try:
                        response = http.get(f"{address}/", headers={"Host": host, "Cookie": existing_cookie})
                        if response.status_code == 200:
                            return handle(existing_cookie)
                    except httpx.HTTPError:
                        pass
                    existing_cookie = ""
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
                                    return handle(cookie)
                        except httpx.HTTPError:
                            pass
                time.sleep(1)
        raise RuntimeFailure("Harness 启动超时，可以重试或请管理员检查运行环境")

    def cleanup_orphans(self, workspace_ids: set[str]):
        for container in self.client.containers.list(
            all=True, filters={"label": f"launcher.managed={self.cfg.docker_prefix}"}
        ):
            if container.labels.get("launcher.asset-reader") == "1":
                container.remove(force=True)
                continue
            workspace_id = container.labels.get("launcher.workspace", "")
            if re.fullmatch(r"[a-f0-9]{32}", workspace_id) and workspace_id not in workspace_ids:
                self.stop(workspace_id)
        for network in self.client.networks.list(
            filters={"label": f"launcher.managed={self.cfg.docker_prefix}"}
        ):
            prefix = f"{self.cfg.docker_prefix}-"
            workspace_id = network.name.removeprefix(prefix).removesuffix("-net")
            if re.fullmatch(r"[a-f0-9]{32}", workspace_id) and workspace_id not in workspace_ids:
                network.reload()
                for container_id in list(network.attrs.get("Containers", {})):
                    network.disconnect(container_id, force=True)
                network.remove()
