"""FR-05-01 合同台账 + FR-05-06 合同归档（原件版本控制、付款条款读取、到账流水自动关联）。"""
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import forbidden, ok
from app.core.timeutil import today
from app.services import contract_service as cs

router = APIRouter(prefix="/contracts", tags=["合同台账"])
Menu = Depends(require_menu("contracts"))


def _filters(business_line: str = "", customer: str = "", status: int | None = None, sign_month: str = "",
             sign_from: date | None = None, sign_to: date | None = None, source_type: int | None = None,
             keyword: str = "", project_code: str = "", paid_month: str = "", owner_id: int | None = None,
             region: str = "") -> dict:
    return {"business_line": business_line, "customer": customer, "status": status, "sign_month": sign_month,
            "sign_from": sign_from, "sign_to": sign_to, "source_type": source_type, "keyword": keyword.strip(),
            "project_code": project_code, "paid_month": paid_month, "owner_id": owner_id, "region": region}


def _need(p: Principal, button: str):
    if not p.can(button):
        raise forbidden("无此操作权限")


@router.get("")
def list_contracts(page: int = 1, page_size: int = 20, f: dict = Depends(_filters), p: Principal = Menu,
                   db: Session = Depends(get_db)):
    data = cs.list_page(db, p, page, page_size, **f)
    data["perms"] = {"write": p.can("contracts:write"), "export": p.can("contracts:export"),
                     "archive": p.can("contracts:archive")}
    return ok(data)


@router.get("/options")
def options(p: Principal = Menu, db: Session = Depends(get_db)):
    from sqlalchemy import select

    from app.models import CONTRACT_STATUS, BizProject, SysUser
    projects = db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False)).order_by(BizProject.project_code)).unique().all()
    owners = db.scalars(select(SysUser).where(SysUser.status == 1).order_by(SysUser.department_id, SysUser.id)).all()
    return ok({"business_lines": cs.LINES, "status": CONTRACT_STATUS, "source_types": cs.SOURCE_TYPES,
               "projects": [{"code": x.project_code, "name": x.name, "business_line": x.business_line,
                             "customer_name": x.customer_name} for x in projects],
               "owners": [{"id": u.id, "name": u.name, "dept_code": u.dept_code} for u in owners
                          if u.dept_code in ("SC", "YX", "ZB")]})


@router.get("/export")
def export(request: Request, f: dict = Depends(_filters), p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "contracts:export")
    data, n = cs.export_xlsx(db, p, **f)
    log_op(db, p.id, "contract.export", "contract", None, None,
           {"filters": {k: v for k, v in f.items() if v not in (None, "")}, "rows": n}, client_ip(request))
    db.commit()
    name = f"合同台账_{today():%Y%m%d}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                             "X-Export-Rows": str(n)})


class ContractIn(BaseModel):
    contract_no: str | None = None
    name: str | None = None
    project_code: str | None = None
    customer_name: str | None = None
    amount: str | float | None = None
    sign_date: date | None = None
    business_line: str | None = None
    region: str | None = None
    owner_id: int | None = None
    status: int | None = None
    remark: str | None = None


@router.post("")
def create(body: ContractIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "contracts:write")
    c = cs.create(db, p, body.model_dump(exclude_unset=True), client_ip(request))
    return ok(cs.detail(db, c))


@router.get("/{cid}")
def detail(cid: int, p: Principal = Menu, db: Session = Depends(get_db)):
    c = cs.get_contract(db, cid, p)
    d = cs.detail(db, c)
    d["perms"] = {"write": p.can("contracts:write"), "archive": p.can("contracts:archive"),
                  "payment": p.can("contracts:write") or p.can("contracts:archive")}
    return ok(d)


@router.put("/{cid}")
def update(cid: int, body: ContractIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "contracts:write")
    c = cs.get_contract(db, cid, p)
    cs.update(db, p, c, body.model_dump(exclude_unset=True), client_ip(request))
    return ok(cs.detail(db, c))


@router.post("/{cid}/archive")
async def archive(cid: int, request: Request, file: UploadFile = File(...), p: Principal = Menu,
                  db: Session = Depends(get_db)):
    """上传合同原件归档 → 解析付款条款生成回款计划 → 自动关联到账流水。返回解析结果供前端确认。"""
    _need(p, "contracts:archive")
    c = cs.get_contract(db, cid, p)
    data = await file.read()
    return ok(cs.archive(db, p, c, file.filename or "contract.docx", data, client_ip(request)))


@router.put("/{cid}/plans")
def save_plans(cid: int, request: Request, stages: list[dict] = Body(..., embed=True), p: Principal = Menu,
               db: Session = Depends(get_db)):
    """确认/修改回款计划（归档解析结果确认后保存）。"""
    if not (p.can("contracts:archive") or p.can("contracts:write")):
        raise forbidden("无此操作权限")
    c = cs.get_contract(db, cid, p)
    return ok(cs.save_plans(db, p, c, stages, client_ip(request)))


class PaymentIn(BaseModel):
    amount: str | float
    payment_date: date
    remark: str | None = None


@router.post("/{cid}/payments")
def add_payment(cid: int, body: PaymentIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    """手工登记回款（无流水，如承兑/现金）。"""
    if not (p.can("contracts:archive") or p.can("contracts:write")):
        raise forbidden("无此操作权限")
    c = cs.get_contract(db, cid, p)
    pay = cs.manual_payment(db, p, c, body.amount, body.payment_date, body.remark, client_ip(request))
    db.refresh(c)
    return ok({"payment_id": pay.id, "detail": cs.detail(db, c)})
