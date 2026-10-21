"""总经办经营决策看板（FR-03）：六板块 + 毛利摘要 + 下钻目标、对比查询、回款目标配置、晨报快照。

口径（PRD 3.4）：
- 当月回款 = 回款登记日期（=银行流水到账日）落在本月的 biz_payment 合计（不含已删除合同）
- 回款目标 = sys_dict(payment_target, YYYY-MM) 按业务线配置之和；达成率 = 当月回款 ÷ 目标
- 签订额 = sign_date 落在期间的合同金额合计（不含已删除、已终止）；年度累计业绩 = 当年签订额
- 商机新增 = created_at 落在期间；漏斗按 opportunity_stage 字典
- 延期 ▲：节点 finish_date > plan_date（延期完成）或计划日已过仍未完成
- 提交率 = 已提交人数 ÷ 应提交人数（sys_dict report_rule），数据源 oa_submission

缓存：进程内 TTL 缓存 300s（PRD ≤ 5 分钟）；refresh=1 或写入接口调用 invalidate() 立即失效。
"""
from __future__ import annotations

import threading
import time as _time
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import TZ, month_of, month_range, now, prev_month, today, week_no, week_range
from app.models import (PROJECT_STATUS, BizContract, BizOpportunity, BizPayment, BizProject, BizProjectFeedback,
                        BizProjectMilestone, SysUser)
from app.services import dict_service

ZERO = Decimal("0")
LINES = {"XY": "数字校园", "AF": "综合智慧安防", "KP": "素质教育考评", "YW": "运维服务"}
CACHE_TTL = 300
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


# ------------------------------------------------------------------ 缓存
def invalidate() -> None:
    with _lock:
        _cache.clear()


def _cached(key: str, fn, refresh: bool = False) -> dict:
    with _lock:
        hit = _cache.get(key)
        if hit and not refresh and _time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    val = fn()
    val["_cached_at"] = now().isoformat()
    with _lock:
        _cache[key] = (_time.time(), val)
    return val


def _q4(v) -> float | None:
    """比率统一输出 4 位小数 float（统一响应包会把 Decimal 截成 2 位，比率需保留精度）。"""
    return None if v is None else float(Decimal(str(v)).quantize(Decimal("0.0001")))


def _rate(a, b) -> float | None:
    return _q4(Decimal(str(a)) / Decimal(str(b))) if b else None


def _line_names(db: Session) -> dict[str, str]:
    d = dict_service.items(db, "business_line")
    return {k: (v if isinstance(v, str) else v.get("name", k)) for k, v in d.items()} or LINES


# ------------------------------------------------------------------ 基础聚合（看板/对比/晨报共用）
def payment_sum(db: Session, start: date, end: date, by_line: bool = False):
    q = select(func.coalesce(func.sum(BizPayment.amount), 0)).join(BizPayment.contract) \
        .where(BizContract.is_deleted.is_(False), BizPayment.payment_date >= start, BizPayment.payment_date <= end)
    if not by_line:
        return Decimal(db.scalar(q))
    q2 = select(BizContract.business_line, func.coalesce(func.sum(BizPayment.amount), 0)).join(BizPayment.contract) \
        .where(BizContract.is_deleted.is_(False), BizPayment.payment_date >= start, BizPayment.payment_date <= end) \
        .group_by(BizContract.business_line)
    return {k: Decimal(v) for k, v in db.execute(q2).all()}


def target_of(db: Session, period: str) -> dict[str, Decimal]:
    raw = dict_service.get(db, "payment_target", period) or {}
    return {k: Decimal(str(v)) for k, v in raw.items()}


def _sign_cond(start: date, end: date):
    return (BizContract.is_deleted.is_(False), BizContract.status != 3, BizContract.sign_date >= start,
            BizContract.sign_date <= end)


def sign_sum(db: Session, start: date, end: date) -> tuple[Decimal, int]:
    amt, n = db.execute(select(func.coalesce(func.sum(BizContract.amount), 0), func.count(BizContract.id))
                        .where(*_sign_cond(start, end))).one()
    return Decimal(amt), int(n)


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ)


