"""财务数据：柠檬云集成（FR-08 双通道同步/对账/一致性/供数）、科目余额表、税务申报归集（FR-09-05）、项目采购成本登记。"""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op, snapshot
from app.core.db import get_db, get_or_404
from app.core.deletion import DeletionIn, verify_deletion
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, conflict, forbidden, ok, page_data
from app.core.storage import file_brief
from app.core.timeutil import today
from app.models import BizProject, BizProjectCost, FinVoucher, SyncLemonBatch, SyncTaxFiling, SysFile, SysUser
from app.services import lemoncloud_service, tax_service

router = APIRouter(prefix="/finance-data", tags=["finance_data"])
Menu = Depends(require_menu("finance_data"))
COST_TYPES = ["采购", "分包", "人工", "其他"]
COST_FIELDS = ["project_code", "cost_date", "cost_type", "amount", "summary", "bank_flow_id"]


def _need(p: Principal, b: str):
    if not p.can(b):
        raise forbidden("无此操作权限")


def _period(v: str | None) -> str:
    v = v or f"{today():%Y-%m}"
    if len(v) != 7 or v[4] != "-":
        raise bad_request("期间格式应为 YYYY-MM")
    return v


# ---------------- 柠檬云同步 ----------------
@router.get("/lemon/batches")
def batches(period: str = "", channel: str = "", page: int = 1, page_size: int = 20, p: Principal = Menu,
            db: Session = Depends(get_db)):
    q = select(SyncLemonBatch)
    if period:
        q = q.where(SyncLemonBatch.period == period)
    if channel:
        q = q.where(SyncLemonBatch.channel == channel)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(SyncLemonBatch.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    from app.core.config import settings
    return ok(page_data([lemoncloud_service.batch_row(db, b) for b in rows], total, page, page_size,
                        mode=settings.source_mode("lemoncloud"), can_sync=p.can("finance:sync")))


@router.get("/lemon/batches/{batch_id}")
def batch_detail(batch_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    b = get_or_404(db, SyncLemonBatch, batch_id, "同步批次")
    return ok({**lemoncloud_service.batch_row(db, b), "diffs": b.diffs, "errors": b.errors})


class SyncBody(BaseModel):
    period: str | None = None


@router.post("/lemon/sync")
def sync(body: SyncBody, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "finance:sync")
    b = lemoncloud_service.sync_api(db, _period(body.period))
    log_op(db, p.id, "lemon.sync", "lemon_batch", b.id, after={"batch_no": b.batch_no, "status": b.reconcile_status},
           ip=client_ip(request))
    db.commit()
    return ok({**lemoncloud_service.batch_row(db, b), "diffs": b.diffs, "errors": b.errors},
              message=f"同步完成：{b.voucher_count} 张凭证，对账{'通过' if b.reconcile_status == 'pass' else '存在差异'}")


@router.post("/lemon/upload")
async def upload(request: Request, file: UploadFile = File(...), period: str = Form(""), p: Principal = Menu,
                 db: Session = Depends(get_db)):
    _need(p, "finance:sync")
    data = await file.read()
    if not data:
        raise bad_request("文件为空")
    b, res = lemoncloud_service.sync_file(db, data, file.filename or "vouchers.xlsx", uploader_id=p.id,
                                          period=period or None)
    log_op(db, p.id, "lemon.upload", "lemon_batch", b.id,
           after={"batch_no": b.batch_no, "filename": file.filename, "status": b.reconcile_status,
                  "errors": len(b.errors or [])}, ip=client_ip(request))
    db.commit()
    return ok({**lemoncloud_service.batch_row(db, b), "diffs": b.diffs, "errors": b.errors,
               "total_rows": res["total_rows"], "parsed_rows": len(res["vouchers"]),
               "parse_rate": round(len(res["vouchers"]) / res["total_rows"], 4) if res["total_rows"] else 0})


@router.post("/lemon/batches/{batch_id}/reconcile")
def re_reconcile(batch_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "finance:sync")
    b = lemoncloud_service.rerun_reconcile(db, get_or_404(db, SyncLemonBatch, batch_id, "同步批次"))
    db.commit()
    return ok({**lemoncloud_service.batch_row(db, b), "diffs": b.diffs, "errors": b.errors})


@router.get("/lemon/consistency")
def consistency(period: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(lemoncloud_service.consistency(db, _period(period)))


@router.get("/lemon/vouchers")
def vouchers(period: str = "", subject: str = "", project: str = "", keyword: str = "", page: int = 1,
             page_size: int = 50, p: Principal = Menu, db: Session = Depends(get_db)):
    from app.core.timeutil import month_range
    s, e = month_range(_period(period))
    q = select(FinVoucher).where(FinVoucher.voucher_date.between(s, e))
    if subject:
        q = q.where(FinVoucher.subject_code.startswith(subject))
    if project:
        q = q.where(FinVoucher.aux_project == project)
    if keyword:
        q = q.where(or_(FinVoucher.summary.contains(keyword), FinVoucher.voucher_no.contains(keyword)))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(FinVoucher.voucher_date, FinVoucher.voucher_no, FinVoucher.line_no)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return ok(page_data([{"id": v.id, "voucher_no": v.voucher_no, "line_no": v.line_no, "voucher_date": v.voucher_date,
                          "summary": v.summary, "subject_code": v.subject_code, "subject_name": v.subject_name,
                          "debit": v.amount if v.direction == 1 else None, "credit": v.amount if v.direction == 2 else None,
                          "aux_project": v.aux_project, "sync_batch": v.sync_batch, "channel": v.channel} for v in rows],
                        total, page, page_size))


@router.get("/lemon/supply")
def supply(period: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(lemoncloud_service.supply(db, _period(period)))


@router.get("/balances")
def balances(period: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    per = _period(period)
    rows = lemoncloud_service.balances(db, per)
    tot = {k: sum((r[k] for r in rows), Decimal(0)) for k in ("opening_debit", "opening_credit", "debit_amount",
                                                               "credit_amount", "debit_balance", "credit_balance")}
    periods = list(db.scalars(select(SyncLemonBatch.period).distinct().order_by(SyncLemonBatch.period.desc())))
    return ok({"period": per, "items": rows, "total": tot, "periods": periods,
               "balanced": tot["debit_balance"] == tot["credit_balance"]})


# ---------------- 税务申报 ----------------
@router.get("/tax")
def tax_list(period: str = "", tax_type: str = "", year: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    q = select(SyncTaxFiling)
    if period:
        q = q.where(SyncTaxFiling.period == period)
    if year:
        q = q.where(SyncTaxFiling.period.startswith(year))
    if tax_type:
        q = q.where(SyncTaxFiling.tax_type == tax_type)
    rows = db.scalars(q.order_by(SyncTaxFiling.period.desc(), SyncTaxFiling.tax_type)).all()
    users = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    items = [{"id": t.id, "period": t.period, "tax_type": t.tax_type, "taxable_amount": t.taxable_amount,
              "payable_amount": t.payable_amount, "paid_amount": t.paid_amount, "filing_date": t.filing_date,
              "uploader": users.get(t.uploader_id), "updated_at": t.updated_at,
              "file": file_brief(db.get(SysFile, t.file_id)) if t.file_id else None} for t in rows]
    by_period: dict[str, Decimal] = {}
    for t in rows:
        by_period[t.period] = by_period.get(t.period, Decimal(0)) + t.payable_amount
    return ok({"items": items, "tax_types": tax_service.TAX_TYPES, "can_upload": p.can("tax:upload"),
               "by_period": [{"period": k, "payable": v} for k, v in sorted(by_period.items())]})


@router.post("/tax/upload")
async def tax_upload(request: Request, file: UploadFile = File(...), period: str = Form(""), tax_type: str = Form(""),
                     payable_amount: str = Form(""), p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "tax:upload")
    data = await file.read()
    if not data:
        raise bad_request("文件为空")
    ov = {"period": period or None, "tax_type": tax_type or None,
          "payable_amount": Decimal(payable_amount) if payable_amount else None}
    r = tax_service.register(db, data, file.filename or "tax.xlsx", p.id, overrides=ov)
    if not r["registered"]:
        db.rollback()
        return ok(r, message="未登记：申报表解析失败")
    log_op(db, p.id, "tax.upload", "tax_filing", None,
           after={"filename": file.filename, "registered": [(x["period"], x["tax_type"], x["payable_amount"]) for x in r["registered"]]},
           ip=client_ip(request))
    db.commit()
    return ok(r, message=f"已登记 {len(r['registered'])} 张申报表")


# ---------------- 项目采购成本（人工登记） ----------------
@router.get("/costs")
def costs(project: str = "", cost_type: str = "", start: date | None = None, end: date | None = None, keyword: str = "",
          page: int = 1, page_size: int = 20, p: Principal = Menu, db: Session = Depends(get_db)):
    q = select(BizProjectCost)
    if project:
        q = q.where(BizProjectCost.project_code == project)
    if cost_type:
        q = q.where(BizProjectCost.cost_type == cost_type)
    if start:
        q = q.where(BizProjectCost.cost_date >= start)
    if end:
        q = q.where(BizProjectCost.cost_date <= end)
    if keyword:
        q = q.where(BizProjectCost.summary.contains(keyword))
    sub = q.subquery()
    total, amt = db.execute(select(func.count(), func.coalesce(func.sum(sub.c.amount), 0))).one()
    rows = db.scalars(q.order_by(BizProjectCost.cost_date.desc(), BizProjectCost.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    projects = dict(db.execute(select(BizProject.project_code, BizProject.name)).all())
    users = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    return ok(page_data([{"id": c.id, "project_code": c.project_code, "project_name": projects.get(c.project_code),
                          "cost_date": c.cost_date, "cost_type": c.cost_type, "amount": c.amount, "summary": c.summary,
                          "bank_flow_id": c.bank_flow_id, "created_by": users.get(c.created_by)} for c in rows],
                        total, page, page_size, amount=amt, cost_types=COST_TYPES, can_write=p.can("cost:write")))


class CostBody(BaseModel):
    project_code: str
    cost_date: date
    cost_type: str = "采购"
    amount: Decimal = Field(gt=0)
    summary: str | None = None


def _check_cost(db: Session, body: CostBody):
    if not db.scalar(select(BizProject.id).where(BizProject.project_code == body.project_code, BizProject.is_deleted.is_(False))):
        raise bad_request("项目不存在")
    if body.cost_type not in COST_TYPES:
        raise bad_request(f"成本类型应为 {'/'.join(COST_TYPES)}")


def _historical_cost_guard(db, cost_id):
    from app.models import FinProjectReportEntry
    if db.scalar(select(FinProjectReportEntry.id).where(FinProjectReportEntry.cost_id == cost_id).limit(1)):
        raise conflict("该成本来自历史项目报表，须连同原凭证核对更正，不能单独修改或删除来源记录")


@router.post("/costs")
def cost_create(body: CostBody, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "cost:write")
    _check_cost(db, body)
    c = BizProjectCost(project_code=body.project_code, cost_date=body.cost_date, cost_type=body.cost_type,
                       amount=body.amount.quantize(Decimal("0.01")), summary=body.summary, created_by=p.id)
    db.add(c)
    db.flush()
    log_op(db, p.id, "cost.create", "project_cost", c.id, None, snapshot(c, COST_FIELDS), client_ip(request))
    db.commit()
    return ok({"id": c.id}, message="已登记")


@router.put("/costs/{cost_id}")
def cost_update(cost_id: int, body: CostBody, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "cost:write")
    _check_cost(db, body)
    c = get_or_404(db, BizProjectCost, cost_id, "项目成本")
    _historical_cost_guard(db, c.id)
    if c.bank_flow_id and (c.amount != body.amount.quantize(Decimal("0.01"))):
        raise conflict("已关联银行流水的成本不可修改金额，请先在流水核对中撤销")
    before = snapshot(c, COST_FIELDS)
    c.project_code, c.cost_date, c.cost_type, c.amount, c.summary = (body.project_code, body.cost_date, body.cost_type,
                                                                     body.amount.quantize(Decimal("0.01")), body.summary)
    if c.bank_flow_id:  # 同步日记账与流水的项目口径
        from app.models import FinBankFlow, FinCashJournal
        for j in db.scalars(select(FinCashJournal).where(FinCashJournal.bank_flow_id == c.bank_flow_id)):
            j.project_code = j.biz_ref = c.project_code
        fl = db.get(FinBankFlow, c.bank_flow_id)
        if fl:
            fl.biz_ref = c.project_code
    log_op(db, p.id, "cost.update", "project_cost", c.id, before, snapshot(c, COST_FIELDS), client_ip(request))
    db.commit()
    return ok({"id": c.id}, message="已更新")


@router.delete("/costs/{cost_id}")
def cost_delete(cost_id: int, body: DeletionIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "cost:write")
    verify_deletion(db, body)
    c = get_or_404(db, BizProjectCost, cost_id, "项目成本")
    _historical_cost_guard(db, c.id)
    if c.bank_flow_id:
        raise conflict("已关联银行流水的成本不可删除，请先在流水核对中撤销")
    log_op(db, p.id, "cost.delete", "project_cost", c.id, snapshot(c, COST_FIELDS), None, client_ip(request))
    db.delete(c)
    db.commit()
    return ok(message="已删除")
