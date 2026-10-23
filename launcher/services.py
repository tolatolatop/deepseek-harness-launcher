from fastapi import HTTPException
from sqlalchemy import func, update
from sqlmodel import Session, select

from launcher.endpoints import desktop_url
from launcher.models import AuditEvent, Operation, Platform, RuntimeEndpoint, User, Workspace, now
from launcher.settings import settings

PLUGIN_CATALOG = [
    {
        "id": "files",
        "name": "文件读写",
        "description": "读取项目资料、修改代码并保存成果。",
        "editable": False,
    },
    {
        "id": "shell",
        "name": "命令执行",
        "description": "在个人容器内运行 Python、Git 和 Shell 命令。",
        "editable": False,
    },
    {
        "id": "instructions",
        "name": "个人指令",
        "description": "将回复语言和个人偏好带入工作会话。",
        "editable": False,
    },
    {
        "id": "browser-desktop",
        "name": "浏览器桌面",
        "description": "启动容器内的 Chromium 与 noVNC，在 Harness 中查看并接管浏览器。",
        "editable": True,
    },
    {
        "id": "browser-use",
        "name": "Browser Use",
        "description": "让 Harness 的官方 Playwright MCP 操作同一浏览器，需要同时启用浏览器桌面。",
        "editable": True,
    },
    {
        "id": "session-titles",
        "name": "会话自动命名",
        "description": "用模型生成会话标题，会产生少量额外模型调用。",
        "editable": True,
    },
]
EDITABLE_PLUGINS = {p["id"] for p in PLUGIN_CATALOG if p["editable"]}


def audit(db: Session, actor: User, action: str, target: str):
    db.add(AuditEvent(actor_id=actor.id, action=action, target_id=target))


def own_workspace(db: Session, user: User, workspace_id: str) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if not ws or ws.user_id != user.id:
        raise HTTPException(404, "工作空间不存在")
    return ws


def public_user(user: User):
    # SQLAlchemy expires mapped attributes after commit; materialize before dumping.
    _ = user.id
    return user.model_dump(exclude={"password_hash"})


def public_workspace(ws: Workspace, db: Session):
    _ = ws.id
    result = ws.model_dump(exclude={"token_encrypted", "runtime_cookie", "runtime_address"})
    result["has_api_token"] = bool(ws.token_encrypted)
    result["entry_url"] = f"{settings().base_path}/w/{ws.id}/"
    endpoint = db.get(RuntimeEndpoint, (ws.id, "desktop"))
    result["desktop_url"] = (
        desktop_url(ws.id)
        if ws.status == "running" and endpoint and endpoint.config_version == ws.applied_version
        else None
    )
    from launcher.templates import runtime_summary

    result["runtime"] = runtime_summary(db, ws)
    return result


def enqueue(db: Session, actor: User, ws: Workspace, action: str) -> Operation:
    if action not in {"start", "stop", "restart", "rebuild"}:
        raise HTTPException(400, "不支持的操作")
    # Serialize resource admission across requests, including SQLite dev mode.
    db.exec(update(Platform).where(Platform.id == 1).values(updated_at=now()))
    ws = db.exec(
        select(Workspace)
        .where(Workspace.id == ws.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    existing = db.exec(select(Operation).where(Operation.active_workspace_id == ws.id)).first()
    if existing:
        if existing.action == action:
            return existing
        raise HTTPException(409, "已有操作正在进行，请等待完成")
    if action != "stop":
        owner = db.get(User, ws.user_id)
        if not owner or not owner.active:
            raise HTTPException(409, "账号已停用")
        count = db.exec(
            select(func.count())
            .select_from(Workspace)
            .where(Workspace.desired_status == "running", Workspace.id != ws.id)
        ).one()
        if count >= settings().max_running:
            raise HTTPException(409, "当前运行名额已满，请稍后重试")
        ws.desired_status = "running"
        ws.status = "starting"
    else:
        ws.desired_status = "stopped"
        ws.status = "stopping"
    ws.error = ""
    ws.updated_at = now()
    op = Operation(workspace_id=ws.id, active_workspace_id=ws.id, actor_id=actor.id, action=action)
    db.add(ws)
    db.add(op)
    audit(db, actor, f"workspace.{action}", ws.id)
    db.commit()
    db.refresh(op)
    return op
