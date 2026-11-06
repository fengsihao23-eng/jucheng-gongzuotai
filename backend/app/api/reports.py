"""报表中心（FR-04）+ 项目利润（FR-04-02 / 7.1）。

报表：类型目录 / 列表（同 type+period 只显示最新版，可展开历史版本）/ 手动生成 / 一键更新 / 详情；
下载走 file_brief 中的签名链接（下载审计在 /files 接口）。
项目利润：GET /reports/profit（profit_service 实时口径 + 下钻）、/reports/profit/trend（近 12 周毛利趋势）。
"""
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, forbidden, not_found, ok, page_data
from app.core.storage import file_brief
from app.core.timeutil import last_week, week_no, week_range
from app.models import (BizContract, BizExpenseReport, BizPayment, BizProject, BizProjectCost, FinVoucher, RptReport,
                        SysUser)
from app.services import profit_service
from app.services import report_service as rs

router = APIRouter(prefix="/reports", tags=["reports"])

Reports = Depends(require_menu("reports"))
ProfitMenu = Depends(require_menu("profit", "reports"))


def _hidden_types(p: Principal) -> list[str]:
    return [t for t in rs.REPORT_TYPES if not rs.allowed(p, t)]


def _check_type(p: Principal, report_type: str):
    if not rs.allowed(p, report_type):
        raise forbidden("无权查看该类报表")


def _brief(db: Session, r: RptReport, versions: int | None = None, names: dict | None = None) -> dict:
    spec = rs.REPORT_TYPES.get(r.report_type, {})
    meta = (r.metrics or {}).get("_meta", {})
    return {"id": r.id, "report_type": r.report_type, "type_name": spec.get("name", r.report_type),
            "period": r.period, "period_label": meta.get("period_label", r.period), "title": r.title,
            "version": r.version, "versions": versions, "trigger": r.trigger, "cost_ms": r.cost_ms,
            "created_at": r.created_at, "created_by": r.created_by,
            "created_by_name": (names or {}).get(r.created_by) if r.created_by else None,
            "summary": (r.metrics or {}).get("summary", []),
            "files": [file_brief(f) for f in rs.report_files(db, r)]}


def _names(db: Session, rows) -> dict:
    ids = {r.created_by for r in rows if r.created_by}
    return dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(ids))).all()) if ids else {}


# ---------------------------------------------------------------- 报表目录 / 列表
@router.get("/types")
def types(p: Principal = Reports, db: Session = Depends(get_db)):
    out = []
    for c in rs.catalog(db):
        if not rs.allowed(p, c["report_type"]):
            continue
        last = db.scalar(select(RptReport).where(RptReport.report_type == c["report_type"])
                         .order_by(RptReport.created_at.desc(), RptReport.id.desc()).limit(1))
        c["latest"] = {"id": last.id, "period": last.period, "title": last.title, "version": last.version,
                       "created_at": last.created_at} if last else None
        c["count"] = db.scalar(select(func.count(func.distinct(RptReport.period)))
                               .where(RptReport.report_type == c["report_type"]))
        out.append(c)
    return ok({"types": out, "can_generate": p.can("report:generate")})