def opp_sum(db: Session, start: date, end: date) -> tuple[int, Decimal]:
    n, amt = db.execute(select(func.count(BizOpportunity.id), func.coalesce(func.sum(BizOpportunity.amount), 0))
                        .where(BizOpportunity.is_deleted.is_(False), BizOpportunity.created_at >= _day_start(start),
                               BizOpportunity.created_at < _day_start(end + timedelta(days=1)))).one()
    return int(n), Decimal(amt)


def milestone_delayed(m: BizProjectMilestone, T: date) -> bool:
    """延期 ▲：finish_date > plan_date（延期完成），或计划日已过仍未完成。"""
    if m.plan_date is None:
        return m.status == 4
    if m.finish_date is not None:
        return m.finish_date > m.plan_date
    return m.status == 4 or m.plan_date < T


# ------------------------------------------------------------------ 六板块
def _payment_block(db: Session, T: date) -> dict:
    period = month_of(T)
    ms, me = month_range(ref=T)
    by_line = payment_sum(db, ms, T, by_line=True)
    target = target_of(db, period)
    total = sum(by_line.values(), ZERO)
    tgt_total = sum(target.values(), ZERO)
    names = _line_names(db)
    lines = []
    for k in sorted(set(target) | set(by_line) | set(LINES), key=lambda x: list(LINES).index(x) if x in LINES else 99):
        a, t = by_line.get(k, ZERO), target.get(k, ZERO)
        lines.append({"line": k, "name": names.get(k, k), "amount": a, "target": t, "rate": _rate(a, t),
                      "drill": {"path": "/contracts", "query": {"paid_month": period, "business_line": k}}})
    # 每日累计 vs 目标线（线性）
    daily = dict(db.execute(select(BizPayment.payment_date, func.sum(BizPayment.amount)).join(BizPayment.contract)
                            .where(BizContract.is_deleted.is_(False), BizPayment.payment_date >= ms,
                                   BizPayment.payment_date <= T).group_by(BizPayment.payment_date)).all())
    days, actual, tline, acc = [], [], [], ZERO
    for i in range(me.day):
        d = ms + timedelta(days=i)
        days.append(f"{d:%m-%d}")
        if d <= T:
            acc += Decimal(daily.get(d, 0))
            actual.append(acc)
        else:
            actual.append(None)
        tline.append((tgt_total * (i + 1) / me.day).quantize(Decimal("1")))
    today_amt, today_n = db.execute(select(func.coalesce(func.sum(BizPayment.amount), 0), func.count(BizPayment.id))
                                    .join(BizPayment.contract)
                                    .where(BizContract.is_deleted.is_(False), BizPayment.payment_date == T)).one()
    time_progress = _q4(Decimal(T.day) / Decimal(me.day))
    return {"period": period, "amount": total, "target": tgt_total, "rate": _rate(total, tgt_total),
            "time_progress": time_progress, "gap": tgt_total - total, "by_line": lines,
            "daily": {"days": days, "actual": actual, "target": tline},
            "today": {"amount": Decimal(today_amt), "count": int(today_n)},
            "drill": {"path": "/contracts", "query": {"paid_month": period}}}


