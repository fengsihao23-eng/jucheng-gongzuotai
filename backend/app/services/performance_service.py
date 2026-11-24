"""业绩统计（FR-05-07 / PRD 3.4）与项目整体运营看板（营销管控部 7.4）。

口径（sys_dict 可调，待财务评审）：
- 当月合同签订额 = 签订日落在本月的合同金额合计（不含已终止、已删除）
- 年度累计业绩   = 当年 1 月 1 日至统计日签订合同金额合计
- 当月回款       = 回款日期（银行流水到账日）落在本月的回款合计
- 回款目标       = sys_dict payment_target[yyyy-MM]（按业务线）；年度回款目标 = 当年各月目标合计
- 签约目标       = sys_dict sign_target[yyyy]（按业务线年度目标）；月度签约目标 = 年度 ÷ 12，
                   或 sign_target[yyyy-MM] 显式覆盖
- 运营看板范围   = 在建/已验收/运维（status 2/3/4）项目；成本/毛利调用 profit_service.project_profit（与周利润表一致）
- 逾期应收       = 回款计划 status=5（逾期）未收金额；账龄 = 计划到期日已过且未收清部分，按 统计日 − 到期日 分档
"""
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import BUSINESS_LINES
from app.core.timeutil import TZ, month_range, prev_month, today
from app.models import BizContract, BizPayment, BizPaymentPlan, BizProject, SysUser
from app.services import dict_service, profit_service

ZERO = Decimal("0")
LINES = list(BUSINESS_LINES)
ACTIVE_STATUS = (2, 3, 4)
AGING_BUCKETS = [("0-30", 0, 30), ("31-90", 31, 90), ("91-180", 91, 180), ("180+", 181, 10 ** 6)]
PROJECT_STATUS = {1: "招标中", 2: "在建", 3: "已验收", 4: "运维", 5: "结项"}


def _d(v) -> Decimal:
    return Decimal(v or 0)


def _rate(a: Decimal, b: Decimal) -> float | None:
    return round(float(a / b), 4) if b else None


def _yoy(cur: Decimal, last: Decimal) -> float | None:
    return round(float((cur - last) / last), 4) if last else None


def _same_day_last_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 2/29
        return d.replace(year=d.year - 1, day=28)


def _contract_base():
    return select(BizContract).where(BizContract.is_deleted.is_(False), BizContract.status != 3)


# ---------------- 聚合原语（SQL） ----------------
def sign_by(db: Session, start: date, end: date, key) -> dict:
    q = select(key, func.coalesce(func.sum(BizContract.amount), 0), func.count()).where(
        BizContract.is_deleted.is_(False), BizContract.status != 3,
        BizContract.sign_date >= start, BizContract.sign_date <= end).group_by(key)
    return {k: (_d(a), n) for k, a, n in db.execute(q).all()}


def pay_by(db: Session, start: date, end: date, key) -> dict:
    q = select(key, func.coalesce(func.sum(BizPayment.amount), 0), func.count()).join(BizPayment.contract).where(
        BizContract.is_deleted.is_(False), BizPayment.payment_date >= start, BizPayment.payment_date <= end).group_by(key)
    return {k: (_d(a), n) for k, a, n in db.execute(q).all()}


def _total(d: dict) -> Decimal:
    return sum((v[0] for v in d.values()), ZERO)


# ---------------- 目标 ----------------
def pay_target(db: Session, period: str) -> dict[str, Decimal]:
    t = dict_service.get(db, "payment_target", period) or {}
    return {k: _d(t.get(k)) for k in LINES}


def pay_target_year(db: Session, year: int) -> dict[str, Decimal]:
    out = {k: ZERO for k in LINES}
    for key, val in dict_service.items(db, "payment_target").items():
        if key.startswith(f"{year}-") and isinstance(val, dict):
            for k in LINES:
                out[k] += _d(val.get(k))
    return out


def sign_target_year(db: Session, year: int) -> dict[str, Decimal]:
    t = dict_service.get(db, "sign_target", str(year)) or {}
    return {k: _d(t.get(k)) for k in LINES}


def sign_target_month(db: Session, period: str) -> dict[str, Decimal]:
    explicit = dict_service.get(db, "sign_target", period)
    if isinstance(explicit, dict):
        return {k: _d(explicit.get(k)) for k in LINES}
    y = sign_target_year(db, int(period[:4]))
    return {k: (v / 12).quantize(Decimal("0.01")) for k, v in y.items()}


