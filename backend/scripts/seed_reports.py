"""报表中心演示数据：通过 report_service（trigger=schedule）预生成历史报表，让报表中心有可下载的历史版本。

运行于 seed_finance_ops 之后；柠檬云/税务数据不存在时月报照常生成并注明「数据缺失」。
- 近 7 天每日支出报表、每日银行流水报表
- 近 4 周每周项目利润表
- 上周公司周报、上周在建/运营项目报表、上月公司月报
同时写入本模块使用的口径字典（notify_rule 各报表收件角色、report_rule.profit 预警线、report_rule.subject_map）。
"""
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from app.core.timeutil import TZ, now, last_week, month_range, prev_month, week_range, week_no
from app.jobs.reports import NOTIFY_DEFAULTS
from app.services import dict_service
from app.services import report_service as rs


def _at(d, h, m=0):
    return datetime.combine(d, time(h, m), TZ)


def seed(db: Session, ctx) -> None:
    T = ctx.T
    for rtype, roles in NOTIFY_DEFAULTS.items():
        if dict_service.get(db, "notify_rule", rtype) is None:
            dict_service.put(db, "notify_rule", rtype, {"roles": roles}, f"{rs.REPORT_TYPES[rtype]['name']}推送角色")
    if dict_service.get(db, "report_rule", "profit") is None:
        dict_service.put(db, "report_rule", "profit", {"alert_margin": "0.2"}, "项目毛利率预警线（低于即标红）")
    if dict_service.get(db, "report_rule", "subject_map") is None:
        dict_service.put(db, "report_rule", "subject_map", rs.DEFAULT_SUBJECT_MAP,
                         "公司月报账务口径科目映射（科目编码前缀）：收入/成本/税金及附加/期间费用")

    plan: list[tuple[str, str, datetime]] = []
    for back in range(7, 0, -1):
        d = T - timedelta(days=back)
        plan.append(("daily_expense", d.isoformat(), _at(d + timedelta(days=1), 8, 0)))
        plan.append(("daily_bankflow", d.isoformat(), _at(d + timedelta(days=1), 8, 10)))
    for back in range(4, 0, -1):
        wk = week_no(T - timedelta(weeks=back))
        nxt_monday = week_range(wk)[1] + timedelta(days=1)
        plan.append(("weekly_profit", wk, _at(nxt_monday, 9, 0)))
    lw = last_week(T)
    monday = week_range(lw)[1] + timedelta(days=1)
    plan.append(("project_operation", lw, _at(monday, 9, 30)))
    plan.append(("company_weekly", lw, _at(monday, 10, 0)))
    lm = prev_month(f"{T:%Y-%m}")
    plan.append(("company_monthly", lm, _at(month_range(lm)[1] + timedelta(days=1), 10, 0)))

    for rtype, period, at in plan:
        r = rs.generate(db, rtype, period, "schedule", None)
        r.created_at = r.updated_at = min(at, now())
    db.flush()
