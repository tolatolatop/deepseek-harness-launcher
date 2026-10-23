import hashlib
import json
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from launcher import files
from launcher.db import engine, get_db, init_db
from launcher.middleware import BodyLimitMiddleware
from launcher.models import (
    AuditEvent,
    LoginAttempt,
    LoginSession,
    Operation,
    Platform,
    RuntimeTemplate,
    User,
    WorkerHeartbeat,
    Workspace,
    WorkspaceRuntime,
    now,
)
from launcher.proxy import proxy_http, proxy_ws
from launcher.runtime import BASE_COMMIT, BASE_SOURCE, prepare_storage
from launcher.schemas import (
    BatchAction,
    LoginInput,
    MaintenanceUpdate,
    PasswordUpdate,
    PlatformUpdate,
    TemplateCreate,
    TemplateState,
    UserCreate,
    UserUpdate,
    WorkspaceConfig,
    WorkspaceCreate,
    WorkspaceTemplateChange,
)
from launcher.security import (
    authenticated,
    check_origin,
    cipher,
    csrf_token,
    current_user,
    decrypt,
    digest,
    encrypt,
    new_session,
    password_hash,
    verify_password,
)
from launcher.services import (
    EDITABLE_PLUGINS,
    PLUGIN_CATALOG,
    audit,
    enqueue,
    own_workspace,
    public_user,
    public_workspace,
)
from launcher.settings import settings
from launcher.templates import available_template, runtime_binding, template_snapshot


