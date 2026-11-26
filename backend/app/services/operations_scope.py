"""One project content policy for pages, reports, notifications and assistant calls."""
from sqlalchemy import select
from app.core.audit import log_op
from app.core.permission_catalog import OPERATION_SECTIONS
from app.core.response import bad_request, conflict, forbidden
from app.models import OpsMember, OpsMemberView, OpsProjectControl, OpsProjectFeatures, SysUser

LEGACY_SECTIONS = {"tasks", "progress", "outreach", "procurement", "income", "products",
                   "team", "reports", "funds", "competitors", "warehouse", "settings"}
ALARM_FEATURES = {"device_maintenance", "arrears", "collection_maintenance", "customers", "warehouse", "scrap"}
SECTIONS = {**OPERATION_SECTIONS, "tasks": "任务情况", "arrears": "欠费维护"}
SCHOOL_FEATURES = {"outreach", "procurement", "income"}


def feature_options():
    common = {"tasks", "team", "competitors", "progress", "reports", "outreach", "user_list", "settings"}
    presets = {"campus": common | LEGACY_SECTIONS, "alarm": common | ALARM_FEATURES | {"products"},
               "general": common}
    return {"features": [{"value": key, "label": label, "required": False, "depends_on": []}
                         for key, label in SECTIONS.items()],
            "feature_presets": {name: [key for key in SECTIONS if key in keys] for name, keys in presets.items()}}


def _legacy_features(project):
    return LEGACY_SECTIONS | (ALARM_FEATURES if project.name.strip() == "一键报警" else set())


def enabled_features(db, project):
    row = db.get(OpsProjectFeatures, project.id)
    return set(row.sections) if row else _legacy_features(project)


def feature_settings(db, project):
    row = db.get(OpsProjectFeatures, project.id)
    enabled = set(row.sections) if row else _legacy_features(project)
    return {"configured": bool(row), "revision": row.revision if row else 0,
            "sections": [key for key in SECTIONS if key in enabled],
            "legacy_campus_income": row.legacy_campus_income if row else project.name == "智慧校园"}


def save_features(db, p, project, sections, expected_revision, *, creating, legacy_campus_income=False):
    before = feature_settings(db, project)
    if not creating and expected_revision != before["revision"]:
        raise conflict("项目标准功能已变化，请刷新后重试")
    chosen = set(sections)
    selected = [key for key in SECTIONS if key in chosen]
    row = db.get(OpsProjectFeatures, project.id)
    if row and row.sections == selected:
        return
    if not row:
        row = OpsProjectFeatures(project_id=project.id, revision=0, legacy_campus_income=legacy_campus_income)
    row.sections, row.updated_by, row.revision = selected, p.id, row.revision + 1
    db.add(row)
    db.flush()
    log_op(db, p.id, "operations.project.features", "ops_project", project.id, before=before,
           after=feature_settings(db, project))


def view(db, p, project):
    from app.services import operations_service as ops
    enabled = enabled_features(db, project)
    if not ops.has_project_content(db, p, project):
        return {"sections": [], "task_scope": "self", "revision": 0, "managed": False}
    if not ops.is_superuser(p):
        enabled = {key for key in enabled if p.has_section("operations", key)}
    if ops.role(db, p, project) == "manager":
        return {"sections": [key for key in SECTIONS if key in enabled], "task_scope": "team", "revision": 0, "managed": True}
    enabled.discard("settings")
    member = db.scalar(select(OpsMember).where(OpsMember.project_id == project.id, OpsMember.user_id == p.id))
    if not member:
        return {"sections": [], "task_scope": "self", "revision": 0, "managed": False}
    row = db.get(OpsMemberView, (project.id, p.id))
    return {"sections": [key for key in (row.sections if row else SECTIONS) if key in enabled],
            "task_scope": row.task_scope if row else "team",
            "revision": row.revision if row else 0, "managed": False}


def can(db, p, project, section):
    return section in view(db, p, project)["sections"]


def require(db, p, project, section):
    if not can(db, p, project, section):
        raise forbidden(f"本项目未授权查看{SECTIONS[section]}")


def require_school_features(db, p, project):
    if not set(view(db, p, project)["sections"]) & SCHOOL_FEATURES:
        raise forbidden("本项目未开通或未授权使用学校相关功能")


