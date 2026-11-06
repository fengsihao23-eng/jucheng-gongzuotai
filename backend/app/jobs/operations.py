"""每天 16:00 汇总运营项目到期、逾期、受阻及待反馈事项到站内通知。"""
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.timeutil import today
from app.jobs.registry import job
from app.models import OpsProject, OpsTask, SysNotify, SysUser
from app.services import notify_service, operations_service as ops


@job("operations_task_reminder", "运营任务进展提醒", {"hour": 16, "minute": 0}, "每日 16:00",
     idempotency=lambda p: f"operations_task_reminder:{today()}")
def operations_task_reminder(db, params):
    projects = db.scalars(select(OpsProject).where(OpsProject.status == "active")).all()
    sent = 0
    for project in projects:
        members = {m["user_id"] for m in ops.members(db, project) if m["active"] and m["role"] != "viewer"}
        tasks = db.scalars(select(OpsTask).where(OpsTask.project_id == project.id, OpsTask.status != "done")).all()
        for task in tasks:
            owner = db.get(SysUser, task.owner_id)
            if not owner or task.owner_id not in members:
                continue
            data = ops.task_dict(db, build_principal(owner), project, task)
            reasons = [label for label, yes in [("已逾期", data["overdue"]), ("遇到阻碍", task.status == "blocked"),
                       ("今日待反馈", data["needs_update"]), ("今日到期", task.due_date == today() or
                        any(n.due_date == today() and not n.completed_at for n in task.nodes))] if yes]
            if not reasons:
                continue
            targets = {task.owner_id}
            if data["overdue"] or task.status == "blocked":
                targets.add(project.owner_id)
            reference = f"{task.id}:{today()}"
            existing = set(db.scalars(select(SysNotify.user_id).where(SysNotify.ref_type == "ops_task_reminder", SysNotify.ref_id == reference)))
            users = db.scalars(select(SysUser).where(SysUser.id.in_(targets - existing))).all()
            rows = notify_service.send(db, users, "operations_reminder", "运营任务进展提醒",
                f"{project.name} · {task.title}\n{'、'.join(reasons)}；负责人：{owner.name}；截止：{task.due_date}。请反馈已完成的工作和下一步安排。",
                link=f"/operations/{project.id}?task={task.id}", ref_type="ops_task_reminder", ref_id=reference, wecom=False)
            sent += len(rows)
    db.commit()
    return {"rows": sent, "message": f"已生成 {sent} 条运营任务站内提醒"}
