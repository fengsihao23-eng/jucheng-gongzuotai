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
