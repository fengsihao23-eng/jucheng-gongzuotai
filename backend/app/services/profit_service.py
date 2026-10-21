"""项目利润口径（共享，PRD 3.3 / 3.5，字典 caliber.profit 可调）。

收入 income_basis：
  contract  — 截至期末已签合同额（默认）
  payment   — 截至期末已到账回款
成本 cost_sources（可多选）：
  expense      — 已归集到项目的报销（status=4，按费用发生日）
  project_cost — 人工标记的采购/分包成本（按成本日期）
  lemon_aux    — 柠檬云凭证项目辅助核算的成本类科目（5 开头借方）；与前两项可能重复，默认不启用
公共费用（project_code 为空）不摊入项目，单列为「管理费用-公共」。

返回每个项目的「累计至期末」与「本期发生」两组数，周利润表/看板/MCP 共用。
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BizContract, BizExpenseReport, BizPayment, BizProject, BizProjectCost, FinVoucher
from app.services import dict_service

ZERO = Decimal("0")
DEFAULT_CALIBER = {"income_basis": "contract", "public_expense": "exclude", "cost_sources": ["expense", "project_cost"]}


def caliber(db: Session) -> dict:
    c = {**DEFAULT_CALIBER, **(dict_service.get(db, "caliber", "profit") or {})}
    return c


def _sum_by_project(db: Session, col_project, col_amount, col_date, start: date | None, end: date, *where) -> dict:
    q = select(col_project, func.coalesce(func.sum(col_amount), 0)).where(col_project.is_not(None), col_date <= end, *where)
    if start:
        q = q.where(col_date >= start)
    return {k: Decimal(v) for k, v in db.execute(q.group_by(col_project)).all()}


def _income(db: Session, basis: str, start: date | None, end: date) -> dict:
    if basis == "payment":
        q = select(BizContract.project_code, func.sum(BizPayment.amount)).join(BizPayment.contract) \
            .where(BizPayment.payment_date <= end, BizContract.is_deleted.is_(False), BizContract.project_code.is_not(None))
        if start:
            q = q.where(BizPayment.payment_date >= start)
        return {k: Decimal(v) for k, v in db.execute(q.group_by(BizContract.project_code)).all()}
    return _sum_by_project(db, BizContract.project_code, BizContract.amount, BizContract.sign_date, start, end,
                           BizContract.is_deleted.is_(False), BizContract.status != 3)


def _costs(db: Session, sources: list[str], start: date | None, end: date) -> dict[str, dict]:
    out: dict[str, dict] = {}

    def add(key, d):
        for code, v in d.items():
            out.setdefault(code, {"expense": ZERO, "project_cost": ZERO, "lemon_aux": ZERO})[key] += v

    if "expense" in sources:
        add("expense", _sum_by_project(db, BizExpenseReport.project_code, BizExpenseReport.amount,
                                       BizExpenseReport.expense_date, start, end, BizExpenseReport.status == 4))
    if "project_cost" in sources:
        add("project_cost", _sum_by_project(db, BizProjectCost.project_code, BizProjectCost.amount,
                                            BizProjectCost.cost_date, start, end))
    if "lemon_aux" in sources:
        add("lemon_aux", _sum_by_project(db, FinVoucher.aux_project, FinVoucher.amount, FinVoucher.voucher_date, start,
                                         end, FinVoucher.direction == 1, FinVoucher.subject_code.like("5%")))
    return out


def project_profit(db: Session, start: date, end: date, project_codes: list[str] | None = None,
                   include_closed: bool = True) -> dict:
    """返回 {caliber, rows:[...], totals, public_expense}。rows 每项含累计与本期。"""
    cal = caliber(db)
    q = select(BizProject).where(BizProject.is_deleted.is_(False))
    if project_codes:
        q = q.where(BizProject.project_code.in_(project_codes))
    if not include_closed:
        q = q.where(BizProject.status.in_([2, 3, 4]))
    projects = db.scalars(q.order_by(BizProject.project_code)).all()
    inc_all, inc_per = _income(db, cal["income_basis"], None, end), _income(db, cal["income_basis"], start, end)
    cost_all, cost_per = _costs(db, cal["cost_sources"], None, end), _costs(db, cal["cost_sources"], start, end)
    rows = []
    for p in projects:
        ca = cost_all.get(p.project_code, {})
        cp = cost_per.get(p.project_code, {})
        income, cost = inc_all.get(p.project_code, ZERO), sum(ca.values(), ZERO)
        if income == 0 and cost == 0:
            continue
        gp = income - cost
        rows.append({
            "project_code": p.project_code, "name": p.name, "business_line": p.business_line, "status": p.status,
            "customer_name": p.customer_name, "is_key": p.is_key,
            "income": income, "cost": cost, "cost_expense": ca.get("expense", ZERO),
            "cost_project": ca.get("project_cost", ZERO), "cost_lemon": ca.get("lemon_aux", ZERO),
            "gross_profit": gp, "gross_margin": (gp / income).quantize(Decimal("0.0001")) if income else None,
            "period_income": inc_per.get(p.project_code, ZERO), "period_cost": sum(cp.values(), ZERO),
            "period_expense": cp.get("expense", ZERO), "period_project_cost": cp.get("project_cost", ZERO),
        })
    t_inc = sum((r["income"] for r in rows), ZERO)
    t_cost = sum((r["cost"] for r in rows), ZERO)
    pub_q = select(func.coalesce(func.sum(BizExpenseReport.amount), 0)).where(
        BizExpenseReport.status == 4, BizExpenseReport.project_code.is_(None),
        BizExpenseReport.expense_date >= start, BizExpenseReport.expense_date <= end)
    return {
        "caliber": cal, "start": start, "end": end, "rows": rows,
        "totals": {"income": t_inc, "cost": t_cost, "gross_profit": t_inc - t_cost,
                   "gross_margin": ((t_inc - t_cost) / t_inc).quantize(Decimal("0.0001")) if t_inc else None,
                   "period_income": sum((r["period_income"] for r in rows), ZERO),
                   "period_cost": sum((r["period_cost"] for r in rows), ZERO)},
        "public_expense": Decimal(db.scalar(pub_q)),
    }
