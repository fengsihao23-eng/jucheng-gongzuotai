"""Existing dashboard, aggregating the four corrected module read models."""
from datetime import date, timedelta
from decimal import Decimal

from app.core.response import bad_request
from app.core.timeutil import month_of, month_range, prev_month, week_range
from app.services import business_workbook as source, workbook_metrics as metrics, dict_service


def rate(a, b):
    return float(a / b) if a is not None and b else None


def signing(wb, end):
    out = {key: metrics.contract_metrics(wb, start, end) for key, start in
           (("today", end), ("week", week_range(ref=end)[0]), ("month", month_range(ref=end)[0]), ("year", date(end.year, 1, 1)))}
    lm = metrics.contract_metrics(wb, *month_range(prev_month(month_of(end))))
    try:
        ly_end = end.replace(year=end.year-1)
    except ValueError:
        ly_end = end.replace(year=end.year-1, day=28)
    ly = metrics.contract_metrics(wb, date(end.year-1, 1, 1), ly_end)
    out["month"].update(prev_month_amount=lm["amount"], mom=rate(out["month"]["amount"] - lm["amount"], lm["amount"])
                        if out["month"]["amount"] is not None and lm["amount"] is not None else None)
    out["year"].update(last_year_same_period=ly["amount"], last_year_count=ly["count"],
                        yoy=rate(out["year"]["amount"] - ly["amount"], ly["amount"])
                        if out["year"]["amount"] is not None and ly["amount"] is not None else None)
    out["items"] = [{"id": r["id"], "contract_no": f"{r['sheet_name']} · 第 {r['row']} 行", "name": r["name"],
                     "customer_name": r.get("customer"), "amount": r.get("amount"), "sign_date": r["date_value"],
                     "source_type": 4, "is_today": r["date_value"] == str(end),
                     "drill": {"path": "/contracts", "query": {"sheet": r["sheet_id"], "row": str(r["row"]), "year": r["date_value"][:4]}}}
                    for r in out["month"]["rows"]]
    for key in ("today", "week", "month", "year"):
        out[key].pop("rows", None)
    out["note"] = "经营业绩按年归集：2026年取销售合同，以前年份取2026年前已签单个项目未回款；按原表签订日期统计"
    return out


def targets(db, period):
    # Demo targets cannot silently become reviewed goals after source cutover.
    if not dict_service.get(db, "business_workbook_target", period):
        return None
    from app.services.board_service import target_of
    return sum(target_of(db, period).values(), Decimal(0))


def payment(db, wb, end):
    from app.services import collection_plans

    period = month_of(end)
    next_ref = date(end.year + (end.month == 12), end.month % 12 + 1, 1)
    next_period = f"{next_ref:%Y-%m}"
    data, target = metrics.collection_metrics(wb, period, end), targets(db, period)
    plan_cur = collection_plans.safe_plan(wb, period)
    plan_next = collection_plans.safe_plan(wb, next_period)
    ms, me = month_range(period)
    dates = [ms + timedelta(days=n) for n in range(me.day)]
    dated = data["available"] and all(r["date_value"] for r in data["rows"])
    daily = [metrics.amount([r for r in data["rows"] if r["date_value"] <= str(d)]) if dated and d <= end else None for d in dates]
    day_rows = [r for r in data["rows"] if r["date_value"] == str(end)]
    drill = {"path": "/collections", "query": {"basis": "monthly", "period": period, "end": str(end)}}
    return {"period": period, "amount": data["amount"], "target": target, "rate": rate(data["amount"], target),
            "gap": target - data["amount"] if target is not None and data["amount"] is not None else None,
            "time_progress": end.day / me.day, "available": data["available"], "note": data["note"],
            "planned_amount": plan_cur["amount"], "planned_count": plan_cur["count"],
            "next_period": next_period, "next_planned_amount": plan_next["amount"], "next_planned_count": plan_next["count"],
            "target_note": None if target is not None else "回款目标尚未重新配置",
            "by_line": [{"line": "source", "name": "原表明确月度回款", "amount": data["amount"], "target": target,
                         "rate": rate(data["amount"], target), "drill": drill}],
            "daily": {"days": [d.strftime("%m-%d") for d in dates], "actual": daily,
                      "target": [target * Decimal(i+1) / me.day if target is not None else None for i in range(me.day)]},
            "today": {"amount": metrics.amount(day_rows) if dated else None, "count": len(day_rows) if dated else None},
            "drill": drill}


def opportunities(wb, end):
    from app.services.opportunity_workbook import status_counts

    sheet = source.opportunity_sheet(wb)
    sid = sheet.id if sheet else ""
    statuses = status_counts(sheet.items) if sheet else []
    return {"month": metrics.opportunity_metrics(wb, month_range(ref=end)[0], end),
            "week": metrics.opportunity_metrics(wb, week_range(ref=end)[0], end),
            "funnel": [{"stage": s["value"], "name": s["value"], "count": s["count"], "amount": None,
                        "drill": {"path": "/opportunities", "query": {"sheet": sid, "status": s["value"]}}}
                       for s in statuses],
            "open_amount": None, "drill": {"path": "/opportunities", "query": {"sheet": sid}},
            "note": "商机信息按原表发起时间统计新增，按原表工单状态分组；原表未提供商机金额"}