def members_config(db, p, project):
    from app.services import operations_service as ops
    from app.core.deps import build_principal
    ops.manage(db, p, project)
    require(db, p, project, "team")
    rows = {r.user_id: r for r in db.scalars(select(OpsMemberView).where(OpsMemberView.project_id == project.id))}
    enabled = enabled_features(db, project) - {"settings"}
    return {"sections": {k: v for k, v in SECTIONS.items() if k in enabled}, "items": [{**m,
        "sections": [k for k in (rows[m["user_id"]].sections if m["user_id"] in rows else SECTIONS) if k in enabled],
        "task_scope": rows[m["user_id"]].task_scope if m["user_id"] in rows else "team",
        "revision": rows[m["user_id"]].revision if m["user_id"] in rows else 0,
        "locked": ops.role(db, build_principal(db.get(SysUser, m["user_id"])), project) == "manager"}
        for m in ops.members(db, project)]}


def save_member_view(db, p, project_id, user_id, body):
    from app.services import operations_service as ops
    from app.core.deps import build_principal
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    require(db, p, project, "team")
    member = db.scalar(select(OpsMember).where(OpsMember.project_id == project_id, OpsMember.user_id == user_id))
    if not member or ops.role(db, build_principal(ops.user(db, user_id)), project) == "manager":
        raise bad_request("项目管理者保留完整管理范围；请为普通成员配置可见内容")
    row = db.get(OpsMemberView, (project_id, user_id))
    if body.expected_revision != (row.revision if row else 0):
        raise conflict("此人的项目可见范围已变更，请重新查看")
    before = {"sections": row.sections, "task_scope": row.task_scope} if row else None
    row = row or OpsMemberView(project_id=project_id, user_id=user_id, revision=0, updated_by=p.id)
    enabled = enabled_features(db, project) - {"settings"}
    stored = (set(before["sections"]) if before else set(SECTIONS)) - {"settings"}
    row.sections = sorted((stored - enabled) | (set(body.sections) & enabled) | {"tasks"})
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
    require(db, p, project, "settings")
    before = controls(db, project_id)
    if body.expected_revision != before["revision"]:
        raise conflict("项目设置已变化，请刷新后重试")
    code = body.business_project_code or None
    enabled = set(view(db, p, project)["sections"])
    if "funds" not in enabled and code != before["business_project_code"]:
        raise forbidden("本项目未开通经费与审批")
    report_ids = sorted(set(body.report_user_ids)) if body.report_audience == "selected" else []
    if "reports" not in enabled and (body.report_audience, report_ids) != (before["report_audience"], before["report_user_ids"]):
        raise forbidden("本项目未开通周报月报")
    if code and code != before["business_project_code"]:
        if not p.has_menu("projects"):
            raise forbidden("关联业务项目需项目台账查看权限")
        project_service.get_project(db, code, p)
    valid = {m["user_id"] for m in ops.members(db, project) if m["active"] and m["role"] != "viewer"}
    if "reports" in enabled and set(body.report_user_ids) - valid:
        raise bad_request("报告对象须为本项目有效执行成员")
    row = db.get(OpsProjectControl, project_id) or OpsProjectControl(project_id=project_id, revision=0, updated_by=p.id)
    row.wecom_reminders, row.business_project_code = body.wecom_reminders, code
    row.report_audience, row.report_user_ids = body.report_audience, report_ids
    row.updated_by, row.revision = p.id, row.revision + 1
    db.add(row)
    db.flush()
    log_op(db, p.id, "operations.project.control", "ops_project", project_id, before=before, after=controls(db, project_id))


def content_project_ids(db, p, section):
    from app.services import operations_service as ops
    return [r.id for r in db.scalars(ops.visible_projects(p)) if can(db, p, r, section)]


def require_settings(p):
    from app.services import operations_service as ops
    if not ops.is_superuser(p) and not p.has_section("operations", "settings"):
        raise forbidden("未授权使用运营项目工作设置")


def redact_schools(rows, sections):
    allowed = {"id", "name", "region"}
    if "outreach" in sections:
        allowed |= {"stage", "potential", "next_action", "next_contact_date", "note", "latest_visit"}
    if "procurement" in sections:
        allowed |= {"equipment", "equipment_cost", "labor_cost", "other_cost", "total_investment", "unpriced_count", "review_count"}
    if "income" in sections:
        allowed |= {"incomes", "total_income"}
    return [{k: v for k, v in r.items() if k in allowed} for r in rows]
