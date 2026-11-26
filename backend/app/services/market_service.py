"""市场部：重点项目跟进看板、市场周报/月报自动成稿（FR-04-03 + 7.3）、竞品简报、项目复盘（结项自动初稿）。"""
from __future__ import annotations

import io
import time as _time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.orm import Session

from app.core.response import bad_request, encode
from app.core.storage import save_bytes
from app.core.timeutil import last_week, month_range, prev_month, today, week_range
from app.core.permissions import BUSINESS_LINES
from app.models import (PROJECT_STATUS, BizContract, BizPayment, BizProject, BizProjectFeedback, BizProjectFollowSetting, BizProjectReview,
                        MktIntel, RptReport, SysUser)
from app.services import project_service as ps

ZERO = Decimal("0")
LINES = BUSINESS_LINES
KIND_TEXT = {"competitor": "竞品动态", "industry": "行业动态", "policy": "政策法规", "bid": "招标情报"}
IMPORTANCE = {1: "一般", 2: "重要", 3: "紧急"}


def _wan(v) -> str:
    return f"{Decimal(v or 0) / 10000:,.1f} 万"


def _pct(v) -> str:
    return "—" if v is None else f"{Decimal(v) * 100:.1f}%"


def _llm(system: str, user: str) -> str | None:
    try:
        from app.integration.llm import complete
    except ImportError:
        return None
    try:
        return complete(system, user)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ 跟进看板
def key_board(db: Session, p=None, include_all: bool = False, include_paused: bool = False, *, is_key=True) -> dict:
    from app.services import key_projects

    paused = set(db.scalars(select(BizProjectFollowSetting.project_id).where(BizProjectFollowSetting.paused.is_(True))))
    standard_scope = key_projects.standard_scope(p, overview=True, is_key=is_key)
    membership = key_projects.membership_scope(p, is_key=is_key)
    if include_all and not is_key:
        standard_scope = (BizProject.is_key.is_(False) & standard_scope) | (
            BizProject.is_key.is_(True) & key_projects.standard_scope(p, overview=True))
        membership = key_projects.membership_scope(p, is_key=None)
    q = select(BizProject, standard_scope.label("standard")).where(
        BizProject.is_deleted.is_(False), BizProject.status != 5, standard_scope | membership)
    if not include_paused:
        q = q.where(BizProject.id.not_in(paused))
    if not include_all:
        q = q.where(BizProject.is_key.is_(is_key))
    projects = db.execute(q.order_by(BizProject.project_code)).unique().all()
    T = today()
    money = ps.money_summary(db, [x.project_code for x, standard in projects if standard])
    ids = [x.id for x, _ in projects]
    latest: dict[int, BizProjectFeedback] = {}
    week_start, _ = week_range(ref=T)
    if ids:
        for f in db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id.in_(ids), BizProjectFeedback.feedback_date <= T)
                            .order_by(BizProjectFeedback.feedback_date, BizProjectFeedback.id)):
            latest[f.project_id] = f
    cards = []
    can_pause = bool(p and key_projects.can_pause(p))
    for x, standard in projects:
        b = key_projects.brief(db, x, standard, day=T, money=money.get(x.project_code))
        b["can_view_financials"] = standard
        b["can_delete"] = bool(standard and p and (not is_key or x.is_key) and key_projects.can_remove_project(p, x))
        b["can_pause"] = standard and can_pause and (not is_key or x.is_key)
        f = latest.get(x.id)
        b["latest_feedback"] = ps.feedback_dict(f) if f else None
        b["paused"] = x.id in paused
        b["reported_this_week"] = bool(f and week_start <= f.feedback_date <= T)
        b["alert"] = b["delayed_nodes"] > 0 or bool(f and f.risk_level >= 2)
        manageable = key_projects.can_manage(db, p, x, standard=standard) if p else False
        task_rows = [key_projects.task_data(db, p, x, m, manageable=manageable) for m in x.milestones] if p else []
        b["needs_feedback"] = sum(t["needs_feedback"] for t in task_rows)
        b["risk_tasks"] = sum(bool(t["problems"]) and t["status"] != 3 for t in task_rows)
        b["delayed_nodes"] += sum(n["overdue"] for t in task_rows if t["status"] != 3 for n in t["nodes"])
        b["alert"] = b["alert"] or any(t["alert"] and t["status"] != 3 for t in task_rows)
        cards.append(b)
    cards.sort(key=lambda c: (not c["alert"], c["project_code"]))
    tot_c = sum((c.get("contract_total", ZERO) for c in cards), ZERO)
    tot_r = sum((c.get("received", ZERO) for c in cards), ZERO)
    current = [x for x in cards if x["is_key"] == is_key and not x["paused"]]
    summary_text = "\n".join(
        f"{x['name']}：{x['market_owner_name'] or x['owner_name'] or '未分配'}；"
        f"{'本周已反馈' if x['reported_this_week'] else '本周未反馈'}；"
        f"{x['current_node'] or '尚无节点'} {x['current_node_plan'] or ''}；"
        f"{(x['latest_feedback'] or {}).get('content', '尚无进展记录')}"
        for x in current)
    return {"projects": cards, "week_start": week_start, "summary_text": summary_text, "summary": {
        "unreported": sum(not x["reported_this_week"] for x in current),
        "count": len(cards), "alert_count": sum(1 for c in cards if c["alert"]),
        "delayed_nodes": sum(c["delayed_nodes"] for c in cards), "contract_total": tot_c, "received": tot_r,
        "payment_rate": (tot_r / tot_c).quantize(Decimal("0.0001")) if tot_c else None}}


