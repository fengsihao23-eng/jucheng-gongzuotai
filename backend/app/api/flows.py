"""银行流水（FR-09-01~04）：上传导入 + 导入报告、流水列表、核对工作台（智能匹配建议 / 一键确认 / 撤销 / 批量自动核对）。"""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db, get_or_404
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, forbidden, ok, page_data
from app.core.storage import file_brief
from app.models import FinBankFlow, FinBankImport, SysFile, SysUser
from app.services import bankflow_service, reconcile_service

router = APIRouter(prefix="/flows", tags=["flows"])
Menu = Depends(require_menu("flows"))
MAX_UPLOAD = 20 * 1024 * 1024


def _need(p: Principal, button: str):
    if not p.can(button):
        raise forbidden("无此操作权限")


# ---------------- 模板 / 账户 ----------------
@router.get("/templates")
def templates(p: Principal = Menu, db: Session = Depends(get_db)):
    return ok({"templates": [{"key": k, "name": v.get("name", k), "bank": v.get("bank"), "columns": v.get("columns")}
                             for k, v in bankflow_service.templates(db).items()],
               "accounts": [{"name": k, **v} for k, v in bankflow_service.accounts(db).items()]})


@router.get("/accounts")
def accounts(p: Principal = Menu, db: Session = Depends(get_db)):
    rows = db.execute(select(FinBankFlow.bank_account, func.count(),
                             func.count().filter(FinBankFlow.match_status == 0), func.max(FinBankFlow.trade_date))
                      .group_by(FinBankFlow.bank_account).order_by(FinBankFlow.bank_account)).all()
    return ok([{"account": a, "count": n, "unmatched": u, "last_date": d} for a, n, u, d in rows])


# ---------------- 导入 ----------------
@router.post("/import")
async def import_flow(request: Request, file: UploadFile = File(...), template: str = Form(""),
                      account: str = Form(""), p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "flows:import")
    data = await file.read()
    if not data:
        raise bad_request("文件为空")
    if len(data) > MAX_UPLOAD:
        raise bad_request("文件超过 20MB")
    imp = bankflow_service.import_file(db, data, file.filename or "bankflow.xlsx", uploader_id=p.id, source="upload",
                                       template_key=template or None, account=account or None)
    log_op(db, p.id, "flows.import", "bank_import", imp.id,
           after={"filename": imp.filename, "inserted": imp.inserted_rows, "duplicate": imp.duplicate_rows,
                  "errors": imp.error_rows}, ip=client_ip(request))
    db.commit()
    return ok(bankflow_service.import_report(db, imp))