def _key_projects(db: Session, T: date) -> dict:
    projects = db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False), BizProject.is_key.is_(True))
                          .order_by(BizProject.project_code)).unique().all()
    ids = [p.id for p in projects]
    latest: dict[int, BizProjectFeedback] = {}
    recent: dict[int, list] = defaultdict(list)
    if ids:
        for f in db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id.in_(ids),
                                                             BizProjectFeedback.feedback_date >= T - timedelta(days=31))
                            .order_by(BizProjectFeedback.feedback_date, BizProjectFeedback.id)):
            latest[f.project_id] = f
            recent[f.project_id].append(f)
    rows = []
    for p in projects:
        ms = sorted(p.milestones, key=lambda m: m.seq)
        cur = next((m for m in ms if m.status != 3), None)
        delayed_nodes = [m.node_name for m in ms if milestone_delayed(m, T)]
        f = latest.get(p.id)
        rows.append({
            "project_code": p.project_code, "name": p.name, "business_line": p.business_line,
            "customer_name": p.customer_name, "status": p.status, "status_text": PROJECT_STATUS.get(p.status),
            "owner_name": p.owner.name if p.owner else None,
            "current_node": cur.node_name if cur else ("已全部完成" if ms else "—"),
            "current_plan_date": cur.plan_date if cur else None,
            "progress": round(sum(1 for m in ms if m.status == 3) / len(ms), 4) if ms else 0,
            "delayed": bool(delayed_nodes), "delayed_nodes": delayed_nodes,
            "current_delayed": bool(cur and milestone_delayed(cur, T)),
            "latest_feedback": {"date": f.feedback_date, "kind": f.kind, "content": f.content,
                                "risk_level": f.risk_level, "reporter": f.reporter.name if f.reporter else None}
            if f else None,
            "risk_count": sum(1 for x in recent.get(p.id, []) if x.risk_level >= 2),
            "feedback_count_week": sum(1 for x in recent.get(p.id, []) if x.feedback_date >= week_range(ref=T)[0]),
            "drill": {"path": f"/projects/{p.project_code}"},
        })
    rows.sort(key=lambda r: (not r["delayed"], r["project_code"]))
    return {"count": len(rows), "delayed_count": sum(1 for r in rows if r["delayed"]), "items": rows,
            "drill": {"path": "/projects", "query": {"is_key": "1"}}}


def _sign_block(db: Session, T: date) -> dict:
    ms, _ = month_range(ref=T)
    ws, _ = week_range(ref=T)
    ys = date(T.year, 1, 1)
    month_amt, month_n = sign_sum(db, ms, T)
    today_amt, today_n = sign_sum(db, T, T)
    week_amt, week_n = sign_sum(db, ws, T)
    year_amt, year_n = sign_sum(db, ys, T)
    try:
        ly_T = T.replace(year=T.year - 1)
    except ValueError:  # 2 月 29 日
        ly_T = T.replace(year=T.year - 1, day=28)
    ly_amt, ly_n = sign_sum(db, date(T.year - 1, 1, 1), ly_T)
    lm_s, lm_e = month_range(prev_month(month_of(T)))
    lm_amt, _ = sign_sum(db, lm_s, lm_e)
    owners = {u.id: u.name for u in db.scalars(select(SysUser))}
    items = [{"id": c.id, "contract_no": c.contract_no, "name": c.name, "customer_name": c.customer_name,
              "amount": c.amount, "sign_date": c.sign_date, "business_line": c.business_line,
              "source_type": c.source_type, "owner_name": owners.get(c.owner_id), "is_today": c.sign_date == T,
              "drill": {"path": f"/contracts/{c.id}"}}
             for c in db.scalars(select(BizContract).where(*_sign_cond(ms, T))
                                 .order_by(BizContract.sign_date.desc(), BizContract.id.desc()))]
    period = month_of(T)
    return {"month": {"amount": month_amt, "count": month_n, "prev_month_amount": lm_amt,
                      "mom": _rate(month_amt - lm_amt, lm_amt),
                      "drill": {"path": "/contracts", "query": {"sign_month": period}}},
            "today": {"amount": today_amt, "count": today_n,
                      "drill": {"path": "/contracts", "query": {"sign_from": T.isoformat(), "sign_to": T.isoformat()}}},
            "week": {"amount": week_amt, "count": week_n,
                     "drill": {"path": "/contracts", "query": {"sign_from": ws.isoformat(), "sign_to": T.isoformat()}}},
            "year": {"amount": year_amt, "count": year_n, "last_year_same_period": ly_amt, "last_year_count": ly_n,
                     "yoy": _rate(year_amt - ly_amt, ly_amt),
                     "drill": {"path": "/contracts", "query": {"sign_from": ys.isoformat(), "sign_to": T.isoformat()}}},
            "items": items}


