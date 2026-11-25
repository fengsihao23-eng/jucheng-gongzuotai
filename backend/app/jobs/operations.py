"""每天 09:00、15:00 提醒运营项目未完成的任务和阶段节点。"""
from sqlalchemy import select

from app.core.deps import build_principal
from app.core.timeutil import now, today
from app.jobs.registry import job
from app.models import OpsProject, OpsTask, OpsTaskDiscussion, SysNotify, SysUser
from app.services import notify_service, operations_service as ops
from app.services.operations_notifications import needs_escalation
from app.services.operations_policy import archived
from app.services import operations_policy as policy
from app.services import operations_scope as scope


def _reminder_slot():
    local = now()
    return local.date(), "09" if local.hour < 15 else "15"


def _reminder_key(_params):
    day, slot = _reminder_slot()
    return f"operations_task_reminder:{day}:{slot}"


@job("operations_task_reminder", "运营任务与节点提醒", {"hour": "9,15", "minute": 0}, "每日 09:00、15:00",
     idempotency=_reminder_key)
def operations_task_reminder(db, params):
    day, slot = _reminder_slot()
    projects = db.scalars(select(OpsProject).where(OpsProject.status == "active")).all()
    sent = 0
    pending_ids = []
    for project in projects:
        members = {m["user_id"] for m in ops.members(db, project) if m["active"] and m["role"] != "viewer"}
        wecom = scope.controls(db, project.id)["wecom_reminders"]
        tasks = db.scalars(select(OpsTask).where(OpsTask.project_id == project.id, OpsTask.status != "done", OpsTask.approval_status == "approved", ~archived())).all()
        for task in tasks:
            if task.owner_id not in members:
                continue
            targets = {task.owner_id}
            if needs_escalation(task, day):
                targets.add(project.owner_id)
            reference = f"{task.id}:{day}:{slot}"
            existing = set(db.scalars(select(SysNotify.user_id).where(SysNotify.ref_type == "ops_task_reminder", SysNotify.ref_id == reference)))
            users = [u for u in db.scalars(select(SysUser).where(SysUser.id.in_(targets - existing)))
                     if build_principal(u).has_menu("operations") and db.scalar(select(OpsTask.id).where(
                         OpsTask.id == task.id, policy.task_access(build_principal(u))))]
            nodes = [f"· {n.name}（{n.due_date} 截止）" for n in task.nodes if not n.completed_at]
            details = [f"{project.name} · {task.title}",
                f"{ops.STATUS_LABELS[task.status]}；负责人：{task.owner.name}；任务截止：{task.due_date}。"]
            if nodes:
                details.extend(["待完成节点：", *nodes])
            rows = notify_service.send(db, users, "operations_reminder", "运营任务待完成提醒", "\n".join(details),
                link=f"/operations/{project.id}?task={task.id}", ref_type="ops_task_reminder", ref_id=reference,
                wecom=wecom, defer=True, wecom_content="请打开工作台查看未完成的任务及阶段节点。")
            sent += sum(r.channel == "inapp" for r in rows)
            pending_ids.extend(r.id for r in rows if r.channel == "wecom_app")
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
            reference = f"{request.id}:{day}"
            if db.scalar(select(SysNotify.id).where(SysNotify.ref_type == "ops_request_reminder", SysNotify.ref_id == reference,
                                                   SysNotify.user_id == recipient.id)):
                continue
            rows = notify_service.send(db, [recipient], "operations_request", "还有一条任务要求待回复",
                "请打开原任务查看要求并回复。已读通知不会结束待回复事项。",
                link=f"/operations/{project.id}?task={request.task_id}", ref_type="ops_request_reminder", ref_id=reference,
                wecom=wecom, defer=True,
                wecom_content="你有一条运营任务要求待回复，请打开工作台查看。")
            sent += sum(r.channel == "inapp" for r in rows)
            pending_ids.extend(r.id for r in rows if r.channel == "wecom_app")
    db.commit()
    # 先提交消息，再在当次调度投递；遇到失败由通用队列继续重试。
    if pending_ids:
        db.expire_all()
        notify_service.deliver_pending(db, pending_ids)
        db.commit()
    return {"rows": sent, "message": f"已生成 {sent} 条站内提醒、{len(pending_ids)} 条企业微信通知"}


@job("task_feedback_deadline_reminders", "运营与重点项目逾期未反馈提醒", {"minute": "*/5"}, "每 5 分钟")
def task_feedback_deadline_reminders(db, params):
    from app.services import key_projects, operations_notifications

    at = now()
    notices = operations_notifications.scan_feedback_deadlines(db, at) + key_projects.scan_feedback_deadlines(db, at)
    pending = [notice.id for notice in notices if notice.channel == "wecom_app"]
    db.commit()
    if pending:
        db.expire_all()
        notify_service.deliver_pending(db, pending)
        db.commit()
    sent = len(notices) - len(pending)
    return {"rows": sent, "message": f"已生成 {sent} 条逾期未反馈提醒、{len(pending)} 条企业微信通知"}


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
