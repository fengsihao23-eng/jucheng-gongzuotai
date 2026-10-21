"""报销归集（PRD 3.3 / FR-05-04 后续人工补归集）。

状态：1 审批中 2 已通过（待归集）3 已驳回 4 已归集（project_code 为空 = 公共费用）。
已归集率 = 已归集金额（含公共费用） ÷ 审批通过金额（状态 2+4）。
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.response import bad_request, conflict
from app.core.timeutil import now
from app.models import (BizExpenseReport, BizProject, FinBankFlow, FinCashJournal, SyncWecomApproval, SysDepartment,
                        SysUser)
from app.models.business import EXPENSE_STATUS

D = Decimal
PUBLIC = "__public__"
STATUS_GROUPS = {"pending": [2], "allocated": [4], "approved": [2, 4], "all": [1, 2, 3, 4]}


def build_query(*, status: str = "approved", project: str | None = None, dept: str | None = None,
                category: str | None = None, start: date | None = None, end: date | None = None,
                applicant: str | None = None, keyword: str | None = None, pay_status: int | None = None):
    q = select(BizExpenseReport)
    if status in STATUS_GROUPS:
        q = q.where(BizExpenseReport.status.in_(STATUS_GROUPS[status]))
    elif status and status.isdigit():
        q = q.where(BizExpenseReport.status == int(status))
    if project == PUBLIC:
        q = q.where(BizExpenseReport.status == 4, BizExpenseReport.project_code.is_(None))
    elif project:
        q = q.where(BizExpenseReport.project_code == project)
    if dept:
        q = q.where(BizExpenseReport.dept_code == dept)
    if category:
        q = q.where(BizExpenseReport.category == category)
    if start:
        q = q.where(BizExpenseReport.expense_date >= start)
    if end:
        q = q.where(BizExpenseReport.expense_date <= end)
    if pay_status is not None:
        q = q.where(BizExpenseReport.pay_status == pay_status)
    if applicant or keyword:
        q = q.outerjoin(SysUser, SysUser.id == BizExpenseReport.applicant_id)
        if applicant:
            q = q.where(or_(SysUser.name.contains(applicant), SysUser.wecom_userid == applicant))
        if keyword:
            k = keyword.strip()
            q = q.where(or_(BizExpenseReport.approval_no.contains(k), BizExpenseReport.reason.contains(k),
                            BizExpenseReport.category.contains(k), SysUser.name.contains(k),
                            BizExpenseReport.project_code.contains(k)))
    return q


def _names(db: Session) -> tuple[dict, dict]:
    projects = dict(db.execute(select(BizProject.project_code, BizProject.name)).all())
    depts = dict(db.execute(select(SysDepartment.dept_code, SysDepartment.name)).all())
    return projects, depts


def row(r: BizExpenseReport, projects: dict, depts: dict) -> dict:
    alloc = "待归集" if r.status == 2 else ("公共费用" if r.status == 4 and not r.project_code else
                                        "项目" if r.status == 4 else EXPENSE_STATUS.get(r.status))
    return {"id": r.id, "approval_no": r.approval_no, "applicant_id": r.applicant_id,
            "applicant": r.applicant.name if r.applicant else None, "dept_code": r.dept_code,
            "dept_name": depts.get(r.dept_code), "project_code": r.project_code, "project_name": projects.get(r.project_code),
            "amount": r.amount, "expense_date": r.expense_date, "approved_at": r.approved_at, "category": r.category,
            "reason": r.reason, "status": r.status, "status_name": "待归集" if r.status == 2 else EXPENSE_STATUS.get(r.status),
            "allocation": alloc, "pay_status": r.pay_status, "bank_flow_id": r.bank_flow_id,
            "raw_project": (r.detail or {}).get("raw_project"), "allocated_by": r.allocated_by}


def summary(db: Session, q) -> dict:
    sub = q.subquery()
    rows = db.execute(select(sub.c.status, (sub.c.project_code.is_(None)).label("pub"), func.count(), func.sum(sub.c.amount))
                      .group_by(sub.c.status, "pub")).all()
    out = {"count": 0, "amount": D(0), "pending_count": 0, "pending_amount": D(0), "project_amount": D(0),
           "public_amount": D(0), "allocated_amount": D(0)}
    for st, pub, n, s in rows:
        s = D(s or 0)
        out["count"] += n
        out["amount"] += s
        if st == 2:
            out["pending_count"] += n
            out["pending_amount"] += s
        elif st == 4:
            out["allocated_amount"] += s
            out["public_amount" if pub else "project_amount"] += s
    approved = out["pending_amount"] + out["allocated_amount"]
    out["allocated_rate"] = (out["allocated_amount"] / approved).quantize(D("0.0001")) if approved else None
    return out


def stats(db: Session, start: date, end: date) -> dict:
    q = build_query(status="approved", start=start, end=end)
    reports = db.scalars(q).all()
    projects, depts = _names(db)
    s = summary(db, q)
    by_project, by_dept, by_cat, trend = defaultdict(D), defaultdict(D), defaultdict(D), defaultdict(D)
    cnt_project = defaultdict(int)
    for r in reports:
        key = r.project_code or ("公共费用" if r.status == 4 else "待归集")
        by_project[key] += r.amount
        cnt_project[key] += 1
        by_dept[depts.get(r.dept_code, r.dept_code or "-")] += r.amount
        by_cat[r.category] += r.amount
        trend[r.expense_date] += r.amount
    pend_all = db.execute(select(func.count(), func.coalesce(func.sum(BizExpenseReport.amount), 0))
                          .where(BizExpenseReport.status == 2)).one()
    return {
        "start": start, "end": end, "summary": s,
        "kpi": {"total_amount": s["amount"], "total_count": s["count"], "allocated_rate": s["allocated_rate"],
                "pending_count": pend_all[0], "pending_amount": D(pend_all[1]), "public_amount": s["public_amount"],
                "project_amount": s["project_amount"]},
        "by_project": sorted([{"key": k, "name": projects.get(k, k), "amount": v, "count": cnt_project[k]}
                              for k, v in by_project.items()], key=lambda x: -x["amount"]),
        "by_dept": sorted([{"name": k, "amount": v} for k, v in by_dept.items()], key=lambda x: -x["amount"]),
        "by_category": sorted([{"name": k, "amount": v} for k, v in by_cat.items()], key=lambda x: -x["amount"]),
        "trend": [{"date": d, "amount": v} for d, v in sorted(trend.items())],
    }


def allocate(db: Session, ids: list[int], project_code: str | None, user_id: int, *, remark: str | None = None,
             ip: str | None = None) -> dict:
    if not ids:
        raise bad_request("请选择报销单")
    if project_code:
        p = db.scalar(select(BizProject).where(BizProject.project_code == project_code, BizProject.is_deleted.is_(False)))
        if not p:
            raise bad_request(f"项目 {project_code} 不存在")
    reports = db.scalars(select(BizExpenseReport).where(BizExpenseReport.id.in_(ids))).all()
    if len(reports) != len(set(ids)):
        raise bad_request("部分报销单不存在")
    bad = [r.approval_no for r in reports if r.status not in (2, 4)]
    if bad:
        raise conflict(f"仅审批通过的报销单可归集：{', '.join(bad[:5])}")
    target = project_code or None
    changed = []
    for r in reports:
        before = {"status": r.status, "project_code": r.project_code}
        r.project_code, r.status, r.allocated_by = target, 4, user_id
        det = dict(r.detail or {})
        log = list(det.get("allocation_log") or [])
        log.append({"at": now().isoformat(timespec="seconds"), "by": user_id, "from": before["project_code"],
                    "from_status": before["status"], "to": target or "公共费用", "remark": remark})
        det["allocation_log"] = log
        r.detail = det
        # 已付款的报销同步日记账项目口径
        if r.bank_flow_id:
            for j in db.scalars(select(FinCashJournal).where(FinCashJournal.bank_flow_id == r.bank_flow_id)):
                j.project_code = target
        log_op(db, user_id, "expense.allocate", "expense", r.id, before,
               {"status": 4, "project_code": target, "remark": remark}, ip)
        changed.append(r.approval_no)
    db.flush()
    return {"count": len(changed), "approval_nos": changed, "project_code": target}


def detail(db: Session, r: BizExpenseReport) -> dict:
    projects, depts = _names(db)
    out = row(r, projects, depts)
    out["items"] = [{"item_name": i.item_name, "amount": i.amount, "invoice_count": i.invoice_count} for i in r.items]
    sp = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == r.approval_no))
    det = r.detail or {}
    out["approval"] = {
        "sp_no": r.approval_no, "exists": bool(sp),
        "sp_name": sp.sp_name if sp else None, "template_id": sp.template_id if sp else None,
        "status": sp.status if sp else None, "apply_time": sp.apply_time if sp else None,
        "received_via": sp.received_via if sp else None, "dispatched_at": sp.dispatched_at if sp else None,
        "dispatch_target": sp.dispatch_target if sp else None,
        "fields": (sp.detail or {}).get("fields") if sp and sp.detail else det.get("fields"),
    }
    out["raw_project"] = det.get("raw_project")
    users = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    out["allocation_log"] = [{**x, "by_name": users.get(x.get("by"))} for x in det.get("allocation_log") or []]
    out["allocated_by_name"] = users.get(r.allocated_by)
    flow = db.get(FinBankFlow, r.bank_flow_id) if r.bank_flow_id else None
    out["bank_flow"] = None if not flow else {
        "id": flow.id, "bank_account": flow.bank_account, "trade_date": flow.trade_date, "amount": flow.amount,
        "counterparty": flow.counterparty, "summary": flow.summary, "match_status": flow.match_status}
    j = db.scalar(select(FinCashJournal).where(FinCashJournal.bank_flow_id == r.bank_flow_id)) if r.bank_flow_id else None
    out["journal"] = None if not j else {"id": j.id, "journal_date": j.journal_date, "amount": j.amount, "summary": j.summary}
    return out


def export_xlsx(db: Session, q, title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    projects, depts = _names(db)
    reports = db.scalars(q.order_by(BizExpenseReport.expense_date.desc(), BizExpenseReport.id.desc())).all()
    wb = Workbook()
    ws = wb.active
    ws.title = "报销归集明细"
    head = ["审批单号", "申请人", "部门", "费用日期", "审批通过时间", "费用类别", "金额", "归属项目编码", "归属项目",
            "归集状态", "付款状态", "报销事由", "审批原始所属项目"]
    ws.append(head)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="EEF1F8")
    total = D(0)
    for r in reports:
        x = row(r, projects, depts)
        total += r.amount
        ws.append([x["approval_no"], x["applicant"], x["dept_name"], r.expense_date.isoformat(),
                   r.approved_at.strftime("%Y-%m-%d %H:%M") if r.approved_at else None, r.category, float(r.amount),
                   r.project_code, x["project_name"] or ("公共费用" if r.status == 4 else None), x["allocation"],
                   "已付" if r.pay_status else "未付", r.reason, x["raw_project"]])
    ws.append(["合计", None, None, None, None, None, float(total)])
    ws[ws.max_row][0].font = Font(bold=True)
    for col, w in zip("ABCDEFGHIJKLM", (20, 10, 12, 12, 17, 10, 13, 17, 30, 10, 9, 18, 24)):
        ws.column_dimensions[col].width = w
    for row_ in ws.iter_rows(min_row=2):
        row_[6].number_format = "#,##0.00"
    ws2 = wb.create_sheet("口径说明")
    for line in [[title], ["归集规则", "企微报销模板「所属项目」必填，审批通过后按该字段自动归集到项目（PRD 3.3-1）。"],
                 ["公共费用", "选择“公共费用”或无法归集的费用计入公共费用，报表单列为「管理费用-公共」，不摊入项目毛利（3.3-2）。"],
                 ["待归集", "所属项目缺失/无效的报销单进入待归集池，由财务人工补充归集，补归集人与时间全量留痕。"],
                 ["金额", "单位：元；明细与页面筛选结果一致。"], ["导出时间", now().strftime("%Y-%m-%d %H:%M")]]:
        ws2.append(line)
    ws2["A1"].font = Font(bold=True, size=13)
    ws2.column_dimensions["A"].width = 12
    ws2.column_dimensions["B"].width = 90
    for r_ in ws2.iter_rows():
        for c in r_:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