def _opp_block(db: Session, T: date) -> dict:
    def opp_stages(db):
        return dict_service.get(db, "opportunity_stage", "stages") or [
            {"key": k, "name": v} for k, v in {"lead": "线索", "contact": "初步接触", "quote": "方案报价",
                                               "negotiation": "商务谈判", "won": "赢单", "lost": "丢单"}.items()]
    ws, _ = week_range(ref=T)
    ms, _ = month_range(ref=T)
    wn, wa = opp_sum(db, ws, T)
    mn, ma = opp_sum(db, ms, T)
    counts = dict(db.execute(select(BizOpportunity.stage, func.count()).where(BizOpportunity.is_deleted.is_(False))
                             .group_by(BizOpportunity.stage)).all())
    amounts = dict(db.execute(select(BizOpportunity.stage, func.sum(BizOpportunity.amount))
                              .where(BizOpportunity.is_deleted.is_(False)).group_by(BizOpportunity.stage)).all())
    funnel = [{"stage": s["key"], "name": s["name"], "count": int(counts.get(s["key"], 0)),
               "amount": Decimal(amounts.get(s["key"]) or 0),
               "drill": {"path": "/opportunities", "query": {"stage": s["key"]}}} for s in opp_stages(db)]
    open_amt = sum((f["amount"] for f in funnel if f["stage"] not in ("won", "lost")), ZERO)
    return {"week": {"count": wn, "amount": wa,
                     "drill": {"path": "/opportunities", "query": {"created_from": ws.isoformat()}}},
            "month": {"count": mn, "amount": ma,
                      "drill": {"path": "/opportunities", "query": {"created_from": ms.isoformat()}}},
            "funnel": funnel, "open_amount": open_amt,
            "drill": {"path": "/opportunities"}}


def _tender_block(db: Session, T: date) -> dict:
    projects = db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False))).unique().all()
    bidding = [p for p in projects if p.status == 1]
    building = [p for p in projects if p.status == 2]
    alerts = []
    for p in projects:
        if p.status not in (2, 3, 4):
            continue
        for m in p.milestones:
            if m.finish_date is not None or m.plan_date is None:
                continue
            if m.status == 4 or m.plan_date < T:
                alerts.append({"type": "overdue", "project_code": p.project_code, "project_name": p.name,
                               "node_name": m.node_name, "plan_date": m.plan_date, "days": (m.plan_date - T).days})
            elif (m.plan_date - T).days <= 7:
                alerts.append({"type": "due_soon", "project_code": p.project_code, "project_name": p.name,
                               "node_name": m.node_name, "plan_date": m.plan_date, "days": (m.plan_date - T).days})
    deadlines = sorted([{"project_code": p.project_code, "name": p.name, "customer_name": p.customer_name,
                         "budget_amount": p.budget_amount, "bid_deadline": p.bid_deadline,
                         "days_left": (p.bid_deadline - T).days, "urgent": (p.bid_deadline - T).days <= 7,
                         "drill": {"path": f"/projects/{p.project_code}"}}
                        for p in bidding if p.bid_deadline and p.bid_deadline >= T], key=lambda x: x["bid_deadline"])
    alerts.sort(key=lambda a: a["days"])
    return {"bidding_count": len(bidding), "bidding_amount": sum((p.budget_amount or ZERO for p in bidding), ZERO),
            "building_count": len(building),
            "building_contract_amount": sum((p.contract_amount or ZERO for p in building), ZERO),
            "alert_count": len(alerts), "overdue_count": sum(1 for a in alerts if a["type"] == "overdue"),
            "due_soon_count": sum(1 for a in alerts if a["type"] == "due_soon"), "alerts": alerts,
            "deadline_soon_count": sum(1 for d in deadlines if d["days_left"] <= 14), "deadlines": deadlines,
            "drill": {"bidding": {"path": "/projects", "query": {"status": "1"}},
                      "building": {"path": "/projects", "query": {"status": "2"}},
                      "alert": {"path": "/projects", "query": {"delayed": "1"}}}}


def _submission_block(db: Session, T: date) -> dict:
    from app.services import submission_service as ss
    period = ss.current_period("weekly", T)
    st = ss.stats(db, "weekly", period)
    mperiod = ss.current_period("monthly", T)
    mst = ss.stats(db, "monthly", mperiod)
    return {"weekly": {k: st[k] for k in ("period", "required", "submitted", "rate", "by_dept", "missing")},
            "monthly": {k: mst[k] for k in ("period", "required", "submitted", "rate")},
            "drill": {"path": "/submissions", "query": {"type": "weekly", "period": period}}}