def _period_window(period: str | None) -> tuple[str, date, date, date]:
    """返回 (period, 月初, 统计截止日, 年初)。当月统计至今天，历史月份统计至月末。"""
    T = today()
    period = period or f"{T:%Y-%m}"
    ms, me = month_range(period)
    end = min(me, T) if ms <= T else me
    return period, ms, end, date(ms.year, 1, 1)


# ---------------- 业绩统计 ----------------
def overview(db: Session, period: str | None = None) -> dict:
    from app.services import business_workbook, workbook_performance
    wb = business_workbook.configured_source(db)
    if wb is not None:
        return workbook_performance.overview(db, wb, period)
    period, ms, end, ys = _period_window(period)
    ly_ms, ly_end, ly_ys = _same_day_last_year(ms), _same_day_last_year(end), date(ms.year - 1, 1, 1)
    L = BizContract.business_line
    m_sign, y_sign = sign_by(db, ms, end, L), sign_by(db, ys, end, L)
    m_pay, y_pay = pay_by(db, ms, end, L), pay_by(db, ys, end, L)
    lm_sign, ly_sign = sign_by(db, ly_ms, ly_end, L), sign_by(db, ly_ys, ly_end, L)
    lm_pay, ly_pay = pay_by(db, ly_ms, ly_end, L), pay_by(db, ly_ys, ly_end, L)
    pm = prev_month(period)
    pms, pme = month_range(pm)
    p_sign, p_pay = sign_by(db, pms, pme, L), pay_by(db, pms, pme, L)

    st_m, st_y = sign_target_month(db, period), sign_target_year(db, ms.year)
    pt_m, pt_y = pay_target(db, period), pay_target_year(db, ms.year)

    def kpi(cur: dict, tgt: dict | None, last: dict, prev: dict | None = None) -> dict:
        c = _total(cur)
        t = sum(tgt.values(), ZERO) if tgt is not None else None
        out = {"amount": c, "count": sum(v[1] for v in cur.values()), "last_year": _total(last),
               "yoy": _yoy(c, _total(last))}
        if tgt is not None:
            out.update(target=t, rate=_rate(c, t))
        if prev is not None:
            out.update(prev=_total(prev), mom=_yoy(c, _total(prev)))
        return out

    by_line = []
    for k in LINES:
        g = lambda d: d.get(k, (ZERO, 0))[0]  # noqa: E731
        by_line.append({
            "business_line": k, "name": BUSINESS_LINES[k],
            "month_sign": g(m_sign), "month_sign_target": st_m[k], "month_sign_rate": _rate(g(m_sign), st_m[k]),
            "year_sign": g(y_sign), "year_sign_target": st_y[k], "year_sign_rate": _rate(g(y_sign), st_y[k]),
            "month_pay": g(m_pay), "month_pay_target": pt_m[k], "month_pay_rate": _rate(g(m_pay), pt_m[k]),
            "year_pay": g(y_pay), "year_pay_target": pt_y[k], "year_pay_rate": _rate(g(y_pay), pt_y[k]),
            "year_sign_last": g(ly_sign), "year_sign_yoy": _yoy(g(y_sign), g(ly_sign)),
            "year_pay_last": g(ly_pay), "year_pay_yoy": _yoy(g(y_pay), g(ly_pay)),
        })
    return {
        "period": period, "start": ms, "end": end, "year_start": ys,
        "compare": {"month": [ly_ms, ly_end], "year": [ly_ys, ly_end], "prev_month": pm},
        "month_sign": kpi(m_sign, st_m, lm_sign, p_sign), "year_sign": kpi(y_sign, st_y, ly_sign),
        "month_pay": kpi(m_pay, pt_m, lm_pay, p_pay), "year_pay": kpi(y_pay, pt_y, ly_pay),
        "by_line": by_line, "by_owner": by_owner(db, period), "trend": trend(db, period),
    }


