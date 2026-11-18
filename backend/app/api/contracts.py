"""FR-05-01 合同台账 + FR-05-06 合同归档（原件版本控制、付款条款读取、到账流水自动关联）。"""
from datetime import date
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, forbidden, ok
from app.core.timeutil import today
from app.services import contract_service as cs
from app.services import contract_workbook_sync as workbook
from app.services import contracts_sync as csync

router = APIRouter(prefix="/contracts", tags=["合同台账"])
Menu = Depends(require_menu("contracts"))


def _filters(business_line: str = "", customer: str = "", status: int | None = None, sign_month: str = "",
             sign_from: date | None = None, sign_to: date | None = None, source_type: int | None = None,
             keyword: str = "", project_code: str = "", paid_month: str = "", owner_id: int | None = None,
             region: str = "", sign_period: Literal["", "all", "month", "week", "today"] = "",
             sign_year: int | None = Query(None, ge=1990, le=2100)) -> dict:
    return {"business_line": business_line, "customer": customer, "status": status, "sign_month": sign_month,
            "sign_from": sign_from, "sign_to": sign_to, "source_type": source_type, "keyword": keyword.strip(),
            "project_code": project_code, "paid_month": paid_month, "owner_id": owner_id, "region": region, "sign_period": sign_period, "sign_year": sign_year}


def _need(p: Principal, button: str):
    if not p.can(button):
        raise forbidden("无此操作权限")


@router.get("")
def list_contracts(page: int = 1, page_size: int = 20, f: dict = Depends(_filters), p: Principal = Menu,
                   db: Session = Depends(get_db)):
    data = cs.list_page(db, p, page, page_size, **f)
    data["perms"] = {"write": p.can("contracts:write"), "export": p.can("contracts:export"),
                     "archive": p.can("contracts:archive"), "workbook": workbook.has_source(db, p)}
    return ok(data)


@router.get("/signing")
def signing(f: dict = Depends(_filters), p: Principal = Menu, db: Session = Depends(get_db)):
    from app.core.timeutil import month_range, week_range
    from app.services import business_workbook, workbook_metrics
    wb = business_workbook.active_source(db, p, "contracts")
    if wb is not None:
        T = today()
        yr = f.get("sign_year") or T.year
        ys, ye = date(yr, 1, 1), (T if yr == T.year else date(yr, 12, 31))
        ms = month_range(ref=T)[0]
        ws = week_range(ref=T)[0]
        y_m = workbook_metrics.contract_metrics(wb, ys, ye)
        m_m = workbook_metrics.contract_metrics(wb, ms, T)
        w_m = workbook_metrics.contract_metrics(wb, ws, T)
        d_m = workbook_metrics.contract_metrics(wb, T, T)
        return ok({
            "source_mode": "workbook",
            "items": [
                {"key": "year", "label": f"{yr}年度累计业绩", "start": ys, "end": ye, "count": y_m["count"], "amount": y_m["amount"]},
                {"key": "month", "label": "当月签约", "start": ms, "end": T, "count": m_m["count"], "amount": m_m["amount"]},
                {"key": "week", "label": "本周签约", "start": ws, "end": T, "count": w_m["count"], "amount": w_m["amount"]},
                {"key": "today", "label": "今日签约", "start": T, "end": T, "count": d_m["count"], "amount": d_m["amount"]},
            ],
            "note": "经营业绩固定来源口径：2026年取「2026年销售合同」，以前年份取「2026年前已签单个项目未回款」，按原表签订日期统计。",
        })
    return ok({"items": cs.signing_board(db, p, **f),
               "note": "按实际签订日统计截至今日的有效合同；周一为一周起点，已终止合同不计入。"})


