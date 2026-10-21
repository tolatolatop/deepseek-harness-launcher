# DeepSeek 团队工作台

Python / FastAPI + React 的企业团队 Harness 启动器。成员通过一个已有域名或 IP 登录，开通个人工作空间、配置 NEW API 模型令牌、进入真实 DeepSeek Harness。停止和重建容器保留项目文件与 Harness 状态。

运行底座使用 [runzhliu/deepseek-harness-docker](https://github.com/runzhliu/deepseek-harness-docker)，目前适配 `0.2.1-rc.2-r1`，源码参考提交 `d482839b469562df36e450c6146ca7afea046a7e`。初始化脚本使用固定镜像摘要，升级须重新运行真实容器测试。

![工作空间](docs/screenshots/workspace.png)

## MVP 功能

- 本地账号、管理员创建成员、密码修改、会话撤销与角色权限。
- 每人一个空间，异步启动／停止／重启／重建，操作进度和失败重试。
- 单入口路径代理，支持 HTTP、流式响应和 WebSocket；Harness 启动凭证不下发浏览器。
- 项目和 `DSH_HOME` 独立持久化，文件列表、上传、下载与存储用量统计。
- 对接现有 NEW API，管理员配置模型目录，成员绑定独立模型调用令牌；凭证加密存储。
- 默认模型、回复语言、个人指令、可选会话自动命名插件；显示待应用配置版本。
- 管理成员资源配额、全部实例状态、停止实例和审计记录。
- CPU、内存、进程数限制。磁盘阈值是告警参考，不是文件系统硬配额。

用户通过 `/launcher/w/{workspace_id}/` 进入自己的空间，无需新增域名、DNS、LiteLLM 或 Kubernetes。

## 本地启动

要求 Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 22+、Docker Engine / Docker Desktop，以及当前用户能访问 Docker。

```bash
git clone https://github.com/tolatolatop/deepseek-harness-launcher.git
cd deepseek-harness-launcher
python scripts/init-env.py --port 18080
uv sync --frozen
npm --prefix web ci
npm --prefix web run build
docker compose --profile images pull runtime-image
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait postgres
```

初始化会创建权限为 `0600` 的 `.env`，包含随机管理员密码和加密密钥；不会覆盖已有文件。初始用户名为 `admin`，密码查看本机 `.env` 的 `LAUNCHER_BOOTSTRAP_PASSWORD`。不要提交或分享此文件。

本地 Python 进程通过 `127.0.0.1:55432` 访问 PostgreSQL；`compose.dev.yaml` 仅为本地开发发布这个回环端口，默认部署不发布数据库端口。如修改 `POSTGRES_PORT`，同时修改 `LAUNCHER_DATABASE_URL` 的端口。已有 `.env` 不会被覆盖，原有数据库选择仍按其中的配置生效。SQLite 可通过显式连接串用于临时开发和测试。

终端一：

```bash
uv run uvicorn launcher.app:create_app --factory \
  --host 127.0.0.1 --port 18080 --no-access-log
```

终端二，在 API 完成初始化之后：

```bash
uv run python -m launcher.worker
```

打开 `http://localhost:18080/launcher/`。本地和 Compose 部署均默认使用 PostgreSQL；MySQL 8.4 兼容能力保留，需显式选择配置启用。Worker 独立运行，API 不调用 Docker。

本地模式使用回环地址上的临时 Docker 映射端口，兼容 Docker Desktop 的网络边界。浏览器仍只访问 Launcher 入口。不要直接把实例端口暴露到局域网。

## 首次使用

1. 管理员进入「管理后台 → 环境与模型」，设置 NEW API 的完整兼容 API 基础地址（一般以 `/v1` 结尾）和模型 ID 列表。
2. 创建成员账号，交付初始密码；成员可自行修改密码。
3. 成员开通空间，在「空间设置」保存自己的模型调用令牌和默认模型。
4. 点击「检查已保存的连接」。此检查只读取模型列表，不发起计费推理。
5. 返回空间首页启动，等待「运行中」，点击「进入 DeepSeek」。
6. 在 Harness 中选择 `/workspace` 为项目目录，然后开始任务。
7. 成果可通过 Launcher 下载；停止后再次启动可继续使用。

NEW API 地址必须能从运行容器中访问，容器内的 `localhost` 指容器自身。生产网关的用户授权、额度和可用模型仍由 NEW API 实施。Launcher 的模型列表是默认配置与界面选择约束，不是不可绕过的网络策略。

当前自动化管理 NEW API 用户／令牌、用量同步和 SSO 未实现；使用 Launcher 本地身份及用户已有模型令牌，避免假定你们的 NEW API 管理接口版本。

## Docker Compose 部署

`scripts/init-env.py` 生成的 `LAUNCHER_DATA_ROOT` 必须是 Docker 宿主机可访问的绝对路径。API、Worker 和运行容器使用一致的主机路径。

```bash
# 首次生成配置；已有 .env 时直接修改相应配置项。
python scripts/init-env.py --public-url https://existing.example/company-ai/launcher --port 18080
docker compose --profile images pull runtime-image
docker compose up -d --build
docker compose ps
```

默认只向宿主机回环地址发布 `18080`。企业访问应通过已有 HTTPS 入口转发，参见 [Nginx 示例](deploy/nginx.conf.example)。可改为 IP 入口并使用企业信任的证书。调整入口时修改 `LAUNCHER_PUBLIC_URL`，重建 API/Worker 后重启运行实例。

Compose 提供 PostgreSQL、API、Worker 三个服务。只有 Worker 挂载 Docker Socket。数据库使用独立持久化卷，不向宿主机发布端口。API 和 Worker 连接独立的出站网络，并由 Worker 加入各空间的专属网络。

API 以 UID/GID 1000 运行。数据根目录应可由该用户读写；现有数据不要递归盲目修改权限。运行时沿用底座 UID 1000、只读根文件系统、`cap_drop: ALL`、`no-new-privileges`、有界临时目录。本模板不启动浏览器桌面/noVNC。

本地直接运行 Worker 时，实例使用当前非 root 用户的 UID/GID，以便正确读写宿主目录；Compose 部署统一使用 1000。

Compose 中初始管理员密码只在数据库没有管理员时使用；修改 `.env` 不会重置已有密码。

### MySQL 部署（可选，默认不启用）

MySQL 使用独立的 `compose.mysql.yaml`，与默认 PostgreSQL 配置二选一，不需要叠加两个文件：

```bash
# 新部署生成 .env；已有文件时跳过此步骤。
python scripts/init-env.py --port 18080
docker compose -f compose.mysql.yaml --profile images pull runtime-image
docker compose -f compose.mysql.yaml up -d --build
docker compose -f compose.mysql.yaml ps
```

初始化脚本会生成 `MYSQL_PASSWORD` 和 `MYSQL_ROOT_PASSWORD`。旧版 `.env` 需补充这两个不同的随机密码；建议使用只含字母、数字、`-`、`_` 的密码，避免连接串转义问题。数据库为 `launcher`，应用使用普通用户 `launcher`，MySQL 数据保存在独立的 `mysql-data` 卷，不向宿主发布端口。备份时使用 MySQL 的备份工具，并同时备份空间文件和加密密钥。

如果直接运行 Python API/Worker 并接入已有 MySQL，在 `.env` 设置：

```dotenv
LAUNCHER_DATABASE_URL=mysql+pymysql://launcher:URL_ENCODED_PASSWORD@mysql-host:3306/launcher?charset=utf8mb4
```

已有 MySQL 需预先创建数据库和账号，允许应用连接及在该数据库中建表、创建索引和读写数据。URL 中的密码如包含 `@`、`:`、`/`、`%` 等字符，需要百分号编码。API 与 Worker 必须使用同一连接串；Compose 内的地址由相应 YAML 配置，修改 `.env` 的 `LAUNCHER_DATABASE_URL` 只影响直接运行模式。

表使用 InnoDB、`utf8mb4` 和区分大小写的排序规则，保持账号名称匹配语义一致；时间字段使用双精度，保留会话、心跳及租约精度。连接池采用存活检查和回收，事务采用 `READ COMMITTED`，确保等待资源分配锁后读到最新名额。实现参考 [SQLAlchemy MySQL 文档](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)。

**已有安装切换数据库不会自动迁移数据。** 两份 Compose 使用同一应用容器名和入口，不能同时启动。切换前停止实例及原部署并备份，迁移数据库记录时保留 ID、空间目录和 `LAUNCHER_SECRET_KEY`；否则新数据库会表现为全新安装。此改动不会修改现有 SQLite/PostgreSQL 表，现有部署可继续使用原配置。

### 主要配置

| 配置 | 默认值 / 用途 |
|---|---|
| `LAUNCHER_PUBLIC_URL` | 浏览器看到的完整入口，可带路径前缀 |
| `LAUNCHER_DATA_ROOT` | 持久化数据绝对路径 |
| `LAUNCHER_SECRET_KEY` | Fernet 密钥，需与数据库一起备份 |
| `LAUNCHER_DATABASE_URL` | 默认 PostgreSQL；兼容 SQLite、MySQL，Compose 按显式选用的 YAML 覆盖 |
| `POSTGRES_PORT` | 本地开发的 PostgreSQL 回环端口，默认 55432，仅 `compose.dev.yaml` 使用 |
| `LAUNCHER_RUNTIME_IMAGE` | 固定版本及摘要的底座镜像 |
| `LAUNCHER_MAX_RUNNING` | 同时运行名额，默认 8；包含启动中的空间 |
| `LAUNCHER_RUNTIME_SUBNET_POOL` | 运行空间网络池，默认 `10.203.0.0/16`，每空间 `/28` |
| `LAUNCHER_CONTROL_SUBNET` | Compose 控制网络，默认 `10.204.0.0/28` |
| `LAUNCHER_EGRESS_SUBNET` | Compose 出站网络，默认 `10.204.0.16/28` |
| `LAUNCHER_MIN_FREE_DISK_MB` | 开通运行时最小剩余磁盘，默认 1024 MB |
| `LAUNCHER_CONTROL_CONTAINERS` | Compose 设置 API/Worker 容器名；本地留空 |

部署前选择不与企业 LAN/VPN 或其他 Docker 网络重叠的地址池。分配器跳过已有 Docker 子网，不会修改其他项目的网络。

## 测试

后端测试覆盖登录、权限、配置、文件、代理、生命周期及并发资源分配；同一套测试可在 SQLite 和真实 MySQL 上执行。首版还验证了管理员到成员的浏览器操作流程、真实 Harness 容器与原生浏览器界面，以及 Docker Compose + PostgreSQL 下的启动、停止和文件持久化。

```bash
uv run pytest -q
uv run ruff check launcher tests scripts
npm --prefix web run build
cd web && npx playwright install chromium && npm test
```

浏览器测试使用独立临时数据库与端口 `18180`，验证登录、管理设置、创建成员、个人配置、文件传输、异步操作与移动端布局。测试没有 Docker Worker，不会创建真实实例。

真实 MySQL 数据库测试（无需更改 `.env`）：

```bash
docker pull mysql:8.4
uv run python scripts/test-mysql.py
```

脚本创建临时 MySQL 容器及随机测试库，完整运行后端测试，然后清理容器与数据卷。CI 在独立 MySQL 任务中运行同一脚本。也可设置 `LAUNCHER_TEST_MYSQL_URL` 后直接运行 `uv run pytest -q`；该测试账号必须具有创建和删除数据库的权限，测试只删除自身生成的 `launcher_test_<随机ID>` 库。

真实 Docker 测试：

```bash
uv run python scripts/smoke.py
```

测试创建独立 API/Worker、随机命名容器和临时目录，验证子路径代理、实际 Python 命令执行、流式模型响应与原生工具调用、停止／启动／重建及持久化，结束后清理自身资源。测试网关是同网络内的合成 OpenAI 兼容服务，不会调用真实模型或产生模型费用；测试还需要本机有 `python:3.12-slim` 镜像。

连接你们实际的 NEW API 后，还应人工完成一次真实流式模型响应和文件／命令工具调用验收。

## 数据、恢复与边界

```text
DATA_ROOT/workspaces/<workspace_id>/
  project/    项目文件，可上传下载
  home/       Harness profile、会话、内部状态
  managed/    平台生成的配置与个人指令，容器只读挂载
```

- 停止移除容器和空间网络，保留上面三个目录。重建同样保留数据。
- 配置保存增加版本；运行中版本不会被悄悄替换，重启后显示已应用版本。
- 后台操作持久化，重复点击返回同一操作；Worker 用单机文件锁防止重复启动，过期租约可恢复。
- 账号停用立即撤销 Launcher 会话，HTTP 流／WebSocket 定期复核权限，Worker 随后停止实例。
- 管理员能停止其他成员的实例，不能通过平台文件接口或代理读取其他成员内容。
- API 下载强制附件，不在 Launcher 来源内渲染上传的 HTML。路径不是浏览器来源隔离，当前只支持受控 Harness 前端；任意前端插件与网站预览不在 MVP 范围内。
- 不应将这一版本当成面向恶意租户的强隔离服务：容器共享宿主内核，运行网络允许出站，模型可访问其容器被授权的数据和个人凭证。企业网络精细访问控制仍需在宿主防火墙／网络层部署。
- 备份前停止空间，备份 PostgreSQL、数据根目录和 `.env` 中的加密密钥。只备份数据库无法恢复项目与会话。当前没有在线快照或一键恢复 UI。
- 本版只支持单 Docker 宿主机、单 Worker。数据库首次建表使用 SQLModel；后续结构变更需要显式迁移，不能用 `create_all` 升级已有结构。

模块划分见 [架构说明](docs/architecture.md)。Launcher 与 Harness Docker 的启动参数、配置文件、认证代理、持久化及版本兼容要求见 [运行时接口契约](docs/runtime-contract.md)。