def by_owner(db: Session, period: str | None = None) -> list[dict]:
    from app.services import business_workbook, workbook_performance
    wb = business_workbook.configured_source(db)
    if wb is not None:
        return workbook_performance.by_owner(db, wb, period)
    period, ms, end, ys = _period_window(period)
    O = BizContract.owner_id
    m_sign, y_sign, m_pay, y_pay = sign_by(db, ms, end, O), sign_by(db, ys, end, O), pay_by(db, ms, end, O), pay_by(db, ys, end, O)
    ids = set(m_sign) | set(y_sign) | set(m_pay) | set(y_pay)
    names = {u.id: u for u in db.scalars(select(SysUser).where(SysUser.id.in_([i for i in ids if i]))).unique()}
    rows = []
    for i in ids:
        u = names.get(i)
        rows.append({"owner_id": i, "owner_name": u.name if u else "未指定", "dept_code": u.dept_code if u else None,
                     "month_sign": m_sign.get(i, (ZERO, 0))[0], "month_sign_count": m_sign.get(i, (ZERO, 0))[1],
                     "year_sign": y_sign.get(i, (ZERO, 0))[0], "year_sign_count": y_sign.get(i, (ZERO, 0))[1],
                     "month_pay": m_pay.get(i, (ZERO, 0))[0], "year_pay": y_pay.get(i, (ZERO, 0))[0]})
    rows.sort(key=lambda r: (r["year_sign"], r["month_sign"]), reverse=True)
    for n, r in enumerate(rows, start=1):
        r["rank"] = n
    return rows


def trend(db: Session, period: str | None = None, months: int = 12) -> list[dict]:
    from app.services import business_workbook, workbook_performance
    wb = business_workbook.configured_source(db)
    if wb is not None:
        return workbook_performance.trend(db, wb, period, months)
    period, _, end, _ = _period_window(period)
    periods = [period]
    for _ in range(months - 1):
        periods.insert(0, prev_month(periods[0]))
    start = month_range(periods[0])[0]
    m = func.to_char(BizContract.sign_date, "YYYY-MM")
    sign = {k: _d(v) for k, v in db.execute(select(m, func.sum(BizContract.amount)).where(
        BizContract.is_deleted.is_(False), BizContract.status != 3, BizContract.sign_date >= start,
        BizContract.sign_date <= end).group_by(m)).all()}
    pm = func.to_char(BizPayment.payment_date, "YYYY-MM")
    pay = {k: _d(v) for k, v in db.execute(select(pm, func.sum(BizPayment.amount)).join(BizPayment.contract).where(
        BizContract.is_deleted.is_(False), BizPayment.payment_date >= start, BizPayment.payment_date <= end)
        .group_by(pm)).all()}
    out = []
    for p in periods:
        tgt = sum(pay_target(db, p).values(), ZERO)
        out.append({"period": p, "sign": sign.get(p, ZERO), "pay": pay.get(p, ZERO), "pay_target": tgt,
                    "pay_rate": _rate(pay.get(p, ZERO), tgt)})
    return out


