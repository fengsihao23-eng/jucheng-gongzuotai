"""出纳日记账（FR-05-03）：日视图/月视图、期初/本期/期末、手工补录（现金账）、导出。

账户：cash（现金）+ 各银行账户（bank_account 展示名）。期初余额来自 sys_dict(journal_opening)：
    {账户: {"date": 启用日, "amount": 启用日期初余额}}；某日期初 = 启用期初 + 启用日至前一日的日记账净额。
银行日记账只含已核对流水（source_type=1）；与银行对账单余额（流水余额列）的差额即未达账项（未核对流水）。
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.timeutil import month_range
from app.models import FinBankFlow, FinCashJournal, SysUser
from app.services import bankflow_service, dict_service

D = Decimal
CASH = "cash"
CASH_NAME = "库存现金"


def openings(db: Session) -> dict[str, dict]:
    return dict_service.items(db, "journal_opening")


def account_list(db: Session) -> list[dict]:
    names = set(bankflow_service.accounts(db))
    names |= set(db.scalars(select(FinCashJournal.bank_account).where(FinCashJournal.account_type == "bank").distinct()))
    names.discard(None)
    used = set(db.scalars(select(FinBankFlow.bank_account).distinct()))
    out = [{"key": CASH, "name": CASH_NAME, "type": "cash"}]
    for n in sorted(names | used):
        out.append({"key": n, "name": n, "type": "bank"})
    return out


def _filter(q, account: str):
    if account == CASH:
        return q.where(FinCashJournal.account_type == "cash")
    return q.where(FinCashJournal.account_type == "bank", FinCashJournal.bank_account == account)


def _net_expr():
    return func.coalesce(func.sum(case((FinCashJournal.direction == 1, FinCashJournal.amount), else_=-FinCashJournal.amount)), 0)


def balance_before(db: Session, account: str, d: date) -> D:
    op = openings(db).get(account) or {}
    start = date.fromisoformat(op["date"]) if op.get("date") else date(1900, 1, 1)
    base = D(str(op.get("amount", 0)))
    q = _filter(select(_net_expr()), account).where(FinCashJournal.journal_date >= start, FinCashJournal.journal_date < d)
    return base + D(db.scalar(q) or 0)


def bank_statement_balance(db: Session, account: str, d: date) -> D | None:
    """银行对账单余额：截至 d 日（含）最后一笔流水的余额列。"""
    return db.scalar(select(FinBankFlow.balance).where(FinBankFlow.bank_account == account, FinBankFlow.trade_date <= d,
                                                       FinBankFlow.balance.is_not(None))
                     .order_by(FinBankFlow.trade_date.desc(), FinBankFlow.trade_time.desc().nulls_last(),
                               FinBankFlow.id.desc()).limit(1))


def summarize(db: Session, account: str, start: date, end: date) -> dict:
    opening = balance_before(db, account, start)
    q = _filter(select(FinCashJournal.direction, func.sum(FinCashJournal.amount), func.count()), account).where(
        FinCashJournal.journal_date.between(start, end)).group_by(FinCashJournal.direction)
    inc, exp, n = D(0), D(0), 0
    for d, s, c in db.execute(q):
        if d == 1:
            inc = D(s)
        else:
            exp = D(s)
        n += c
    out = {"account": account, "name": CASH_NAME if account == CASH else account, "type": "cash" if account == CASH else "bank",
           "opening": opening, "income": inc, "expense": exp, "closing": opening + inc - exp, "count": n}
    if account != CASH:
        stmt = bank_statement_balance(db, account, end)
        unmatched = db.execute(select(func.count(), func.coalesce(func.sum(case((FinBankFlow.direction == 1, FinBankFlow.amount),
                                                                              else_=-FinBankFlow.amount)), 0))
                               .where(FinBankFlow.bank_account == account, FinBankFlow.match_status == 0,
                                      FinBankFlow.trade_date <= end)).one()
        out.update({"statement_balance": stmt, "unmatched_count": unmatched[0], "unmatched_net": D(unmatched[1]),
                    "difference": (stmt - out["closing"]) if stmt is not None else None})
    return out


def entry_row(j: FinCashJournal, users: dict[int, str]) -> dict:
    return {"id": j.id, "journal_date": j.journal_date, "account_type": j.account_type,
            "account": CASH if j.account_type == "cash" else j.bank_account,
            "account_name": CASH_NAME if j.account_type == "cash" else j.bank_account, "direction": j.direction,
            "income": j.amount if j.direction == 1 else None, "expense": j.amount if j.direction == 2 else None,
            "amount": j.amount, "summary": j.summary, "category": j.category, "project_code": j.project_code,
            "biz_ref": j.biz_ref, "bank_flow_id": j.bank_flow_id, "source_type": j.source_type,
            "source_name": "流水核对" if j.source_type == 1 else "手工", "register_by": users.get(j.register_by),
            "created_at": j.created_at, "editable": j.source_type == 2}


def entries(db: Session, account: str | None, start: date, end: date) -> list[dict]:
    q = select(FinCashJournal).where(FinCashJournal.journal_date.between(start, end))
    if account:
        q = _filter(q, account)
    rows = db.scalars(q.order_by(FinCashJournal.journal_date, FinCashJournal.id)).all()
    users = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    out = [entry_row(j, users) for j in rows]
    if account:
        bal = balance_before(db, account, start)
        for r in out:
            bal += r["amount"] if r["direction"] == 1 else -r["amount"]
            r["balance"] = bal
    return out


def view(db: Session, mode: str, ref: date | None = None, period: str | None = None, account: str | None = None) -> dict:
    if mode == "month":
        start, end = month_range(period) if period else month_range(ref=ref)
    else:
        start = end = ref
    accts = [a["key"] for a in account_list(db)]
    cards = [summarize(db, a, start, end) for a in accts]
    cards = [c for c in cards if c["count"] or c["opening"] or c["type"] == "cash" or c.get("statement_balance")]
    total = {k: sum((c[k] for c in cards), D(0)) for k in ("opening", "income", "expense", "closing")}
    bank_total = {k: sum((c[k] for c in cards if c["type"] == "bank"), D(0)) for k in ("opening", "income", "expense", "closing")}
    out = {"mode": mode, "start": start, "end": end, "accounts": cards, "total": total, "bank_total": bank_total,
           "entries": entries(db, account, start, end)}
    if mode == "month":
        q = select(FinCashJournal.journal_date, FinCashJournal.direction, func.sum(FinCashJournal.amount)).where(
            FinCashJournal.journal_date.between(start, end))
        if account:
            q = _filter(q, account)
        daily = defaultdict(lambda: {"income": D(0), "expense": D(0)})
        for d, di, s in db.execute(q.group_by(FinCashJournal.journal_date, FinCashJournal.direction)):
            daily[d]["income" if di == 1 else "expense"] += D(s)
        days, cur = [], start
        bal = balance_before(db, account, start) if account else total["opening"]
        while cur <= end:
            v = daily.get(cur, {"income": D(0), "expense": D(0)})
            bal += v["income"] - v["expense"]
            days.append({"date": cur, **v, "balance": bal})
            cur += timedelta(days=1)
        out["daily"] = days
    return out


# ---------------- 导出 ----------------
def export_xlsx(db: Session, start: date, end: date) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="EEF1F8")
    cols = ["日期", "账户", "摘要", "类别", "项目编码", "关联单号", "收入", "支出", "余额", "来源", "登记人"]

    def sheet(ws, keys):
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), head_fill
        for acct in keys:
            s = summarize(db, acct, start, end)
            ws.append([start.isoformat(), s["name"], "期初余额", None, None, None, None, None, float(s["opening"]), None, None])
            for r in entries(db, acct, start, end):
                ws.append([r["journal_date"].isoformat(), r["account_name"], r["summary"], r["category"], r["project_code"],
                           r["biz_ref"], float(r["income"]) if r["income"] is not None else None,
                           float(r["expense"]) if r["expense"] is not None else None, float(r["balance"]),
                           r["source_name"], r["register_by"]])
            ws.append([end.isoformat(), s["name"], "本期合计", None, None, None, float(s["income"]), float(s["expense"]),
                       float(s["closing"]), None, None])
            ws.append([])
        for col, w in zip("ABCDEFGHIJK", (12, 28, 40, 10, 16, 22, 14, 14, 16, 10, 10)):
            ws.column_dimensions[col].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row[6:9]:
                c.number_format = "#,##0.00"

    ws = wb.active
    ws.title = "银行日记账"
    sheet(ws, [a["key"] for a in account_list(db) if a["type"] == "bank"])
    sheet(wb.create_sheet("现金日记账"), [CASH])
    ws3 = wb.create_sheet("口径说明")
    for line in [
        ["出纳日记账导出口径说明"],
        ["期间", f"{start} ~ {end}"],
        ["银行日记账", "由银行流水核对确认后自动登记（来源=流水核对），一条流水对应一条日记账；未核对流水不入账，属未达账项。"],
        ["现金日记账", "出纳手工补录（来源=手工），编辑/删除全量留痕于操作审计。"],
        ["期初余额", "按 sys_dict(journal_opening) 账户启用期初 + 启用日至期间前一日的日记账净额计算。"],
        ["收入/支出", "收入=借方（增加），支出=贷方（减少）；金额单位：元。"],
        ["回款口径", "回款日期以银行流水到账日为准（PRD 3.4）。"],
    ]:
        ws3.append(line)
    ws3["A1"].font = Font(bold=True, size=13)
    ws3.column_dimensions["A"].width = 14
    ws3.column_dimensions["B"].width = 90
    for row in ws3.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
