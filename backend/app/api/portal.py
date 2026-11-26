"""FR-02 个性化门户：GET /portal/cards 按角色方案（principal.portal）返回卡片配置 + 数据。

- 卡片方案存 sys_dict(portal_cards, key=方案名)：[{key, title?, span?}]，管理员可在线调整；缺省用 DEFAULT_SCHEMES
- 卡片类型：metric / list / chart / shortcut / reminder
- 个人偏好：user.preferences.card_order / hidden_cards（PUT /auth/preferences）
- 聚合：经营类卡片共用看板缓存（board_service.overview，≤5min），其余按卡片直接查表；表为空返回空卡片不报错
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, get_principal
from app.core.response import ok
from app.core.timeutil import TZ, now, today, week_range
from app.models import (BizContract, BizExpenseReport, BizProject, BizProjectMilestone, FinBankFlow, FinCashJournal,
                        SyncWecomApproval)
from app.services import dict_service

router = APIRouter(prefix="/portal", tags=["个性化门户"])
log = logging.getLogger("portal")
ZERO = Decimal("0")

DEFAULT_SCHEMES: dict[str, list[dict]] = {
    "exec": [{"key": k} for k in ["exec_payment", "exec_sign_today", "exec_year", "exec_opportunity", "exec_tender",
                                  "morning_entry", "key_projects", "submission_reminder", "my_todo"]],
    "exec_staff": [{"key": k} for k in ["exec_payment", "exec_sign_today", "submission_rate", "exec_opportunity",
                                        "exec_tender", "submission_reminder", "key_projects", "morning_entry", "my_todo"]],
    "finance": [{"key": k} for k in ["fin_today_expense", "fin_pending_allocation", "fin_unmatched_flows",
                                     "fin_week_profit", "fin_profit_entry", "fin_expense_trend", "my_todo"]],
    "cashier": [{"key": k} for k in ["cash_unmatched_today", "cash_unmatched_total", "cash_balance_entry",
                                     "cash_journal_recent", "my_todo"]],
    "market": [{"key": k} for k in ["market_key_projects", "market_weekly_entry", "market_intel", "market_feedback_reminder",
                                    "my_todo"]],
    "marketing": [{"key": k} for k in ["mk_month_sign", "mk_year_sign", "mk_payment_progress", "mk_ledger_entry",
                                       "mk_funnel", "mk_recent_contracts", "my_todo"]],
    "product": [{"key": k} for k in ["prod_tender_tasks", "prod_entry", "prod_bidding_deadlines", "my_todo"]],
    "hr": [{"key": k} for k in ["hr_attendance", "hr_alerts", "hr_recruit_funnel", "hr_entry", "my_todo"]],
    "pm": [{"key": k} for k in ["pm_my_nodes", "pm_acceptance", "pm_delayed", "pm_entry", "my_todo"]],
    "service": [{"key": k} for k in ["cs_entry", "cs_pending", "cs_top_categories", "my_todo"]],
    "admin": [{"key": k} for k in ["admin_jobs", "admin_approval_queue", "admin_users", "admin_entry", "my_todo"]],
    "employee": [{"key": k} for k in ["my_expenses", "my_approvals", "emp_entry"]],
}


# ------------------------------------------------------------------ 卡片注册表
CARDS: dict[str, dict] = {}


def card(key: str, type_: str, title: str, span: int = 1, menu: str | None = None):
    def deco(fn):
        CARDS[key] = {"type": type_, "title": title, "span": span, "menu": menu, "fn": fn}
        return fn
    return deco


class Ctx:
    def __init__(self, db: Session, p: Principal):
        self.db, self.p, self.T = db, p, today()
        self._board = None

    @property
    def board(self) -> dict:
        if self._board is None:
            from app.services import board_service, business_workbook, contract_workbook_sync
            wb = business_workbook.configured_source(self.db)
            allowed = wb is None or contract_workbook_sync.can_view(self.p, wb.config) or any(
                business_workbook.can_access_view(self.p, wb.config, view)
                for view in ("board", "contracts", "collections", "opportunities", "tender"))
            if not allowed:
                self._board = {"key_projects": board_service._key_projects(self.db, self.T, self.p),
                               "submission": board_service._submission_block(self.db, self.T),
                               "profit": board_service._profit_block(self.db, self.T, self.p)}
                return self._board
            self._board = board_service.overview(self.db, self.T, principal=self.p)
        return self._board


def _wan(v) -> str:
    return "未提供" if v is None else f"{Decimal(v) / 10000:,.1f}"


def _pct(v) -> str:
    return "—" if v is None else f"{Decimal(v) * 100:.1f}%"


def _drill(d: dict | None) -> dict | None:
    return {"path": d["path"], "query": d.get("query") or {}} if d else None


def _day(d: date) -> tuple[datetime, datetime]:
    s = datetime(d.year, d.month, d.day, tzinfo=TZ)
    return s, s + timedelta(days=1)


# ---------- 总经办 ----------
@card("exec_payment", "metric", "当月回款", menu="board")
def _(c: Ctx):
    pay = c.board["payment"]
    return {"value": _wan(pay["amount"]), "unit": "万", "progress": pay["rate"],
            "hint": f"目标 {_wan(pay['target'])} 万 · 达成 {_pct(pay['rate'])}", "to": _drill(pay["drill"])}


@card("exec_sign_today", "metric", "今日签约", menu="board")
def _(c: Ctx):
    s = c.board["sign"]
    return {"value": _wan(s["today"]["amount"]), "unit": "万",
            "hint": f"{s['today']['count']} 份 · 本月 {_wan(s['month']['amount'])} 万", "to": _drill(s["month"]["drill"])}


@card("exec_year", "metric", "年度累计业绩", menu="board")
def _(c: Ctx):
    y = c.board["sign"]["year"]
    return {"value": _wan(y["amount"]), "unit": "万", "delta": y["yoy"], "hint": "同比去年同期",
            "to": _drill(y["drill"])}


@card("exec_opportunity", "metric", "本月新增商机", menu="board")
def _(c: Ctx):
    o = c.board["opportunity"]
    return {"value": o["month"]["count"], "unit": "个",
            "hint": f"{_wan(o['month']['amount'])} 万 · 本周 {o['week']['count']} 个", "to": _drill(o["month"]["drill"])}


@card("exec_tender", "metric", "招标 / 在建", menu="board")
def _(c: Ctx):
    t = c.board["tender"]
    return {"value": f"{t['bidding_count']} / {t['building_count']}", "unit": "个",
            "hint": (f"{t['building_label']} {t['building_count']} 个；原表未提供节点预警" if t.get('building_label')
                     else f"节点预警 {t['alert_count']} · 两周内截标 {t['deadline_soon_count']}"),
            "to": _drill(t["drill"]["building"])}


@card("submission_rate", "metric", "本周周报提交率", menu="submissions")
def _(c: Ctx):
    w = c.board["submission"]["weekly"]
    return {"value": _pct(w["rate"]).rstrip("%"), "unit": "%", "progress": w["rate"],
            "hint": f"{w['submitted']}/{w['required']} 人 · {w['period']}",
            "to": {"path": "/submissions", "query": {"type": "weekly"}}}


@card("submission_reminder", "reminder", "周报未提交", menu="submissions")
def _(c: Ctx):
    w = c.board["submission"]["weekly"]
    items = [{"text": f"{m['name']}（{m['dept_name']}）", "level": "warn" if m["consecutive"] else "info",
              "hint": "连续两期未交" if m["consecutive"] else None} for m in w["missing"][:12]]
    return {"items": items, "total": len(w["missing"]),
            "summary": f"{w['period']} 未提交 {len(w['missing'])} 人，提交率 {_pct(w['rate'])}",
            "to": {"path": "/submissions", "query": {"type": "weekly"}}}


@card("morning_entry", "shortcut", "晨报与看板", menu="board")
def _(c: Ctx):
    return {"items": [{"label": "经营看板", "desc": "六板块实时数据", "to": {"path": "/board"}},
                      {"label": "大屏模式", "desc": "投屏轮播", "to": {"path": "/board/screen"}},
                      {"label": "今日晨报", "desc": "08:00 企微推送内容", "to": {"path": "/board", "query": {"morning": "1"}}},
                      {"label": "报表中心", "desc": "日/周/月经营报表", "to": {"path": "/reports"}}]}


@card("key_projects", "list", "重点项目进展", span=2, menu="board")
def _(c: Ctx):
    kp = c.board["key_projects"]
    rows = [{"name": r["name"], "node": r["current_node"], "status": "延期 ▲" if r["delayed"] else "正常 ●",
             "feedback": (r["latest_feedback"] or {}).get("content") or "—", "alert": r["delayed"],
             "to": _drill(r["drill"])} for r in kp["items"]]
    return {"columns": [{"prop": "name", "label": "项目"}, {"prop": "node", "label": "当前节点", "width": 110},
                        {"prop": "status", "label": "状态", "width": 80},
                        {"prop": "feedback", "label": "最新进展"}],
            "rows": rows, "summary": f"{kp['count']} 个重点项目，延期 {kp['delayed_count']} 个", "to": _drill(kp["drill"])}


# ---------- 财务 ----------
@card("fin_today_expense", "metric", "今日支出", menu="expenses")
def _(c: Ctx):
    amt, n = c.db.execute(select(func.coalesce(func.sum(FinBankFlow.amount), 0), func.count(FinBankFlow.id))
                          .where(FinBankFlow.direction == 2, FinBankFlow.trade_date == c.T)).one()
    s, e = _day(c.T)
    exp_amt, exp_n = c.db.execute(select(func.coalesce(func.sum(BizExpenseReport.amount), 0), func.count())
                                  .where(BizExpenseReport.approved_at >= s, BizExpenseReport.approved_at < e,
                                         BizExpenseReport.status.in_([2, 4]))).one()
    return {"value": _wan(amt), "unit": "万", "hint": f"流水支出 {n} 笔 · 今日通过报销 {exp_n} 单 {_wan(exp_amt)} 万",
            "to": {"path": "/flows", "query": {"direction": "2", "date": c.T.isoformat()}}}


@card("fin_pending_allocation", "metric", "待归集报销", menu="expenses")
def _(c: Ctx):
    n, amt = c.db.execute(select(func.count(BizExpenseReport.id), func.coalesce(func.sum(BizExpenseReport.amount), 0))
                          .where(BizExpenseReport.status == 2)).one()
    return {"value": n, "unit": "单", "hint": f"金额 ¥{Decimal(amt):,.2f} · 所属项目缺失/无法匹配",
            "to": {"path": "/expenses", "query": {"status": "pending"}}}


@card("fin_unmatched_flows", "metric", "流水待核对", menu="flows")
def _(c: Ctx):
    rows = dict(c.db.execute(select(FinBankFlow.direction, func.count()).where(FinBankFlow.match_status == 0)
                             .group_by(FinBankFlow.direction)).all())
    return {"value": sum(rows.values()), "unit": "笔", "hint": f"收入 {rows.get(1, 0)} · 支出 {rows.get(2, 0)}",
            "to": {"path": "/flows", "query": {"match_status": "0"}}}


@card("fin_week_profit", "metric", "本周项目毛利", menu="profit")
def _(c: Ctx):
    pf = c.board["profit"]
    return {"value": _wan(pf["week_gross_profit"]), "unit": "万", "progress": None,
            "hint": f"累计毛利率 {_pct(pf['gross_margin'])} · 本周收入 {_wan(pf['week_income'])} 万",
            "to": {"path": "/profit"}}


@card("fin_profit_entry", "shortcut", "快捷入口", menu="profit")
def _(c: Ctx):
    return {"items": [{"label": "周利润表", "desc": "每周一 09:00 自动生成", "to": {"path": "/profit"}},
                      {"label": "报销归集", "desc": "补归集项目", "to": {"path": "/expenses", "query": {"status": "pending"}}},
                      {"label": "流水核对", "desc": "导入/核对银行流水", "to": {"path": "/flows"}},
                      {"label": "报表中心", "desc": "每日支出 / 公司周报", "to": {"path": "/reports"}}]}


@card("fin_expense_trend", "chart", "近 7 日支出", span=2, menu="expenses")
def _(c: Ctx):
    start = c.T - timedelta(days=6)
    rows = dict(c.db.execute(select(FinBankFlow.trade_date, func.sum(FinBankFlow.amount))
                             .where(FinBankFlow.direction == 2, FinBankFlow.trade_date >= start,
                                    FinBankFlow.trade_date <= c.T).group_by(FinBankFlow.trade_date)).all())
    days = [start + timedelta(days=i) for i in range(7)]
    return {"kind": "bar", "categories": [f"{d:%m-%d}" for d in days],
            "series": [{"name": "流水支出", "data": [Decimal(rows.get(d, 0)) for d in days]}],
            "to": {"path": "/reports"}}


# ---------- 出纳 ----------
@card("cash_unmatched_today", "metric", "今日流水待核对", menu="flows")
def _(c: Ctx):
    n = c.db.scalar(select(func.count()).where(FinBankFlow.match_status == 0, FinBankFlow.trade_date >= c.T - timedelta(days=1)))
    return {"value": n, "unit": "笔", "hint": "昨日至今到账/出账未核对",
            "to": {"path": "/flows", "query": {"match_status": "0"}}}


@card("cash_unmatched_total", "metric", "全部待核对", menu="flows")
def _(c: Ctx):
    n, amt = c.db.execute(select(func.count(), func.coalesce(func.sum(FinBankFlow.amount), 0))
                          .where(FinBankFlow.match_status == 0)).one()
    return {"value": n, "unit": "笔", "hint": f"金额 ¥{Decimal(amt):,.2f}", "to": {"path": "/flows", "query": {"match_status": "0"}}}


@card("cash_balance_entry", "shortcut", "快捷入口", menu="journal")
def _(c: Ctx):
    return {"items": [{"label": "流水核对工作台", "desc": "左流水右日记账", "to": {"path": "/flows"}},
                      {"label": "出纳日记账", "desc": "日视图 / 月视图", "to": {"path": "/journal"}},
                      {"label": "手工补录", "desc": "现金收支", "to": {"path": "/journal", "query": {"new": "1"}}}]}


@card("cash_journal_recent", "list", "日记账速览", span=2, menu="journal")
def _(c: Ctx):
    rows = c.db.scalars(select(FinCashJournal).order_by(FinCashJournal.journal_date.desc(), FinCashJournal.id.desc())
                        .limit(8)).all()
    inc = c.db.scalar(select(func.coalesce(func.sum(FinCashJournal.amount), 0)).where(
        FinCashJournal.journal_date == c.T, FinCashJournal.direction == 1))
    out = c.db.scalar(select(func.coalesce(func.sum(FinCashJournal.amount), 0)).where(
        FinCashJournal.journal_date == c.T, FinCashJournal.direction == 2))
    return {"columns": [{"prop": "date", "label": "日期", "width": 96}, {"prop": "summary", "label": "摘要"},
                        {"prop": "category", "label": "类别", "width": 80}, {"prop": "amount", "label": "金额", "width": 130, "money": True}],
            "rows": [{"date": r.journal_date, "summary": r.summary, "category": r.category,
                      "amount": r.amount if r.direction == 1 else -r.amount} for r in rows],
            "summary": f"今日收入 ¥{Decimal(inc):,.2f} · 支出 ¥{Decimal(out):,.2f}", "to": {"path": "/journal"}}


# ---------- 市场 ----------
@card("market_key_projects", "list", "重点项目跟进", span=2, menu="market")
def _(c: Ctx):
    from app.models import BizProjectFeedback
    from app.services.project_service import visible_query
    q = visible_query(c.p).where(BizProject.is_key.is_(True))
    if "SC-01" not in c.p.role_codes and c.p.dept_code == "SC":
        q = q.where(BizProject.market_owner_id == c.p.id)
    projects = c.db.scalars(q.order_by(BizProject.project_code)).unique().all()
    latest = {}
    if projects:
        for f in c.db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id.in_([x.id for x in projects]))
                              .order_by(BizProjectFeedback.feedback_date, BizProjectFeedback.id)):
            latest[f.project_id] = f
    from app.services.board_service import milestone_delayed
    rows = []
    for x in projects:
        f = latest.get(x.id)
        cur = next((m for m in sorted(x.milestones, key=lambda m: m.seq) if m.status != 3), None)
        delayed = any(milestone_delayed(m, c.T) for m in x.milestones)
        rows.append({"name": x.name, "node": cur.node_name if cur else ("招标中" if x.status == 1 else "—"),
                     "last": f"{f.feedback_date:%m-%d} {f.content}" if f else "暂无反馈",
                     "alert": delayed or bool(f and f.risk_level >= 2),
                     "stale": not f or (c.T - f.feedback_date).days > 3, "to": {"path": "/market"}})
    return {"columns": [{"prop": "name", "label": "项目"}, {"prop": "node", "label": "当前节点", "width": 110},
                        {"prop": "last", "label": "最近反馈"}],
            "rows": rows, "summary": f"{len(rows)} 个重点项目，{sum(r['stale'] for r in rows)} 个超 3 天未反馈",
            "to": {"path": "/market"}}


@card("market_weekly_entry", "shortcut", "快捷入口", menu="market")
def _(c: Ctx):
    return {"items": [{"label": "填报进展反馈", "desc": "重点项目每日进展", "to": {"path": "/market", "query": {"feedback": "1"}}},
                      {"label": "市场周报", "desc": "自动成稿，一键提交", "to": {"path": "/market", "query": {"tab": "report"}}},
                      {"label": "竞品情报", "desc": "录入 / 简报", "to": {"path": "/intel"}},
                      {"label": "商机信息", "desc": "工单状态与列表", "to": {"path": "/opportunities"}}]}


@card("market_intel", "list", "竞品情报摘要", span=2, menu="intel")
def _(c: Ctx):
    from app.models import MktIntel
    rows = c.db.scalars(select(MktIntel).order_by(MktIntel.published_on.desc().nulls_last(), MktIntel.id.desc()).limit(6)).all()
    return {"columns": [{"prop": "date", "label": "日期", "width": 96}, {"prop": "title", "label": "标题"},
                        {"prop": "competitor", "label": "对象", "width": 110}],
            "rows": [{"date": r.published_on, "title": r.title, "competitor": r.competitor or r.kind,
                      "alert": r.importance >= 3, "to": {"path": "/intel"}} for r in rows],
            "to": {"path": "/intel"}}


@card("market_feedback_reminder", "reminder", "跟进提醒", menu="market")
def _(c: Ctx):
    from app.models import BizProjectFeedback
    from app.services.project_service import visible_query
    q = visible_query(c.p).where(BizProject.is_key.is_(True))
    if "SC-01" not in c.p.role_codes:
        q = q.where(BizProject.market_owner_id == c.p.id)
    items = []
    for x in c.db.scalars(q).unique().all():
        last = c.db.scalar(select(func.max(BizProjectFeedback.feedback_date)).where(BizProjectFeedback.project_id == x.id))
        if not last or (c.T - last).days >= 2:
            items.append({"text": f"{x.name} {'暂无进展反馈' if not last else f'已 {(c.T - last).days} 天未反馈'}",
                          "level": "warn", "to": {"path": "/market"}})
        if x.status == 1 and x.bid_deadline and 0 <= (x.bid_deadline - c.T).days <= 7:
            items.append({"text": f"{x.name} 招标截止 {x.bid_deadline:%m-%d}", "level": "down", "to": {"path": "/market"}})
    return {"items": items, "total": len(items), "summary": "今日无待跟进事项" if not items else None}


# ---------- 营销管控 ----------
@card("mk_month_sign", "metric", "本月签约", menu="contracts")
def _(c: Ctx):
    m = c.board["sign"]["month"]
    return {"value": _wan(m["amount"]), "unit": "万", "delta": m["mom"], "hint": f"{m['count']} 份 · 环比上月",
            "to": _drill(m["drill"])}


@card("mk_year_sign", "metric", "年度累计业绩", menu="contracts")
def _(c: Ctx):
    y = c.board["sign"]["year"]
    return {"value": _wan(y["amount"]), "unit": "万", "delta": y["yoy"], "hint": f"{y['count']} 份 · 同比",
            "to": _drill(y["drill"])}


@card("mk_payment_progress", "chart", "业绩进度（当月回款 vs 目标）", menu="contracts")
def _(c: Ctx):
    pay = c.board["payment"]
    return {"kind": "progress", "items": [{"name": x["name"], "value": x["amount"], "target": x["target"],
                                           "rate": x["rate"], "to": _drill(x["drill"])} for x in pay["by_line"]],
            "summary": f"合计 {_wan(pay['amount'])} / {_wan(pay['target'])} 万（{_pct(pay['rate'])}）"}


@card("mk_ledger_entry", "shortcut", "台账入口", menu="contracts")
def _(c: Ctx):
    items = [{"label": "合同台账", "desc": "筛选 / 导出", "to": {"path": "/contracts"}}]
    if c.p.can("contracts:write"):
        items.append({"label": "新增合同", "desc": "人工录入", "to": {"path": "/contracts", "query": {"new": "1"}}})
    items += [{"label": "商机信息", "desc": "工单状态与列表", "to": {"path": "/opportunities"}},
              {"label": "数据清洗", "desc": "Excel 导入", "to": {"path": "/clean"}}]
    return {"items": items}


@card("mk_funnel", "chart", "商机漏斗", menu="opportunities")
def _(c: Ctx):
    f = c.board["opportunity"]["funnel"]
    return {"kind": "funnel", "items": [{"name": x["name"], "value": x["count"], "amount": x["amount"],
                                         "to": _drill(x["drill"])} for x in f if x["stage"] != "lost"],
            "summary": f"在跟进金额 {_wan(c.board['opportunity']['open_amount'])} 万"}


@card("mk_recent_contracts", "list", "本月新签合同", span=2, menu="contracts")
def _(c: Ctx):
    items = c.board["sign"]["items"][:8]
    return {"columns": [{"prop": "sign_date", "label": "签订日", "width": 96}, {"prop": "customer_name", "label": "客户"},
                        {"prop": "amount", "label": "金额", "width": 130, "money": True},
                        {"prop": "source", "label": "来源", "width": 90}],
            "rows": [{**{k: x[k] for k in ("sign_date", "customer_name", "amount")},
                      "source": {1: "企微审批", 2: "人工", 3: "导入", 4: "指定表格"}.get(x["source_type"]), "to": _drill(x["drill"])}
                     for x in items], "to": {"path": "/contracts", "query": {"sign_month": f"{c.T:%Y-%m}"}}}


# ---------- 产品中心 ----------
@card("prod_tender_tasks", "list", "标书任务", span=2, menu="tender")
def _(c: Ctx):
    from app.models import ProdBidCheck, ProdTenderDoc
    docs = c.db.scalars(select(ProdTenderDoc).where(ProdTenderDoc.doc_type == "tender")
                        .order_by(ProdTenderDoc.id.desc()).limit(6)).all()
    running = c.db.scalar(select(func.count()).select_from(ProdBidCheck).where(ProdBidCheck.status == "running")) or 0
    return {"columns": [{"prop": "title", "label": "招标文件 / 需求书"}, {"prop": "project", "label": "项目", "width": 150},
                        {"prop": "clauses", "label": "条款数", "width": 80}],
            "rows": [{"title": d.title, "project": d.project_code or "—", "clauses": len(d.clauses or []),
                      "to": {"path": "/tender"}} for d in docs],
            "summary": f"进行中的合规检查 {running} 个", "to": {"path": "/tender"}}


@card("prod_entry", "shortcut", "快捷入口", menu="tender")
def _(c: Ctx):
    return {"items": [{"label": "投标合规检查", "desc": "逐条比对招标要求", "to": {"path": "/tender", "query": {"tab": "check"}}},
                      {"label": "实施方案生成", "desc": "引用乐享模板", "to": {"path": "/tender", "query": {"tab": "generate"}}},
                      {"label": "资料库", "desc": "售前资料 / 资质", "to": {"path": "/kb"}}]}


@card("prod_bidding_deadlines", "reminder", "招标截止提醒")
def _(c: Ctx):
    from app.services.project_service import visible_query
    rows = c.db.scalars(visible_query(c.p).where(BizProject.status == 1,
                                                 BizProject.bid_deadline >= c.T).order_by(BizProject.bid_deadline)).unique().all()
    return {"items": [{"text": f"{x.name}：{x.bid_deadline:%m-%d} 截止（剩 {(x.bid_deadline - c.T).days} 天）",
                       "level": "down" if (x.bid_deadline - c.T).days <= 7 else "info"} for x in rows],
            "total": len(rows)}


# ---------- 人事 ----------
@card("hr_attendance", "metric", "考勤异常待核对", menu="hr")
def _(c: Ctx):
    from app.models import HrAttendanceRecord
    ms = c.T.replace(day=1)
    n = c.db.scalar(select(func.count()).select_from(HrAttendanceRecord).where(
        HrAttendanceRecord.work_date >= ms, HrAttendanceRecord.exception_type.is_not(None),
        HrAttendanceRecord.exception_type != "")) or 0
    return {"value": n, "unit": "条", "hint": "本月考勤异常（迟到/缺卡/旷工等）", "to": {"path": "/hr", "query": {"tab": "attendance"}}}


@card("hr_alerts", "reminder", "合同 / 试用期到期预警", menu="hr")
def _(c: Ctx):
    from app.models import HrEmployee, SysUser
    rule = dict_service.get(c.db, "alert_rule", "hr") or {}
    cl, pl = int(rule.get("contract_lead_days", 30)), int(rule.get("probation_lead_days", 15))
    items = []
    for e, u in c.db.execute(select(HrEmployee, SysUser).join(SysUser, SysUser.id == HrEmployee.user_id)
                             .where(SysUser.status == 1)).all():
        if e.contract_end_date and 0 <= (e.contract_end_date - c.T).days <= cl:
            items.append({"text": f"{u.name} 劳动合同 {e.contract_end_date:%m-%d} 到期", "level": "warn",
                          "days": (e.contract_end_date - c.T).days})
        if e.employ_type == "试用" and e.probation_end_date and 0 <= (e.probation_end_date - c.T).days <= pl:
            items.append({"text": f"{u.name} 试用期 {e.probation_end_date:%m-%d} 到期", "level": "down",
                          "days": (e.probation_end_date - c.T).days})
    items.sort(key=lambda x: x["days"])
    return {"items": items, "total": len(items), "to": {"path": "/hr", "query": {"tab": "alerts"}}}


@card("hr_recruit_funnel", "chart", "招聘漏斗", menu="hr")
def _(c: Ctx):
    from app.models import HrCandidate
    names = {"resume": "简历", "screen_pass": "初筛通过", "interview": "面试", "offer": "Offer", "hired": "入职"}
    rows = dict(c.db.execute(select(HrCandidate.stage, func.count()).group_by(HrCandidate.stage)).all())
    return {"kind": "funnel", "items": [{"name": v, "value": int(rows.get(k, 0))} for k, v in names.items()],
            "to": {"path": "/hr", "query": {"tab": "recruit"}}}


@card("hr_entry", "shortcut", "快捷入口", menu="hr")
def _(c: Ctx):
    return {"items": [{"label": "考勤导入", "desc": "月度考勤汇总", "to": {"path": "/hr", "query": {"tab": "attendance"}}},
                      {"label": "入职资料包", "desc": "文书生成", "to": {"path": "/hr", "query": {"tab": "docs"}}},
                      {"label": "汇报提交", "desc": "周报月报提交率", "to": {"path": "/submissions"}}]}


# ---------- 项目部 ----------
def _my_nodes(c: Ctx):
    from app.services.project_service import visible_filter
    return c.db.execute(select(BizProjectMilestone, BizProject).join(BizProject)
                        .where(BizProject.is_deleted.is_(False), visible_filter(c.p), BizProjectMilestone.status != 3,
                               (BizProjectMilestone.owner_id == c.p.id) | (BizProject.owner_id == c.p.id))
                        .order_by(BizProjectMilestone.plan_date)).all()


@card("pm_my_nodes", "list", "我负责的项目节点", span=2, menu="projects")
def _(c: Ctx):
    from app.services.board_service import milestone_delayed
    rows = []
    for m, pj in _my_nodes(c)[:12]:
        d = milestone_delayed(m, c.T)
        rows.append({"project": pj.name, "node": m.node_name, "plan": m.plan_date,
                     "state": "延期 ▲" if d else {1: "未开始", 2: "进行中", 4: "延期"}.get(m.status, "—"),
                     "alert": d, "to": {"path": f"/projects/{pj.project_code}"}})
    return {"columns": [{"prop": "project", "label": "项目"}, {"prop": "node", "label": "节点", "width": 100},
                        {"prop": "plan", "label": "计划日", "width": 100}, {"prop": "state", "label": "状态", "width": 80}],
            "rows": rows, "to": {"path": "/projects", "query": {"mine": "1"}}}


@card("pm_acceptance", "reminder", "验收提醒", menu="projects")
def _(c: Ctx):
    items = []
    for m, pj in _my_nodes(c):
        if ("验" in m.node_name or "上线" in m.node_name) and m.plan_date and (m.plan_date - c.T).days <= 30:
            days = (m.plan_date - c.T).days
            items.append({"text": f"{pj.name} · {m.node_name} {m.plan_date:%m-%d}"
                                  + (f"（已超期 {-days} 天）" if days < 0 else f"（剩 {days} 天）"),
                          "level": "down" if days < 0 else "warn", "to": {"path": f"/projects/{pj.project_code}"}})
    return {"items": items, "total": len(items)}


@card("pm_delayed", "metric", "我的延期节点", menu="projects")
def _(c: Ctx):
    from app.services.board_service import milestone_delayed
    nodes = _my_nodes(c)
    n = sum(1 for m, _ in nodes if milestone_delayed(m, c.T))
    soon = sum(1 for m, _ in nodes if m.plan_date and 0 <= (m.plan_date - c.T).days <= 7)
    return {"value": n, "unit": "个", "hint": f"未完成节点 {len(nodes)} · 7 天内到期 {soon}",
            "to": {"path": "/projects", "query": {"mine": "1", "delayed": "1"}}}


@card("pm_entry", "shortcut", "快捷入口", menu="projects")
def _(c: Ctx):
    return {"items": [{"label": "项目流程", "desc": "更新节点进度", "to": {"path": "/projects", "query": {"mine": "1"}}},
                      {"label": "项目周进度", "desc": "导出周报", "to": {"path": "/projects", "query": {"tab": "weekly"}}},
                      {"label": "知识库", "desc": "施工规范 / 模板", "to": {"path": "/kb"}}]}


# ---------- 客服 ----------
@card("cs_entry", "shortcut", "工单登记", menu="service")
def _(c: Ctx):
    return {"items": [{"label": "登记客户问题", "desc": "自动分类 / 去重", "to": {"path": "/service", "query": {"new": "1"}}},
                      {"label": "常见问题", "desc": "知识库检索", "to": {"path": "/kb", "query": {"category": "客服"}}},
                      {"label": "原因分析", "desc": "归因统计", "to": {"path": "/service", "query": {"tab": "analysis"}}}]}


@card("cs_pending", "metric", "待处理工单", menu="service")
def _(c: Ctx):
    from app.models import CsTicket
    rows = dict(c.db.execute(select(CsTicket.status, func.count()).group_by(CsTicket.status)).all())
    mine = c.db.scalar(select(func.count()).select_from(CsTicket).where(CsTicket.handler_id == c.p.id,
                                                                         CsTicket.status.in_([1, 2]))) or 0
    return {"value": rows.get(1, 0) + rows.get(2, 0), "unit": "单",
            "hint": f"登记 {rows.get(1, 0)} · 排查中 {rows.get(2, 0)} · 我的 {mine}",
            "to": {"path": "/service", "query": {"status": "pending"}}}


@card("cs_top_categories", "chart", "高频问题（近 30 天）", menu="service")
def _(c: Ctx):
    from app.models import CsTicket
    s, _ = _day(c.T - timedelta(days=30))
    rows = c.db.execute(select(CsTicket.category, func.count()).where(CsTicket.created_at >= s)
                        .group_by(CsTicket.category).order_by(func.count().desc()).limit(6)).all()
    return {"kind": "hbar", "categories": [r[0] for r in rows], "series": [{"name": "工单数", "data": [r[1] for r in rows]}],
            "to": {"path": "/service"}}


# ---------- 管理员 ----------
@card("admin_jobs", "list", "最近任务运行", span=2, menu="admin")
def _(c: Ctx):
    from app.models import SyncJobLog
    rows = c.db.scalars(select(SyncJobLog).order_by(SyncJobLog.id.desc()).limit(8)).all()
    return {"columns": [{"prop": "job", "label": "任务"}, {"prop": "status", "label": "状态", "width": 80},
                        {"prop": "at", "label": "时间", "width": 150}, {"prop": "msg", "label": "说明"}],
            "rows": [{"job": r.job_name, "status": r.status, "at": r.run_at, "msg": (r.message or r.error_msg or "")[:60],
                      "alert": r.status == "failed"} for r in rows], "to": {"path": "/admin", "query": {"tab": "jobs"}}}


@card("admin_approval_queue", "metric", "审批待人工处理", menu="admin")
def _(c: Ctx):
    n = c.db.scalar(select(func.count()).select_from(SyncWecomApproval).where(SyncWecomApproval.dispatch_status == 2)) or 0
    return {"value": n, "unit": "单", "hint": "自动登记失败转人工", "to": {"path": "/admin", "query": {"tab": "approvals"}}}


@card("admin_users", "metric", "在职账号", menu="admin")
def _(c: Ctx):
    from app.models import SysUser
    n = c.db.scalar(select(func.count()).select_from(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False)))
    t = c.db.scalar(select(func.count()).select_from(SysUser).where(SysUser.access_token.is_not(None)))
    return {"value": n, "unit": "人", "hint": f"已开通 MCP 令牌 {t} 人", "to": {"path": "/admin", "query": {"tab": "users"}}}


@card("admin_entry", "shortcut", "系统管理", menu="admin")
def _(c: Ctx):
    return {"items": [{"label": "字典口径", "desc": "回款目标 / 门户卡片", "to": {"path": "/admin", "query": {"tab": "dicts"}}},
                      {"label": "定时任务", "desc": "手动触发 / 日志", "to": {"path": "/admin", "query": {"tab": "jobs"}}},
                      {"label": "账号角色", "desc": "RBAC", "to": {"path": "/admin", "query": {"tab": "users"}}},
                      {"label": "WorkBuddy 接入", "desc": "MCP 配置", "to": {"path": "/workbuddy"}}]}


# ---------- 通用 / 普通员工 ----------
def _my_expense_rows(c: Ctx):
    return c.db.scalars(select(BizExpenseReport).where(BizExpenseReport.applicant_id == c.p.id)
                        .order_by(BizExpenseReport.expense_date.desc(), BizExpenseReport.id.desc()).limit(6)).all()


def _my_approval_rows(c: Ctx):
    return c.db.scalars(select(SyncWecomApproval).where(SyncWecomApproval.applicant_userid == c.p.user.wecom_userid)
                        .order_by(SyncWecomApproval.apply_time.desc().nulls_last(), SyncWecomApproval.id.desc()).limit(6)).all()


EXP_STATUS = {1: "审批中", 2: "待归集", 3: "已驳回", 4: "已归集"}
SP_STATUS = {1: "审批中", 2: "已通过", 3: "已驳回", 4: "已撤销"}


@card("my_expenses", "list", "我的报销", span=2)
def _(c: Ctx):
    rows = _my_expense_rows(c)
    return {"columns": [{"prop": "date", "label": "日期", "width": 96}, {"prop": "category", "label": "类别", "width": 90},
                        {"prop": "reason", "label": "事由"}, {"prop": "amount", "label": "金额", "width": 120, "money": True},
                        {"prop": "status", "label": "状态", "width": 80}],
            "rows": [{"date": r.expense_date, "category": r.category, "reason": r.reason, "amount": r.amount,
                      "status": EXP_STATUS.get(r.status) + ("·已付" if r.pay_status else "")} for r in rows]}


@card("my_approvals", "list", "我的审批", span=2)
def _(c: Ctx):
    rows = _my_approval_rows(c)
    return {"columns": [{"prop": "time", "label": "提交时间", "width": 150}, {"prop": "name", "label": "审批"},
                        {"prop": "sp_no", "label": "单号", "width": 170}, {"prop": "status", "label": "状态", "width": 80}],
            "rows": [{"time": r.apply_time, "name": r.sp_name, "sp_no": r.sp_no, "status": SP_STATUS.get(r.status),
                      "alert": r.status == 3} for r in rows]}


@card("my_todo", "reminder", "我的待办")
def _(c: Ctx):
    items = []
    exp = _my_expense_rows(c)
    for r in exp:
        if r.status in (1, 2):
            items.append({"text": f"报销 ¥{r.amount:,.2f}（{r.category}）{EXP_STATUS[r.status]}", "level": "info"})
    for a in _my_approval_rows(c):
        if a.status == 1:
            items.append({"text": f"{a.sp_name} {a.sp_no} 审批中", "level": "info"})
        elif a.status == 3:
            items.append({"text": f"{a.sp_name} {a.sp_no} 已驳回", "level": "down"})
    from app.models import OaSubmission
    from app.services import submission_service as ss
    if c.p.id in {u.id for u in ss.required_users(c.db, "weekly")}:
        wk = ss.current_period("weekly", c.T)
        done = c.db.scalar(select(OaSubmission.id).where(OaSubmission.user_id == c.p.id, OaSubmission.report_type == "weekly",
                                                         OaSubmission.period == wk))
        if not done:
            items.insert(0, {"text": f"本周周报（{wk}）尚未提交，截止周五", "level": "warn"})
    from app.models import SysNotify
    unread = c.db.scalar(select(func.count()).select_from(SysNotify).where(SysNotify.user_id == c.p.id,
                                                                            SysNotify.channel == "inapp",
                                                                            SysNotify.read_status.is_(False))) or 0
    if unread:
        items.append({"text": f"{unread} 条未读通知", "level": "info", "to": {"path": "/notifications"}})
    return {"items": items, "total": len(items), "summary": "暂无待办" if not items else None}


@card("emp_entry", "shortcut", "常用入口")
def _(c: Ctx):
    return {"items": [{"label": "知识库", "desc": "制度 / 模板 / 案例", "to": {"path": "/kb"}},
                      {"label": "WorkBuddy 接入", "desc": "个人 MCP 令牌", "to": {"path": "/workbuddy"}},
                      {"label": "消息通知", "desc": "提醒与通知", "to": {"path": "/notifications"}},
                      {"label": "个人中心", "desc": "账号与偏好", "to": {"path": "/account"}}]}


# ------------------------------------------------------------------ 聚合接口
def scheme_cards(db: Session, scheme: str) -> list[dict]:
    conf = dict_service.get(db, "portal_cards", scheme)
    if isinstance(conf, dict):
        conf = conf.get("cards")
    if not conf:
        conf = DEFAULT_SCHEMES.get(scheme) or DEFAULT_SCHEMES["employee"]
    return [x if isinstance(x, dict) else {"key": x} for x in conf]


def _menu_ok(p: Principal, menu: str | None) -> bool:
    return menu is None or p.has_menu(menu)


@router.get("/cards")
def cards(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = Ctx(db, p)
    prefs = p.user.preferences or {}
    order = prefs.get("card_order") or []
    hidden = set(prefs.get("hidden_cards") or [])
    conf = [x for x in scheme_cards(db, p.portal) if x.get("key") in CARDS]
    rank = {k: i for i, k in enumerate(order)}
    conf = [x for _, x in sorted(enumerate(conf), key=lambda t: (rank.get(t[1]["key"], 10_000), t[0]))]
    out = []
    for x in conf:
        d = CARDS[x["key"]]
        if not _menu_ok(p, d["menu"]):
            continue
        item = {"key": x["key"], "type": d["type"], "title": x.get("title") or d["title"],
                "span": x.get("span") or d["span"], "hidden": x["key"] in hidden}
        if not item["hidden"]:
            try:
                item["data"] = d["fn"](c)
            except Exception as exc:  # noqa: BLE001  单卡失败不影响整页
                log.warning("portal card %s failed: %s", x["key"], exc)
                db.rollback()
                item["data"], item["error"] = {}, "数据暂不可用"
        out.append(item)
    u = p.user
    return ok({"scheme": p.portal, "user": {"name": u.name, "position": u.position,
                                            "dept_name": u.department.name if u.department else None,
                                            "roles": [r.name for r in u.roles] or ["普通员工"]},
               "date": c.T, "now": now(), "weekday": "一二三四五六日"[c.T.weekday()],
               "cards": out})


@router.get("/work")
def personal_work(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.operations_workspace import workbench
    return ok(workbench(db, p))


@router.get("/schemes")
def schemes(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """卡片目录（管理员调整 portal_cards 字典时参考）。"""
    return ok({"cards": {k: {"type": v["type"], "title": v["title"], "span": v["span"], "menu": v["menu"]}
                         for k, v in CARDS.items()},
               "schemes": {k: scheme_cards(db, k) for k in DEFAULT_SCHEMES}})
