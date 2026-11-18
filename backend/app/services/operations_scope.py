"""One project content policy for pages, reports, notifications and assistant calls."""
from sqlalchemy import select
from app.core.audit import log_op
from app.core.response import bad_request, conflict, forbidden
from app.models import OpsMember, OpsMemberView, OpsProject, OpsProjectControl, OpsProjectFeatures, OpsTask, SysUser

LEGACY_SECTIONS = {"tasks", "progress", "outreach", "procurement", "income", "products", "team", "reports", "funds"}
ALARM_FEATURES = {"device_maintenance", "arrears", "collection_maintenance", "customers", "warehouse", "scrap"}
SECTIONS = {"tasks": "任务", "progress": "项目进度", "outreach": "学校与市场开拓", "procurement": "学校采购",
            "income": "每日营收", "products": "设备产品", "team": "项目团队", "reports": "周报月报", "funds": "经费与审批",
            "warehouse": "仓库物料", "device_maintenance": "设备运维", "arrears": "欠费维护",
            "collection_maintenance": "回款维护", "customers": "客户资料维护", "scrap": "设备报废"}
BASE_FEATURES = {"tasks", "team"}
FEATURE_LABELS = {**SECTIONS, "tasks": "任务情况", "outreach": "市场开拓", "income": "收入情况", "products": "设备产品库",
                  "reports": "周报 / 月报"}
FEATURE_ORDER = ("tasks", "team", "progress", "reports", "outreach", "procurement", "income", "products", "funds",
                 "warehouse", "device_maintenance", "arrears", "collection_maintenance", "customers", "scrap")
SCHOOL_FEATURES = {"outreach", "procurement", "income"}


def feature_options():
    return {"features": [{"value": key, "label": FEATURE_LABELS[key], "required": key in BASE_FEATURES,
                           "depends_on": ["products"] if key in {"procurement", "warehouse", "scrap"} else []} for key in FEATURE_ORDER],
            "feature_presets": {"campus": [key for key in FEATURE_ORDER if key in LEGACY_SECTIONS | {"warehouse"}],
                                "alarm": [key for key in FEATURE_ORDER if key in BASE_FEATURES | ALARM_FEATURES | {"products", "reports", "progress"}],
                                "general": ["tasks", "team", "progress", "reports"]}}


def enabled_features(db, project):
    row = db.get(OpsProjectFeatures, project.id)
    return set(row.sections) if row else LEGACY_SECTIONS | {"warehouse"} | (ALARM_FEATURES if project.name.strip() == "一键报警" else set())


def feature_settings(db, project):
    row = db.get(OpsProjectFeatures, project.id)
    enabled = enabled_features(db, project)
    return {"configured": bool(row), "revision": row.revision if row else 0,
            "sections": [key for key in FEATURE_ORDER if key in enabled],
            "legacy_campus_income": row.legacy_campus_income if row else project.name == "智慧校园"}


def save_features(db, p, project, sections, expected_revision, *, creating, legacy_campus_income=False):
    before = feature_settings(db, project)
    if not creating and expected_revision != before["revision"]:
        raise conflict("项目标准功能已变化，请刷新后重试")
    chosen = set(sections) | BASE_FEATURES
    if chosen & {"procurement", "warehouse", "scrap"}:
        chosen.add("products")
    selected = [key for key in FEATURE_ORDER if key in chosen]
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
    if ops.can_view_all(p) or ops.role(db, p, project) == "manager":
        return {"sections": [key for key in SECTIONS if key in enabled], "task_scope": "team", "revision": 0, "managed": True}
    member = db.scalar(select(OpsMember).where(OpsMember.project_id == project.id, OpsMember.user_id == p.id))
    if not member:
        return {"sections": ["tasks"], "task_scope": "self", "revision": 0, "managed": False}
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
    rows = {r.user_id: r for r in db.scalars(select(OpsMemberView).where(OpsMemberView.project_id == project.id))}
    enabled = enabled_features(db, project)
    return {"sections": {k: v for k, v in SECTIONS.items() if k in enabled}, "items": [{**m,
        "sections": [k for k in (rows[m["user_id"]].sections if m["user_id"] in rows else SECTIONS) if k in enabled],
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
    enabled = enabled_features(db, project)
    stored = set(before["sections"]) if before else set(SECTIONS)
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
    before = controls(db, project_id)
    if body.expected_revision != before["revision"]:
        raise conflict("项目设置已变化，请刷新后重试")
    code = body.business_project_code or None
    enabled = enabled_features(db, project)
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


def redact_schools(rows, sections):
    allowed = {"id", "name", "region"}
    if "outreach" in sections:
        allowed |= {"stage", "potential", "next_action", "next_contact_date", "note", "latest_visit"}
    if "procurement" in sections:
        allowed |= {"equipment", "equipment_cost", "labor_cost", "other_cost", "total_investment", "unpriced_count", "review_count"}
    if "income" in sections:
        allowed |= {"incomes", "total_income"}
    return [{k: v for k, v in r.items() if k in allowed} for r in rows]