@router.get("")
def list_reports(report_type: str = "", period: str = "", page: int = 1, page_size: int = 20,
                 p: Principal = Reports, db: Session = Depends(get_db)):
    page_size = max(1, min(page_size, 100))
    sub = (select(RptReport.report_type.label("t"), RptReport.period.label("p"),
                  func.max(RptReport.version).label("v"), func.count().label("n"))
           .group_by(RptReport.report_type, RptReport.period))
    hidden = _hidden_types(p)
    if hidden:
        sub = sub.where(RptReport.report_type.not_in(hidden))
    if report_type:
        sub = sub.where(RptReport.report_type == report_type)
    if period:
        sub = sub.where(RptReport.period.like(f"{period}%"))
    sub = sub.subquery()
    q = select(RptReport, sub.c.n).join(sub, (RptReport.report_type == sub.c.t) & (RptReport.period == sub.c.p) &
                                        (RptReport.version == sub.c.v))
    total = db.scalar(select(func.count()).select_from(sub))
    rows = db.execute(q.order_by(RptReport.created_at.desc(), RptReport.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    names = _names(db, [r for r, _ in rows])
    return ok(page_data([_brief(db, r, n, names) for r, n in rows], total, page, page_size))


# ---------------------------------------------------------------- 生成 / 一键更新
@router.post("/generate")
def generate(request: Request, report_type: str = Body(..., embed=True), period: str | None = Body(None, embed=True),
             project_codes: list[str] | None = Body(None, embed=True), p: Principal = Reports,
             db: Session = Depends(get_db)):
    if not p.can("report:generate"):
        raise forbidden("无报表生成权限")
    if report_type not in rs.REPORT_TYPES:
        raise bad_request(f"未知报表类型 {report_type}")
    _check_type(p, report_type)
    if project_codes and report_type not in ("weekly_profit", "project_operation"):
        raise bad_request("该报表不支持按项目生成")
    if project_codes:
        found = set(db.scalars(select(BizProject.project_code).where(BizProject.project_code.in_(project_codes))))
        if missing := set(project_codes) - found:
            raise bad_request(f"项目不存在：{'、'.join(sorted(missing))}")
    r = rs.generate(db, report_type, period or None, "manual", p.id, project_codes)
    log_op(db, p.id, "report.generate", "rpt_report", r.id,
           after={"report_type": report_type, "period": r.period, "version": r.version}, ip=client_ip(request))
    db.commit()
    return ok({**_brief(db, r, names={p.id: p.user.name}), "metrics": r.metrics})


@router.post("/regenerate-all")
def regenerate_all(request: Request, p: Principal = Reports, db: Session = Depends(get_db)):
    if not p.can("report:generate"):
        raise forbidden("无报表生成权限")
    allowed_types = [t for t in rs.REPORT_TYPES if rs.allowed(p, t)]
    res = rs.regenerate_all(db, p.id, "manual", allowed_types)
    for r in res["results"]:
        r["type_name"] = rs.REPORT_TYPES[r["report_type"]]["name"]
    log_op(db, p.id, "report.regenerate_all", "rpt_report", None,
           after={"success": res["success"], "failed": res["failed"]}, ip=client_ip(request))
    db.commit()
    return ok(res)


# ---------------------------------------------------------------- 项目利润
def _profit_principal(p: Principal) -> Principal:
    """项目利润为财务敏感数据：需「项目利润」或「经营看板」菜单（市场/营销/出纳仅有报表中心菜单者不可见）。"""
    if not (p.is_admin or p.has_menu("profit") or p.has_menu("board")):
        raise forbidden("无权查看项目利润")
    return p


def _scope_codes(p: Principal, db: Session) -> list[str] | None:
    if p.data_scope == "all":
        return None
    q = select(BizProject.project_code).where(BizProject.is_deleted.is_(False))
    q = q.where(BizProject.owner_id == p.id) if p.data_scope == "self" else \
        q.join(SysUser, SysUser.id == BizProject.owner_id).where(SysUser.department_id == p.user.department_id)
    return list(db.scalars(q)) or ["__none__"]


def _range(week: str | None, start: date | None, end: date | None) -> tuple[date, date, str]:
    if start and end:
        if start > end:
            raise bad_request("开始日期不能晚于结束日期")
        if (end - start).days > 400:
            raise bad_request("区间不能超过 400 天")
        return start, end, f"{start:%Y-%m-%d}~{end:%Y-%m-%d}"
    try:
        w = week or last_week()
        s, e = week_range(w)
    except (ValueError, IndexError) as exc:
        raise bad_request("周格式应为 2026-W42") from exc
    return s, e, w


def project_detail(db: Session, code: str, start: date, end: date, cal: dict, codes=None) -> dict:
    codes = codes or [code]
    exps = db.scalars(select(BizExpenseReport).where(BizExpenseReport.project_code.in_(codes),
                                                     BizExpenseReport.status == 4, BizExpenseReport.expense_date <= end)
                      .order_by(BizExpenseReport.expense_date.desc(), BizExpenseReport.id.desc())).all()
    costs = db.scalars(select(BizProjectCost).where(BizProjectCost.project_code.in_(codes), BizProjectCost.cost_date <= end)
                       .order_by(BizProjectCost.cost_date.desc())).all()
    historical_expenses = [c for c in costs if c.cost_type == "历史报销"]
    costs = [c for c in costs if c.cost_type != "历史报销"]
    cons = db.scalars(select(BizContract).where(BizContract.project_code.in_(codes), BizContract.is_deleted.is_(False),
                                                BizContract.sign_date <= end).order_by(BizContract.sign_date)).all()
    received = dict(db.execute(select(BizPayment.contract_id, func.sum(BizPayment.amount)).where(
        BizPayment.contract_id.in_([c.id for c in cons] or [0]), BizPayment.payment_date <= end)
        .group_by(BizPayment.contract_id)).all())
    lemon = []
    if "lemon_aux" in cal.get("cost_sources", []):
        lemon = db.scalars(select(FinVoucher).where(FinVoucher.aux_project.in_(codes), FinVoucher.direction == 1,
                                                    FinVoucher.subject_code.like("5%"), FinVoucher.voucher_date <= end)
                           .order_by(FinVoucher.voucher_date.desc())).all()
    inp = lambda d: start <= d <= end  # noqa: E731
    return {
        "expenses": [{"approval_no": e.approval_no, "expense_date": e.expense_date, "approved_at": e.approved_at,
                      "applicant": e.applicant.name if e.applicant else "", "dept_code": e.dept_code,
                      "category": e.category, "reason": e.reason, "amount": e.amount, "in_period": inp(e.expense_date)}
                     for e in exps] + [{"approval_no": "历史凭证", "expense_date": c.cost_date, "approved_at": None,
                         "applicant": "", "dept_code": None, "category": "历史报销", "reason": c.summary,
                         "amount": c.amount, "in_period": inp(c.cost_date)} for c in historical_expenses],
        "costs": [{"id": c.id, "cost_date": c.cost_date, "cost_type": c.cost_type, "summary": c.summary,
                   "amount": c.amount, "in_period": inp(c.cost_date)} for c in costs],
        "contracts": [{"contract_no": c.contract_no, "name": c.name, "sign_date": c.sign_date, "amount": c.amount,
                       "status": c.status, "counted": c.status != 3, "received": received.get(c.id, 0),
                       "in_period": inp(c.sign_date)} for c in cons],
        "lemon": [{"voucher_no": v.voucher_no, "voucher_date": v.voucher_date, "subject": f"{v.subject_code} {v.subject_name}",
                   "summary": v.summary, "amount": v.amount, "in_period": inp(v.voucher_date)} for v in lemon],
    }


@router.get("/profit")
def profit(week: str | None = None, start: date | None = None, end: date | None = None,
           project_code: str | None = None, include_detail: bool = False,
           p: Principal = ProfitMenu, db: Session = Depends(get_db)):
    _profit_principal(p)
    s, e, label = _range(week, start, end)
    scope = _scope_codes(p, db)
    codes = profit_service.scoped_project_codes(db, project_code, scope) if project_code else scope
    if project_code and scope is not None and project_code not in scope:
        raise forbidden("无权查看该项目")
    pr = profit_service.project_profit(db, s, e, codes)
    th = rs.alert_margin(db)
    rows = []
    for r in pr["rows"]:
        row = {**r, "line_name": rs.LINES.get(r["business_line"], r["business_line"]),
               "status_name": rs.PROJECT_STATUS.get(r["status"], ""),
               "period_gross_profit": r["period_income"] - r["period_cost"],
               "low_margin": r["gross_margin"] is not None and r["gross_margin"] < th}
        if include_detail:
            row["detail"] = project_detail(db, r["project_code"], s, e, pr["caliber"], r.get("group_codes"))
        rows.append(row)
    t = pr["totals"]
    out = {"period": label, "week": None if (start and end) else label, "start": s, "end": e,
           "caliber": pr["caliber"], "caliber_lines": rs.caliber_lines(db, "weekly_profit"), "alert_margin": th,
           "rows": rows, "totals": t, "public_expense": pr["public_expense"],
           "public_detail": rs.public_expense_detail(db, s, e),
           "kpi": {"income": t["income"], "cost": t["cost"], "gross_profit": t["gross_profit"],
                   "gross_margin": t["gross_margin"], "period_cost": t["period_cost"],
                   "period_income": t["period_income"],
                   "period_expense": sum((r["period_expense"] for r in rows), rs.ZERO),
                   "public_expense": pr["public_expense"], "low_margin_count": sum(1 for r in rows if r["low_margin"])},
           "report": None}
    if not (start and end) and not project_code:
        r = rs.latest(db, "weekly_profit", label)
        if r:
            out["report"] = {"id": r.id, "version": r.version, "created_at": r.created_at,
                             "files": [file_brief(f) for f in rs.report_files(db, r)]}
    return ok(rs._keep_precision(out))


@router.get("/profit/trend")
def profit_trend(week: str | None = None, weeks: int = Query(12, ge=2, le=26), p: Principal = ProfitMenu,
                 db: Session = Depends(get_db)):
    _profit_principal(p)
    s0, _ = _range(week, None, None)[:2]
    scope = _scope_codes(p, db)
    out = []
    for i in range(weeks - 1, -1, -1):
        ws = s0 - timedelta(weeks=i)
        s, e = week_range(ref=ws)
        pr = profit_service.project_profit(db, s, e, scope)
        t = pr["totals"]
        pg = t["period_income"] - t["period_cost"]
        out.append({"week": week_no(s), "start": s, "end": e, "income": t["income"], "cost": t["cost"],
                    "gross_profit": t["gross_profit"], "gross_margin": t["gross_margin"],
                    "period_income": t["period_income"], "period_cost": t["period_cost"], "period_gross_profit": pg,
                    "public_expense": pr["public_expense"]})
    return ok(rs._keep_precision({"weeks": out}))


@router.get("/profit/{project_code}")
def profit_project(project_code: str, week: str | None = None, start: date | None = None, end: date | None = None,
                   p: Principal = ProfitMenu, db: Session = Depends(get_db)):
    """单项目下钻：归集报销清单、采购成本清单、合同清单（及柠檬云辅助核算，口径启用时）。"""
    _profit_principal(p)
    scope = _scope_codes(p, db)
    if scope is not None and project_code not in scope:
        raise forbidden("无权查看该项目")
    proj = db.scalar(select(BizProject).where(BizProject.project_code == project_code))
    if not proj:
        raise not_found("项目")
    s, e, label = _range(week, start, end)
    codes = profit_service.scoped_project_codes(db, project_code, scope)
    pr = profit_service.project_profit(db, s, e, codes)
    row = pr["rows"][0] if pr["rows"] else None
    return ok(rs._keep_precision({"project_code": project_code, "name": proj.name, "period": label, "start": s,
                                  "end": e, "profit": row, "detail": project_detail(db, project_code, s, e, pr["caliber"], codes)}))


# ---------------------------------------------------------------- 详情 / 历史版本
@router.get("/project-sources")
def project_sources(project_code: str | None = None, contract_id: int | None = None,
                    period: str | None = Query(None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
                    page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                    p: Principal = Depends(require_menu("profit", "board")), db: Session = Depends(get_db)):
    from app.services import project_report_service as source
    return ok(source.list_reports(db, p, project_code=project_code, contract_id=contract_id, period=period,
                                  page=page, size=size))


@router.get("/project-sources/{source_id}")
def project_source(source_id: int, p: Principal = Depends(require_menu("profit", "board")),
                   db: Session = Depends(get_db)):
    from app.services import project_report_service as source
    return ok(source.report_view(db, source.get_report(db, p, source_id), detail=True))


@router.get("/{report_id}")
def detail(report_id: int, p: Principal = Reports, db: Session = Depends(get_db)):
    r = db.get(RptReport, report_id)
    if not r:
        raise not_found("报表")
    _check_type(p, r.report_type)
    hist = db.scalars(select(RptReport).where(RptReport.report_type == r.report_type, RptReport.period == r.period)
                      .order_by(RptReport.version.desc())).all()
    names = _names(db, hist)
    spec = rs.REPORT_TYPES.get(r.report_type)
    return ok({**_brief(db, r, len(hist), names), "metrics": r.metrics,
               "caliber": (r.metrics or {}).get("_meta", {}).get("caliber") or (spec and rs.caliber_lines(db, r.report_type)),
               "history": [_brief(db, h, None, names) for h in hist]})


@router.get("/{report_id}/versions")
def versions(report_id: int, p: Principal = Reports, db: Session = Depends(get_db)):
    r = db.get(RptReport, report_id)
    if not r:
        raise not_found("报表")
    _check_type(p, r.report_type)
    hist = db.scalars(select(RptReport).where(RptReport.report_type == r.report_type, RptReport.period == r.period)
                      .order_by(RptReport.version.desc())).all()
    names = _names(db, hist)
    return ok([_brief(db, h, None, names) for h in hist])