def key_events(db: Session, p=None, days: int = 60, kinds=("event", "risk"), project_id: int | None = None) -> list[dict]:
    q = select(BizProjectFeedback, BizProject).join(BizProject, BizProject.id == BizProjectFeedback.project_id) \
        .where(BizProjectFeedback.kind.in_(list(kinds)), BizProjectFeedback.feedback_date >= today() - timedelta(days=days),
               BizProject.is_deleted.is_(False))
    cond = ps.visible_filter(p)
    if cond is not None:
        q = q.where(cond)
    if project_id:
        q = q.where(BizProject.id == project_id)
    rows = db.execute(q.order_by(BizProjectFeedback.feedback_date.desc(), BizProjectFeedback.id.desc()).limit(200)).all()
    return [{**ps.feedback_dict(f), "project_code": x.project_code, "project_name": x.name, "is_key": x.is_key}
            for f, x in rows]


def add_feedback(db: Session, proj: BizProject, data: dict, reporter_id: int | None, *, item=None, notice_ids=None) -> BizProjectFeedback:
    content = (data.get("content") or "").strip()
    if not content:
        raise bad_request("反馈内容必填")
    if problems := (data.get("problems") or "").strip():
        content += f"\n问题：{problems}"
    if step := (data.get("next_step") or "").strip():
        content += f"\n下一步：{step}"
        if data.get("next_due_on"):
            content += f"（{data['next_due_on']}）"
    elif data.get("next_due_on"):
        raise bad_request("请填写跟进日期对应的下一步安排")
    kind = data.get("kind") or "progress"
    if kind not in ("progress", "event", "risk"):
        raise bad_request("反馈类型无效（progress/event/risk）")
    fd = data.get("feedback_date") or today()
    if isinstance(fd, str):
        fd = date.fromisoformat(fd)
    f = BizProjectFeedback(project_id=proj.id, feedback_date=fd, kind=kind, content=content,
                           risk_level=int(data.get("risk_level") or (2 if kind == "risk" else 0)), reporter_id=reporter_id)
    db.add(f)
    db.flush()
    from app.services.key_projects import project_label, queue_team_notice

    pending = queue_team_notice(db, proj, f"{project_label(proj)}有新进展", exclude=[reporter_id] if reporter_id else [], item=item)
    if notice_ids is not None:
        notice_ids.extend(pending)
    return f


# ------------------------------------------------------------------ 市场周报 / 月报
def period_bounds(period_type: str, period: str | None) -> tuple[str, date, date]:
    if period_type == "weekly":
        period = period or last_week()
        s, e = week_range(period)
    elif period_type == "monthly":
        period = period or prev_month(f"{today():%Y-%m}")
        s, e = month_range(period)
    else:
        raise bad_request("period_type 仅支持 weekly / monthly")
    return period, s, e


GENERAL_REPORT_TYPES = ("general_weekly", "general_monthly")
KEY_REPORT_TYPES = ("market_weekly", "market_monthly")
PROJECT_REPORT_TYPES = (*KEY_REPORT_TYPES, *GENERAL_REPORT_TYPES, "project_weekly")
TEAM_SCOPED_REPORT_TYPES = (*PROJECT_REPORT_TYPES, "weekly_profit", "project_operation", "company_weekly", "morning_report")