# ---------------- 项目整体运营看板 ----------------
def board(db: Session, business_line: str = "", ref: date | None = None) -> dict:
    from app.services import business_workbook, workbook_performance
    wb = business_workbook.configured_source(db)
    if wb is not None:
        return workbook_performance.board(db, wb, business_line, ref)
    T = ref or today()
    q = select(BizProject).where(BizProject.is_deleted.is_(False), BizProject.status.in_(ACTIVE_STATUS)) \
        .options(selectinload(BizProject.milestones)).order_by(BizProject.business_line, BizProject.project_code)
    if business_line:
        q = q.where(BizProject.business_line.in_(business_line.split(",")))
    projects = db.scalars(q).unique().all()
    codes = [p.project_code for p in projects]
    contracts = db.scalars(_contract_base().where(BizContract.project_code.in_(codes))
                           .options(selectinload(BizContract.payments), selectinload(BizContract.plans))).all() if codes else []
    by_proj: dict[str, list[BizContract]] = defaultdict(list)
    for c in contracts:
        by_proj[c.project_code].append(c)
    prof = {r["project_code"]: r for r in profit_service.project_profit(db, date(T.year, 1, 1), T, codes)["rows"]} if codes else {}

    rows, aging_detail = [], []
    buckets = {b[0]: ZERO for b in AGING_BUCKETS}
    bucket_n = {b[0]: 0 for b in AGING_BUCKETS}
    not_due = ZERO
    for p in projects:
        cs = by_proj.get(p.project_code, [])
        amount = sum((c.amount for c in cs), ZERO)
        received = sum((pay.amount for c in cs for pay in c.payments), ZERO)
        overdue = sum((pl.amount - pl.received_amount for c in cs for pl in c.plans if pl.status == 5), ZERO)
        for c in cs:
            for pl in c.plans:
                left = pl.amount - pl.received_amount
                if left <= 0:
                    continue
                if not pl.due_date or pl.due_date >= T:
                    not_due += left
                    continue
                days = (T - pl.due_date).days
                b = next(name for name, lo, hi in AGING_BUCKETS if lo <= days <= hi)
                buckets[b] += left
                bucket_n[b] += 1
                aging_detail.append({"project_code": p.project_code, "project_name": p.name, "contract_no": c.contract_no,
                                     "customer_name": c.customer_name, "stage_name": pl.stage_name, "due_date": pl.due_date,
                                     "amount": pl.amount, "received": pl.received_amount, "outstanding": left,
                                     "days": days, "bucket": b})
        ms = p.milestones
        done = sum(1 for m in ms if m.status == 3)
        delayed = [m for m in ms if m.status == 4 or (m.status != 3 and m.plan_date and m.plan_date < T)]
        current = next((m for m in ms if m.status != 3), None)
        pr = prof.get(p.project_code, {})
        rows.append({
            "project_code": p.project_code, "name": p.name, "business_line": p.business_line,
            "line_name": BUSINESS_LINES.get(p.business_line), "customer_name": p.customer_name,
            "status": p.status, "status_name": PROJECT_STATUS.get(p.status), "is_key": p.is_key,
            "owner_name": p.owner.name if p.owner else None, "contract_count": len(cs),
            "contract_amount": amount, "received": received, "receivable": amount - received,
            "pay_rate": _rate(received, amount), "overdue": overdue,
            "cost": pr.get("cost", ZERO), "gross_profit": pr.get("gross_profit", amount - pr.get("cost", ZERO)),
            "gross_margin": float(pr["gross_margin"]) if pr.get("gross_margin") is not None else None,
            "milestone_total": len(ms), "milestone_done": done, "progress": round(done / len(ms), 4) if ms else None,
            "current_node": current.node_name if current else ("全部完成" if ms else None),
            "delayed_count": len(delayed), "delayed_nodes": [m.node_name for m in delayed],
            "alert": bool(overdue > 0 or delayed),
        })

    def summarize(rs: list[dict]) -> dict:
        amt = sum((r["contract_amount"] for r in rs), ZERO)
        rec = sum((r["received"] for r in rs), ZERO)
        cost = sum((r["cost"] for r in rs), ZERO)
        gp = sum((r["gross_profit"] for r in rs), ZERO)
        return {"projects": len(rs), "contract_amount": amt, "received": rec, "receivable": amt - rec,
                "pay_rate": _rate(rec, amt), "overdue": sum((r["overdue"] for r in rs), ZERO), "cost": cost,
                "gross_profit": gp, "gross_margin": _rate(gp, amt),
                "delayed_projects": sum(1 for r in rs if r["delayed_count"]),
                "overdue_projects": sum(1 for r in rs if r["overdue"] > 0)}

    by_line = [{"business_line": k, "name": BUSINESS_LINES[k], **summarize([r for r in rows if r["business_line"] == k])}
               for k in LINES if any(r["business_line"] == k for r in rows)]
    cust: dict[str, dict] = {}
    for r in rows:
        c = cust.setdefault(r["customer_name"], {"customer_name": r["customer_name"], "projects": 0,
                                                  "contract_amount": ZERO, "received": ZERO, "overdue": ZERO})
        c["projects"] += 1
        c["contract_amount"] += r["contract_amount"]
        c["received"] += r["received"]
        c["overdue"] += r["overdue"]
    top = sorted(cust.values(), key=lambda x: x["contract_amount"], reverse=True)[:10]
    for c in top:
        c["receivable"] = c["contract_amount"] - c["received"]
        c["pay_rate"] = _rate(c["received"], c["contract_amount"])
    aging_total = sum(buckets.values(), ZERO)
    aging_detail.sort(key=lambda x: x["days"], reverse=True)
    return {
        "as_of": T, "summary": summarize(rows), "projects": rows, "by_line": by_line, "top_customers": top,
        "aging": {"buckets": [{"bucket": b, "amount": buckets[b], "count": bucket_n[b],
                               "ratio": _rate(buckets[b], aging_total)} for b, _, _ in AGING_BUCKETS],
                  "total": aging_total, "not_due": not_due, "detail": aging_detail},
        "caliber": "运营项目=在建/已验收/运维；回款率=累计回款÷合同额；逾期应收=逾期回款计划未收额；"
                   "成本/毛利=profit_service 口径（当年累计至统计日）；账龄按计划到期日计",
    }


