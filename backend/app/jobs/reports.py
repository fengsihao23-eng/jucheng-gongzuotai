"""报表定时任务（PRD 6.5 / FR-04）。每个任务幂等键 = 任务名:期间；推送消息带报表中心链接。

收件角色读 sys_dict notify_rule.<key>（daily_expense 已由核心种子写入；其余默认值见 NOTIFY_DEFAULTS）。
params 可传 {"period": "..."} 补跑指定期间，或 {"notify": false} 只生成不推送。
"""
from datetime import timedelta

from app.core.response import encode
from app.core.timeutil import last_week, prev_month, today
from app.jobs.registry import job
from app.services import dict_service, notify_service
from app.services import report_service as rs
from app.services.report_render import pct, yuan

NOTIFY_DEFAULTS = {
    "daily_expense": ["CW-01", "ZB-01"],
    "daily_bankflow": ["CW-01", "CW-02"],
    "weekly_profit": ["CW-01", "ZB-01"],
    "company_weekly": ["ZB-01", "ZB-02"],
    "company_monthly": ["ZB-01", "ZB-02", "CW-01"],
    "project_operation": ["CW-01", "ZB-01"],
}


def _default_period(report_type: str) -> str:
    kind = rs.REPORT_TYPES[report_type]["period_kind"]
    T = today()
    if kind == "day":
        return (T - timedelta(days=1)).isoformat()
    if kind == "week":
        return last_week(T)
    return prev_month(f"{T:%Y-%m}")


def _period(report_type: str, params: dict) -> str:
    return params.get("period") or _default_period(report_type)


def recipients(db, report_type: str):
    rule = dict_service.get(db, "notify_rule", report_type) or {}
    roles = rule.get("roles") if isinstance(rule, dict) else None
    return notify_service.users_by_roles(db, roles or NOTIFY_DEFAULTS[report_type])


def _fmt(item: dict) -> str:
    v, k = item.get("value"), item.get("kind")
    if k == "money":
        return yuan(v)
    if k == "pct":
        return pct(v)
    return str(v)


def run_report(db, report_type: str, params: dict) -> dict:
    period = _period(report_type, params)
    r = rs.generate(db, report_type, period, "schedule", None)
    sent = 0
    if params.get("notify", True):
        users = recipients(db, report_type)
        lines = [f"> {s['label']}：**{_fmt(s)}**" for s in encode(r.metrics.get("summary", []))]
        link = f"/reports?id={r.id}"
        content = f"### {r.title}\n" + "\n".join(lines) + f"\n\n[查看报表与下载]({link})"
        rows = notify_service.send(db, users, "daily_expense" if report_type == "daily_expense" else "report",
                                   r.title, content, link=link, ref_type="rpt_report", ref_id=str(r.id))
        sent = len({n.user_id for n in rows})
    db.commit()
    return {"rows": 1, "message": f"{r.title} v{r.version} 已生成（{r.cost_ms}ms），推送 {sent} 人"}


def _key(name: str, report_type: str):
    return lambda p: f"{name}:{_period(report_type, p or {})}"


@job("daily_expense_report", "每日支出报表生成+推送", {"hour": 8, "minute": 0}, "每日 08:00",
     idempotency=_key("daily_expense_report", "daily_expense"))
def daily_expense_report(db, params):
    return run_report(db, "daily_expense", params)


@job("daily_bankflow_report", "每日银行流水报表", {"hour": 8, "minute": 10}, "每日 08:10",
     idempotency=_key("daily_bankflow_report", "daily_bankflow"))
def daily_bankflow_report(db, params):
    return run_report(db, "daily_bankflow", params)


@job("weekly_project_profit", "周利润表生成", {"day_of_week": "mon", "hour": 9, "minute": 0}, "每周一 09:00",
     idempotency=_key("weekly_project_profit", "weekly_profit"))
def weekly_project_profit(db, params):
    return run_report(db, "weekly_profit", params)


@job("project_operation_report", "在建/运营项目报表", {"day_of_week": "mon", "hour": 9, "minute": 30}, "每周一 09:30",
     idempotency=_key("project_operation_report", "project_operation"))
def project_operation_report(db, params):
    return run_report(db, "project_operation", params)


@job("company_weekly_report", "公司周报初稿", {"day_of_week": "mon", "hour": 10, "minute": 0}, "每周一 10:00",
     idempotency=_key("company_weekly_report", "company_weekly"))
def company_weekly_report(db, params):
    return run_report(db, "company_weekly", params)


@job("monthly_company_report", "公司月报", {"day": 1, "hour": 10, "minute": 0}, "每月 1 日 10:00",
     idempotency=_key("monthly_company_report", "company_monthly"))
def monthly_company_report(db, params):
    return run_report(db, "company_monthly", params)
