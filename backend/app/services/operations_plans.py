"""Monthly plans and explicit two-stage reviews; shared by the UI and assistant."""
from collections import defaultdict
from datetime import datetime
import re

from sqlalchemy import select

from app.core.audit import log_op, snapshot
from app.core.deps import build_principal
from app.core.response import bad_request, conflict, forbidden
from app.core.timeutil import TZ, month_range, now, today
from app.models import OpsMember, OpsTaskReview, SysUser


def company_reviewer(p):
    return p.is_admin or p.can("operations:approve")


def department_reviewer(db, project, user_id):
    return project.owner_id == user_id or db.scalar(select(OpsMember.id).where(
        OpsMember.project_id == project.id, OpsMember.user_id == user_id, OpsMember.role.in_(["manager", "reviewer"]))) is not None


def validate_reviewers(db, project, owner_id, department_id, company_id):
    from app.services import operations_service as ops
    if not department_id or not company_id:
        raise bad_request("自行安排任务须指定部门审核人和公司审核人")
    if len({owner_id, department_id, company_id}) != 3:
        raise bad_request("提交人、部门审核人和公司审核人须为不同人员")
    dept = ops.user(db, department_id)
    company = ops.user(db, company_id)
    if not department_reviewer(db, project, dept.id):
        raise bad_request("部门审核人须为项目负责人、项目管理员或团队中指定的部门审核人")
    if not company_reviewer(build_principal(company)):
        raise bad_request("公司审核人须具有公司审核权限")


def review_stage(db, p, project, task):
    if task.source != "self" or p.id in {task.owner_id, task.creator_id}:
        return None
    if task.approval_status == "department_pending" and task.department_reviewer_id == p.id and department_reviewer(db, project, p.id):
        return "department"
    if task.approval_status == "company_pending" and task.company_reviewer_id == p.id and company_reviewer(p):
        return "company"
    return None


def options(db, p, project):
    from app.services import operations_service as ops
    team = ops.members(db, project)
    candidates = db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False)).order_by(SysUser.id)).all()
    return {"department_reviewers": [{"id": m["user_id"], "name": m["name"], "dept": m["dept"]}
                                     for m in team if m["active"] and m["role"] in {"manager", "reviewer"}],
            "company_reviewers": [{"id": u.id, "name": u.name, "dept": u.dept_code}
                                  for u in candidates if company_reviewer(build_principal(u))],
            "can_propose": ops.role(db, p, project) in {"manager", "member", "reviewer"}}


def review_task(db, p, task_id, body):
    from app.services import operations_service as ops
    task = ops.get_task(db, p, task_id, lock=True)
    project = ops.get_project(db, p, task.project_id, lock=True)
    ops.active(project)
    if task.revision != body.expected_revision:
        raise conflict("任务内容或审核状态已变化，请刷新后核对再审核")
    stage = review_stage(db, p, project, task)
    if not stage:
        raise forbidden("仅当前阶段的指定审核人可处理，不能跳级或审核自己的任务")
    validate_reviewers(db, project, task.owner_id, task.department_reviewer_id, task.company_reviewer_id)
    before = snapshot(task, ops.TASK_FIELDS)
    db.add(OpsTaskReview(task_id=task.id, reviewer_id=p.id, stage=stage, decision=body.decision,
                        comment=body.comment, task_revision=task.revision, task_snapshot=before, created_at=now()))
    label = "部门审核" if stage == "department" else "公司审核"
    if body.decision == "reject":
        task.approval_status = "rejected"
        recipients = [task.creator_id]
        title = "自行安排任务已退回"
    elif stage == "department":
        task.approval_status = "company_pending"
        recipients = [task.company_reviewer_id]
        title = "任务等待公司审核"
    else:
        task.approval_status = "approved"
        if task.original_due_date is None:
            task.original_due_date = task.due_date
        recipients = [task.owner_id]
        title = "自行安排任务审核通过"
    task.revision += 1
    ops._event(db, p, task, "review", f"{label}{'通过' if body.decision == 'approve' else '退回'}：{body.comment}")
    log_op(db, p.id, "operations.task.review", "ops_task", task.id, before=before, after=snapshot(task, ops.TASK_FIELDS))
    ops._notify(db, recipients, project, task, title, f"{task.title} · {task.plan_month}\n{label}：{body.comment}")
    db.flush()
    return task


def monthly(db, p, month=None, owner_id=None, project_id=None, mine=False, source=None, review_mine=False, delay_review_mine=False):
    from app.services import operations_service as ops
    month = month or today().strftime("%Y-%m")
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
        raise bad_request("月份格式为 YYYY-MM")
    if project_id is not None:
        ops.get_project(db, p, project_id)
    rows = ops.tasks(db, p, project_id, mine)
    if owner_id is not None:
        rows = [r for r in rows if r["owner_id"] == owner_id]
    if source:
        rows = [r for r in rows if r["source"] == source]
    if review_mine:
        rows = [r for r in rows if r["can_review"]]
    if delay_review_mine:
        rows = [r for r in rows if r["can_review_delay"]]
    start, _ = month_range(month)
    selected = []
    for row in rows:
        carryover = row["plan_month"] < month and row["approval_status"] == "approved" and (
            row["status"] != "done" or (row["completed_at"] and datetime.fromisoformat(row["completed_at"]).astimezone(TZ).date() >= start))
        if row["plan_month"] == month or carryover:
            selected.append({**row, "carryover": carryover,
                             "completion": "已完成" if row["status"] == "done" else "未完成",
                             "reason": row["incomplete_reason"] or ("尚未开始" if row["status"] == "todo" else "待补充未完成原因")})
    groups = defaultdict(list)
    for row in selected:
        groups[(row["owner_id"], row["owner_name"])].append(row)
    approved = [r for r in selected if r["approval_status"] == "approved"]
    owner_stats = [{"owner_id": key[0], "owner_name": key[1], **ops.task_stats(values)}
                   for key, values in groups.items() if any(row["approval_status"] == "approved" for row in values)]
    return {"month": month, "items": selected, "groups": [{"owner_id": key[0], "owner_name": key[1],
        **{f"{origin}_tasks": [t for t in values if t["source"] == origin] for origin in ops.SOURCE_LABELS}}
        for key, values in groups.items()], "stats": ops.task_stats(selected), "owner_stats": owner_stats,
        "review_inbox": [t for t in selected if t["can_review"]],
        "source_counts": {origin: sum(t["source"] == origin for t in approved) for origin in ops.SOURCE_LABELS},
        "carryover_count": sum(t["carryover"] for t in selected), "today": today(),
        "available_months": sorted({r["plan_month"] for r in rows}),
        "note": "只统计已生效任务，含跨月事项，显示当前状态。准时按首次生效的截止日与实际完成日期比较（北京时间）；改期或认可延期原因不会改写准时结果。"}