# ---------------- 导出 ----------------
HEAD_FILL = PatternFill("solid", fgColor="1B3275")
MONEY = "#,##0.00"
PCT = "0.0%"


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], widths: list[int] | None = None,
           money_cols: tuple = (), pct_cols: tuple = (), first: bool = False):
    ws = wb.active if first else wb.create_sheet(title)
    ws.title = title
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), HEAD_FILL, Alignment(vertical="center")
    for r in rows:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in r])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if c.column in money_cols:
                c.number_format = MONEY
            elif c.column in pct_cols:
                c.number_format = PCT
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    ws.freeze_panes = "A2"
    return ws


def _caliber_sheet(wb: Workbook, lines: list[tuple[str, str]]):
    _sheet(wb, "口径说明", ["口径项", "说明"], [list(x) for x in lines] +
           [["生成时间", datetime.now(TZ).strftime("%Y-%m-%d %H:%M")], ["数据来源", "聚成数智统一工作台 · 合同台账/回款/回款计划/项目流程"]],
           [16, 90])


def _bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


PERF_CALIBER = [
    ("当月合同签订额", "签订日期（企微合同审批通过日）落在统计月内的合同金额合计；已终止合同不计"),
    ("年度累计业绩", "当年 1 月 1 日至统计截止日签订的合同金额合计"),
    ("当月回款", "回款日期（以银行流水到账日为准）落在统计月内的回款合计"),
    ("回款目标", "sys_dict payment_target 按月按业务线配置；年度回款目标 = 当年各月目标合计"),
    ("签约目标", "sys_dict sign_target 按年按业务线配置；月度目标 = 年度 ÷ 12（可按月覆盖）"),
    ("同比", "与上年同期（同月同日截止 / 上年 1 月 1 日至同日）比较"),
    ("口径状态", "PRD 3.4 建议稿，待财务评审；口径调整只改 sys_dict 配置"),
]


def performance_xlsx(db: Session, period: str | None = None) -> tuple[bytes, dict]:
    ov = overview(db, period)
    wb = Workbook()
    k = [("当月签约", ov["month_sign"]), ("年度累计业绩", ov["year_sign"]), ("当月已回款", ov["month_pay"]),
         ("年度累计回款", ov["year_pay"])]
    kpi_rows = [[n, v["amount"], v.get("target"), v.get("rate"), v["last_year"], v["yoy"]] for n, v in k]
    if ov.get("planned_pay"):
        pp = ov["planned_pay"]
        kpi_rows.append([pp.get("title", "当月预估回款金额"), pp.get("amount"), None, None, None, None])
        kpi_rows.append([pp.get("next_title", "次月预估回款金额"), pp.get("next_amount"), None, None, None, None])
    dim_title = ov.get("dimension_label") or "按业务线"
    dim_col = ov.get("dimension_col_label") or "业务线"
    _sheet(wb, "业绩总览", ["指标", "金额(元)", "目标(元)", "达成率", "上年同期(元)", "同比"],
           kpi_rows,
           [18, 18, 18, 10, 18, 10], money_cols=(2, 3, 5), pct_cols=(4, 6), first=True)
    _sheet(wb, dim_title, [dim_col, "当月签约", "月签约目标", "达成率", "年度签约", "年签约目标", "达成率",
                         "当月回款", "月回款目标", "达成率", "年度回款", "年回款目标", "达成率", "年签约同比"],
           [[r["name"], r["month_sign"], r["month_sign_target"], r["month_sign_rate"], r["year_sign"],
             r["year_sign_target"], r["year_sign_rate"], r["month_pay"], r["month_pay_target"], r["month_pay_rate"],
             r["year_pay"], r["year_pay_target"], r["year_pay_rate"], r["year_sign_yoy"]] for r in ov["by_line"]],
           [14] + [14] * 13, money_cols=(2, 3, 5, 6, 8, 9, 11, 12), pct_cols=(4, 7, 10, 13, 14))
    _sheet(wb, "按销售负责人", ["排名", "负责人", "当月签约", "当月单数", "年度签约", "年度单数", "当月回款", "年度回款"],
           [[r["rank"], r["owner_name"], r["month_sign"], r["month_sign_count"], r["year_sign"], r["year_sign_count"],
             r["month_pay"], r["year_pay"]] for r in ov["by_owner"]],
           [8, 12, 16, 10, 16, 10, 16, 16], money_cols=(3, 5, 7, 8))
    _sheet(wb, "近12月趋势", ["月份", "签约额", "回款额", "回款目标", "回款达成率"],
           [[t["period"], t["sign"], t["pay"], t["pay_target"], t["pay_rate"]] for t in ov["trend"]],
           [10, 16, 16, 16, 12], money_cols=(2, 3, 4), pct_cols=(5,))
    _caliber_sheet(wb, PERF_CALIBER + [("统计区间", f"{ov['start']} ~ {ov['end']}（年度自 {ov['year_start']}）")])
    metrics = {"period": ov["period"], "month_sign": ov["month_sign"]["amount"], "year_sign": ov["year_sign"]["amount"],
               "month_pay": ov["month_pay"]["amount"], "year_pay": ov["year_pay"]["amount"],
               "month_pay_rate": ov["month_pay"].get("rate"), "year_sign_rate": ov["year_sign"].get("rate")}
    return _bytes(wb), metrics


