"""企业微信客户联系（只读）：成员客户列表与商机登记辅助。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, require_menu
from app.core.response import ok
from app.services import wecom_external_service

router = APIRouter(prefix="/wecom-external", tags=["企业微信客户"])
Menu = Depends(require_menu("market", "opportunities"))


@router.get("/overview")
def overview(p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(wecom_external_service.summary(db, p.user))


@router.get("/customers")
def customers(userid: str, p: Principal = Menu):
    return ok({"userid": userid, "items": wecom_external_service.customers(userid)})
