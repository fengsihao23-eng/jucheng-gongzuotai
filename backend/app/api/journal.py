"""出纳日记账（FR-05-03）：日/月视图（期初/本期收支/期末，按账户）、手工补录现金账（编辑/删除留痕）、导出。"""
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_op, snapshot
from app.core.db import get_db, get_or_404
from app.core.deletion import DeletionIn, verify_deletion
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, conflict, forbidden, ok
from app.core.timeutil import month_range, today
from app.models import BizProject, FinCashJournal, SysUser
from app.services import journal_service

router = APIRouter(prefix="/journal", tags=["journal"])
Menu = Depends(require_menu("journal"))
FIELDS = ["journal_date", "account_type", "bank_account", "direction", "amount", "summary", "category", "project_code",
          "biz_ref", "source_type"]
CATEGORIES = ["备用金", "办公费", "差旅费", "交通费", "招待费", "快递费", "其他"]


def _need(p: Principal):
    if not p.can("journal:write"):
        raise forbidden("无此操作权限")


@router.get("/accounts")
def accounts(p: Principal = Menu, db: Session = Depends(get_db)):
    return ok({"accounts": journal_service.account_list(db), "categories": CATEGORIES, "can_write": p.can("journal:write")})


@router.get("")
def view(mode: str = "day", day: date | None = None, period: str = "", account: str = "", p: Principal = Menu,
         db: Session = Depends(get_db)):
    if mode not in ("day", "month", "year"):
        raise bad_request("mode 仅支持 day / month / year")
    if mode == "year" and period and (len(period) != 4 or not period.isascii() or not period.isdigit() or period == "0000"):
        raise bad_request("年份格式应为 YYYY")
    return ok(journal_service.view(db, mode, ref=day or today(), period=period or None, account=account or None))


class EntryBody(BaseModel):
    journal_date: date
    direction: int = Field(ge=1, le=2)
    amount: Decimal = Field(gt=0)
    summary: str = Field(min_length=1, max_length=255)
    category: str | None = "其他"
    project_code: str | None = None
    biz_ref: str | None = None


def _validate(db: Session, body: EntryBody):
    if body.journal_date > today():
        raise bad_request("日期不能晚于今天")
    if body.project_code and not db.scalar(select(BizProject.id).where(BizProject.project_code == body.project_code)):
        raise bad_request("项目不存在")


@router.post("")
def create(body: EntryBody, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    """手工补录：仅现金账（银行账由流水核对自动登记，避免重复）。"""
    _need(p)
    _validate(db, body)
    j = FinCashJournal(journal_date=body.journal_date, account_type="cash", direction=body.direction,
                       amount=body.amount.quantize(Decimal("0.01")), summary=body.summary, category=body.category,
                       project_code=body.project_code or None, biz_ref=body.biz_ref or None, register_by=p.id,
                       source_type=2)
    db.add(j)
    db.flush()
    log_op(db, p.id, "journal.create", "journal", j.id, None, snapshot(j, FIELDS), client_ip(request))
    db.commit()
    users = {p.id: p.user.name}
    return ok(journal_service.entry_row(j, users), message="已登记")


def _editable(db: Session, entry_id: int) -> FinCashJournal:
    j = get_or_404(db, FinCashJournal, entry_id, "日记账")
    if j.source_type != 2:
        raise conflict("流水核对生成的日记账不可直接修改，请在流水核对工作台撤销核对")
    return j


@router.put("/{entry_id}")
def update(entry_id: int, body: EntryBody, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p)
    _validate(db, body)
    j = _editable(db, entry_id)
    before = snapshot(j, FIELDS)
    j.journal_date, j.direction, j.amount = body.journal_date, body.direction, body.amount.quantize(Decimal("0.01"))
    j.summary, j.category, j.project_code, j.biz_ref = body.summary, body.category, body.project_code or None, body.biz_ref or None
    log_op(db, p.id, "journal.update", "journal", j.id, before, snapshot(j, FIELDS), client_ip(request))
    db.commit()
    users = dict(db.execute(select(SysUser.id, SysUser.name)).all())
    return ok(journal_service.entry_row(j, users), message="已更新")


@router.delete("/{entry_id}")
def delete(entry_id: int, body: DeletionIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _need(p)
    verify_deletion(db, body)
    j = _editable(db, entry_id)
    log_op(db, p.id, "journal.delete", "journal", j.id, snapshot(j, FIELDS), None, client_ip(request))
    db.delete(j)
    db.commit()
    return ok(message="已删除")


@router.get("/export")
def export(request: Request, period: str = "", start: date | None = None, end: date | None = None,
           p: Principal = Menu, db: Session = Depends(get_db)):
    if not (start and end):
        start, end = month_range(period) if period else month_range(ref=today())
    data = journal_service.export_xlsx(db, start, end)
    log_op(db, p.id, "journal.export", "journal", None, after={"start": start, "end": end}, ip=client_ip(request))
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'出纳日记账_{start:%Y%m%d}-{end:%Y%m%d}.xlsx')}"})