BOARD_CALIBER = [
    ("项目范围", "状态为 在建 / 已验收 / 运维 的项目（招标中、结项不计）"),
    ("合同额", "项目关联的有效合同（不含已终止）金额合计"),
    ("回款率", "累计回款 ÷ 合同额；应收余额 = 合同额 − 累计回款"),
    ("逾期应收", "回款计划状态为「逾期」（到期未回款）的未收金额"),
    ("成本/毛利", "调用 profit_service.project_profit（当年 1 月 1 日至统计日），与周利润表、经营看板同一口径"),
    ("节点延期", "流程节点状态为延期，或计划日期已过仍未完成"),
    ("账龄", "回款计划到期日已过且未收清部分，按 统计日 − 到期日 分 0-30/31-90/91-180/180+ 天"),
]


def board_xlsx(db: Session, ref: date | None = None) -> tuple[bytes, dict]:
    b = board(db, ref=ref)
    wb = Workbook()
    _sheet(wb, "运营项目", ["项目编码", "项目名称", "业务线", "客户", "状态", "项目经理", "合同额", "累计回款", "回款率",
                         "应收余额", "逾期应收", "成本", "毛利", "毛利率", "节点进度", "当前节点", "延期节点"],
           [[r["project_code"], r["name"], r["line_name"], r["customer_name"], r["status_name"], r["owner_name"],
             r["contract_amount"], r["received"], r["pay_rate"], r["receivable"], r["overdue"], r["cost"],
             r["gross_profit"], r["gross_margin"], f"{r['milestone_done']}/{r['milestone_total']}", r["current_node"],
             "、".join(r["delayed_nodes"])] for r in b["projects"]],
           [16, 30, 12, 24, 8, 10, 16, 16, 9, 16, 14, 14, 14, 9, 9, 12, 24],
           money_cols=(7, 8, 10, 11, 12, 13), pct_cols=(9, 14), first=True)
    s = b["summary"]
    _sheet(wb, "业务线汇总", ["业务线", "项目数", "合同额", "累计回款", "回款率", "应收余额", "逾期应收", "毛利", "毛利率",
                          "延期项目数"],
           [[r["name"], r["projects"], r["contract_amount"], r["received"], r["pay_rate"], r["receivable"], r["overdue"],
             r["gross_profit"], r["gross_margin"], r["delayed_projects"]] for r in b["by_line"]] +
           [["合计", s["projects"], s["contract_amount"], s["received"], s["pay_rate"], s["receivable"], s["overdue"],
             s["gross_profit"], s["gross_margin"], s["delayed_projects"]]],
           [14, 8, 16, 16, 9, 16, 14, 16, 9, 10], money_cols=(3, 4, 6, 7, 8), pct_cols=(5, 9))
    _sheet(wb, "客户Top10", ["排名", "客户", "项目数", "合同额", "累计回款", "回款率", "应收余额", "逾期应收"],
           [[i, c["customer_name"], c["projects"], c["contract_amount"], c["received"], c["pay_rate"], c["receivable"],
             c["overdue"]] for i, c in enumerate(b["top_customers"], start=1)],
           [6, 28, 8, 16, 16, 9, 16, 14], money_cols=(4, 5, 7, 8), pct_cols=(6,))
    ag = b["aging"]
    ws = _sheet(wb, "回款账龄", ["账龄", "笔数", "未收金额", "占比"],
                [[x["bucket"] + " 天", x["count"], x["amount"], x["ratio"]] for x in ag["buckets"]] +
                [["合计（已到期）", sum(x["count"] for x in ag["buckets"]), ag["total"], 1 if ag["total"] else None],
                 ["未到期应收", None, ag["not_due"], None]],
                [16, 8, 16, 9], money_cols=(3,), pct_cols=(4,))
    ws.append([])
    ws.append(["明细：项目编码", "合同编号", "客户", "回款阶段", "到期日", "未收金额", "逾期天数", "账龄"])
    for d in ag["detail"]:
        ws.append([d["project_code"], d["contract_no"], d["customer_name"], d["stage_name"], d["due_date"],
                   float(d["outstanding"]), d["days"], d["bucket"]])
    _caliber_sheet(wb, BOARD_CALIBER + [("统计日", str(b["as_of"]))])
    metrics = {"as_of": b["as_of"], **{k: s[k] for k in ("projects", "contract_amount", "received", "pay_rate",
                                                         "receivable", "overdue", "gross_profit", "delayed_projects")},
               "aging": {x["bucket"]: x["amount"] for x in ag["buckets"]}}
    return _bytes(wb), metrics


