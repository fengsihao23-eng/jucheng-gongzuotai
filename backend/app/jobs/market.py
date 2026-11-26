"""市场部 / 项目部定时任务。

- project_milestone_scan  每日 09:00：节点到期/延期扫描标红（PRD 6.5），到期前 3 天提醒责任人，延期提醒责任人+项目经理
- project_weekly_progress 每周一 09:00：给每个项目经理推送其负责项目的周进度，汇总推送市场部与总办
- market_weekly_report    每周一 10:30：生成上周市场部重点项目周报，推送 SC-01/SC-02/ZB-01
- market_monthly_report   每月 1 日 11:00：生成上月市场部重点项目月报
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import or_, select

from app.core.storage import save_bytes
from app.core.timeutil import last_week, prev_month, today, week_no
from app.jobs.registry import job
from app.models import BizMilestoneLog, BizProject, BizProjectMilestone, KeyProjectTaskPlan, RptReport, SysUser
from app.services import market_service, notify_service
from app.services import project_service as ps

DUE_LEAD_DAYS = 3


@job("key_project_feedback_reminders", "项目任务节点与反馈提醒", {"hour": 9, "minute": 10}, "每天 09:10（北京时间）")
def key_project_feedback_reminders(db, params):
    from app.services.key_projects import scan_reminders
    result = scan_reminders(db)
    db.commit()
    return result


def _day(params: dict) -> date:
    d = params.get("date")
    return date.fromisoformat(d) if d else today()


@job("project_milestone_scan", "项目节点到期/延期扫描", {"hour": 9, "minute": 0}, "每日 09:00",
     idempotency=lambda p: f"milestone_scan:{p.get('date') or today().isoformat()}")
def project_milestone_scan(db, params: dict) -> dict:
    from app.core.timeutil import utcnow
    T = _day(params)
    rows = db.execute(select(BizProjectMilestone, BizProject)
                      .join(BizProject, BizProject.id == BizProjectMilestone.project_id)
                      .outerjoin(KeyProjectTaskPlan, KeyProjectTaskPlan.milestone_id == BizProjectMilestone.id)
                      .where(BizProject.is_deleted.is_(False), BizProject.status.in_([2, 3, 4]),
                             or_(BizProject.is_key.is_(True), KeyProjectTaskPlan.milestone_id.is_(None)),
                             BizProjectMilestone.status.in_([1, 2, 4]),
                             BizProjectMilestone.plan_date.is_not(None))).all()
    users = {u.id: u for u in db.scalars(select(SysUser))}
    newly_delayed, due_soon = [], []
    for m, proj in rows:
        if m.plan_date < T and (m.status != 4 or not m.alert_flag):
            before = ps._snap(m)
            m.status, m.alert_flag = 4, True
            db.add(BizMilestoneLog(project_id=proj.id, milestone_id=m.id, action="update", before=before,
                                   after=ps._snap(m), operator_id=None, source="system", created_at=utcnow()))
            newly_delayed.append((m, proj))
        elif m.status in (1, 2) and T <= m.plan_date <= T + timedelta(days=DUE_LEAD_DAYS):
            due_soon.append((m, proj))
    sent = 0
    for m, proj in due_soon:
        u = users.get(m.owner_id)
        if u and not proj.is_key:
            notify_service.send(db, [u], "milestone_due", "项目节点即将到期",
                                f"**{proj.name}**\n节点「{m.node_name}」计划 {m.plan_date} 完成，"
                                f"剩余 {(m.plan_date - T).days} 天。\n岗位职责：{m.duty or '—'}",
                                link=f"/projects/{proj.project_code}", ref_type="milestone", ref_id=str(m.id))
            sent += 1
    for m, proj in newly_delayed:
        targets = {uid for uid in (m.owner_id, proj.owner_id) if uid}
        us = [users[i] for i in targets if i in users]
        if us and not proj.is_key:
            notify_service.send(db, us, "milestone_delay", "项目节点延期",
                                f"**{proj.name}**（{proj.project_code}）\n<font color=\"warning\">节点「{m.node_name}」"
                                f"计划 {m.plan_date} 未完成，已延期 {(T - m.plan_date).days} 天并标红。</font>\n"
                                f"责任人：{users[m.owner_id].name if m.owner_id in users else '—'}；"
                                f"进展：{m.progress_note or '请尽快更新进展'}",
                                link=f"/projects/{proj.project_code}", ref_type="milestone", ref_id=str(m.id))
            sent += len(us)
    db.commit()
    return {"rows": len(newly_delayed) + len(due_soon),
            "message": f"新增延期 {len(newly_delayed)} 个，3 天内到期 {len(due_soon)} 个，提醒 {sent} 人次"}


@job("project_weekly_progress", "项目周进度推送", {"day_of_week": "mon", "hour": 9, "minute": 0}, "每周一 09:00",
     idempotency=lambda p: f"project_weekly_progress:{p.get('week') or last_week()}")
def project_weekly_progress(db, params: dict) -> dict:
    week = params.get("week") or last_week()
    projects = db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False), BizProject.status.in_([2, 3, 4]))
                          .order_by(BizProject.project_code)).unique().all()
    by_pm: dict[int, list] = defaultdict(list)
    for x in projects:
        if x.owner_id:
            by_pm[x.owner_id].append(x)
    n = 0
    for uid, ps_ in by_pm.items():
        u = db.get(SysUser, uid)
        data = ps.weekly_progress(db, ps_, week)
        notify_service.send(db, [u], "project_weekly", f"我负责的项目周进度 {data['week']}", ps.weekly_text(data),
                            link="/projects?mine=1", ref_type="project_weekly", ref_id=data["week"])
        n += 1
    content, fname, data = ps.export_weekly_xlsx(db, list(projects), week, title=f"项目周进度汇总 {week}")
    f = save_bytes(db, data=content, filename=fname, category="report",
                   key=f"report/project_weekly/{data['end']:%Y-%m}/{fname}")
    ver = (db.scalar(select(RptReport.version).where(RptReport.report_type == "project_weekly", RptReport.period == week)
                     .order_by(RptReport.version.desc()).limit(1).execution_options(include_removed_records=True)) or 0) + 1
    delayed = sum(len(r["delayed"]) for r in data["projects"])
    db.add(RptReport(report_type="project_weekly", period=week, title=f"项目周进度汇总 {week}", version=ver,
                     metrics={"projects": len(projects), "delayed_nodes": delayed,
                              "done_nodes": sum(len(r["done"]) for r in data["projects"])},
                     file_ids=[f.id], trigger=params.get("trigger", "schedule")))
    summary = (f"**项目周进度汇总 {week}**\n在建/验收/运维项目 {len(projects)} 个，本周完成节点 "
               f"{sum(len(r['done']) for r in data['projects'])} 个，<font color=\"warning\">延期节点 {delayed} 个</font>。\n"
               + "\n".join(f"> {r['name']}：延期 {'、'.join(x['node_name'] for x in r['delayed'])}"
                           for r in data["projects"] if r["delayed"])[:1500])
    leaders = notify_service.users_by_roles(db, ["SC-01", "SC-02", "ZB-01"])
    notify_service.send(db, leaders, "project_weekly", f"项目周进度汇总 {week}", summary, link="/market")
    db.commit()
    return {"rows": n, "message": f"推送项目经理 {n} 人，汇总 {len(projects)} 个项目（延期节点 {delayed}）"}


def _push_report(db, res, title: str) -> int:
    t = res["data"]["totals"]
    content = (f"**{title}**\n重点项目 {t['projects']} 个，异常 {t['alert_projects']} 个；完成节点 {t['done_nodes']} 个，"
               f"延期节点 {t['delayed_nodes']} 个；本期新签 {market_service._wan(t['period_signed'])}，"
               f"本期回款 {market_service._wan(t['period_received'])}。\n初稿已生成，请润色后发布。")
    users = notify_service.users_by_roles(db, ["SC-01", "SC-02", "ZB-01"])
    notify_service.send(db, users, "report", title, content, link="/market?tab=reports",
                        ref_type="rpt_report", ref_id=str(res["report"].id))
    return len(users)


@job("market_weekly_report", "市场部重点项目周报", {"day_of_week": "mon", "hour": 10, "minute": 30}, "每周一 10:30",
     idempotency=lambda p: f"market_weekly_report:{p.get('period') or last_week()}")
def market_weekly_report(db, params: dict) -> dict:
    period = params.get("period") or last_week()
    res = market_service.build_market_report(db, "weekly", period, trigger=params.get("trigger", "schedule"))
    n = _push_report(db, res, f"市场部重点项目周报 {period}")
    db.commit()
    return {"rows": res["data"]["totals"]["projects"], "message": f"{period} 周报已生成，推送 {n} 人"}


@job("market_monthly_report", "市场部重点项目月报", {"day": 1, "hour": 11, "minute": 0}, "每月 1 日 11:00",
     idempotency=lambda p: f"market_monthly_report:{p.get('period') or prev_month(f'{today():%Y-%m}')}")
def market_monthly_report(db, params: dict) -> dict:
    period = params.get("period") or prev_month(f"{today():%Y-%m}")
    res = market_service.build_market_report(db, "monthly", period, trigger=params.get("trigger", "schedule"))
    n = _push_report(db, res, f"市场部重点项目月报 {period}")
    db.commit()
    return {"rows": res["data"]["totals"]["projects"], "message": f"{period} 月报已生成，推送 {n} 人"}


__all__ = ["project_milestone_scan", "project_weekly_progress", "market_weekly_report", "market_monthly_report",
           "week_no"]