@router.get("/options")
def options(p: Principal = Menu, db: Session = Depends(get_db)):
    from sqlalchemy import select, extract

    from app.models import CONTRACT_STATUS, BizContract, BizProject, SysUser
    from app.core.deps import owner_scope
    projects = db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False)).order_by(BizProject.project_code)).unique().all()
    owners = db.scalars(select(SysUser).where(SysUser.status == 1).order_by(SysUser.department_id, SysUser.id)).all()
    years = sorted({today().year, *[int(y) for y in db.scalars(select(extract("year", BizContract.sign_date)).where(
        BizContract.is_deleted.is_(False), owner_scope(BizContract.owner_id, p)).distinct()) if y is not None]}, reverse=True)
    return ok({"business_lines": cs.LINES, "status": CONTRACT_STATUS, "source_types": cs.SOURCE_TYPES,
               "years": years, "current_year": today().year,
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


@router.get("/reconciliation")
def source_reconciliation(p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services.contract_reconciliation import reconcile
    return ok(reconcile(db, p))


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
    return ok({"items": [{"key": k, **v} for k, v in items.items() if isinstance(v, dict) and v.get("reader_id") == p.id],
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
    previous = dict_service.get(db, csync.DICT_TYPE, key)
    if body.reader_id != p.id or (isinstance(previous, dict) and previous.get("reader_id") != p.id):
        raise forbidden("固定来源只能使用和维护本人的企微连接")
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
    if row and (not isinstance(row.dict_value, dict) or row.dict_value.get("reader_id") != p.id):
        raise forbidden("固定来源只能使用和维护本人的企微连接")
    if row:
        row.status = 0
        log_op(db, p.id, "contract.sync_config_delete", "contract", None, None, {"key": key}, client_ip(request))
        db.commit()
    return ok({"deleted": bool(row)})


def _sync_config(db: Session, source: str, p: Principal) -> dict:
    from app.services import dict_service

    config = dict_service.get(db, csync.DICT_TYPE, source) if source else None
    if not isinstance(config, dict) or config.get("reader_id") != p.id:
        raise forbidden("没有这个固定来源，请先在同步设置里配置")
    return config


@router.get("/sync/preview")
def sync_preview(source: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    """读取企业微信在线表格并与台账比对，只返回差异，不改台账。"""
    return ok({"source": source, **csync.preview(db, _sync_config(db, source, p), source)})


@router.post("/sync/apply")
def sync_apply(request: Request, source: str = Body(..., embed=True),
               contract_nos: list[str] | None = Body(None, embed=True), p: Principal = Menu,
               db: Session = Depends(get_db)):
    """确认差异后落库；只写合同主档，不碰回款、付款计划与归档原件。"""
    _need(p, "contracts:write")
    config = _sync_config(db, source, p)
    data = csync.preview(db, config, source)
    result = csync.apply(db, p, config, data, only=contract_nos, ip=client_ip(request), key=source)
    log_op(db, p.id, "contract.sync_apply", "contract", None, None,
           {"source": source, "created": result["created"], "updated": result["updated"],
            "skipped": result["skipped"]}, client_ip(request))
    db.commit()
    return ok(result)


class WorkbookIn(BaseModel):
    key: str
    title: str
    url: str = Field(max_length=500)
    view_department_ids: list[int] = Field(default_factory=list, max_length=50)
    view_user_ids: list[int] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    auto_apply: bool = False
    auto_receipts: bool = False


class WorkbookLinkIn(BaseModel):
    sheet_id: str = Field(min_length=1, max_length=200)
    row_number: int = Field(ge=1, le=20000)
    number: str = Field(min_length=1, max_length=32)
    revision: str = Field(min_length=1, max_length=64)


@router.get("/workbooks")
def workbooks(p: Principal = Menu, db: Session = Depends(get_db)):
    return ok({"items": workbook.visible_sources(db, p)}, headers={"Cache-Control": "no-store"})


@router.put("/workbooks")
def configure_workbook(body: WorkbookIn, p: Principal = Menu, db: Session = Depends(get_db)):
    if not p.is_admin:
        raise forbidden("仅管理员可配置企业微信固定来源")
    from sqlalchemy import select

    from app.models import ContractWorkbook
    existing = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == body.key))
    if existing and existing.config["reader_id"] != p.id:
        raise forbidden("仅绑定账号可修改此固定来源")
    row = workbook.configure(db, body.key, title=body.title, url=body.url, reader_id=p.id,
                             view_department_ids=body.view_department_ids, view_user_ids=body.view_user_ids,
                             profiles=existing.config.get("profiles") if existing else None,
                             enabled=body.enabled, auto_apply=body.auto_apply, auto_receipts=body.auto_receipts, actor_id=p.id)
    db.commit()
    return ok(workbook.describe(row, p), headers={"Cache-Control": "no-store"})


@router.get("/workbooks/{key}")
def workbook_detail(key: str, p: Principal = Menu, db: Session = Depends(get_db)):
    row = workbook.for_view(db, p, key)
    return ok({**workbook.describe(row, p), "sheets": workbook.sheet_list(row)}, headers={"Cache-Control": "no-store"})


@router.get("/workbooks/{key}/rows")
def workbook_rows(key: str, sheet: str, page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                  keyword: str = Query("", max_length=100),
                  state: Literal["", "business", "review", "unresolved"] = "",
                  source_row: int | None = Query(None, ge=1),
                  p: Principal = Menu, db: Session = Depends(get_db)):
    row = workbook.for_view(db, p, key)
    return ok(workbook.sheet_page(db, row, sheet, page, size, keyword, state, source_row),
              headers={"Cache-Control": "no-store"})


@router.get("/workbooks/{key}/export")
def workbook_export(key: str, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    row = workbook.for_view(db, p, key)
    _need(p, "contracts:export")
    if not row.sheets:
        raise bad_request("尚没有可导出的同步数据")
    data = workbook.export_snapshot(row)
    log_op(db, p.id, "contract.workbook.export", "contract_workbook", key,
           after={"revision": row.revision}, ip=client_ip(request))
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.config['title'] + '.xlsx')}",
                             "Cache-Control": "no-store"})


@router.post("/workbooks/{key}/sync")
def sync_workbook(key: str, p: Principal = Menu, db: Session = Depends(get_db)):
    row = workbook.for_view(db, p, key)
    if not workbook.describe(row, p)["can_run"]:
        raise forbidden("仅绑定账号可立即检查此固定来源")
    return ok(workbook.sync(db, key, force=not row.config["enabled"]), headers={"Cache-Control": "no-store"})


@router.post("/workbooks/{key}/link")
def link_workbook_row(key: str, body: WorkbookLinkIn, request: Request, p: Principal = Menu,
                      db: Session = Depends(get_db)):
    return ok(workbook.review_link(db, p, key, **body.model_dump(), ip=client_ip(request)),
              headers={"Cache-Control": "no-store"})


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
    source_opp_id: int | None = None  # 由商机赢单转入时回填关联


@router.post("")
def create(body: ContractIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p, "contracts:write")
    payload = body.model_dump(exclude_unset=True)
    opp_id = payload.pop("source_opp_id", None)
    c = cs.create(db, p, payload, client_ip(request))
    if opp_id:
        # 商机赢单转入：回填关联合同号并把商机推进到赢单，链路可反查
        from app.models import BizOpportunity

        opp = db.get(BizOpportunity, opp_id)
        if opp and not opp.is_deleted:
            opp.contract_no = c.contract_no
            opp.stage, opp.status = "won", "won"
            log_op(db, p.id, "opportunity.won", "opportunity", opp.id, None,
                   {"contract_no": c.contract_no}, client_ip(request))
            db.commit()
    return ok(cs.detail(db, c))


@router.get("/{cid}")
def detail(cid: int, p: Principal = Menu, db: Session = Depends(get_db)):
    c = cs.get_contract(db, cid, p)
    d = cs.detail(db, c)
    d["workbook_sources"] = workbook.linked_sources(db, p, "contract", c.id)
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