def _profit_block(db: Session, T: date) -> dict:
    from app.services.profit_service import project_profit
    ws, _ = week_range(ref=T)
    r = project_profit(db, ws, T)
    t = r["totals"]
    top = sorted(r["rows"], key=lambda x: x["gross_profit"], reverse=True)[:5]
    return {"week_start": ws, "week_end": T, "week_income": t["period_income"], "week_cost": t["period_cost"],
            "week_gross_profit": t["period_income"] - t["period_cost"],
            "income": t["income"], "cost": t["cost"], "gross_profit": t["gross_profit"],
            "gross_margin": _q4(t["gross_margin"]), "public_expense": r["public_expense"],
            "top": [{"project_code": x["project_code"], "name": x["name"], "gross_profit": x["gross_profit"],
                     "gross_margin": _q4(x["gross_margin"])} for x in top],
            "drill": {"path": "/profit"}}


def overview(db: Session, ref: date | None = None, refresh: bool = False) -> dict:
    T = ref or today()

    def build():
        return {"date": T, "payment": _payment_block(db, T), "key_projects": _key_projects(db, T),
                "sign": _sign_block(db, T), "opportunity": _opp_block(db, T), "tender": _tender_block(db, T),
                "submission": _submission_block(db, T), "profit": _profit_block(db, T)}
    return _cached(f"overview:{T}", build, refresh)


# ------------------------------------------------------------------ FR-03-04 对比查询
METRICS = {"payment_rate": "回款完成率", "payment": "回款额", "sign": "签约额", "opportunity": "新增商机"}


def _period_bounds(period: str, T: date) -> tuple[date, date, bool]:
    """支持 YYYY-MM（月）与 YYYY-Www（周）。当前期截至今天。"""
    if "-W" in period:
        s, e = week_range(period)
    else:
        s, e = month_range(period)
    partial = s <= T <= e
    return s, min(e, T), partial


def metric_value(db: Session, metric: str, period: str, T: date | None = None) -> dict:
    T = T or today()
    s, e, partial = _period_bounds(period, T)
    out = {"period": period, "start": s, "end": e, "partial": partial}
    if metric in ("payment_rate", "payment"):
        amt = payment_sum(db, s, e)
        tgt = sum(target_of(db, period).values(), ZERO) if "-W" not in period else None
        out.update(amount=amt, target=tgt, rate=_rate(amt, tgt) if tgt else None)
        out["value"] = out["rate"] if metric == "payment_rate" else amt
    elif metric == "sign":
        amt, n = sign_sum(db, s, e)
        out.update(amount=amt, count=n, value=amt)
    elif metric == "opportunity":
        n, amt = opp_sum(db, s, e)
        out.update(amount=amt, count=n, value=n)
    else:
        from app.core.response import bad_request
        raise bad_request(f"不支持的指标 {metric}，可选：{', '.join(METRICS)}")
    return out


def _fmt(metric: str, v) -> str:
    if v is None:
        return "—"
    if metric == "payment_rate":
        return f"{Decimal(v) * 100:.1f}%"
    if metric == "opportunity":
        return f"{int(v)} 个"
    return f"{Decimal(v) / 10000:,.1f} 万元"