def create_app() -> FastAPI:
    cfg = settings()

    @asynccontextmanager
    async def lifespan(app):
        cipher()
        cfg.data_root.mkdir(parents=True, exist_ok=True)
        init_db()
        yield

    app = FastAPI(
        title="DeepSeek 团队工作台", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(BodyLimitMiddleware, limit=(cfg.upload_limit_mb + 1) * 1024**2)
    api = APIRouter(prefix=f"{cfg.base_path}/api")

    def member(request: Request, db: Session = Depends(get_db)):
        return current_user(request, db)

    def admin(user: User = Depends(member)):
        if user.role != "admin":
            raise HTTPException(403, "需要管理员权限")
        return user

    @app.middleware("http")
    async def secure_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if "/api/" in request.url.path:
            response.headers["Cache-Control"] = "no-store"
        return response

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.post("/auth/login")
    def login(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
        check_origin(request)
        key = hashlib.sha256(f"{request.client.host}:{body.username}".encode()).hexdigest()
        db.exec(delete(LoginAttempt).where(LoginAttempt.created_at < now() - 900))
        attempts = db.exec(select(LoginAttempt).where(LoginAttempt.key == key)).all()
        if len(attempts) >= 10:
            raise HTTPException(429, "登录尝试过于频繁，请 15 分钟后重试")
        user = db.exec(select(User).where(User.username == body.username)).first()
        if not user or not user.active or not verify_password(body.password, user.password_hash):
            # Apply a password hash even for unknown users to reduce timing leakage.
            if not user:
                password_hash(body.password)
            db.add(LoginAttempt(key=key))
            db.commit()
            raise HTTPException(401, "用户名或密码不正确，或账号已停用")
        db.exec(delete(LoginAttempt).where(LoginAttempt.key == key))
        token = new_session(db, user)
        response.set_cookie(
            "launcher_session",
            token,
            httponly=True,
            secure=cfg.secure_cookie,
            samesite="strict",
            path=cfg.base_path or "/",
            max_age=cfg.session_hours * 3600,
        )
        audit(db, user, "auth.login", user.id)
        db.commit()
        return {"user": public_user(user), "csrf_token": csrf_token(token)}

    @api.get("/auth/me")
    def me(request: Request, user: User = Depends(member)):
        return {"user": public_user(user), "csrf_token": csrf_token(request.cookies["launcher_session"])}

    @api.post("/auth/logout")
    def logout(
        request: Request, response: Response, user: User = Depends(member), db: Session = Depends(get_db)
    ):
        db.exec(
            delete(LoginSession).where(LoginSession.token_hash == digest(request.cookies["launcher_session"]))
        )
        db.commit()
        response.delete_cookie("launcher_session", path=cfg.base_path or "/")
        return {"ok": True}

    @api.put("/auth/password")
    def change_password(body: PasswordUpdate, user: User = Depends(member), db: Session = Depends(get_db)):
        if not verify_password(body.old_password, user.password_hash):
            raise HTTPException(400, "原密码不正确")
        user.password_hash = password_hash(body.new_password)
        db.add(user)
        db.exec(delete(LoginSession).where(LoginSession.user_id == user.id))
        audit(db, user, "auth.password_changed", user.id)
        db.commit()
        return {"ok": True, "message": "密码已修改，请重新登录"}

    @api.get("/catalog")
    def catalog(user: User = Depends(member), db: Session = Depends(get_db)):
        platform = db.get(Platform, 1)
        heartbeat = db.get(WorkerHeartbeat, "primary")
        return {
            "models": platform.models,
            "gateway_configured": bool(platform.gateway_url),
            "template_name": platform.template_name,
            "plugins": PLUGIN_CATALOG,
            "worker_online": bool(heartbeat and now() - heartbeat.updated_at < cfg.startup_timeout + 60),
            "runtime_image": cfg.runtime_image,
            "templates": [
                t.model_dump()
                for t in db.exec(
                    select(RuntimeTemplate)
                    .where(RuntimeTemplate.published, RuntimeTemplate.enabled)
                    .order_by(RuntimeTemplate.created_at)
                ).all()
            ],
        }

    @api.get("/workspace")
    def my_workspace(user: User = Depends(member), db: Session = Depends(get_db)):
        ws = db.exec(select(Workspace).where(Workspace.user_id == user.id)).first()
        return public_workspace(ws, db) if ws else None

    @api.post("/workspaces", status_code=201)
    def create_workspace(body: WorkspaceCreate, user: User = Depends(member), db: Session = Depends(get_db)):
        existing = db.exec(select(Workspace).where(Workspace.user_id == user.id)).first()
        if existing:
            return public_workspace(existing, db)
        platform = db.get(Platform, 1)
        template = available_template(db, body.template_id)
        ws = Workspace(user_id=user.id, name=body.name, model=platform.models[0] if platform.models else "")
        prepare_storage(ws.id)
        db.add(ws)
        try:
            db.flush()
            db.add(WorkspaceRuntime(workspace_id=ws.id, snapshot=template_snapshot(template)))
            audit(db, user, "workspace.create", ws.id)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "工作空间已经创建，请刷新页面") from None
        db.refresh(ws)
        return public_workspace(ws, db)

    @api.post("/workspaces/{workspace_id}/actions/{action}", status_code=202)
    def workspace_action(
        workspace_id: str, action: str, user: User = Depends(member), db: Session = Depends(get_db)
    ):
        return enqueue(db, user, own_workspace(db, user, workspace_id), action).model_dump()

    @api.get("/workspaces/{workspace_id}/operations")
    def operations(workspace_id: str, user: User = Depends(member), db: Session = Depends(get_db)):
        own_workspace(db, user, workspace_id)
        return db.exec(
            select(Operation)
            .where(Operation.workspace_id == workspace_id)
            .order_by(Operation.created_at.desc())
            .limit(30)
        ).all()

    def change_template(workspace_id: str, body: WorkspaceTemplateChange, user: User, db: Session):
        from sqlalchemy import update

        db.exec(update(Platform).where(Platform.id == 1).values(updated_at=now()))
        ws = db.exec(
            select(Workspace)
            .where(Workspace.id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if not ws or (user.role != "admin" and ws.user_id != user.id):
            raise HTTPException(404, "工作空间不存在")
        if ws.config_version != body.expected_version:
            raise HTTPException(409, "空间配置已变化，请刷新后重试")
        if db.exec(select(Operation).where(Operation.active_workspace_id == ws.id)).first():
            raise HTTPException(409, "已有运行操作，请等待完成")
        template = available_template(db, body.template_id)
        binding = runtime_binding(db, ws)
        if ws.desired_status == "running" and not binding.applied_snapshot:
            raise HTTPException(409, "正在接管现有运行版本，请稍后再切换模板")
        if binding.snapshot["spec"]["compatibility_group"] != template.spec["compatibility_group"]:
            raise HTTPException(409, "模板的数据兼容组不同，不能直接复用现有会话目录")
        if binding.snapshot["id"] == template.id:
            return ws
        binding.snapshot = template_snapshot(template)
        binding.pinned_image_id = ""
        ws.config_version += 1
        db.add(binding)
        db.add(ws)
        audit(db, user, "workspace.template_changed", ws.id)
        return ws

    @api.put("/workspaces/{workspace_id}/template")
    def workspace_template(
        workspace_id: str,
        body: WorkspaceTemplateChange,
        user: User = Depends(member),
        db: Session = Depends(get_db),
    ):
        own_workspace(db, user, workspace_id)
        ws = change_template(workspace_id, body, user, db)
        db.commit()
        return public_workspace(ws, db)

    @api.put("/workspaces/{workspace_id}/maintenance")
    def maintenance(
        workspace_id: str,
        body: MaintenanceUpdate,
        user: User = Depends(member),
        db: Session = Depends(get_db),
    ):
        ws = own_workspace(db, user, workspace_id)
        binding = runtime_binding(db, ws)
        binding.max_runtime_minutes = body.max_runtime_minutes
        binding.stop_at = (
            now() + body.max_runtime_minutes * 60
            if body.max_runtime_minutes and ws.status in {"running", "recovering"}
            else None
        )
        db.add(binding)
        audit(db, user, "workspace.maintenance_updated", ws.id)
        db.commit()
        return public_workspace(ws, db)

    @api.get("/admin/templates")
    def list_templates(user: User = Depends(admin), db: Session = Depends(get_db)):
        return db.exec(select(RuntimeTemplate).order_by(RuntimeTemplate.created_at.desc())).all()

    @api.post("/admin/templates", status_code=201)
    def create_template(body: TemplateCreate, user: User = Depends(admin), db: Session = Depends(get_db)):
        template = RuntimeTemplate(**body.model_dump())
        db.add(template)
        audit(db, user, "template.created", template.id)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "模板标识与版本已存在，请使用新版本号") from None
        db.refresh(template)
        return template

    @api.post("/admin/templates/{template_id}/publish")
    def publish_template(template_id: str, user: User = Depends(admin), db: Session = Depends(get_db)):
        # Also serialize writers on SQLite, where SELECT FOR UPDATE is ignored.
        db.exec(
            update(RuntimeTemplate)
            .where(RuntimeTemplate.id == template_id)
            .values(enabled=RuntimeTemplate.enabled)
        )
        template = db.exec(
            select(RuntimeTemplate).where(RuntimeTemplate.id == template_id).with_for_update()
        ).first()
        if not template:
            raise HTTPException(404, "模板不存在")
        template.published = True
        db.add(template)
        audit(db, user, "template.published", template.id)
        db.commit()
        db.refresh(template)
        return template

    @api.put("/admin/templates/{template_id}")
    def edit_template(
        template_id: str, body: TemplateCreate, user: User = Depends(admin), db: Session = Depends(get_db)
    ):
        db.exec(
            update(RuntimeTemplate)
            .where(RuntimeTemplate.id == template_id)
            .values(enabled=RuntimeTemplate.enabled)
        )
        template = db.exec(
            select(RuntimeTemplate).where(RuntimeTemplate.id == template_id).with_for_update()
        ).first()
        if not template:
            raise HTTPException(404, "模板不存在")
        if template.published:
            raise HTTPException(409, "已发布模板不可修改，请创建新版本")
        for key, value in body.model_dump().items():
            setattr(template, key, value)
        db.add(template)
        audit(db, user, "template.updated", template.id)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "模板标识与版本已存在") from None
        db.refresh(template)
        return template

    @api.patch("/admin/templates/{template_id}")
    def template_state(
        template_id: str, body: TemplateState, user: User = Depends(admin), db: Session = Depends(get_db)
    ):
        template = db.get(RuntimeTemplate, template_id)
        if not template:
            raise HTTPException(404, "模板不存在")
        template.enabled = body.enabled
        db.add(template)
        audit(db, user, "template.enabled" if body.enabled else "template.disabled", template.id)
        db.commit()
        db.refresh(template)
        return template

    @api.post("/admin/workspaces/{workspace_id}/upgrade", status_code=202)
    def upgrade_workspace(
        workspace_id: str,
        body: WorkspaceTemplateChange,
        user: User = Depends(admin),
        db: Session = Depends(get_db),
    ):
        ws = change_template(workspace_id, body, user, db)
        return enqueue(db, user, ws, "rebuild").model_dump()

    @api.post("/admin/workspaces/actions")
    def batch_action(body: BatchAction, user: User = Depends(admin), db: Session = Depends(get_db)):
        results = []
        for workspace_id in dict.fromkeys(body.workspace_ids):
            try:
                ws = db.get(Workspace, workspace_id)
                if not ws:
                    raise HTTPException(404, "空间不存在")
                op = enqueue(db, user, ws, body.action)
                results.append({"workspace_id": workspace_id, "ok": True, "operation_id": op.id})
            except HTTPException as exc:
                db.rollback()
                results.append({"workspace_id": workspace_id, "ok": False, "message": exc.detail})
        return {"results": results}

    @api.put("/workspaces/{workspace_id}/config")
    def workspace_config(
        workspace_id: str, body: WorkspaceConfig, user: User = Depends(member), db: Session = Depends(get_db)
    ):
        ws = own_workspace(db, user, workspace_id)
        # Lock before the version comparison so parallel saves cannot lose updates.
        from sqlalchemy import update

        db.exec(update(Workspace).where(Workspace.id == ws.id).values(updated_at=now()))
        db.refresh(ws)
        if ws.config_version != body.expected_version:
            raise HTTPException(409, "设置已更新，请刷新后重新保存")
        if body.model and body.model not in db.get(Platform, 1).models:
            raise HTTPException(400, "请选择管理员提供的模型")
        if set(body.plugins) - EDITABLE_PLUGINS:
            raise HTTPException(400, "插件不在允许目录中")
        ws.model, ws.language, ws.instructions = body.model, body.language, body.instructions
        if "browser-use" in body.plugins and "browser-desktop" not in body.plugins:
            raise HTTPException(422, "Browser Use 需要同时启用浏览器桌面")
        ws.plugins = sorted(set(body.plugins))
        if body.api_token is not None:
            ws.token_encrypted = encrypt(body.api_token.strip())
        ws.config_version += 1
        db.add(ws)
        audit(db, user, "workspace.config_saved", ws.id)
        db.commit()
        db.refresh(ws)
        return public_workspace(ws, db)

    @api.post("/workspaces/{workspace_id}/test-model")
    async def test_model(workspace_id: str, user: User = Depends(member), db: Session = Depends(get_db)):
        ws = own_workspace(db, user, workspace_id)
        platform = db.get(Platform, 1)
        if not ws.token_encrypted or not platform.gateway_url:
            raise HTTPException(400, "请先保存模型令牌并配置网关")
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
                result = await client.get(
                    f"{platform.gateway_url}/models",
                    headers={"Authorization": f"Bearer {decrypt(ws.token_encrypted)}"},
                )
            if result.status_code != 200:
                return {"ok": False, "message": f"网关返回 HTTP {result.status_code}，请检查地址和令牌"}
            payload = result.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                return {"ok": False, "message": "网关返回的模型列表格式不兼容"}
            available = [m.get("id") for m in payload["data"] if isinstance(m, dict)]
            if ws.model not in available:
                return {"ok": False, "message": "网关可连接，但所选模型不在当前令牌的模型列表中"}
            return {"ok": True, "message": "凭证和模型列表检查通过；尚未发起计费推理请求"}
        except (httpx.HTTPError, ValueError, TypeError):
            return {"ok": False, "message": "无法读取网关模型列表，请检查网络和地址"}

    @api.get("/workspaces/{workspace_id}/files")
    def list_workspace_files(
        workspace_id: str, path: str = "", user: User = Depends(member), db: Session = Depends(get_db)
    ):
        own_workspace(db, user, workspace_id)
        return files.list_files(workspace_id, path)

    @api.get("/workspaces/{workspace_id}/storage")
    def storage(workspace_id: str, user: User = Depends(member), db: Session = Depends(get_db)):
        own_workspace(db, user, workspace_id)
        return {
            "used_bytes": files.usage_bytes(workspace_id),
            "threshold_bytes": user.storage_mb * 1024**2,
            "quota_type": "advisory",
        }

    @api.post("/workspaces/{workspace_id}/files")
    async def upload(
        workspace_id: str,
        file: UploadFile,
        path: str = "",
        user: User = Depends(member),
        db: Session = Depends(get_db),
    ):
        own_workspace(db, user, workspace_id)
        if shutil.disk_usage(cfg.data_root).free < cfg.min_free_disk_mb * 1024**2:
            raise HTTPException(507, "服务器可用存储空间不足")
        result = await files.save_upload(workspace_id, path, file)
        audit(db, user, "file.upload", workspace_id)
        db.commit()
        return result

    @api.get("/workspaces/{workspace_id}/download")
    def download(
        workspace_id: str,
        path: str,
        request: Request,
        user: User = Depends(member),
        db: Session = Depends(get_db),
    ):
        own_workspace(db, user, workspace_id)
        source, name = files.open_download(workspace_id, path)
        token = request.cookies.get("launcher_session")

        def chunks():
            checked = 0
            try:
                while data := source.read(65536):
                    if time.monotonic() - checked > 2:
                        try:
                            with Session(engine()) as current:
                                authenticated(current, token)
                        except HTTPException:
                            return
                        checked = time.monotonic()
                    yield data
            finally:
                source.close()

        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}"},
        )

    @api.get("/admin/users")
    def users(user: User = Depends(admin), db: Session = Depends(get_db)):
        return [public_user(u) for u in db.exec(select(User).order_by(User.created_at)).all()]

    @api.post("/admin/users", status_code=201)
    def create_user(body: UserCreate, user: User = Depends(admin), db: Session = Depends(get_db)):
        account = User(**body.model_dump(exclude={"password"}), password_hash=password_hash(body.password))
        db.add(account)
        audit(db, user, "user.create", account.id)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "用户名已经存在") from None
        db.refresh(account)
        return public_user(account)

    @api.patch("/admin/users/{user_id}")
    def edit_user(user_id: str, body: UserUpdate, user: User = Depends(admin), db: Session = Depends(get_db)):
        account = db.get(User, user_id)
        if not account:
            raise HTTPException(404, "用户不存在")
        if account.role == "admin" and body.active is False:
            raise HTTPException(400, "不能停用管理员账号")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(account, k, v)
        db.add(account)
        ws = db.exec(select(Workspace).where(Workspace.user_id == account.id)).first()
        if not account.active:
            db.exec(delete(LoginSession).where(LoginSession.user_id == account.id))
            if ws:
                ws.desired_status = "stopped"
                ws.runtime_cookie = ""
        if ws:
            ws.config_version += 1
            db.add(ws)
        audit(db, user, "user.update", account.id)
        db.commit()
        return public_user(account)

    @api.get("/admin/workspaces")
    def admin_workspaces(user: User = Depends(admin), db: Session = Depends(get_db)):
        return [public_workspace(w, db) for w in db.exec(select(Workspace)).all()]

    @api.post("/admin/workspaces/{workspace_id}/stop", status_code=202)
    def admin_stop(workspace_id: str, user: User = Depends(admin), db: Session = Depends(get_db)):
        ws = db.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "工作空间不存在")
        active = db.exec(select(Operation).where(Operation.active_workspace_id == ws.id)).first()
        if active:
            ws.desired_status = "stopped"
            ws.runtime_cookie = ""
            db.add(ws)
            audit(db, user, "workspace.stop_requested", ws.id)
            db.commit()
            return {"message": "已请求停止，正在中断启动后的访问"}
        return enqueue(db, user, ws, "stop").model_dump()

    @api.get("/admin/platform")
    def platform(user: User = Depends(admin), db: Session = Depends(get_db)):
        return {
            **db.get(Platform, 1).model_dump(),
            "runtime_image": cfg.runtime_image,
            "base_source": BASE_SOURCE,
            "base_commit": BASE_COMMIT,
            "max_running": cfg.max_running,
        }

    @api.put("/admin/platform")
    def update_platform(body: PlatformUpdate, user: User = Depends(admin), db: Session = Depends(get_db)):
        p = db.get(Platform, 1)
        for key, value in body.model_dump().items():
            setattr(p, key, value)
        p.updated_at = now()
        db.add(p)
        for ws in db.exec(select(Workspace)).all():
            ws.config_version += 1
            db.add(ws)
        audit(db, user, "platform.update", "1")
        db.commit()
        db.refresh(p)
        return p.model_dump()

    @api.get("/admin/audit")
    def audit_log(user: User = Depends(admin), db: Session = Depends(get_db)):
        return db.exec(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)).all()

    app.include_router(api)

    @app.get(f"{cfg.base_path}/w/{{workspace_id}}")
    def runtime_mount(request: Request, workspace_id: str, db: Session = Depends(get_db)):
        own_workspace(db, current_user(request, db), workspace_id)
        return RedirectResponse(f"{cfg.base_path}/w/{workspace_id}/", status_code=308)

    @app.api_route(
        f"{cfg.base_path}/w/{{workspace_id}}/desktop/{{path:path}}",
        methods=["GET", "HEAD"],
    )
    async def desktop_http(request: Request, workspace_id: str, path: str):
        return await proxy_http(request, workspace_id, path, desktop=True)

    @app.websocket(f"{cfg.base_path}/w/{{workspace_id}}/desktop/{{path:path}}")
    async def desktop_ws(websocket: WebSocket, workspace_id: str, path: str):
        await proxy_ws(websocket, workspace_id, path, desktop=True)

    @app.api_route(
        f"{cfg.base_path}/w/{{workspace_id}}/{{path:path}}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def runtime_http(request: Request, workspace_id: str, path: str):
        return await proxy_http(request, workspace_id, path)

    @app.websocket(f"{cfg.base_path}/w/{{workspace_id}}/{{path:path}}")
    async def runtime_ws(websocket: WebSocket, workspace_id: str, path: str):
        await proxy_ws(websocket, workspace_id, path)

    if cfg.base_path:

        @app.get("/")
        def root():
            return RedirectResponse(f"{cfg.base_path}/")

        @app.get(cfg.base_path)
        def base_redirect():
            return RedirectResponse(f"{cfg.base_path}/")

    @app.get(f"{cfg.base_path}/{{path:path}}")
    def frontend(path: str):
        directory = cfg.frontend_dir.resolve()
        asset = (directory / path).resolve()
        if not asset.is_relative_to(directory):
            raise HTTPException(404)
        if path and asset.is_file() and asset.name != "index.html":
            return FileResponse(asset)
        if path and (path.startswith(("api/", "w/")) or "." in Path(path).name):
            raise HTTPException(404)
        index = directory / "index.html"
        if not index.exists():
            return HTMLResponse(
                "Frontend not built. Run npm --prefix web ci && npm --prefix web run build.", status_code=503
            )
        html = index.read_text().replace(
            '"__LAUNCHER_BASE_JSON__"', json.dumps(cfg.base_path).replace("<", "\\u003c")
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    return app
