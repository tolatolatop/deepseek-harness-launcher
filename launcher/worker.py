import fcntl
import logging
import os
import time
import uuid

from sqlmodel import Session, select

from launcher.db import engine
from launcher.models import AuditEvent, Operation, Platform, User, WorkerHeartbeat, Workspace, now
from launcher.runtime import DockerRuntime, RuntimeFailure, build_runtime_spec, render_config
from launcher.services import enqueue
from launcher.settings import settings
from launcher.templates import runtime_binding

log = logging.getLogger("launcher.worker")


def process_one(runtime, worker_id: str) -> bool:
    with Session(engine()) as db:
        op = db.exec(
            select(Operation)
            .where(
                (Operation.status == "queued")
                | ((Operation.status == "running") & (Operation.lease_until < now()))
            )
            .order_by(Operation.created_at)
        ).first()
        if not op:
            return False
        op.status = "running"
        op.worker_id = worker_id
        op.attempts += 1
        op.lease_until = now() + settings().startup_timeout + 90
        op.message = "正在停止运行环境" if op.action == "stop" else "正在准备运行环境"
        db.add(op)
        db.commit()
        ws = db.get(Workspace, op.workspace_id)
        user = db.get(User, ws.user_id)
        platform = db.get(Platform, 1)
        version = ws.config_version
        binding = runtime_binding(db, ws)
        snapshot = dict(binding.snapshot)
        try:
            if op.action == "stop" or not user.active or ws.desired_status == "stopped":
                runtime.stop(ws.id)
                state, address, cookie = "stopped", "", ""
            else:
                render_config(ws, platform)
                handle = runtime.start(
                    build_runtime_spec(db, ws, user), rebuild=op.action in {"restart", "rebuild"}
                )
                address, cookie = handle.address, handle.encrypted_cookie
                state = "running"
            # Reload after slow operations so disable/config changes are not overwritten.
            db.refresh(user)
            db.refresh(ws)
            if state == "running" and (not user.active or ws.desired_status == "stopped"):
                runtime.stop(ws.id)
                state, address, cookie = "stopped", "", ""
            ws.status = state
            ws.desired_status = state
            ws.runtime_address, ws.runtime_cookie = address, cookie
            ws.error = ""
            db.refresh(binding)
            if state == "running":
                ws.applied_version = version
                binding.applied_snapshot = snapshot
                binding.pinned_image_id = handle.image_id
                binding.actual_image_id, binding.actual_image_ref = handle.image_id, handle.image_ref
                binding.checked_at = now()
                binding.stop_at = (
                    now() + binding.max_runtime_minutes * 60 if binding.max_runtime_minutes else None
                )
            else:
                binding.stop_at = None
                binding.actual_image_id = binding.actual_image_ref = ""
            op.status = "succeeded"
            op.message = "空间已就绪" if state == "running" else "已停止，文件和会话已保留"
        except Exception as exc:
            log.error("Operation %s failed (%s)", op.id, type(exc).__name__)
            try:
                runtime.stop(ws.id)
            except Exception:
                log.error("Cleanup pending for workspace %s", ws.id)
            db.refresh(ws)
            ws.status = "error"
            ws.desired_status = "stopped"
            ws.runtime_address = ws.runtime_cookie = ""
            ws.error = (
                str(exc)
                if isinstance(exc, RuntimeFailure)
                else "运行操作失败，请检查 Docker 服务或联系管理员"
            )
            op.status, op.message = "failed", ws.error
        ws.updated_at = now()
        op.finished_at = now()
        op.active_workspace_id = None
        op.lease_until = 0
        if ws.status == "error":
            binding.stop_at = None
            binding.actual_image_id = binding.actual_image_ref = ""
        db.add(binding)
        db.add(ws)
        db.add(op)
        db.commit()
    return True


def reconcile(runtime):
    with Session(engine()) as db:
        workspace_ids = [w.id for w in db.exec(select(Workspace)).all()]
    for workspace_id in workspace_ids:
        with Session(engine()) as db:
            ws = db.get(Workspace, workspace_id)
            if db.exec(select(Operation).where(Operation.active_workspace_id == ws.id)).first():
                continue
            owner = db.get(User, ws.user_id)
            binding = runtime_binding(db, ws)
            stop = not owner.active or ws.desired_status == "stopped"
            expired = bool(binding.stop_at and binding.stop_at <= now() and not stop)
            spec = build_runtime_spec(db, ws, owner)
            cookie = ws.runtime_cookie
            checked_at = binding.checked_at
        if expired:
            with Session(engine()) as db:
                ws = db.get(Workspace, workspace_id)
                binding = runtime_binding(db, ws)
                if binding.stop_at and binding.stop_at <= now():
                    enqueue(db, db.get(User, ws.user_id), ws, "stop")
                    db.add(AuditEvent(actor_id="system", action="workspace.expired", target_id=ws.id))
                    db.commit()
            continue
        handle, error, observed = None, "", "unknown"
        try:
            observed = runtime.inspect(workspace_id)
            if stop:
                # A missing container can still leave its network attached to control containers.
                runtime.stop(workspace_id)
            elif observed == "running" and (not cookie or now() - checked_at >= 10):
                handle = runtime.recover(spec, cookie)
        except Exception as exc:
            error = str(exc) if isinstance(exc, RuntimeFailure) else "运行环境暂时无法接管，正在自动重试"
        with Session(engine()) as db:
            ws = db.exec(select(Workspace).where(Workspace.id == workspace_id).with_for_update()).one()
            if db.exec(select(Operation).where(Operation.active_workspace_id == ws.id)).first():
                continue
            binding = runtime_binding(db, ws)
            if error:
                ws.status = "error" if stop else "recovering"
                ws.error = error
                ws.runtime_address = ws.runtime_cookie = ""
            elif stop and ws.desired_status == "stopped":
                if ws.status != "error":
                    ws.status = "stopped"
                ws.runtime_address = ws.runtime_cookie = ""
                binding.stop_at = None
                binding.actual_image_id = binding.actual_image_ref = ""
            elif not stop and observed != "running":
                ws.status, ws.desired_status = "error", "stopped"
                ws.error = "运行环境已退出，数据已保留，请重新启动"
                ws.runtime_address = ws.runtime_cookie = ""
                binding.stop_at = None
            elif handle and ws.desired_status == "running":
                ws.status, ws.error = "running", ""
                ws.runtime_address, ws.runtime_cookie = handle.address, handle.encrypted_cookie
                ws.applied_version = handle.config_version
                binding.actual_image_id, binding.actual_image_ref = handle.image_id, handle.image_ref
                binding.checked_at = now()
                # Existing installations have no template labels; adopt their actual image.
                if not binding.applied_snapshot:
                    binding.applied_snapshot = {**binding.snapshot, "image": handle.image_ref}
                    if not binding.pinned_image_id and not handle.template_id:
                        binding.pinned_image_id = handle.image_id
            db.add(ws)
            db.add(binding)
            db.commit()
    runtime.cleanup_orphans(set(workspace_ids))
    with Session(engine()) as db:
        db.merge(WorkerHeartbeat(updated_at=now()))
        db.commit()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = settings()
    cfg.data_root.mkdir(parents=True, exist_ok=True)
    # This release intentionally supports one Docker host and one worker.
    lock = open(cfg.data_root / ".worker.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    runtime = DockerRuntime()
    while True:
        try:
            reconcile(runtime)
            if not process_one(runtime, worker_id):
                time.sleep(2)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.error("Worker loop failed (%s)", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
