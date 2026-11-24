from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deletion import verify_deletion
from app.core.deps import Principal, require_menu
from app.core.response import bad_request, forbidden, ok, page_data
from app.core.timeutil import month_of, month_range, today
from app.models import BizContract, FinReceipt, FinReceiptAllocation, FinReceivablesSourceRow, FinSalesInvoice, SyncJobLog
from app.services import receivables_fields as f, receivables_service as svc, receivables_sync as sync

router = APIRouter(prefix="/receivables", tags=["开票与收款"])


def viewer(p: Principal = Depends(require_menu("receivables"))):
    if p.data_scope != "all":
        raise forbidden()
    return p


def _role(button):
    def require(p: Principal = Depends(viewer)):
        if not p.can(button):
            raise forbidden("无此操作权限")
        return p
    return Depends(require)


InvoiceWrite = _role("receivables:invoice")
ReceiptWrite = _role("receivables:receipt")
Correct = _role("receivables:correct")
Source = _role("receivables:source")
Viewer = Depends(viewer)
DB = Depends(get_db)


def _range(period):
    try:
        return month_range(period or month_of(today()))
    except ValueError:
        raise bad_request("月份格式应为 YYYY-MM") from None


def _save(db, result):
    db.commit()
    svc.invalidate()
    return ok(result)


def _page(db, q, sort, page, size):
    count = db.scalar(select(func.count()).select_from(q.subquery()))
    return count, db.execute(q.order_by(*sort).offset((page - 1) * size).limit(size))


@router.get("/summary")
def summary(period: str = "", p: Principal = Viewer, db: Session = DB):
    start, end = _range(period)
    return ok(svc.summary(db, start, min(end, today())))


@router.get("/invoices")
def invoices(period: str = "", keyword: str = Query("", max_length=120), contract_id: int | None = None,
             without_contract: bool = False, customer: str = Query("", max_length=128),
             state: Literal["", "pending", "issued", "void", "cancelled", "outstanding"] = "",
             page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100), p: Principal = Viewer, db: Session = DB):
    m = FinSalesInvoice
    allocated = svc._allocated_query().where(FinReceiptAllocation.invoice_id == m.id).correlate(m).scalar_subquery()
    q = select(m, BizContract.contract_no, func.coalesce(allocated, 0)).outerjoin(BizContract, BizContract.id == m.contract_id)
    if state == "outstanding":
        q = q.where(m.state == "issued", m.issued_amount - m.reduced_amount > func.coalesce(allocated, 0))
    elif state:
        q = q.where(m.state == state)
    if period:
        q = q.where(m.issued_on.between(*_range(period)))
    if keyword:
        q = q.where(or_(*(col.icontains(keyword, autoescape=True) for col in
                          (m.invoice_no, m.customer, m.approval_no, BizContract.contract_no))))
    if contract_id:
        q = q.where(m.contract_id == contract_id)
    if without_contract:
        q = q.where(m.contract_id.is_(None))
    if customer:
        q = q.where(m.customer == customer)
    count, rows = _page(db, q, [m.id.desc()], page, size)
    return ok(page_data([svc.invoice_row(r, amt, cno) for r, cno, amt in rows], count, page, size))


@router.get("/receipts")
def receipts(period: str = "", keyword: str = Query("", max_length=120),
             kind: Literal["", "contract", "unmatched", "other", "non_contract"] = "",
             page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100), p: Principal = Viewer, db: Session = DB):
    m = FinReceipt
    allocated = select(func.sum(FinReceiptAllocation.amount)).where(FinReceiptAllocation.receipt_id == m.id).scalar_subquery()
    q = select(m, BizContract.contract_no, func.coalesce(allocated, 0)).outerjoin(BizContract, BizContract.id == m.contract_id)
    if period:
        q = q.where(m.received_on.between(*_range(period)))
    if kind:
        q = q.where(m.kind != "contract" if kind == "non_contract" else m.kind == kind)
    if keyword:
        q = q.where(or_(*(col.icontains(keyword, autoescape=True) for col in
                          (m.reference, m.payer, m.approval_no, BizContract.contract_no))))
    count, rows = _page(db, q, [m.id.desc()], page, size)
    return ok(page_data([svc.receipt_row(r, amt if r.state == "confirmed" else svc.ZERO, cno)
                         for r, cno, amt in rows], count, page, size))


@router.post("/invoices")
def record_invoice(body: f.InvoiceRecord, p: Principal = InvoiceWrite, db: Session = DB):
    row = svc.record_invoice(db, **body.model_dump(), actor=p.id)
    return _save(db, {"id": row.id})


@router.get("/invoices/{ident}")
def invoice_detail(ident: int, p: Principal = Viewer, db: Session = DB):
    row = svc.get(db, FinSalesInvoice, ident, "发票")
    allocated = db.scalar(svc._allocated_query().where(FinReceiptAllocation.invoice_id == ident)) or svc.ZERO
    contract_no = db.scalar(select(BizContract.contract_no).where(BizContract.id == row.contract_id)) if row.contract_id else None
    return ok(svc.invoice_row(row, allocated, contract_no))


@router.post("/invoices/{ident}/issue")
def issue(ident: int, body: f.Issue, p: Principal = InvoiceWrite, db: Session = DB):
    svc.lock(db)
    row = svc.get(db, FinSalesInvoice, ident, "开票申请")
    svc.issue(db, row, **body.model_dump(), actor=p.id)
    return _save(db, {"id": row.id})


@router.post("/invoices/{ident}/correct")
def correct(ident: int, body: f.Correction, p: Principal = Correct, db: Session = DB):
    svc.lock(db)
    row = svc.get(db, FinSalesInvoice, ident, "发票")
    svc.correct_invoice(db, row, **body.model_dump(), actor=p.id)
    return _save(db, {"id": row.id})


