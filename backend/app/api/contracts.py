"""FR-05-01 合同台账 + FR-05-06 合同归档（原件版本控制、付款条款读取、到账流水自动关联）。"""
from datetime import date
from urllib.parse import quote
from typing import Literal

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
from app.services import contracts_sync as csync

router = APIRouter(prefix="/contracts", tags=["合同台账"])
Menu = Depends(require_menu("contracts"))


def _filters(business_line: str = "", customer: str = "", status: int | None = None, sign_month: str = "",
             sign_from: date | None = None, sign_to: date | None = None, source_type: int | None = None,
             keyword: str = "", project_code: str = "", paid_month: str = "", owner_id: int | None = None,
             region: str = "", sign_period: Literal["", "all", "month", "week", "today"] = "") -> dict:
    return {"business_line": business_line, "customer": customer, "status": status, "sign_month": sign_month,
            "sign_from": sign_from, "sign_to": sign_to, "source_type": source_type, "keyword": keyword.strip(),
            "project_code": project_code, "paid_month": paid_month, "owner_id": owner_id, "region": region, "sign_period": sign_period}


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


@router.get("/signing")
def signing(f: dict = Depends(_filters), p: Principal = Menu, db: Session = Depends(get_db)):
    return ok({"items": cs.signing_board(db, p, **f),
               "note": "按实际签订日统计截至今日的有效合同；周一为一周起点，已终止合同不计入。"})


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


class SyncSourceIn(BaseModel):
    key: str
    enabled: bool = True
    docid: str
    sheet_id: str
    reader_id: int
    auto_apply: bool = False
    fields: dict[str, str]


@router.get("/sync/sources")
def sync_sources(p: Principal = Menu, db: Session = Depends(get_db)):
    """合同台账的固定来源配置（企业微信在线表格）。"""
    from app.services import dict_service

    items = dict_service.items(db, csync.DICT_TYPE)
    return ok({"items": [{"key": k, **v} for k, v in items.items() if isinstance(v, dict)],
               "required": list(csync.REQUIRED), "regions": sorted(csync.REGIONS),
               "business_lines": cs.LINES})


@router.put("/sync/sources")
def save_sync_source(body: SyncSourceIn, request: Request, p: Principal = Menu,
                     db: Session = Depends(get_db)):
    from app.services import dict_service

    _need(p, "contracts:write")
    key = body.key.strip()
    if not key or len(key) > 40:
        raise forbidden("来源名称需为 1~40 字")
    fields = {k: v.strip() for k, v in body.fields.items() if v and v.strip()}
    missing = [f for f in csync.REQUIRED if f not in fields]
    if missing:
        raise forbidden("未映射必填字段：" + "、".join(missing))
    value = {"enabled": body.enabled, "format": "sheet", "docid": body.docid.strip(),
             "sheet_id": body.sheet_id.strip(), "reader_id": body.reader_id,
             "auto_apply": body.auto_apply, "fields": fields}
    dict_service.put(db, csync.DICT_TYPE, key, value)
    log_op(db, p.id, "contract.sync_config", "contract", None, None,
           {"key": key, "reader_id": body.reader_id, "auto_apply": body.auto_apply}, client_ip(request))
    db.commit()
    return ok({"key": key, **value})


@router.delete("/sync/sources/{key}")
def delete_sync_source(key: str, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    from sqlalchemy import select

    from app.models import SysDict
    from app.services import dict_service

    _need(p, "contracts:write")
    row = db.scalar(select(SysDict).where(SysDict.dict_type == csync.DICT_TYPE, SysDict.dict_key == key))
    if row:
        row.status = 0
        log_op(db, p.id, "contract.sync_config_delete", "contract", None, None, {"key": key}, client_ip(request))
        db.commit()
    return ok({"deleted": bool(row)})


def _sync_config(db: Session, source: str) -> dict:
    from app.services import dict_service

    config = dict_service.get(db, csync.DICT_TYPE, source) if source else None
    if not isinstance(config, dict):
        raise forbidden("没有这个固定来源，请先在同步设置里配置")
    return config


@router.get("/sync/preview")
def sync_preview(source: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    """读取企业微信在线表格并与台账比对，只返回差异，不改台账。"""
    return ok({"source": source, **csync.preview(db, _sync_config(db, source), source)})


@router.post("/sync/apply")
def sync_apply(request: Request, source: str = Body(..., embed=True),
               contract_nos: list[str] | None = Body(None, embed=True), p: Principal = Menu,
               db: Session = Depends(get_db)):
    """确认差异后落库；只写合同主档，不碰回款、付款计划与归档原件。"""
    _need(p, "contracts:write")
    config = _sync_config(db, source)
    data = csync.preview(db, config, source)
    result = csync.apply(db, p, config, data, only=contract_nos, ip=client_ip(request), key=source)
    log_op(db, p.id, "contract.sync_apply", "contract", None, None,
           {"source": source, "created": result["created"], "updated": result["updated"],
            "skipped": result["skipped"]}, client_ip(request))
    db.commit()
    return ok(result)


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
