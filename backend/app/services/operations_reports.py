"""Versioned, permission-checked operation reports, shared by UI and scheduler."""
from sqlalchemy import select

from app.core.audit import log_op
from app.core.response import encode, forbidden, not_found
from app.core.timeutil import now
from app.models import OpsDigestPreference, OpsDigestSnapshot, OpsTask, OpsProject
from app.services import operations_service as ops, operations_digest as digest, operations_policy as policy, operations_scope as scope


def preferences(db, p, project_id):
    project = ops.get_project(db, p, project_id)
    scope.require(db, p, project, "reports")
    row = db.get(OpsDigestPreference, project_id)
    return {"weekly": row.weekly if row else True, "monthly": row.monthly if row else True,
            "can_manage": ops.role(db, p, project) == "manager",
            "schedule": "每周一 08:15 生成上周、每月 1 日 08:20 生成上月；仅进行中的项目及有效执行成员。"}


def set_preferences(db, p, project_id, body):
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    scope.require(db, p, project, "reports")
    row = db.get(OpsDigestPreference, project_id) or OpsDigestPreference(project_id=project_id, updated_by=p.id)
    row.weekly, row.monthly, row.updated_by = body.weekly, body.monthly, p.id
    db.add(row)
    log_op(db, p.id, "operations.digest.schedule", "ops_project", project_id, after=body.model_dump())


def can_read(db, p, row):
    project = ops.get_project(db, p, row.project_id)
    if not scope.can(db, p, project, "reports"):
        return False
    manager = ops.role(db, p, project) == "manager"
    if row.audience == "project" and not manager:
        return False
    if row.audience == "personal" and row.owner_id != p.id and not manager:
        return False
    ids = {r["id"] for r in row.facts.get("items", [])}
    ids.update(r["task_id"] for r in row.facts.get("project_progress", []) if r.get("task_id"))
    allowed = set(db.scalars(select(OpsTask.id).where(OpsTask.id.in_(ids), policy.task_access(p))))
    if ids != allowed:
        return False
    if row.facts.get("visits") and not scope.can(db, p, project, "outreach"):
        return False
    if any(not scope.can(db, p, project, section) for section in row.facts.get("content_sections", [])):
        return False
    return True


def brief(row):
    return {"id": row.id, "project_id": row.project_id, "owner_id": row.owner_id, "audience": row.audience,
            "mode": row.mode, "period": row.period, "version": row.version, "created_at": row.created_at,
            "automatic": row.automatic, "person": row.facts.get("person", "项目团队")}


def get(db, p, report_id):
    row = db.get(OpsDigestSnapshot, report_id)
    if not row:
        raise not_found("报告")
    if not can_read(db, p, row):
        raise forbidden("当前权限不能读取该报告中的全部记录")
    return {**brief(row), "text": row.text, "facts": row.facts}


def listing(db, p, project_id=None, mode=None, period="", mine=False, owner_id=None):
    if project_id:
        ops.get_project(db, p, project_id)
    q = select(OpsDigestSnapshot).where(OpsDigestSnapshot.project_id.in_(ops.visible_projects(p).with_only_columns(OpsProject.id)))
    if project_id:
        q = q.where(OpsDigestSnapshot.project_id == project_id)
    if mode:
        q = q.where(OpsDigestSnapshot.mode == mode)
    if period:
        q = q.where(OpsDigestSnapshot.period == period)
    if mine:
        q = q.where(OpsDigestSnapshot.owner_id == p.id, OpsDigestSnapshot.audience == "personal")
    elif owner_id:
        q = q.where(OpsDigestSnapshot.owner_id == owner_id, OpsDigestSnapshot.audience == "personal")
    return [brief(row) for row in db.scalars(q.order_by(OpsDigestSnapshot.id.desc()).limit(300)) if can_read(db, p, row)]


def generate(db, p, project_id, mode, period="", mine=False, new_version=False, automatic=False, owner_id=None):
    project = ops.get_project(db, p, project_id, lock=True)
    scope.require(db, p, project, "reports")
    actor_id = p.id
    subject = p
    if owner_id:
        if owner_id != p.id:
            ops.manage(db, p, project)
            from app.core.deps import build_principal
            if owner_id not in {m["user_id"] for m in ops.members(db, project) if m["active"] and m["role"] != "viewer"}:
                raise forbidden("个人报告对象须为本项目有效执行成员")
            subject = build_principal(ops.user(db, owner_id))
            if not subject.has_menu("operations"):
                raise forbidden("该成员未开通运营项目")
            scope.require(db, subject, project, "reports")
        mine = True
    if not mine:
        ops.manage(db, p, project)
    audience = "personal" if mine else "project"
    owner_id = subject.id if mine else project.owner_id
    facts = digest.period_summary(db, subject, mode, period, project_id, mine)
    history = select(OpsDigestSnapshot).where(OpsDigestSnapshot.project_id == project_id,
        OpsDigestSnapshot.owner_id == owner_id, OpsDigestSnapshot.audience == audience, OpsDigestSnapshot.mode == mode,
        OpsDigestSnapshot.period == facts["period"]).order_by(OpsDigestSnapshot.version.desc()).limit(1)
    previous = db.scalar(history)
    if previous and not new_version:
        if not can_read(db, p, previous):
            raise forbidden("旧报告包含目前无权查看的事项；可按当前权限另存新版本")
        return previous
    facts["note"] = "已保存的期间报告；任务归属及名称按生成时记录，后来补录或更正请另存新版本。未反馈不推测进展。"
    facts["project_name"] = project.name
    facts["text"] = digest.render_text(facts)
    version = db.scalar(history.with_only_columns(OpsDigestSnapshot.version).execution_options(include_removed_records=True)) or 0
    row = OpsDigestSnapshot(project_id=project_id, owner_id=owner_id, audience=audience, mode=mode,
        period=facts["period"], version=version + 1, generated_by=actor_id,
        automatic=automatic, facts=encode(facts), text=facts["text"], created_at=now())
    db.add(row)
    db.flush()
    log_op(db, p.id, "operations.digest.generate", "ops_digest_snapshot", row.id,
           after={"project_id": project_id, "mode": mode, "period": row.period, "version": row.version, "automatic": automatic})
    return row
