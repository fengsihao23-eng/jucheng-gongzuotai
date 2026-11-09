"""企业微信审批提交入口：模板配置、控件读取与发起审批。"""
from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal, require_menu
from app.core.response import BizError, ok
from app.services import wecom_approval_service

router = APIRouter(prefix="/wecom-approval", tags=["企业微信审批"])
Admin = Depends(require_menu("admin"))

MENU_OF = {"expense": "expenses", "contract": "contracts"}


class SubmitBody(BaseModel):
    kind: str
    values: dict = {}
    source_type: str = ""
    source_id: str = ""


class ConfigBody(BaseModel):
    expense: dict | None = None
    contract: dict | None = None


def _guard(p: Principal, kind: str) -> None:
    menu = MENU_OF.get(kind)
    if menu and not p.has_menu(menu):
        raise BizError(40301, "没有该模块的发起权限")


@router.get("/kinds")
def kinds(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = wecom_approval_service.config(db)
    for kind, row in rows.items():
        row["can_submit"] = bool(MENU_OF.get(kind) and p.has_menu(MENU_OF[kind]))
    return ok(list(rows.values()))


@router.get("/template/{kind}")
def template(kind: str, refresh: bool = False, p: Principal = Depends(get_principal),
             db: Session = Depends(get_db)):
    _guard(p, kind)
    return ok(wecom_approval_service.controls(db, kind, refresh=refresh))


@router.put("/config")
def put_config(body: ConfigBody, request: Request = None, p: Principal = Admin, db: Session = Depends(get_db)):
    patch = {key: value for key, value in body.model_dump().items() if value is not None}
    data = wecom_approval_service.put_config(db, patch)
    from app.core.audit import log_op
    log_op(db, p.id, "wecom.approval.config", "dict", wecom_approval_service.DICT_KEY, None, patch, client_ip(request))
    db.commit()
    return ok(data)


@router.post("/submit")
def submit(body: SubmitBody, request: Request = None, p: Principal = Depends(get_principal),
           db: Session = Depends(get_db)):
    _guard(p, body.kind)
    result = wecom_approval_service.submit(db, p.user, body.kind, body.values)
    return ok(result)


# ---------------------------------------------------------------- 审批台账（同步记录）
STATUS_TEXT = {1: "审批中", 2: "已通过", 3: "已驳回", 4: "已撤销"}
DISPATCH_TEXT = {0: "待处理", 1: "已登记台账", 2: "登记失败", 3: "无需处理"}
LedgerMenu = Depends(require_menu("contracts"))


@router.get("/records")
def records(status: int | None = None, template_key: str | None = None, keyword: str | None = None,
            page: int = 1, page_size: int = 30, p: Principal = LedgerMenu, db: Session = Depends(get_db)):
    """企微审批同步台账：按状态/模板/关键词筛选，供合同与财务核对。"""
    from sqlalchemy import func, or_, select

    from app.models import SyncWecomApproval, SysUser

    q = select(SyncWecomApproval)
    if status:
        q = q.where(SyncWecomApproval.status == status)
    if template_key:
        q = q.where(SyncWecomApproval.template_key == template_key)
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.where(or_(SyncWecomApproval.sp_no.ilike(like), SyncWecomApproval.sp_name.ilike(like),
                        SyncWecomApproval.applicant_userid.ilike(like)))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(SyncWecomApproval.apply_time.desc().nullslast(), SyncWecomApproval.id.desc())
                      .offset((max(page, 1) - 1) * page_size).limit(min(page_size, 100))).all()
    names = {u.wecom_userid: u.name for u in db.scalars(select(SysUser).where(SysUser.wecom_userid.is_not(None)))}
    stats = {int(k): int(v) for k, v in db.execute(
        select(SyncWecomApproval.status, func.count()).group_by(SyncWecomApproval.status)).all()}
    dispatched = db.scalar(select(func.count()).select_from(SyncWecomApproval)
                           .where(SyncWecomApproval.dispatch_status == 1)) or 0
    return ok({"total": total, "page": page, "stats": stats, "dispatched": dispatched,
               "items": [{"sp_no": r.sp_no, "sp_name": r.sp_name, "template_key": r.template_key,
                          "applicant": r.applicant_userid, "applicant_name": names.get(r.applicant_userid),
                          "status": r.status, "status_text": STATUS_TEXT.get(r.status, str(r.status)),
                          "apply_time": r.apply_time, "dispatch_status": r.dispatch_status,
                          "dispatch_text": DISPATCH_TEXT.get(r.dispatch_status, str(r.dispatch_status)),
                          "dispatch_target": r.dispatch_target, "error_msg": r.error_msg,
                          "received_via": r.received_via} for r in rows]})


@router.get("/records/{sp_no}")
def record_detail(sp_no: str, p: Principal = LedgerMenu, db: Session = Depends(get_db)):
    from sqlalchemy import select

    from app.core.response import not_found
    from app.models import SyncWecomApproval, SysUser

    r = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == sp_no))
    if r is None:
        raise not_found("审批单")
    u = db.scalar(select(SysUser).where(SysUser.wecom_userid == r.applicant_userid))
    detail = r.detail or {}
    return ok({"sp_no": r.sp_no, "sp_name": r.sp_name, "template_id": r.template_id,
               "template_key": r.template_key, "applicant": r.applicant_userid,
               "applicant_name": u.name if u else None, "status": r.status,
               "status_text": STATUS_TEXT.get(r.status, str(r.status)), "apply_time": r.apply_time,
               "dispatch_status": r.dispatch_status, "dispatch_text": DISPATCH_TEXT.get(r.dispatch_status, ""),
               "dispatch_target": r.dispatch_target, "error_msg": r.error_msg, "received_via": r.received_via,
               "fields": detail.get("fields") or {}, "approved_time": detail.get("approved_time")})
