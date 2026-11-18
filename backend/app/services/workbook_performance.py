"""Workbook-backed performance statistics and project collection board.

When the fixed WeCom contract workbook is active, `/performance` (API, UI, Excel
export, and Xiaoju queries) reads the exact same source truth as `/contracts`,
`/collections`, and `/board` instead of the legacy `biz_contract` / `biz_payment` tables.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.timeutil import month_range, prev_month, today
from app.services import (
    business_workbook as source,
    collection_amounts,
    collection_plans,
    workbook_board,
    workbook_metrics as metrics,
)

ZERO = Decimal("0")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else ZERO


def _rate(a: Decimal | None, b: Decimal | None) -> float | None:
    if a is None or not b:
        return None
    return round(float(Decimal(str(a)) / Decimal(str(b))), 4)


def _yoy(cur: Decimal | None, last: Decimal | None) -> float | None:
    if cur is None or not last:
        return None
    return round(float((Decimal(str(cur)) - Decimal(str(last))) / Decimal(str(last))), 4)


def _same_day_last_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _period_window(period: str | None) -> tuple[str, date, date, date]:
    T = today()
    period = period or f"{T:%Y-%m}"
    ms, me = month_range(period)
    end = min(me, T) if ms <= T else me
    return period, ms, end, date(ms.year, 1, 1)


def _next_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    return f"{y + (m == 12)}-{m % 12 + 1:02d}"


def _group_sum(rows: list[dict], key_fn, value_field: str = "amount") -> dict[str, tuple[Decimal, int]]:
    out: dict[str, list] = {}
    for r in rows:
        k = key_fn(r)
        val = _d(r.get(value_field)) if r.get(value_field) is not None else ZERO
        cur = out.setdefault(k, [ZERO, 0])
        cur[0] += val
        cur[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def by_owner(db: Session, wb, period: str | None = None) -> list[dict]:
    period, ms, end, ys = _period_window(period)
    perf_rows = source.performance_rows(wb)
    m_rows = metrics.between(perf_rows, ms, end)
    y_rows = metrics.between(perf_rows, ys, end)
    pay_m = metrics.collection_metrics(wb, period, end)
    m_pay_rows = pay_m["rows"] if pay_m["available"] else []

    owner_key = lambda r: (r.get("owner") or "").strip() or "未注明"  # noqa: E731
    m_sign = _group_sum(m_rows, owner_key, "amount")
    y_sign = _group_sum(y_rows, owner_key, "amount")
    y_rec = _group_sum(y_rows, owner_key, "received")
    m_pay = _group_sum(m_pay_rows, owner_key, "received")

    names = sorted(set(m_sign) | set(y_sign) | set(m_pay))
    rows = []
    for idx, name in enumerate(names, start=1):
        rows.append({
            "owner_id": idx,
            "owner_name": name,
            "dept_code": None,
            "month_sign": m_sign.get(name, (ZERO, 0))[0],
            "month_sign_count": m_sign.get(name, (ZERO, 0))[1],
            "year_sign": y_sign.get(name, (ZERO, 0))[0],
            "year_sign_count": y_sign.get(name, (ZERO, 0))[1],
            "month_pay": m_pay.get(name, (ZERO, 0))[0],
            "year_pay": y_rec.get(name, (ZERO, 0))[0],
        })
    rows.sort(key=lambda r: (r["year_sign"], r["month_sign"]), reverse=True)
    for n, r in enumerate(rows, start=1):
        r["rank"] = n
    return rows


def trend(db: Session, wb, period: str | None = None, months: int = 12) -> list[dict]:
    period, _, end, _ = _period_window(period)
    periods = [period]
    for _ in range(months - 1):
        periods.insert(0, prev_month(periods[0]))
    out = []
    for p in periods:
        ms, me = month_range(p)
        finish = min(me, end) if p == period else me
        s = metrics.contract_metrics(wb, ms, finish)
        pay = metrics.collection_metrics(wb, p, finish if p == period else None)
        plan = collection_plans.safe_plan(wb, p)
        tgt = workbook_board.targets(db, p)
        pay_amt = pay["amount"] if pay["available"] and pay["amount"] is not None else ZERO
        out.append({
            "period": p,
            "sign": s["amount"] if s["amount"] is not None else ZERO,
            "sign_count": s["count"],
            "pay": pay_amt,
            "pay_available": pay["available"],
            "planned_pay": plan["amount"],
            "pay_target": tgt,
            "pay_rate": _rate(pay_amt, tgt) if tgt else None,
        })
    return out


def overview(db: Session, wb, period: str | None = None) -> dict:
    period, ms, end, ys = _period_window(period)
    ly_ms, ly_end, ly_ys = _same_day_last_year(ms), _same_day_last_year(end), date(ms.year - 1, 1, 1)
    pm = prev_month(period)
    pms, pme = month_range(pm)
    next_p = _next_period(period)

    m_sign = metrics.contract_metrics(wb, ms, end)
    y_sign = metrics.contract_metrics(wb, ys, end)
    lm_sign = metrics.contract_metrics(wb, ly_ms, ly_end)
    ly_sign = metrics.contract_metrics(wb, ly_ys, ly_end)
    p_sign = metrics.contract_metrics(wb, pms, pme)

    m_pay = metrics.collection_metrics(wb, period, end)
    p_pay = metrics.collection_metrics(wb, pm)
    plan_cur = collection_plans.safe_plan(wb, period)
    plan_next = collection_plans.safe_plan(wb, next_p)
    tgt_m = workbook_board.targets(db, period)

    # 当年已签合同原表累计已收（如 2026年销售合同 I75 合计 10,310,858.60）
    y_rows = y_sign["rows"]
    y_received_vals = [Decimal(r["received"]) for r in y_rows if r.get("received") is not None]
    y_received = sum(y_received_vals, ZERO)

    m_sign_amt = m_sign["amount"] or ZERO
    y_sign_amt = y_sign["amount"] or ZERO
    lm_sign_amt = lm_sign["amount"] or ZERO
    ly_sign_amt = ly_sign["amount"] or ZERO
    p_sign_amt = p_sign["amount"] or ZERO
    m_pay_amt = m_pay["amount"] if m_pay["available"] and m_pay["amount"] is not None else ZERO
    p_pay_amt = p_pay["amount"] if p_pay["available"] and p_pay["amount"] is not None else None

    # 按所属区域统计（原表无业务线列，按真实所属区域汇总）
    reg_key = lambda r: (r.get("region") or "").strip() or "未注明"  # noqa: E731
    m_by_reg = _group_sum(m_sign["rows"], reg_key, "amount")
    y_by_reg = _group_sum(y_rows, reg_key, "amount")
    ly_by_reg = _group_sum(ly_sign["rows"], reg_key, "amount")
    y_rec_by_reg = _group_sum(y_rows, reg_key, "received")
    m_pay_by_reg = _group_sum(m_pay["rows"] if m_pay["available"] else [], reg_key, "received")
    regions = sorted(set(y_by_reg) | set(m_by_reg), key=lambda k: (k == "未注明", -y_by_reg.get(k, (ZERO, 0))[0]))
    by_line = []
    for k in regions:
        ys_amt = y_by_reg.get(k, (ZERO, 0))[0]
        lys_amt = ly_by_reg.get(k, (ZERO, 0))[0]
        by_line.append({
            "business_line": k,
            "name": k,
            "month_sign": m_by_reg.get(k, (ZERO, 0))[0],
            "month_sign_target": None,
            "month_sign_rate": None,
            "year_sign": ys_amt,
            "year_sign_target": None,
            "year_sign_rate": None,
            "month_pay": m_pay_by_reg.get(k, (ZERO, 0))[0],
            "month_pay_target": None,
            "month_pay_rate": None,
            "year_pay": y_rec_by_reg.get(k, (ZERO, 0))[0],
            "year_pay_target": None,
            "year_pay_rate": None,
            "year_sign_last": lys_amt,
            "year_sign_yoy": _yoy(ys_amt, lys_amt),
            "year_pay_last": None,
            "year_pay_yoy": None,
        })

    return {
        "source_mode": "workbook",
        "source_revision": wb.revision,
        "dimension_label": "按所属区域",
        "dimension_col_label": "区域",
        "period": period,
        "start": ms,
        "end": end,
        "year_start": ys,
        "compare": {"month": [ly_ms, ly_end], "year": [ly_ys, ly_end], "prev_month": pm},
        "month_sign": {
            "amount": m_sign_amt, "count": m_sign["count"],
            "last_year": lm_sign_amt, "yoy": _yoy(m_sign_amt, lm_sign_amt),
            "prev": p_sign_amt, "mom": _yoy(m_sign_amt, p_sign_amt),
            "target": None, "rate": None,
        },
        "year_sign": {
            "amount": y_sign_amt, "count": y_sign["count"],
            "last_year": ly_sign_amt, "yoy": _yoy(y_sign_amt, ly_sign_amt),
            "target": None, "rate": None,
        },
        "month_pay": {
            "amount": m_pay_amt, "count": m_pay["count"] or 0, "available": m_pay["available"],
            "last_year": None, "yoy": None,
            "prev": p_pay_amt, "mom": _yoy(m_pay_amt, p_pay_amt) if p_pay_amt is not None else None,
            "target": tgt_m, "rate": _rate(m_pay_amt, tgt_m) if tgt_m else None,
            "planned_amount": plan_cur["amount"], "planned_count": plan_cur["count"],
        },
        "planned_pay": {
            "title": "当月预估回款金额",
            "period": period,
            "amount": plan_cur["amount"],
            "count": plan_cur["count"],
            "available": plan_cur["available"],
            "next_title": "次月预估回款金额",
            "next_period": next_p,
            "next_amount": plan_next["amount"],
            "next_count": plan_next["count"],
        },
        "year_pay": {
            "amount": y_received, "count": len(y_received_vals),
            "last_year": None, "yoy": None, "target": None, "rate": None,
        },
        "by_line": by_line,
        "by_owner": by_owner(db, wb, period),
        "trend": trend(db, wb, period),
    }


def board(db: Session, wb, region: str = "", ref: date | None = None) -> dict:
    T = ref or today()
    period = f"{T:%Y-%m}"
    next_p = _next_period(period)
    sheets = source.collection_sheets(wb)
    all_rows = source.module_rows(wb, "collections") if sheets else source.performance_rows(wb)
    if region:
        wanted = {x.strip() for x in region.split(",") if x.strip()}
        all_rows = [r for r in all_rows if ((r.get("region") or "").strip() or "未注明") in wanted]

    overall = collection_amounts.summary(sheets[0], all_rows) if sheets else None
    plan_cur = collection_plans.safe_plan(wb, period)
    plan_next = collection_plans.safe_plan(wb, next_p)

    rows = []
    cust: dict[str, dict] = {}
    for r in all_rows:
        amt = _d(r.get("amount")) if r.get("amount") is not None else ZERO
        rec = _d(r.get("received")) if r.get("received") is not None else ZERO
        out_val = _d(r.get("outstanding")) if r.get("outstanding") is not None else ZERO
        sheet_label = "2026年销售合同" if r.get("profile") == "sales" else "2026年前合同"
        reg_name = (r.get("region") or "").strip() or sheet_label
        state_name = "未收未填写" if r.get("outstanding") is None else ("待回款" if out_val > 0 else "已结清")
        rows.append({
            "project_code": f"{r['sheet_id']}:{r['row']}",
            "sheet_id": r["sheet_id"],
            "row": r["row"],
            "detail_path": f"/collections/items/{r['sheet_id']}/{r['row']}/report",
            "name": r.get("name") or "—",
            "business_line": reg_name,
            "line_name": reg_name,
            "customer_name": r.get("customer") or "未注明",
            "status": 2 if out_val > 0 else 5,
            "status_name": state_name,
            "is_key": False,
            "owner_name": r.get("owner") or "—",
            "contract_count": 1,
            "contract_amount": amt,
            "received": rec,
            "receivable": out_val,
            "pay_rate": _rate(rec, amt),
            "overdue": ZERO,
            "cost": ZERO,
            "gross_profit": ZERO,
            "gross_margin": None,
            "milestone_total": 0,
            "milestone_done": 0,
            "progress": None,
            "current_node": r.get("date") or "—",
            "delayed_count": 0,
            "delayed_nodes": [],
            "alert": False,
        })
        cname = r.get("customer") or "未注明"
        c = cust.setdefault(cname, {"customer_name": cname, "projects": 0, "contract_amount": ZERO, "received": ZERO, "receivable": ZERO, "overdue": ZERO})
        c["projects"] += 1
        c["contract_amount"] += amt
        c["received"] += rec
        c["receivable"] += out_val

    total_amt = _d(overall["amount"]["value"]) if overall and overall["amount"].get("value") is not None else sum((r["contract_amount"] for r in rows), ZERO)
    total_rec = _d(overall["received"]["value"]) if overall and overall["received"].get("value") is not None else sum((r["received"] for r in rows), ZERO)
    total_out = _d(overall["outstanding"]["value"]) if overall and overall["outstanding"].get("value") is not None else sum((r["receivable"] for r in rows), ZERO)
    unpaid_count = sum(1 for r in all_rows if r.get("outstanding") is None or _d(r.get("outstanding")) != 0)

    summary = {
        "projects": len(rows),
        "unpaid_projects": unpaid_count,
        "contract_amount": total_amt,
        "received": total_rec,
        "receivable": total_out,
        "pay_rate": _rate(total_rec, total_amt),
        "planned_month": plan_cur["amount"],
        "planned_month_count": plan_cur["count"],
        "planned_next": plan_next["amount"],
        "planned_next_count": plan_next["count"],
        "overdue": ZERO,
        "cost": ZERO,
        "gross_profit": ZERO,
        "gross_margin": None,
        "delayed_projects": 0,
        "overdue_projects": 0,
    }

    by_reg_map: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_reg_map[r["line_name"]].append(r)
    by_line = []
    for k, rs in sorted(by_reg_map.items(), key=lambda item: sum((x["contract_amount"] for x in item[1]), ZERO), reverse=True):
        a = sum((x["contract_amount"] for x in rs), ZERO)
        rc = sum((x["received"] for x in rs), ZERO)
        ou = sum((x["receivable"] for x in rs), ZERO)
        by_line.append({
            "business_line": k, "name": k, "projects": len(rs),
            "contract_amount": a, "received": rc, "receivable": ou,
            "pay_rate": _rate(rc, a), "overdue": ZERO, "cost": ZERO,
            "gross_profit": ZERO, "gross_margin": None,
            "delayed_projects": 0, "overdue_projects": 0,
        })

    top = sorted(cust.values(), key=lambda x: x["contract_amount"], reverse=True)[:10]
    for c in top:
        c["pay_rate"] = _rate(c["received"], c["contract_amount"])

    return {
        "source_mode": "workbook",
        "source_revision": wb.revision,
        "dimension_label": "区域 / 来源汇总",
        "as_of": T,
        "summary": summary,
        "projects": rows,
        "by_line": by_line,
        "top_customers": top,
        "aging": {
            "buckets": [{"bucket": b, "amount": ZERO, "count": 0, "ratio": None} for b in ("0-30", "31-90", "91-180", "180+")],
            "total": ZERO,
            "not_due": total_out,
            "detail": [],
        },
        "caliber": "固定来源口径：合并「2026年销售合同」与「2026年前（已签单个项目+未回款）」，全部合同金额按已核定主体防重口径计，已收/未收及当月/次月预估回款金额直接取企微原表对应列。",
    }
