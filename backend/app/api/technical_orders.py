"""技术部工单统计与审批时间明细。"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, require_menu
from app.core.response import ok
from app.services.technical_orders import report

router = APIRouter(prefix="/technical-orders", tags=["技术部工单"])


@router.get("")
def monthly_orders(
    month: str | None = Query(default=None, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
    owner: str | None = Query(default=None, max_length=64),
    stage: Literal["waiting", "in_progress", "completed", "unverified"] | None = None,
    keyword: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    p: Principal = Depends(require_menu("technical_orders")),
    db: Session = Depends(get_db),
):
    return ok(report(db, p, month, owner, stage, keyword, page, page_size))
