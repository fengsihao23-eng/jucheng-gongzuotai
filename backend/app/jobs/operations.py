"""每天 16:00 汇总运营项目到期、逾期、受阻及待反馈事项到站内通知。"""
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.timeutil import today
from app.jobs.registry import job
from app.models import OpsProject, OpsTask, OpsTaskDiscussion, SysNotify, SysUser
from app.services import notify_service, operations_service as ops
from app.services.operations_policy import archived
from app.services import operations_policy as policy
from app.services import operations_scope as scope


@job("operations_task_reminder", "运营任务进展提醒", {"hour": 16, "minute": 0}, "每日 16:00",
     idempotency=lambda p: f"operations_task_reminder:{today()}")
def operations_task_reminder(db, params):
    projects = db.scalars(select(OpsProject).where(OpsProject.status == "active")).all()
    sent = 0
    for project in projects:
        members = {m["user_id"] for m in ops.members(db, project) if m["active"] and m["role"] != "viewer"}
        tasks = db.scalars(select(OpsTask).where(OpsTask.project_id == project.id, OpsTask.status != "done", OpsTask.approval_status == "approved", ~archived())).all()
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
            users = [u for u in db.scalars(select(SysUser).where(SysUser.id.in_(targets - existing)))
                     if build_principal(u).has_menu("operations") and db.scalar(select(OpsTask.id).where(
                         OpsTask.id == task.id, policy.task_access(build_principal(u))))]
            rows = notify_service.send(db, users, "operations_reminder", "运营任务进展提醒",
                f"{project.name} · {task.title}\n{'、'.join(reasons)}；负责人：{owner.name}；截止：{task.due_date}。请反馈已完成的工作和下一步安排。",
                link=f"/operations/{project.id}?task={task.id}", ref_type="ops_task_reminder", ref_id=reference,
                wecom=scope.controls(db, project.id)["wecom_reminders"], defer=True,
                wecom_content="你有待跟进的运营事项，请打开工作台反馈进展。")
            sent += sum(r.channel == "inapp" for r in rows)
        requests = db.scalars(select(OpsTaskDiscussion).join(OpsTask, OpsTask.id == OpsTaskDiscussion.task_id).where(
            OpsTask.project_id == project.id, OpsTask.status != "done", ~archived(), OpsTask.approval_status == "approved",
            OpsTaskDiscussion.kind == "request", OpsTaskDiscussion.resolved_at.is_(None))).all()
        for request in requests:
            recipient = db.get(SysUser, request.recipient_id)
            if not recipient or recipient.status != 1 or recipient.is_deleted:
                continue
            principal = build_principal(recipient)
            if not principal.has_menu("operations") or not db.scalar(select(OpsTask.id).where(
                    OpsTask.id == request.task_id, policy.task_access(principal))):
                continue
            reference = f"{request.id}:{today()}"
            if db.scalar(select(SysNotify.id).where(SysNotify.ref_type == "ops_request_reminder", SysNotify.ref_id == reference,
                                                   SysNotify.user_id == recipient.id)):
                continue
            rows = notify_service.send(db, [recipient], "operations_request", "还有一条任务要求待回复",
                "请打开原任务查看要求并回复。已读通知不会结束待回复事项。",
                link=f"/operations/{project.id}?task={request.task_id}", ref_type="ops_request_reminder", ref_id=reference,
                wecom=scope.controls(db, project.id)["wecom_reminders"], defer=True,
                wecom_content="你有一条运营任务要求待回复，请打开工作台查看。")
            sent += sum(r.channel == "inapp" for r in rows)
    db.commit()
    return {"rows": sent, "message": f"已生成 {sent} 条运营任务站内提醒"}


def generate_period_reports(db, mode):
    from app.models import OpsDigestPreference, OpsDigestSnapshot
    from app.services import operations_reports as reports
    from app.core.timeutil import last_week, prev_month
    period = last_week() if mode == "week" else prev_month(today().strftime("%Y-%m"))
    count = 0
    for project in db.scalars(select(OpsProject).where(OpsProject.status == "active")):
        setting = db.get(OpsDigestPreference, project.id)
        if setting and not (setting.weekly if mode == "week" else setting.monthly):
            continue
        owner = db.get(SysUser, project.owner_id)
        if not owner or owner.status != 1 or owner.is_deleted or not build_principal(owner).has_menu("operations"):
            continue
        users = {m["user_id"] for m in ops.members(db, project) if m["active"] and m["role"] != "viewer"}
        control = scope.controls(db, project.id)
        if control["report_audience"] == "selected":
            users &= set(control["report_user_ids"])
        for user_id, mine in [(owner.id, False), *[(uid, True) for uid in sorted(users)]]:
            user = db.get(SysUser, user_id)
            if not user or user.status != 1 or user.is_deleted:
                continue
            principal = build_principal(user)
            if not principal.has_menu("operations") or not scope.can(db, principal, project, "reports"):
                continue
            audience = "personal" if mine else "project"
            exists = db.scalar(select(OpsDigestSnapshot.id).where(OpsDigestSnapshot.project_id == project.id,
                OpsDigestSnapshot.owner_id == user_id, OpsDigestSnapshot.audience == audience,
                OpsDigestSnapshot.mode == mode, OpsDigestSnapshot.period == period))
            if exists:
                continue
            report = reports.generate(db, principal, project.id, mode, period, mine, automatic=True)
            notify_service.send(db, [user], "operations_digest", "运营工作总结已生成", f"{period} 的工作总结已保存，可查看原任务依据。",
                link=f"/operations/{project.id}?digest={report.id}", ref_type="ops_digest", ref_id=str(report.id), wecom=False)
            count += 1
    db.commit()
    return {"rows": count, "message": f"已生成 {count} 份 {period} 文字总结；重复运行保留已有版本"}


@job("operations_weekly_digest", "运营个人与项目周报", {"day_of_week": "mon", "hour": 8, "minute": 15}, "每周一 08:15")
def weekly_digest(db, params):
    return generate_period_reports(db, "week")


@job("operations_monthly_digest", "运营个人与项目月报", {"day": 1, "hour": 8, "minute": 20}, "每月 1 日 08:20")
def monthly_digest(db, params):
    return generate_period_reports(db, "month")