def compare(db: Session, metric: str, a: str, b: str) -> dict:
    T = today()
    va, vb = metric_value(db, metric, a, T), metric_value(db, metric, b, T)
    x, y = va["value"], vb["value"]
    diff = None if x is None or y is None else Decimal(str(x)) - Decimal(str(y))
    change = _rate(diff, y) if diff is not None and y else None
    name = METRICS[metric]
    if diff is None:
        conclusion = f"{a} 与 {b} 的{name}数据不完整（缺少回款目标配置），无法对比。"
    else:
        up = diff > 0
        if metric == "payment_rate":
            delta_txt = f"{'提升' if up else '下降'} {abs(diff) * 100:.1f} 个百分点" if diff else "持平"
        else:
            delta_txt = (f"{'增加' if up else '减少'} {_fmt(metric, abs(diff))}" +
                         (f"（{'+' if up else '-'}{abs(change) * 100:.1f}%）" if change is not None else "")) if diff else "持平"
        conclusion = f"{a} {name} {_fmt(metric, x)}，{b} 为 {_fmt(metric, y)}，{delta_txt}。"
        if va["partial"] and metric in ("payment_rate", "payment") and va.get("target"):
            ms, me = month_range(a) if "-W" not in a else week_range(a)
            tp = Decimal((T - ms).days + 1) / Decimal((me - ms).days + 1)
            rate = Decimal(str(va["rate"] or 0))
            gap = va["target"] - va["amount"]
            judge = "进度超前" if rate >= tp else "进度落后于时间进度"
            conclusion += (f"{a} 尚未结束（时间进度 {tp * 100:.0f}%），当前达成 {rate * 100:.1f}%，{judge}；"
                           f"距目标还差 {gap / 10000:,.1f} 万元。")
        elif va["partial"]:
            conclusion += f"注意 {a} 为截至 {T} 的期间累计，与完整期对比偏低属正常。"
        if metric in ("payment_rate", "payment") and diff < 0 and not va["partial"]:
            conclusion += "建议关注逾期回款计划与重点项目验收节点。"
    if diff is not None and metric == "payment_rate":
        diff = _q4(diff)
    elif diff is not None and metric == "opportunity":
        diff = int(diff)
    return {"metric": metric, "metric_name": name, "a": va, "b": vb, "diff": diff, "change_rate": change,
            "conclusion": conclusion}


# ------------------------------------------------------------------ FR-03-05 回款目标配置
def get_targets(db: Session, period: str) -> dict:
    t = target_of(db, period)
    names = _line_names(db)
    return {"period": period, "lines": [{"line": k, "name": v, "target": t.get(k, ZERO)} for k, v in names.items()],
            "total": sum(t.values(), ZERO), "configured": bool(t)}


def set_targets(db: Session, period: str, values: dict[str, Decimal], user_id: int | None, ip: str | None = None) -> dict:
    from app.core.audit import log_op
    from app.core.response import bad_request
    month_range(period)  # 校验格式
    names = _line_names(db)
    clean = {}
    for k, v in values.items():
        if k not in names:
            raise bad_request(f"未知业务线 {k}")
        v = Decimal(str(v))
        if v < 0:
            raise bad_request("目标金额不能为负")
        clean[k] = int(v) if v == v.to_integral_value() else float(v)
    before = dict_service.get(db, "payment_target", period)
    merged = {**(before or {}), **clean}
    dict_service.put(db, "payment_target", period, merged, f"{period} 回款目标（元），按业务线")
    log_op(db, user_id, "board.target", "payment_target", period, {"value": before}, {"value": merged}, ip)
    db.commit()
    invalidate()
    return get_targets(db, period)


# ------------------------------------------------------------------ FR-03-03 晨报
def _wan(v) -> str:
    return f"{Decimal(v or 0) / 10000:,.1f}万"


def _pct(v) -> str:
    return "—" if v is None else f"{Decimal(v) * 100:.1f}%"


def morning_summary(db: Session, d: date) -> dict:
    ov = overview(db, d, refresh=True)
    pay, sign, opp, tender, sub, kp = (ov["payment"], ov["sign"], ov["opportunity"], ov["tender"], ov["submission"],
                                       ov["key_projects"])
    return {
        "date": d, "payment_amount": pay["amount"], "payment_target": pay["target"], "payment_rate": pay["rate"],
        "time_progress": pay["time_progress"], "payment_today": pay["today"]["amount"],
        "sign_today_amount": sign["today"]["amount"], "sign_today_count": sign["today"]["count"],
        "sign_month_amount": sign["month"]["amount"], "sign_year_amount": sign["year"]["amount"],
        "sign_yoy": sign["year"]["yoy"], "opp_week_count": opp["week"]["count"], "opp_week_amount": opp["week"]["amount"],
        "opp_month_count": opp["month"]["count"], "bidding_count": tender["bidding_count"],
        "building_count": tender["building_count"], "alert_count": tender["alert_count"],
        "deadline_soon_count": tender["deadline_soon_count"], "key_project_count": kp["count"],
        "key_delayed_count": kp["delayed_count"],
        "key_projects": [{"name": r["name"], "current_node": r["current_node"], "delayed": r["delayed"],
                          "feedback": (r["latest_feedback"] or {}).get("content")} for r in kp["items"]],
        "weekly_submit_rate": sub["weekly"]["rate"], "weekly_missing": len(sub["weekly"]["missing"]),
        "by_line": [{"name": x["name"], "amount": x["amount"], "rate": x["rate"]} for x in pay["by_line"]],
        "gross_margin": ov["profit"]["gross_margin"],
    }