@router.get("/imports")
def imports(page: int = 1, page_size: int = 20, p: Principal = Menu, db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(FinBankImport))
    rows = db.scalars(select(FinBankImport).order_by(FinBankImport.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    users = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    items = []
    for imp in rows:
        r = bankflow_service.import_report(db, imp, with_suggest=False)
        r["uploader"] = users.get(imp.uploader_id)
        r["file"] = file_brief(db.get(SysFile, imp.file_id)) if imp.file_id else None
        items.append(r)
    return ok(page_data(items, total, page, page_size))


@router.get("/imports/{import_id}")
def import_detail(import_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    imp = get_or_404(db, FinBankImport, import_id, "导入记录")
    r = bankflow_service.import_report(db, imp)
    r["file"] = file_brief(db.get(SysFile, imp.file_id)) if imp.file_id else None
    return ok(r)


# ---------------- 列表 ----------------
def _flow_query(account, direction, status, start, end, min_amount, max_amount, keyword, import_id=None):
    q = select(FinBankFlow)
    if account:
        q = q.where(FinBankFlow.bank_account == account)
    if direction:
        q = q.where(FinBankFlow.direction == direction)
    if status not in (None, "", "all"):
        q = q.where(FinBankFlow.match_status == int(status)) if str(status).isdigit() else \
            q.where(FinBankFlow.match_status != 0) if status == "matched" else q
    if start:
        q = q.where(FinBankFlow.trade_date >= start)
    if end:
        q = q.where(FinBankFlow.trade_date <= end)
    if min_amount is not None:
        q = q.where(FinBankFlow.amount >= min_amount)
    if max_amount is not None:
        q = q.where(FinBankFlow.amount <= max_amount)
    if keyword:
        k = keyword.strip()
        q = q.where(or_(FinBankFlow.counterparty.contains(k), FinBankFlow.summary.contains(k), FinBankFlow.biz_ref.contains(k)))
    if import_id:
        q = q.where(FinBankFlow.import_id == import_id)
    return q


@router.get("")
def list_flows(account: str = "", direction: int | None = None, status: str = "all", start: date | None = None,
               end: date | None = None, min_amount: Decimal | None = None, max_amount: Decimal | None = None,
               keyword: str = "", import_id: int | None = None, page: int = 1, page_size: int = 20,
               p: Principal = Menu, db: Session = Depends(get_db)):
    q = _flow_query(account, direction, status, start, end, min_amount, max_amount, keyword, import_id)
    sub = q.subquery()
    total, inc, exp = db.execute(select(func.count(), func.coalesce(func.sum(sub.c.amount).filter(sub.c.direction == 1), 0),
                                        func.coalesce(func.sum(sub.c.amount).filter(sub.c.direction == 2), 0))).one()
    rows = db.scalars(q.order_by(FinBankFlow.trade_date.desc(), FinBankFlow.trade_time.desc().nulls_last(), FinBankFlow.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return ok(page_data([reconcile_service.flow_row(f) for f in rows], total, page, page_size,
                        summary={"income": inc, "expense": exp}))


# ---------------- 核对工作台 ----------------
@router.get("/workbench")
def workbench(account: str = "", direction: int | None = None, keyword: str = "", start: date | None = None,
              end: date | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    q = _flow_query(account, direction, "0", start, end, None, None, keyword)
    flows = db.scalars(q.order_by(FinBankFlow.trade_date.desc(), FinBankFlow.id.desc()).limit(300)).all()
    ctx = reconcile_service.MatchContext(db)
    items, high = [], 0
    for f in flows:
        s = reconcile_service.suggest(db, f, ctx, limit=3)
        row = reconcile_service.flow_row(f)
        row["top"] = s[0] if s else None
        row["suggestion_count"] = len(s)
        if s and s[0]["score"] >= ctx.rule["auto_threshold"]:
            high += 1
        items.append(row)
    total_unmatched = db.scalar(select(func.count()).select_from(FinBankFlow).where(FinBankFlow.match_status == 0))
    return ok({"items": items, "unmatched": total_unmatched, "high_confidence": high,
               "threshold": ctx.rule["auto_threshold"], "can_match": p.can("flows:match")})


@router.get("/{flow_id}")
def flow_detail(flow_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    f = get_or_404(db, FinBankFlow, flow_id, "流水")
    from app.models import BizExpenseReport, BizPayment, BizProjectCost, FinCashJournal
    row = reconcile_service.flow_row(f)
    users = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    row["matched_by_name"] = users.get(f.matched_by)
    j = db.scalar(select(FinCashJournal).where(FinCashJournal.bank_flow_id == f.id))
    row["journal"] = j and {"id": j.id, "journal_date": j.journal_date, "category": j.category, "summary": j.summary,
                            "project_code": j.project_code}
    pay = db.scalar(select(BizPayment).where(BizPayment.bank_flow_id == f.id))
    row["payment"] = pay and {"id": pay.id, "contract_no": pay.contract.contract_no, "customer": pay.contract.customer_name,
                              "amount": pay.amount, "contract_id": pay.contract_id}
    rep = db.scalar(select(BizExpenseReport).where(BizExpenseReport.bank_flow_id == f.id))
    row["expense"] = rep and {"id": rep.id, "approval_no": rep.approval_no, "applicant": rep.applicant.name if rep.applicant else None}
    cost = db.scalar(select(BizProjectCost).where(BizProjectCost.bank_flow_id == f.id))
    row["project_cost"] = cost and {"id": cost.id, "project_code": cost.project_code, "cost_type": cost.cost_type}
    row["suggestions"] = reconcile_service.suggest(db, f) if f.match_status == 0 else []
    return ok(row)


@router.get("/{flow_id}/suggestions")
def suggestions(flow_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    f = get_or_404(db, FinBankFlow, flow_id, "流水")
    return ok(reconcile_service.suggest(db, f))


@router.get("/{flow_id}/candidates")
def candidates(flow_id: int, kind: str, keyword: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    f = get_or_404(db, FinBankFlow, flow_id, "流水")
    return ok(reconcile_service.candidates(db, f, kind, keyword))


class MatchBody(BaseModel):
    kind: str  # payment | expense | project_cost | public | other_income
    target_id: int | None = None
    project_code: str | None = None
    cost_type: str | None = None
    remark: str | None = None


@router.post("/{flow_id}/match")
def match(flow_id: int, body: MatchBody, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "flows:match")
    f = db.scalar(select(FinBankFlow).where(FinBankFlow.id == flow_id).with_for_update())
    if not f:
        raise bad_request("流水不存在")
    r = reconcile_service.confirm(db, f, body.kind, p.id, target_id=body.target_id, project_code=body.project_code,
                                  cost_type=body.cost_type, remark=body.remark)
    log_op(db, p.id, "flows.match", "bank_flow", f.id, {"match_status": 0}, {**body.model_dump(), **r}, client_ip(request))
    db.commit()
    return ok(r, message="核对成功，已登记日记账")


@router.post("/{flow_id}/unmatch")
def unmatch(flow_id: int, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "flows:match")
    f = db.scalar(select(FinBankFlow).where(FinBankFlow.id == flow_id).with_for_update())
    if not f:
        raise bad_request("流水不存在")
    before = {"match_status": f.match_status, "biz_ref": f.biz_ref}
    r = reconcile_service.unmatch(db, f)
    log_op(db, p.id, "flows.unmatch", "bank_flow", f.id, before, r, client_ip(request))
    db.commit()
    return ok(r, message="已撤销核对")


class AutoBody(BaseModel):
    flow_ids: list[int] | None = None
    threshold: int | None = None
    dry_run: bool = False


@router.post("/auto-match")
def auto_match(body: AutoBody, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "flows:match")
    r = reconcile_service.auto_match(db, p.id, flow_ids=body.flow_ids, threshold=body.threshold, dry_run=body.dry_run)
    if not body.dry_run:
        log_op(db, p.id, "flows.auto_match", "bank_flow", None,
               after={"matched": r["matched"], "flow_ids": [x["flow_id"] for x in r["items"]]}, ip=client_ip(request))
        db.commit()
    else:
        db.rollback()
    return ok(r, message=f"自动核对 {r['matched']} 笔")
