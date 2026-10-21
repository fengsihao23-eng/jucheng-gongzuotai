"""知识库与客服定时任务：乐享增量同步、客服问题归因报告（周报 + 月报）。"""
from datetime import timedelta

from app.core.timeutil import month_range, prev_month, today, week_no, week_range
from app.jobs.registry import job


@job("kb_sync", "乐享知识库增量同步", {"hour": 2, "minute": 0}, "每日 02:00",
     idempotency=lambda p: None if p.get("full") else f"kb_sync:{today()}")
def kb_sync(db, params):
    from app.services import kb_service
    st = kb_service.sync(db, full=bool(params.get("full")))
    return {"rows": st.get("added", 0) + st.get("updated", 0),
            "message": f"新增 {st.get('added', 0)}、更新 {st.get('updated', 0)}、跳过 {st.get('skipped', 0)}，"
                       f"共 {st['total_docs']} 篇 / {st['total_chunks']} 块"}


def _push(db, r, link="/service?tab=analysis"):
    from app.services import notify_service
    m = r.metrics
    top = "；".join(f"{i}. {c['label']}（{c['count']}）" for i, c in enumerate(m.get("top10", [])[:3], 1)) or "无"
    content = (f"**{r.title}**\n> 工单 {m['total']} 张，已解决 {m['resolved']} 张，重复率 {m['duplicate_rate']:.1%}\n"
               f"> 高频问题：{top}\n> 建议：{(m.get('advice') or ['—'])[0][:80]}")
    users = notify_service.users_by_roles(db, ["KF-01"])
    notify_service.send(db, users, "cs_attribution", r.title, content, link=link, ref_type="rpt_report", ref_id=str(r.id))
    return len(users)


@job("cs_attribution_report", "客服问题归因周报", {"day_of_week": "mon", "hour": 11, "minute": 0}, "每周一 11:00",
     idempotency=lambda p: f"cs_attribution_report:{p.get('week') or week_no(today() - timedelta(days=7))}")
def cs_attribution_weekly(db, params):
    from app.services import cs_service
    wk = params.get("week") or week_no(today() - timedelta(days=7))
    start, end = week_range(wk)
    r = cs_service.generate_report(db, start, end, wk, trigger="schedule")
    n = _push(db, r)
    db.commit()
    return {"rows": r.metrics["total"], "message": f"{wk} 归因报告 v{r.version} 已生成，推送客服 {n} 人"}


@job("cs_attribution_monthly", "客服问题归因月报", {"day": 1, "hour": 11, "minute": 0}, "每月 1 日 11:00",
     idempotency=lambda p: f"cs_attribution_monthly:{p.get('period') or prev_month(f'{today():%Y-%m}')}")
def cs_attribution_monthly(db, params):
    from app.services import cs_service
    period = params.get("period") or prev_month(f"{today():%Y-%m}")
    start, end = month_range(period)
    r = cs_service.generate_report(db, start, end, period, trigger="schedule")
    n = _push(db, r)
    db.commit()
    return {"rows": r.metrics["total"], "message": f"{period} 归因月报 v{r.version} 已生成，推送客服 {n} 人"}