def _report_data(db: Session, period_type: str, period: str | None, user_scope=None, *, is_key=True) -> dict:
    period, s, e = period_bounds(period_type, period)
    T = today()
    q = ps.visible_query(user_scope).where(BizProject.is_key.is_(is_key))
    if not is_key:
        paused = select(BizProjectFollowSetting.project_id).where(BizProjectFollowSetting.paused.is_(True))
        q = q.where(BizProject.status != 5, BizProject.id.not_in(paused))
    projects = db.scalars(q.order_by(BizProject.project_code)).unique().all()
    money = ps.money_summary(db, [x.project_code for x in projects])
    nxt_s, nxt_e = (e + timedelta(days=1), e + timedelta(days=7)) if period_type == "weekly" else \
        month_range(ref=e + timedelta(days=1))
    items = []
    for x in projects:
        ms = sorted(x.milestones, key=lambda m: m.seq)
        done = [ps.ms_dict(m, T) for m in ms if m.finish_date and s <= m.finish_date <= e]
        doing = [ps.ms_dict(m, T) for m in ms if m.status == 2 and not ps.ms_delayed(m, T)]
        delayed = [ps.ms_dict(m, T) for m in ms if ps.ms_delayed(m, T)]
        nxt = [ps.ms_dict(m, T) for m in ms if m.status != 3 and m.plan_date and nxt_s <= m.plan_date <= nxt_e]
        fbs = db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id == x.id,
                                                          BizProjectFeedback.feedback_date >= s,
                                                          BizProjectFeedback.feedback_date <= e)
                         .order_by(BizProjectFeedback.feedback_date)).all()
        contracts = db.scalars(select(BizContract).where(BizContract.project_code == x.project_code,
                                                         BizContract.is_deleted.is_(False), BizContract.status != 3,
                                                         BizContract.sign_date >= s, BizContract.sign_date <= e)
                               .order_by(BizContract.sign_date)).all()
        pays = db.execute(select(BizPayment, BizContract.contract_no).join(BizPayment.contract)
                          .where(BizContract.project_code == x.project_code, BizContract.is_deleted.is_(False),
                                 BizContract.status != 3, BizPayment.payment_date >= s, BizPayment.payment_date <= e)
                          .order_by(BizPayment.payment_date)).all()
        mon = money.get(x.project_code, {})
        risks = [f"节点「{d['node_name']}」计划 {d['plan_date']} 未完成（{d['progress_note'] or '原因待补充'}）"
                 for d in delayed]
        risks += [f.content for f in fbs if f.kind == "risk" or f.risk_level >= 2]
        b = ps.project_brief(db, x, T, mon)
        items.append({
            "project_code": x.project_code, "name": x.name, "customer_name": x.customer_name,
            "business_line": x.business_line, "status_text": PROJECT_STATUS.get(x.status),
            "owner_name": b["owner_name"], "market_owner_name": b["market_owner_name"],
            "progress": b["progress"], "done_nodes": b["done_nodes"], "total_nodes": b["total_nodes"],
            "current_node": b["current_node"], "alert": bool(delayed) or any(f.risk_level >= 2 for f in fbs),
            "done": done, "doing": doing, "delayed": delayed, "next": nxt,
            "feedbacks": [{"date": f.feedback_date, "kind": f.kind, "content": f.content, "risk_level": f.risk_level}
                          for f in fbs],
            "contracts_signed": [{"contract_no": c.contract_no, "name": c.name, "amount": c.amount,
                                  "sign_date": c.sign_date} for c in contracts],
            "payments": [{"contract_no": no, "amount": pm.amount, "payment_date": pm.payment_date} for pm, no in pays],
            "period_signed": sum((c.amount for c in contracts), ZERO),
            "period_received": sum((pm.amount for pm, _ in pays), ZERO),
            "contract_total": mon.get("contract_total", ZERO), "received": mon.get("received", ZERO),
            "payment_rate": mon.get("payment_rate"), "risks": risks})
    tot = {
        "projects": len(items), "alert_projects": sum(1 for i in items if i["alert"]),
        "done_nodes": sum(len(i["done"]) for i in items), "delayed_nodes": sum(len(i["delayed"]) for i in items),
        "period_signed": sum((i["period_signed"] for i in items), ZERO),
        "period_received": sum((i["period_received"] for i in items), ZERO),
        "contract_total": sum((i["contract_total"] for i in items), ZERO),
        "received": sum((i["received"] for i in items), ZERO)}
    tot["payment_rate"] = (tot["received"] / tot["contract_total"]).quantize(Decimal("0.0001")) \
        if tot["contract_total"] else None
    return {"period_type": period_type, "period": period, "start": s, "end": e, "generated_on": T, "is_key": is_key,
            "totals": tot, "projects": items}


