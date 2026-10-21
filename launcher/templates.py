"""Published templates are immutable; each workspace keeps its own snapshot."""

from fastapi import HTTPException
from sqlmodel import Session, select

from launcher.models import RuntimeTemplate, User, Workspace, WorkspaceRuntime
from launcher.schemas import TemplateSpec
from launcher.settings import settings

DEFAULT_TEMPLATE_ID = "builtin-standard-v1"


def template_snapshot(template: RuntimeTemplate) -> dict:
    return template.model_dump(include={"id", "slug", "name", "version", "description", "image", "spec"})


def available_template(db: Session, template_id: str | None) -> RuntimeTemplate:
    template = db.get(RuntimeTemplate, template_id or DEFAULT_TEMPLATE_ID)
    if not template or not template.published or not template.enabled:
        raise HTTPException(409, "所选模板尚未发布或已停用，请重新选择")
    return template


def runtime_binding(db: Session, ws: Workspace) -> WorkspaceRuntime:
    binding = db.get(WorkspaceRuntime, ws.id)
    if binding is None:
        snapshot = template_snapshot(db.get(RuntimeTemplate, DEFAULT_TEMPLATE_ID))
        owner = db.get(User, ws.user_id)
        snapshot["spec"] = {**snapshot["spec"], "cpu": owner.cpu, "memory_mb": owner.memory_mb}
        binding = WorkspaceRuntime(
            workspace_id=ws.id,
            snapshot=snapshot,
        )
        db.add(binding)
        db.flush()
    return binding


def effective_resources(snapshot: dict, user: User) -> dict:
    spec = TemplateSpec.model_validate(snapshot["spec"])
    return {"cpu": min(user.cpu, spec.cpu), "memory_mb": min(user.memory_mb, spec.memory_mb)}


def seed_templates(db: Session, name: str):
    if not db.get(RuntimeTemplate, DEFAULT_TEMPLATE_ID):
        db.add(
            RuntimeTemplate(
                id=DEFAULT_TEMPLATE_ID,
                slug="standard",
                name=name,
                version="1.0.0",
                description="沿用当前 DeepSeek Harness 底座，持久保存项目与会话。",
                image=settings().runtime_image,
                spec=TemplateSpec().model_dump(),
                published=True,
            )
        )
        db.flush()
    for ws in db.exec(select(Workspace)).all():
        runtime_binding(db, ws)


def runtime_summary(db: Session, ws: Workspace) -> dict:
    binding = db.get(WorkspaceRuntime, ws.id)
    if not binding:
        return {}
    user = db.get(User, ws.user_id)
    snapshot = binding.snapshot
    return {
        "template_id": snapshot["id"],
        "template_name": snapshot["name"],
        "template_version": snapshot["version"],
        "image": snapshot["image"],
        "contract_version": snapshot["spec"]["contract_version"],
        "compatibility_group": snapshot["spec"]["compatibility_group"],
        "resources": effective_resources(snapshot, user),
        "applied_template": {k: binding.applied_snapshot.get(k) for k in ("id", "name", "version", "image")},
        "actual_image_id": binding.actual_image_id,
        "actual_image_ref": binding.actual_image_ref,
        "checked_at": binding.checked_at,
        "max_runtime_minutes": binding.max_runtime_minutes,
        "stop_at": binding.stop_at,
    }