def morning_text(s: dict) -> str:
    d: date = s["date"]
    wk = "一二三四五六日"[d.weekday()]
    lines = [f"## 聚成数智 · 经营晨报（{d:%Y-%m-%d} 周{wk}）",
             f"> **回款**：本月 {_wan(s['payment_amount'])} / 目标 {_wan(s['payment_target'])}，"
             f"达成 <font color=\"warning\">{_pct(s['payment_rate'])}</font>（时间进度 {_pct(s['time_progress'])}）",
             f"> **签约**：今日 {s['sign_today_count']} 份 {_wan(s['sign_today_amount'])}；本月 {_wan(s['sign_month_amount'])}；"
             f"年度累计 {_wan(s['sign_year_amount'])}（同比 {_pct(s['sign_yoy'])}）",
             f"> **商机**：本周新增 {s['opp_week_count']} 个 {_wan(s['opp_week_amount'])}；本月新增 {s['opp_month_count']} 个",
             f"> **招标/在建**：招标中 {s['bidding_count']} 个（{s['deadline_soon_count']} 个两周内截止），"
             f"在建 {s['building_count']} 个，节点预警 <font color=\"warning\">{s['alert_count']}</font> 个",
             f"> **重点项目**：{s['key_project_count']} 个，延期 <font color=\"warning\">{s['key_delayed_count']}</font> 个",
             ]
    for kp in s["key_projects"][:5]:
        mark = "▲" if kp["delayed"] else "●"
        fb = f"｜{kp['feedback'][:24]}" if kp.get("feedback") else ""
        lines.append(f"> {mark} {kp['name']}：{kp['current_node']}{fb}")
    lines.append(f"> **周报提交率**：{_pct(s['weekly_submit_rate'])}，未提交 {s['weekly_missing']} 人")
    return "\n".join(lines)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    cands = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
             "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
             "/System/Library/Fonts/STHeiti Light.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"]
    for path in cands:
        try:
            return ImageFont.truetype(path, size, index=1 if bold and path.endswith("Hiragino Sans GB.ttc") else 0)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def morning_image(s: dict) -> bytes:
    """Pillow 绘制看板摘要卡片：暖白底、藏青标题条、5 个指标块、回款进度条、重点项目列表。"""
    import io

    from PIL import Image, ImageDraw
    W, H = 1200, 860
    PAPER, INK, INK3, LINE, BRAND = (246, 244, 239), (29, 31, 36), (123, 126, 134), (230, 226, 217), (27, 50, 117)
    DOWN, UP = (166, 61, 50), (47, 107, 79)
    img = Image.new("RGB", (W, H), PAPER)
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, 0, W, 104], fill=BRAND)
    d: date = s["date"]
    dr.text((48, 26), "聚成数智 · 经营晨报", font=_font(36, True), fill=(255, 255, 255))
    dr.text((W - 48, 40), f"{d:%Y-%m-%d} 周{'一二三四五六日'[d.weekday()]}", font=_font(22), fill=(221, 227, 240),
            anchor="ra")
    tiles = [("当月回款", _wan(s["payment_amount"]), f"目标 {_wan(s['payment_target'])} · 达成 {_pct(s['payment_rate'])}"),
             ("今日签约", _wan(s["sign_today_amount"]), f"{s['sign_today_count']} 份 · 本月 {_wan(s['sign_month_amount'])}"),
             ("年度累计业绩", _wan(s["sign_year_amount"]), f"同比 {_pct(s['sign_yoy'])}"),
             ("本周新增商机", f"{s['opp_week_count']} 个", f"金额 {_wan(s['opp_week_amount'])}"),
             ("招标 / 在建", f"{s['bidding_count']} / {s['building_count']}", f"节点预警 {s['alert_count']} 个")]
    tw, th, gap, x0, y0 = 208, 150, 15, 48, 136
    for i, (lbl, val, hint) in enumerate(tiles):
        x = x0 + i * (tw + gap)
        dr.rounded_rectangle([x, y0, x + tw, y0 + th], radius=10, fill=(255, 255, 255), outline=LINE, width=2)
        dr.text((x + 18, y0 + 18), lbl, font=_font(18), fill=INK3)
        dr.text((x + 18, y0 + 50), val, font=_font(34, True), fill=INK)
        dr.text((x + 18, y0 + 108), hint, font=_font(14), fill=INK3)
    # 回款进度条
    y = y0 + th + 40
    dr.text((48, y), "回款目标达成", font=_font(20, True), fill=INK)
    rate = float(s["payment_rate"] or 0)
    tp = float(s["time_progress"] or 0)
    bx0, bx1, by = 48, W - 48, y + 40
    dr.rounded_rectangle([bx0, by, bx1, by + 18], radius=9, fill=(239, 236, 229))
    dr.rounded_rectangle([bx0, by, bx0 + int((bx1 - bx0) * min(rate, 1)), by + 18], radius=9,
                         fill=BRAND if rate >= tp else (168, 116, 26))
    tx = bx0 + int((bx1 - bx0) * min(tp, 1))
    dr.line([tx, by - 6, tx, by + 24], fill=INK3, width=2)
    dr.text((tx, by + 30), f"时间进度 {tp * 100:.0f}%", font=_font(14), fill=INK3, anchor="ma")
    dr.text((bx1, y), f"{_pct(s['payment_rate'])}", font=_font(20, True), fill=BRAND, anchor="ra")
    # 业务线
    y = by + 64
    lx = 48
    for ln in s["by_line"]:
        dr.text((lx, y), f"{ln['name']} {_wan(ln['amount'])}（{_pct(ln['rate'])}）", font=_font(15), fill=INK3)
        lx += 272
    # 重点项目
    y += 44
    dr.line([48, y, W - 48, y], fill=LINE, width=2)
    y += 18
    dr.text((48, y), f"重点项目进展（{s['key_project_count']} 个，延期 {s['key_delayed_count']} 个）",
            font=_font(20, True), fill=INK)
    y += 40
    for kp in s["key_projects"][:6]:
        color = DOWN if kp["delayed"] else UP
        dr.text((48, y), "▲" if kp["delayed"] else "●", font=_font(18), fill=color)
        dr.text((80, y), kp["name"][:20], font=_font(18), fill=INK)
        dr.text((470, y), kp["current_node"] or "—", font=_font(18), fill=color if kp["delayed"] else INK3)
        fb = (kp.get("feedback") or "")[:28]
        dr.text((640, y), fb, font=_font(16), fill=INK3)
        y += 36
    dr.line([48, H - 54, W - 48, H - 54], fill=LINE, width=1)
    dr.text((48, H - 40), f"周报提交率 {_pct(s['weekly_submit_rate'])} · 未提交 {s['weekly_missing']} 人",
            font=_font(15), fill=INK3)
    dr.text((W - 48, H - 40), f"数据截至 {now():%Y-%m-%d %H:%M} · 打开工作台查看实时看板", font=_font(15), fill=INK3,
            anchor="ra")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def build_morning_report(db: Session, d: date | None = None, uploader_id: int | None = None) -> dict:
    """返回 {text_markdown, summary, image_file(SysFile)}；图片按 report/morning/{yyyy-MM}/ 版本化存储。"""
    from app.core.storage import save_bytes
    d = d or today()
    s = morning_summary(db, d)
    text = morning_text(s)
    png = morning_image(s)
    f = save_bytes(db, data=png, filename=f"morning-{d}.png", category="report",
                   key=f"report/morning/{d:%Y-%m}/morning-{d}.png", uploader_id=uploader_id, content_type="image/png")
    db.commit()
    return {"text_markdown": text, "summary": s, "image_file": f}