def report_text(data: dict) -> str:
    """汇总稿文本（markdown，供 MCP「汇总本周重点项目周报」及前端预览）。"""
    name = "周报" if data["period_type"] == "weekly" else "月报"
    is_key = data.get("is_key", True)
    label = "重点项目" if is_key else "一般项目"
    t = data["totals"]
    out = [f"# {'市场部' if is_key else ''}{label}{name}（{data['period']}：{data['start']} ~ {data['end']}）", "",
           f"本期跟进{label} {t['projects']} 个，其中异常 {t['alert_projects']} 个；完成节点 {t['done_nodes']} 个，"
           f"延期节点 {t['delayed_nodes']} 个；本期新签合同 {_wan(t['period_signed'])}，本期回款 {_wan(t['period_received'])}；"
           f"{label}累计合同 {_wan(t['contract_total'])}，累计回款 {_wan(t['received'])}，回款率 {_pct(t['payment_rate'])}。"]
    for i, x in enumerate(data["projects"], start=1):
        flag = "【异常】" if x["alert"] else ""
        out += ["", f"## {i}. {flag}{x['name']}（{x['project_code']}）",
                f"- 客户：{x['customer_name']}；项目经理：{x['owner_name'] or '—'}；市场负责人：{x['market_owner_name'] or '—'}；"
                f"状态：{x['status_text']}",
                f"- 进度：{x['done_nodes']}/{x['total_nodes']} 节点完成，当前节点：{x['current_node'] or '—'}",
                "- 本期完成：" + ("、".join(f"{d['node_name']}（{d['finish_date']}）" for d in x["done"]) or "无"),
                "- 进行中：" + ("、".join(f"{d['node_name']}（计划 {d['plan_date']}）" for d in x["doing"]) or "无"),
                "- 延期：" + ("、".join(f"{d['node_name']}（计划 {d['plan_date']}）" for d in x["delayed"]) or "无")]
        if x["feedbacks"]:
            out.append("- 进展反馈：" + "；".join(f"{f['date']} {f['content']}" for f in x["feedbacks"][-3:]))
        out.append(f"- 合同与回款：本期新签 {_wan(x['period_signed'])}（{len(x['contracts_signed'])} 份），"
                   f"本期回款 {_wan(x['period_received'])}（{len(x['payments'])} 笔）；累计合同 {_wan(x['contract_total'])}，"
                   f"累计回款 {_wan(x['received'])}，回款率 {_pct(x['payment_rate'])}")
        out.append("- 风险：" + ("；".join(x["risks"]) or "无"))
        out.append("- 下期计划：" + ("、".join(f"{d['node_name']}（{d['plan_date']}）" for d in x["next"]) or "按计划推进"))
    return "\n".join(out)