def generate_report(db: Session, kind: str, period: str, *, trigger: str = "manual", user_id: int | None = None,
                    notify: bool = True):
    """生成快照 RptReport（performance_monthly / operation_board）+ xlsx，并推送营销管控与总办。"""
    import time

    from app.core.response import encode
    from app.core.storage import save_bytes
    from app.models import RptReport
    from app.services import notify_service
    t0 = time.perf_counter()
    if kind == "performance_monthly":
        data, metrics = performance_xlsx(db, period)
        title = f"{period} 业绩统计"
        filename = f"业绩统计_{period}.xlsx"
        link = f"/performance?period={period}"
    else:
        data, metrics = board_xlsx(db)
        title = f"项目运营看板 {period}"
        filename = f"项目运营看板_{period}.xlsx"
        link = "/performance?tab=board"
    month = period[:7] if kind == "performance_monthly" else f"{today():%Y-%m}"
    f = save_bytes(db, data=data, filename=filename, category="report",
                   key=f"report/{kind}/{month}/{filename}", uploader_id=user_id)
    ver = (db.scalar(select(func.max(RptReport.version)).where(RptReport.report_type == kind,
                                                               RptReport.period == period)
                     .execution_options(include_removed_records=True)) or 0) + 1
    rpt = RptReport(report_type=kind, period=period, title=title, version=ver, metrics=encode(metrics),
                    file_ids=[f.id], trigger=trigger, created_by=user_id,
                    cost_ms=int((time.perf_counter() - t0) * 1000))
    db.add(rpt)
    db.flush()
    if notify:
        if kind == "performance_monthly":
            content = (f"当月签约 ¥{_d(metrics['month_sign']):,.0f}，年度累计 ¥{_d(metrics['year_sign']):,.0f}\n"
                       f"当月回款 ¥{_d(metrics['month_pay']):,.0f}，达成率 "
                       f"{(metrics['month_pay_rate'] or 0) * 100:.1f}%")
        else:
            content = (f"运营项目 {metrics['projects']} 个，合同额 ¥{_d(metrics['contract_amount']):,.0f}，"
                       f"回款率 {(metrics['pay_rate'] or 0) * 100:.1f}%\n逾期应收 ¥{_d(metrics['overdue']):,.0f}，"
                       f"节点延期项目 {metrics['delayed_projects']} 个")
        notify_service.send(db, notify_service.users_by_roles(db, ["YX-01", "ZB-01"]), "report", title, content,
                            link=link, ref_type="report", ref_id=str(rpt.id))
    return rpt, f