@router.post("/receipts")
def record_receipt(body: f.Receipt, p: Principal = ReceiptWrite, db: Session = DB):
    row = svc.receipt(db, **body.model_dump(), actor=p.id)
    return _save(db, {"id": row.id, "payment_id": row.payment_id})


@router.get("/receipts/{ident}")
def receipt_detail(ident: int, p: Principal = Viewer, db: Session = DB):
    row = svc.get(db, FinReceipt, ident, "到账记录")
    allocated = db.scalar(select(func.sum(FinReceiptAllocation.amount)).where(FinReceiptAllocation.receipt_id == ident)) or svc.ZERO
    contract_no = db.scalar(select(BizContract.contract_no).where(BizContract.id == row.contract_id)) if row.contract_id else None
    return ok(svc.receipt_row(row, allocated if row.state == "confirmed" else svc.ZERO, contract_no))


@router.post("/receipts/{ident}/assign")
def assign(ident: int, body: f.Assign, p: Principal = ReceiptWrite, db: Session = DB):
    svc.lock(db)
    row = svc.get(db, FinReceipt, ident, "到账记录")
    svc.assign_receipt(db, row, body.contract_no, p.id)
    return _save(db, {"id": row.id, "payment_id": row.payment_id})


@router.get("/receipts/{ident}/allocations")
def allocations(ident: int, p: Principal = Viewer, db: Session = DB):
    svc.get(db, FinReceipt, ident, "到账记录")
    rows = db.execute(select(FinReceiptAllocation, FinSalesInvoice.invoice_no).join(
        FinSalesInvoice, FinSalesInvoice.id == FinReceiptAllocation.invoice_id).where(FinReceiptAllocation.receipt_id == ident))
    return ok([{"invoice_id": row.invoice_id, "invoice_no": number, "amount": row.amount} for row, number in rows])


@router.put("/receipts/{ident}/allocations")
def allocate(ident: int, body: f.Allocations, p: Principal = ReceiptWrite, db: Session = DB):
    svc.lock(db)
    row = svc.get(db, FinReceipt, ident, "到账记录")
    svc.allocate(db, row, body.items, p.id)
    return _save(db, {"id": row.id})


@router.post("/receipts/{ident}/void")
def void(ident: int, body: f.Void, p: Principal = Correct, db: Session = DB):
    verify_deletion(db, body)
    svc.lock(db)
    row = svc.get(db, FinReceipt, ident, "到账记录")
    svc.void_receipt(db, row, body.reason, p.id)
    return _save(db, {"id": row.id})


SourceKind = Literal["invoice", "receipt"]


@router.get("/sources/template/{kind}")
def template(kind: SourceKind, p: Principal = Source):
    return Response("\ufeff" + ",".join(sync.TEMPLATE[kind]) + "\r\n", media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{kind}-template.csv"'})


@router.post("/sources/upload/{kind}")
def upload(kind: SourceKind, file: UploadFile = File(...), p: Principal = Source, db: Session = DB):
    limit = 2 * 1024 * 1024
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise bad_request("每批 CSV 不能超过 2 MB")
    changed = sync.stage_csv(db, kind, content)
    db.commit()
    return ok({"changed": changed})


@router.get("/sources")
def sources(state: Literal["", "pending", "applied", "error", "changed", "missing"] = "",
            page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100), p: Principal = Source, db: Session = DB):
    m = FinReceivablesSourceRow
    q = select(m)
    if state:
        q = q.where(m.state == state)
    count, rows = _page(db, q, [m.updated_at.desc(), m.id.desc()], page, size)
    keys = ("id", "source_key", "record_key", "kind", "state", "version", "payload", "error", "target_id", "updated_at")
    return ok(page_data([{k: getattr(r, k) for k in keys} for r in rows.scalars()], count, page, size))


@router.get("/sources/status")
def source_status(p: Principal = Source, db: Session = DB):
    job = db.scalar(select(SyncJobLog).where(SyncJobLog.job_name == "receivables_source_sync",
                 SyncJobLog.status != "skipped").order_by(SyncJobLog.id.desc()).limit(1))
    return ok({"status": job.status, "run_at": job.run_at, "message": job.message} if job else None)


@router.get("/sources/{ident}")
def source_detail(ident: int, p: Principal = Source, db: Session = DB):
    row = svc.get(db, FinReceivablesSourceRow, ident, "来源记录")
    keys = ("id", "source_key", "record_key", "kind", "state", "version", "payload", "error", "target_id", "updated_at")
    return ok({key: getattr(row, key) for key in keys})


class SourceVersion(f.Strict):
    version: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceSelection(SourceVersion):
    id: f.ID


class SourceBatch(f.Strict):
    items: list[SourceSelection] = Field(min_length=1, max_length=100)


@router.post("/sources/apply-batch")
def apply_batch(body: SourceBatch, p: Principal = Source, db: Session = DB):
    svc.lock(db)
    results = []
    from app.core.response import BizError
    for item in body.items:
        try:
            row = svc.get(db, FinReceivablesSourceRow, item.id, "来源记录")
            sync.apply_row(db, row, item.version, p.id)
            results.append({"id": row.id, "state": row.state, "error": row.error})
        except BizError as exc:
            results.append({"id": item.id, "state": "stale", "error": exc.message})
    return _save(db, results)


@router.post("/sources/{ident}/apply")
def apply(ident: int, body: SourceVersion, p: Principal = Source, db: Session = DB):
    svc.lock(db)
    row = svc.get(db, FinReceivablesSourceRow, ident, "来源记录")
    sync.apply_row(db, row, body.version, p.id)
    return _save(db, {"id": row.id, "state": row.state, "target_id": row.target_id, "error": row.error})