def _docx_report(data: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    red = RGBColor(0xA6, 0x3D, 0x32)
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Microsoft YaHei", Pt(10.5)
    name = "周报" if data["period_type"] == "weekly" else "月报"
    is_key = data.get("is_key", True)
    label = "重点项目" if is_key else "一般项目"
    doc.add_heading(f"{'市场部' if is_key else ''}{label}{name}（初稿）", 0)
    doc.add_paragraph(f"统计周期：{data['period']}（{data['start']} ~ {data['end']}）  生成日期：{data['generated_on']}  "
                      "数据来源：项目流程节点 / 进展反馈 / 合同台账 / 回款记录")
    t = data["totals"]
    doc.add_heading("一、总体概况", 1)
    doc.add_paragraph(
        f"本期跟进{label} {t['projects']} 个，异常 {t['alert_projects']} 个；完成节点 {t['done_nodes']} 个，延期节点 "
        f"{t['delayed_nodes']} 个。本期新签合同 {_wan(t['period_signed'])}，本期回款 {_wan(t['period_received'])}；"
        f"{label}累计合同 {_wan(t['contract_total'])}，累计回款 {_wan(t['received'])}，回款率 {_pct(t['payment_rate'])}。")
    tbl = doc.add_table(rows=1, cols=7)
    tbl.style = "Light Grid Accent 1"
    for c, h in zip(tbl.rows[0].cells, ["项目", "当前节点", "进度", "延期", "本期回款", "累计回款", "回款率"]):
        c.text = h
    for x in data["projects"]:
        r = tbl.add_row().cells
        vals = [x["name"], x["current_node"] or "—", f"{x['done_nodes']}/{x['total_nodes']}", str(len(x["delayed"])),
                _wan(x["period_received"]), _wan(x["received"]), _pct(x["payment_rate"])]
        for c, v in zip(r, vals):
            c.text = v
        if x["delayed"]:
            for run in r[3].paragraphs[0].runs:
                run.font.color.rgb = red
    doc.add_heading(f"二、{label}进展", 1)
    for i, x in enumerate(data["projects"], start=1):
        h = doc.add_heading(f"{i}. {x['name']}（{x['project_code']}）", 2)
        if x["alert"]:
            run = h.add_run("  [异常]")
            run.font.color.rgb = red
        doc.add_paragraph(f"客户：{x['customer_name']}；项目经理：{x['owner_name'] or '—'}；市场负责人："
                          f"{x['market_owner_name'] or '—'}；状态：{x['status_text']}；进度 {x['done_nodes']}/{x['total_nodes']}，"
                          f"当前节点：{x['current_node'] or '—'}")
        doc.add_paragraph("本期进度", style="Heading 3")
        doc.add_paragraph("已完成：" + ("、".join(f"{d['node_name']}（{d['finish_date']}）" for d in x["done"]) or "无"),
                          style="List Bullet")
        doc.add_paragraph("进行中：" + ("、".join(f"{d['node_name']}（计划 {d['plan_date']}，{d['owner_name'] or '-'}）"
                                              for d in x["doing"]) or "无"), style="List Bullet")
        p = doc.add_paragraph(style="List Bullet")
        run = p.add_run("延期：" + ("、".join(f"{d['node_name']}（计划 {d['plan_date']}）" for d in x["delayed"]) or "无"))
        if x["delayed"]:
            run.font.color.rgb = red
        if x["feedbacks"]:
            doc.add_paragraph("进展反馈摘录", style="Heading 3")
            for f in x["feedbacks"][-5:]:
                doc.add_paragraph(f"{f['date']}  {f['content']}", style="List Bullet")
        doc.add_paragraph("合同签订与回款动态", style="Heading 3")
        for c in x["contracts_signed"]:
            doc.add_paragraph(f"新签 {c['contract_no']} {c['name'] or ''} {_wan(c['amount'])}（{c['sign_date']}）",
                              style="List Bullet")
        for pm in x["payments"]:
            doc.add_paragraph(f"回款 {pm['contract_no']} {_wan(pm['amount'])}（{pm['payment_date']}）", style="List Bullet")
        doc.add_paragraph(f"累计合同 {_wan(x['contract_total'])}，累计回款 {_wan(x['received'])}，回款率 {_pct(x['payment_rate'])}。")
        doc.add_paragraph("风险与下期计划", style="Heading 3")
        for rk in x["risks"] or ["暂无风险"]:
            doc.add_paragraph("风险：" + rk, style="List Bullet")
        doc.add_paragraph("下期计划：" + ("、".join(f"{d['node_name']}（{d['plan_date']}）" for d in x["next"]) or "按计划推进"),
                          style="List Bullet")
    doc.add_heading("三、口径说明", 1)
    for s in ["重点项目：项目台账中标记为重点（is_key）的项目。" if is_key else "一般项目：项目台账中当前跟进的一般项目。",
              "本期完成：完成日落在统计周期内的节点；延期：未完成且计划日早于生成日，或状态为延期的节点。",
              "本期回款：回款日（银行流水到账日）落在统计周期内的回款；累计回款率 = 累计回款 / 累计合同额（不含已终止合同）。",
              "本稿为系统自动生成初稿，请市场部润色后发布。" if is_key else "本稿为系统自动生成初稿，请核对后发布。"]:
        doc.add_paragraph(s, style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _next_version(db: Session, rtype: str, period: str) -> int:
    return (db.scalar(select(func.max(RptReport.version)).where(RptReport.report_type == rtype,
                                                                 RptReport.period == period)
                      .execution_options(include_removed_records=True)) or 0) + 1


def build_market_report(db: Session, period_type: str = "weekly", period: str | None = None, user_scope=None,
                        trigger: str = "manual", created_by: int | None = None, save: bool = True, *, is_key=True) -> dict:
    """生成市场周报/月报：docx 初稿 + 结构化 JSON + 汇总稿文本；存 RptReport(report_type=market_weekly|market_monthly)。"""
    t0 = _time.perf_counter()
    data = _report_data(db, period_type, period, user_scope, is_key=is_key)
    text = report_text(data)
    out = {"data": data, "text": text, "report": None, "file": None}
    if not save:
        return out
    rtype = f"{'market' if is_key else 'general'}_{period_type}"
    if not is_key and db.bind.dialect.name == "postgresql":
        db.execute(sql_text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"report:{rtype}:{data['period']}"})
    ver = _next_version(db, rtype, data["period"])
    name = "周报" if period_type == "weekly" else "月报"
    label = "市场部重点项目" if is_key else "一般项目"
    fname = f"{label}{name}_{data['period']}_v{ver}.docx"
    f = save_bytes(db, data=_docx_report(data), filename=fname, category="project_team_report",
                   key=f"report/{rtype}/{data['end']:%Y-%m}/{fname}", uploader_id=created_by)
    r = RptReport(report_type=rtype, period=data["period"], title=f"{label}{name} {data['period']}", version=ver,
                  metrics=encode({**data["totals"], "project_count": data["totals"]["projects"], "start": data["start"], "end": data["end"], "text": text,
                                  "projects": [{k: v for k, v in x.items() if k not in ("doing",)}
                                               for x in data["projects"]]}),
                  file_ids=[f.id], trigger=trigger, created_by=created_by,
                  cost_ms=int((_time.perf_counter() - t0) * 1000))
    db.add(r)
    db.flush()
    out.update(report=r, file=f)
    return out


def can_read_project_report(db, p, report, *, team_only=False):
    if not p.workbench_enabled or report.report_type not in TEAM_SCOPED_REPORT_TYPES:
        return False
    if report.report_type in PROJECT_REPORT_TYPES:
        if not p.has_menu("market" if report.report_type in KEY_REPORT_TYPES else "projects"):
            return False
    else:
        from app.services.report_service import allowed
        if not allowed(p, report.report_type):
            return False
    metrics = report.metrics or {}
    if report.report_type == "morning_report":
        return p.has_menu("board") and (metrics.get("key_projects") == [] or (not team_only and p.data_scope == "all"))
    if not team_only and p.data_scope == "all":
        return True
    if report.report_type == "company_weekly":
        codes = {item["project_code"] for key in ("key_projects", "delays") for item in metrics.get(key, [])}
    elif "project_codes" in metrics:
        codes = set(metrics["project_codes"])
    elif isinstance(metrics.get("projects"), list):
        codes = {item["project_code"] for item in metrics["projects"]}
    elif isinstance(metrics.get("rows"), list):
        codes = {code for item in metrics["rows"] for code in (item.get("group_codes") or [item.get("project_code")]) if code}
    else:
        return False
    if team_only:
        from app.services.key_projects import membership_scope
        query = select(BizProject.project_code).where(BizProject.is_deleted.is_(False), membership_scope(p, is_key=None))
    else:
        query = ps.visible_query(p).with_only_columns(BizProject.project_code)
    visible = db.scalars(query.where(BizProject.project_code.in_(codes)))
    return codes.issubset(visible)


def can_read_general_report(db, p, report):
    return (report.report_type in GENERAL_REPORT_TYPES and (p.data_scope == "all" or report.created_by == p.id)
            and can_read_project_report(db, p, report))


def summarize_week_text(db: Session, user_scope=None, period: str | None = None) -> str:
    """「汇总本周重点项目周报」：返回本周（默认）汇总稿文本，不落库。"""
    from app.core.timeutil import week_no
    return build_market_report(db, "weekly", period or week_no(today()), user_scope, save=False)["text"]


# ------------------------------------------------------------------ 竞品简报
TAG_LINE = {"数字校园": "XY", "智慧校园": "XY", "智慧教室": "XY", "数字基座": "XY", "安防": "AF", "视频": "AF",
            "明厨亮灶": "AF", "校园安全": "AF", "综合评价": "KP", "体质": "KP", "考评": "KP", "运维": "YW"}


def _impact(items: list[MktIntel]) -> tuple[str, str]:
    lines = set()
    for it in items:
        for tg in (it.tags or []):
            for k, ln in TAG_LINE.items():
                if k in tg or k in it.title:
                    lines.add(LINES[ln])
    urgent = [it for it in items if it.importance >= 3]
    imp = (f"主要影响我方{('、'.join(sorted(lines)))}业务" if lines else "对我方业务存在间接影响") + \
          (f"，其中 {len(urgent)} 条为紧急情报需立即关注" if urgent else "") + "。"
    kinds = {it.kind for it in items}
    sugg = []
    if "competitor" in kinds:
        sugg.append("梳理同区域在跟商机，提前对标其方案亮点与报价策略，强化本地化服务与交付案例")
    if "bid" in kinds:
        sugg.append("评估招标需求与资质匹配度，产品中心提前启动标书准备")
    if "policy" in kinds:
        sugg.append("将政策要点纳入方案模板与客户沟通材料，争取前期规划介入")
    if "industry" in kinds:
        sugg.append("跟踪行业趋势，评估产品路线与合作伙伴生态调整")
    return imp, "；".join(sugg) + "。"


def intel_brief(db: Session, start: date, end: date, competitors: list[str] | None = None, author_id: int | None = None,
                to_kb: bool = False, save: bool = True) -> dict:
    q = select(MktIntel).where(func.coalesce(MktIntel.published_on, func.date(MktIntel.created_at)) >= start,
                               func.coalesce(MktIntel.published_on, func.date(MktIntel.created_at)) <= end)
    if competitors:
        q = q.where(MktIntel.competitor.in_(competitors))
    rows = db.scalars(q.order_by(MktIntel.importance.desc(), MktIntel.published_on.desc())).all()
    groups: dict[str, list[MktIntel]] = defaultdict(list)
    for r in rows:
        groups[r.competitor or KIND_TEXT.get(r.kind, "其他")].append(r)
    sections = []
    for g, items in sorted(groups.items(), key=lambda kv: (-max(i.importance for i in kv[1]), -len(kv[1]))):
        imp, sugg = _impact(items)
        sections.append({"group": g, "count": len(items),
                         "items": [{"id": i.id, "title": i.title, "kind": i.kind, "summary": i.summary,
                                    "published_on": i.published_on, "importance": i.importance, "tags": i.tags,
                                    "source_name": i.source_name, "source_url": i.source_url} for i in items],
                         "impact": imp, "suggestion": sugg})
    polished = None
    if sections:
        polished = _llm("你是教育信息化企业的市场分析师，请把以下竞品情报整理为不超过 300 字的简报导语，客观克制。",
                        "\n".join(f"{s['group']}：" + "；".join(i["title"] for i in s["items"]) for s in sections))
    lead = polished or (f"{start} ~ {end} 共收录情报 {len(rows)} 条，涉及 {len(sections)} 个竞品/类别；"
                        f"紧急 {sum(1 for r in rows if r.importance >= 3)} 条，重要 {sum(1 for r in rows if r.importance == 2)} 条。")
    out = {"start": start, "end": end, "total": len(rows), "lead": lead, "llm": bool(polished), "sections": sections,
           "file": None, "kb": None}
    if not save:
        return out
    from docx import Document
    doc = Document()
    doc.add_heading(f"竞品情报简报（{start} ~ {end}）", 0)
    doc.add_paragraph(lead)
    for i, s in enumerate(sections, start=1):
        doc.add_heading(f"{i}. {s['group']}（{s['count']} 条）", 1)
        doc.add_paragraph("竞品动态", style="Heading 3")
        for it in s["items"]:
            doc.add_paragraph(f"[{IMPORTANCE.get(it['importance'])}] {it['published_on'] or ''} {it['title']}：{it['summary']}"
                              + (f"（来源：{it['source_name']}）" if it["source_name"] else ""), style="List Bullet")
        doc.add_paragraph("对我方影响", style="Heading 3")
        doc.add_paragraph(s["impact"])
        doc.add_paragraph("应对建议", style="Heading 3")
        doc.add_paragraph(s["suggestion"])
    buf = io.BytesIO()
    doc.save(buf)
    period = f"{start}~{end}"
    fname = f"竞品情报简报_{start:%Y%m%d}-{end:%Y%m%d}.docx"
    f = save_bytes(db, data=buf.getvalue(), filename=fname, category="report",
                   key=f"report/intel_brief/{end:%Y-%m}/{fname}", uploader_id=author_id)
    r = RptReport(report_type="intel_brief", period=period, title=f"竞品情报简报 {period}",
                  version=_next_version(db, "intel_brief", period), metrics=encode({"total": len(rows), "lead": lead,
                                                                                     "groups": [s["group"] for s in sections]}),
                  file_ids=[f.id], trigger="manual", created_by=author_id)
    db.add(r)
    db.flush()
    out.update(file=f, report=r)
    if to_kb:
        out["kb"] = _to_kb(db, f"竞品情报简报 {period}", lead + "\n" + "\n".join(
            f"{s['group']}：{s['impact']}{s['suggestion']}" for s in sections), f)
    return out


def _to_kb(db: Session, title: str, text: str, f) -> dict | None:
    """可选沉淀到知识库：kb_service.add_local_document 存在则调用，否则跳过。"""
    try:
        from app.services import kb_service
        fn = getattr(kb_service, "add_local_document", None)
        if not fn:
            return {"ok": False, "message": "知识库未提供沉淀接口，已跳过"}
        res = fn(db, title=title, content=text, category="案例", file_id=f.id if f else None)
        return {"ok": True, "result": res if isinstance(res, (dict, str, int)) else None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"沉淀知识库跳过：{exc}"[:200]}


# ------------------------------------------------------------------ 项目复盘
def review_draft(db: Session, proj: BizProject) -> dict:
    T = today()
    ms = sorted(proj.milestones, key=lambda m: m.seq)
    mon = ps.money_summary(db, [proj.project_code])[proj.project_code]
    prof = ps.profit_of(db, proj.project_code, T)
    plan_days = (proj.plan_end_date - proj.start_date).days if proj.start_date and proj.plan_end_date else None
    finished = [m.finish_date for m in ms if m.finish_date]
    actual_end = max(finished) if finished else None
    actual_days = (actual_end - proj.start_date).days if proj.start_date and actual_end else None
    late = [m for m in ms if (m.status == 3 and m.alert_flag) or ps.ms_delayed(m, T)]
    late_days = {m.id: ((m.finish_date or T) - m.plan_date).days for m in late if m.plan_date}
    risks = db.scalars(select(BizProjectFeedback).where(BizProjectFeedback.project_id == proj.id,
                                                        (BizProjectFeedback.kind == "risk") | (BizProjectFeedback.risk_level >= 2))
                       .order_by(BizProjectFeedback.feedback_date)).all()
    goal = "\n".join([
        f"合同额：{_wan(mon['contract_total'])}（{mon['contract_count']} 份合同）；预算/投标额：{_wan(proj.budget_amount)}",
        f"计划工期：{proj.start_date or '—'} ~ {proj.plan_end_date or '—'}" + (f"（{plan_days} 天）" if plan_days else ""),
        f"计划节点：{len(ms)} 个（" + " → ".join(m.node_name for m in ms) + "）"])
    result = "\n".join([
        f"实际回款：{_wan(mon['received'])}，回款率 {_pct(mon['payment_rate'])}；待回款 {_wan(mon['receivable'])}",
        f"项目成本：{_wan(prof['cost'])}（报销 {_wan(prof['cost_expense'])}，采购/分包 {_wan(prof['cost_project'])}）；"
        f"毛利 {_wan(prof['gross_profit'])}，毛利率 {_pct(prof['gross_margin'])}",
        f"节点完成：{sum(1 for m in ms if m.status == 3)}/{len(ms)}；延期节点 {len(late)} 个" +
        (f"，累计延期 {sum(late_days.values())} 天" if late_days else ""),
        f"实际工期：{proj.start_date or '—'} ~ {actual_end or '—'}" + (f"（{actual_days} 天" if actual_days else "") +
        (f"，较计划{'超出' if actual_days > plan_days else '提前'} {abs(actual_days - plan_days)} 天）" if actual_days and plan_days
         else ("）" if actual_days else ""))])
    lessons, tags = [], {LINES.get(proj.business_line, proj.business_line)}
    for m in late:
        reason = (m.progress_note or "").replace("已完成", "").strip("（）() ") or "原因未记录"
        lessons.append(f"「{m.node_name}」延期 {late_days.get(m.id, 0)} 天：{reason}。建议在该节点前置风险识别，明确"
                       f"{m.duty.split('，')[0] if m.duty else '责任人'}的交付时点并按周跟踪。")
        tags.add(f"{m.node_name}延期")
        for kw, tg in (("图纸", "设计确认"), ("到货", "供应链"), ("供应商", "供应链"), ("土建", "现场条件"), ("甲方", "甲方协调")):
            if kw in reason:
                tags.add(tg)
    for f in risks:
        lessons.append(f"风险事件（{f.feedback_date}）：{f.content}")
        tags.add("风险管控")
    if mon["payment_rate"] is not None and mon["payment_rate"] < Decimal("0.6"):
        lessons.append("回款进度偏慢：建议在合同中明确验收与付款节点，验收完成后 5 个工作日内提交付款申请并跟进。")
        tags.add("回款管理")
    if prof["gross_margin"] is not None and prof["gross_margin"] < Decimal("0.2"):
        lessons.append("毛利率偏低：复核采购比价与分包策略，前期报价需预留现场变更费用。")
        tags.add("成本控制")
    if not late:
        lessons.append("各节点均按计划完成，标准控制流程执行到位，可作为同类项目交付样板。")
        tags.add("按期交付")
    text = "\n".join(f"{i}. {s}" for i, s in enumerate(lessons, start=1))
    polished = _llm("你是项目管理专家，请把以下项目复盘经验条目润色为条理清晰的经验总结，保留事实与数字。", text)
    return {"title": f"{proj.name} 项目复盘", "goal": goal, "result": result, "lessons": polished or text,
            "tags": sorted(tags)}


def generate_review(db: Session, proj: BizProject, author_id: int | None = None, force: bool = False):
    """结项/验收自动生成复盘初稿；已存在则跳过（force=True 时重写初稿，不覆盖已定稿）。"""
    exist = db.scalar(select(BizProjectReview).where(BizProjectReview.project_id == proj.id)
                      .order_by(BizProjectReview.id.desc()))
    if exist and (not force or exist.status == 2):
        return exist
    d = review_draft(db, proj)
    r = exist or BizProjectReview(project_id=proj.id, status=1, author_id=author_id)
    r.title, r.goal, r.result, r.lessons, r.tags = d["title"], d["goal"], d["result"], d["lessons"], d["tags"]
    if not exist:
        db.add(r)
    db.flush()
    return r


def review_dict(db: Session, r: BizProjectReview) -> dict:
    proj = db.get(BizProject, r.project_id)
    au = db.get(SysUser, r.author_id) if r.author_id else None
    return {"id": r.id, "project_id": r.project_id, "project_code": proj.project_code if proj else None,
            "project_name": proj.name if proj else None, "business_line": proj.business_line if proj else None,
            "project_status": proj.status if proj else None, "title": r.title, "goal": r.goal, "result": r.result,
            "lessons": r.lessons, "tags": r.tags or [], "status": r.status, "status_text": "已定稿" if r.status == 2 else "初稿",
            "author_id": r.author_id, "author_name": au.name if au else None, "file_id": r.file_id,
            "created_at": r.created_at, "updated_at": r.updated_at}


def review_docx(db: Session, r: BizProjectReview) -> bytes:
    from docx import Document
    d = review_dict(db, r)
    doc = Document()
    doc.add_heading(d["title"], 0)
    doc.add_paragraph(f"项目：{d['project_name']}（{d['project_code']}）  状态：{d['status_text']}  "
                      f"标签：{'、'.join(d['tags']) or '—'}")
    for h, body in (("一、目标", d["goal"]), ("二、结果", d["result"]), ("三、经验与改进", d["lessons"])):
        doc.add_heading(h, 1)
        for line in (body or "—").split("\n"):
            doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
