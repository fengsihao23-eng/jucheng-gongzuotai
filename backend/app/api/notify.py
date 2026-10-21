"""站内通知（FR-12-02）：右上角铃铛。"""
from fastapi import APIRouter, Body, Depends
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, get_principal
from app.core.response import ok, page_data
from app.models import SysNotify

router = APIRouter(prefix="/notifications", tags=["通知"])


@router.get("")
def my_notifications(page: int = 1, page_size: int = 20, unread: bool = False,
                     p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    q = select(SysNotify).where(SysNotify.user_id == p.id, SysNotify.channel == "inapp")
    if unread:
        q = q.where(SysNotify.read_status.is_(False))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(SysNotify.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    unread_n = db.scalar(select(func.count()).where(SysNotify.user_id == p.id, SysNotify.channel == "inapp",
                                                    SysNotify.read_status.is_(False)))
    items = [{"id": r.id, "type": r.msg_type, "title": r.title, "content": r.content, "link": r.link,
              "read": r.read_status, "created_at": r.created_at} for r in rows]
    return ok(page_data(items, total, page, page_size, unread=unread_n))


@router.post("/read")
def mark_read(ids: list[int] | None = Body(default=None, embed=True), p: Principal = Depends(get_principal),
              db: Session = Depends(get_db)):
    q = update(SysNotify).where(SysNotify.user_id == p.id, SysNotify.channel == "inapp")
    if ids:
        q = q.where(SysNotify.id.in_(ids))
    db.execute(q.values(read_status=True))
    db.commit()
    return ok()


account = APIRouter(prefix="/account", tags=["个人中心"])


@account.get("/approvals")
def my_approvals(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """我提交的企微审批与自动登记结果（与 MCP oa.pending_approvals 同源）。"""
    from app.mcp_server.server import t_pending_approvals
    return ok(t_pending_approvals(db, p, type="submitted")["data"])


@account.get("/expenses")
def my_expenses(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.models import BizExpenseReport
    rows = db.scalars(select(BizExpenseReport).where(BizExpenseReport.applicant_id == p.id)
                      .order_by(BizExpenseReport.expense_date.desc()).limit(30)).all()
    return ok([{"approval_no": r.approval_no, "amount": r.amount, "category": r.category, "expense_date": r.expense_date,
                "project_code": r.project_code, "status": r.status, "pay_status": r.pay_status} for r in rows])