def tender(wb, end):
    sheets = source.module_sheets(wb, "tender")
    tenders = [r for s in sheets if s.profile == "tender" for r in s.items]
    pending = [r for s in sheets if s.profile == "pending" for r in s.items]
    sid = next((s.id for s in sheets if s.profile == "tender"), "")
    pid = next((s.id for s in sheets if s.profile == "pending"), "")
    deadlines = [{"project_code": r["id"], "name": r["name"], "bid_deadline": r["date_value"],
                  "days_left": (date.fromisoformat(r["date_value"]) - end).days,
                  "urgent": (date.fromisoformat(r["date_value"]) - end).days <= 7,
                  "drill": {"path": "/tender-projects", "query": {"sheet": sid, "row": str(r["row"])}}}
                 for r in tenders if r["date_value"] and r["date_value"] >= str(end)]
    return {"bidding_count": len({(r["sheet_id"], r["group_row"]) for r in tenders}),
            "bidding_amount": metrics.amount(tenders), "building_count": len(pending),
            "building_contract_amount": metrics.amount(pending), "building_label": "已建设 / 中标待签",
            "alert_count": None, "overdue_count": None, "due_soon_count": None, "alerts": [],
            "deadline_soon_count": sum(d["days_left"] <= 14 for d in deadlines), "deadlines": deadlines,
            "note": "招标项目按合并项目去重，标段金额合计；已建设或中标待签独立统计，原表无施工节点预警",
            "drill": {"bidding": {"path": "/tender-projects", "query": {"sheet": sid}},
                      "building": {"path": "/tender-projects", "query": {"sheet": pid}}, "alert": None}}


def overview(db, wb, end):
    from app.services import board_service as board
    return {"date": end, "_cached_at": wb.synced_at, "source": source.source_info(wb, None),
            "payment": payment(db, wb, end), "sign": signing(wb, end), "opportunity": opportunities(wb, end),
            "tender": tender(wb, end), "key_projects": board._key_projects(db, end),
            "submission": board._submission_block(db, end), "profit": board._profit_block(db, end)}


def monthly(wb, year):
    if not 1990 <= year <= 2100:
        raise bad_request("年份不在支持范围内")
    rows = []
    for month in range(1, 13):
        period = f"{year}-{month:02d}"
        sign, pay = metrics.contract_metrics(wb, *month_range(period)), metrics.collection_metrics(wb, period)
        rows.append({"month": period, "sign_amount": sign["amount"], "sign_count": sign["count"],
                     "pay_amount": pay["amount"], "pay_count": pay["count"]})
    return {"year": year, "months": rows, "source_revision": wb.revision,
            "note": "签约按经营业绩对应年度来源及原表日期统计；回款仅展示原表有明确月度进度并核对一致的月份",
            "totals": {"sign_amount": sum((r["sign_amount"] or Decimal(0) for r in rows), Decimal(0)),
                       "sign_count": sum(r["sign_count"] for r in rows),
                       "pay_amount": sum((r["pay_amount"] for r in rows if r["pay_amount"] is not None), Decimal(0))
                       if any(r["pay_amount"] is not None for r in rows) else None,
                       "pay_count": sum(r["pay_count"] or 0 for r in rows),
                       "pay_months": sum(r["pay_amount"] is not None for r in rows)}}


def metric(db, wb, name, period, end):
    from app.services.board_service import _period_bounds
    start, finish, partial = _period_bounds(period, end)
    result = {"period": period, "start": start, "end": finish, "partial": partial}
    if name == "sign":
        item = metrics.contract_metrics(wb, start, finish)
        return {**result, "value": item["amount"], "amount": item["amount"], "count": item["count"]}
    if name == "opportunity":
        item = metrics.opportunity_metrics(wb, start, finish)
        return {**result, "value": item["count"], **item}
    if name in {"payment", "payment_rate"}:
        if "-W" in period:
            item = {"amount": None, "target": None, "rate": None}
        elif len(period) == 7 and "-Q" not in period:
            item = payment(db, wb, finish)
        else:
            months = []
            cur = date(start.year, start.month, 1)
            while cur <= finish:
                months.append(f"{cur:%Y-%m}")
                cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
            pays = [metrics.collection_metrics(wb, m, finish if m == month_of(finish) else None) for m in months]
            tgts = [targets(db, m) for m in months]
            amt = sum((p["amount"] for p in pays if p["amount"] is not None), Decimal(0)) if any(p["amount"] is not None for p in pays) else None
            tgt = sum((t for t in tgts if t is not None), Decimal(0)) if any(t is not None for t in tgts) else None
            item = {"amount": amt, "target": tgt, "rate": rate(amt, tgt)}
        return {**result, "value": item["amount"] if name == "payment" else item["rate"],
                "amount": item["amount"], "target": item["target"], "rate": item["rate"]}
    raise bad_request("不支持的经营指标")
