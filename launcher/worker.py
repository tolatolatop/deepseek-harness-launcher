import fcntl
import logging
import os
import time
import uuid

from sqlmodel import Session, select

from launcher.db import engine
from launcher.models import Operation, Platform, User, WorkerHeartbeat, Workspace, now
from launcher.runtime import DockerRuntime, RuntimeFailure
from launcher.settings import settings

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
        try:
            if op.action == "stop" or not user.active or ws.desired_status == "stopped":
                runtime.stop(ws.id)
                state, address, cookie = "stopped", "", ""
            else:
                address, cookie = runtime.start(
                    ws, user, platform, rebuild=op.action in {"restart", "rebuild"}
                )
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
            if state == "running":
                ws.applied_version = version
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
        db.add(ws)
        db.add(op)
        db.commit()
    return True


def reconcile(runtime):
    with Session(engine()) as db:
        workspace_ids = [w.id for w in db.exec(select(Workspace)).all()]
        for workspace_id in workspace_ids:
            ws = db.exec(
                select(Workspace)
                .where(Workspace.id == workspace_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one()
            owner = db.get(User, ws.user_id)
            active = db.exec(select(Operation).where(Operation.active_workspace_id == ws.id)).first()
            if active:
                continue
            if not owner.active or ws.desired_status == "stopped":
                if runtime.inspect(ws.id) != "missing":
                    runtime.stop(ws.id)
                if ws.status != "error":
                    ws.status = "stopped"
                ws.desired_status = "stopped"
                ws.runtime_address = ws.runtime_cookie = ""
            elif ws.status == "running" and runtime.inspect(ws.id) != "running":
                ws.status = "error"
                ws.desired_status = "stopped"
                ws.error = "运行环境已退出，数据已保留，请重新启动"
                ws.runtime_address = ws.runtime_cookie = ""
            db.add(ws)
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
