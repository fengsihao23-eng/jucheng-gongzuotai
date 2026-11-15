"""One project content policy for pages, reports, notifications and assistant calls."""
from sqlalchemy import select
from app.core.audit import log_op
from app.core.response import bad_request, conflict, forbidden
from app.models import OpsMember, OpsMemberView, OpsProject, OpsProjectControl, OpsTask, SysUser

SECTIONS = {"tasks": "任务", "progress": "项目进度", "outreach": "学校与市场开拓", "procurement": "学校采购",
            "income": "每日营收", "products": "设备产品", "team": "项目团队", "reports": "周报月报", "funds": "经费与审批"}


def view(db, p, project):
    from app.services import operations_service as ops
    if ops.can_view_all(p) or ops.role(db, p, project) == "manager":
        return {"sections": list(SECTIONS), "task_scope": "team", "revision": 0, "managed": True}
    member = db.scalar(select(OpsMember).where(OpsMember.project_id == project.id, OpsMember.user_id == p.id))
    if not member:
        return {"sections": ["tasks"], "task_scope": "self", "revision": 0, "managed": False}
    row = db.get(OpsMemberView, (project.id, p.id))
    return {"sections": row.sections if row else list(SECTIONS), "task_scope": row.task_scope if row else "team",
            "revision": row.revision if row else 0, "managed": False}


def can(db, p, project, section):
    return section in view(db, p, project)["sections"]


def require(db, p, project, section):
    if not can(db, p, project, section):
        raise forbidden(f"本项目未授权查看{SECTIONS[section]}")


def members_config(db, p, project):
    from app.services import operations_service as ops
    from app.core.deps import build_principal
    ops.manage(db, p, project)
    rows = {r.user_id: r for r in db.scalars(select(OpsMemberView).where(OpsMemberView.project_id == project.id))}
    return {"sections": SECTIONS, "items": [{**m,
        "sections": rows[m["user_id"]].sections if m["user_id"] in rows else list(SECTIONS),
        "task_scope": rows[m["user_id"]].task_scope if m["user_id"] in rows else "team",
        "revision": rows[m["user_id"]].revision if m["user_id"] in rows else 0,
        "locked": m["is_owner"] or m["role"] in {"manager", "co_owner"} or ops.can_view_all(build_principal(db.get(SysUser, m["user_id"])))}
        for m in ops.members(db, project)]}


def save_member_view(db, p, project_id, user_id, body):
    from app.services import operations_service as ops
    from app.core.deps import build_principal
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    member = db.scalar(select(OpsMember).where(OpsMember.project_id == project_id, OpsMember.user_id == user_id))
    if project.owner_id == user_id or not member or member.role in {"manager", "co_owner"} or ops.can_view_all(build_principal(ops.user(db, user_id))):
        raise bad_request("项目管理者保留完整管理范围；请为普通成员配置可见内容")
    row = db.get(OpsMemberView, (project_id, user_id))
    if body.expected_revision != (row.revision if row else 0):
        raise conflict("此人的项目可见范围已变更，请重新查看")
    before = {"sections": row.sections, "task_scope": row.task_scope} if row else None
    row = row or OpsMemberView(project_id=project_id, user_id=user_id, revision=0, updated_by=p.id)
    row.sections = sorted(set(body.sections) | {"tasks"})
    row.task_scope, row.updated_by = body.task_scope, p.id
    row.revision += 1
    db.add(row)
    log_op(db, p.id, "operations.member.visibility", "ops_project", project_id, before=before,
           after={"user_id": user_id, "sections": row.sections, "task_scope": row.task_scope})
    return row


def controls(db, project_id):
    row = db.get(OpsProjectControl, project_id)
    return {"wecom_reminders": row.wecom_reminders if row else True,
            "business_project_code": row.business_project_code if row else None,
            "report_audience": row.report_audience if row else "all",
            "report_user_ids": row.report_user_ids if row else [], "revision": row.revision if row else 0}


def save_controls(db, p, project_id, body):
    from app.services import operations_service as ops, project_service
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    before = controls(db, project_id)
    if body.expected_revision != before["revision"]:
        raise conflict("项目设置已变化，请刷新后重试")
    code = body.business_project_code or None
    if code and code != before["business_project_code"]:
        if not p.has_menu("projects"):
            raise forbidden("关联业务项目需项目台账查看权限")
        project_service.get_project(db, code, p)
    valid = {m["user_id"] for m in ops.members(db, project) if m["active"] and m["role"] != "viewer"}
    if set(body.report_user_ids) - valid:
        raise bad_request("报告对象须为本项目有效执行成员")
    row = db.get(OpsProjectControl, project_id) or OpsProjectControl(project_id=project_id, revision=0, updated_by=p.id)
    row.wecom_reminders, row.business_project_code = body.wecom_reminders, code
    row.report_audience, row.report_user_ids = body.report_audience, sorted(set(body.report_user_ids)) if body.report_audience == "selected" else []
    row.updated_by, row.revision = p.id, row.revision + 1
    db.add(row)
    db.flush()
    log_op(db, p.id, "operations.project.control", "ops_project", project_id, before=before, after=controls(db, project_id))


def content_project_ids(db, p, section):
    from app.services import operations_service as ops
    return [r.id for r in db.scalars(ops.visible_projects(p)) if can(db, p, r, section)]


def redact_schools(rows, sections):
    allowed = {"id", "name", "region"}
    if "outreach" in sections:
        allowed |= {"stage", "potential", "next_action", "next_contact_date", "note", "latest_visit"}
    if "procurement" in sections:
        allowed |= {"equipment", "equipment_cost", "labor_cost", "other_cost", "total_investment", "unpriced_count", "review_count"}
    if "income" in sections:
        allowed |= {"incomes", "total_income"}
    return [{k: v for k, v in r.items() if k in allowed} for r in rows]
