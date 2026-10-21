"""客服工单（PRD 7.8）：一句话登记、自动分类/去重/相似工单/排查建议、流转、辅助回复、归因分析与报告。"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, require_menu
from app.core.response import bad_request, forbidden, ok
from app.core.storage import file_brief
from app.core.timeutil import today
from app.models import RptReport, SysFile
from app.services import cs_service

router = APIRouter(prefix="/service", tags=["service"])
menu = require_menu("service")


def _writer(p: Principal):
    if not p.can("ticket:write"):
        raise forbidden("无工单登记/处理权限")


class RegisterIn(BaseModel):
    text: str | None = Field(None, max_length=2000)
    customer_name: str | None = Field(None, max_length=128)
    contact: str | None = Field(None, max_length=64)
    channel: str | None = None
    device: str | None = Field(None, max_length=64)
    problem_desc: str | None = Field(None, max_length=4000)
    category: str | None = None


class StatusIn(BaseModel):
    status: int
    root_cause_type: str | None = None
    root_cause: str | None = Field(None, max_length=255)
    solution: str | None = Field(None, max_length=4000)


class AdoptIn(BaseModel):
    adopted: bool = True


class ReportIn(BaseModel):
    start: date | None = None
    end: date | None = None
    period: str | None = None


@router.get("/meta")
def meta(p: Principal = Depends(menu), db: Session = Depends(get_db)):
    return ok({"categories": [*cs_service.category_rules(db).keys(), "其他"], "channels": cs_service.CHANNELS,
               "root_cause_types": cs_service.ROOT_CAUSE_TYPES, "status": cs_service.STATUS,
               "can_write": p.can("ticket:write")})


@router.get("/kpi")
def kpi(p: Principal = Depends(menu), db: Session = Depends(get_db)):
    return ok(cs_service.kpi(db))


@router.post("/tickets")
def register(body: RegisterIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    fields = body.model_dump(exclude={"text"})
    if not body.text and not body.problem_desc:
        raise bad_request("请输入一句话描述或填写问题描述")
    res = cs_service.register_ticket(db, text=body.text, fields=fields, user=p.user)
    db.commit()
    return ok(res)


@router.get("/tickets")
def tickets(status: int | None = None, category: str | None = None, keyword: str | None = None,
            start: date | None = None, end: date | None = None, only_open: bool = False,
            page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
            p: Principal = Depends(menu), db: Session = Depends(get_db)):
    return ok(cs_service.list_tickets(db, status, category, keyword, start, end, page, page_size, only_open))


@router.get("/tickets/{ticket_id}")
def ticket(ticket_id: int, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    return ok(cs_service.detail(db, cs_service.ticket_or_404(db, ticket_id)))


@router.put("/tickets/{ticket_id}/status")
def set_status(ticket_id: int, body: StatusIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    t = cs_service.ticket_or_404(db, ticket_id)
    res = cs_service.update_status(db, t, body.status, body.root_cause_type, body.root_cause, body.solution, p.user)
    db.commit()
    return ok(res)


@router.post("/tickets/{ticket_id}/suggestion")
def refresh_suggestion(ticket_id: int, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    res = cs_service.refresh_suggestion(db, cs_service.ticket_or_404(db, ticket_id))
    db.commit()
    return ok(res)


@router.post("/tickets/{ticket_id}/reply/adopt")
def adopt(ticket_id: int, body: AdoptIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    res = cs_service.mark_reply_adopted(db, cs_service.ticket_or_404(db, ticket_id), body.adopted)
    db.commit()
    return ok(res)


def _range(start: date | None, end: date | None) -> tuple[date, date]:
    end = end or today()
    start = start or end.replace(day=1)
    if start > end:
        raise bad_request("开始日期不能晚于结束日期")
    return start, end


@router.get("/attribution")
def attribution(start: date | None = None, end: date | None = None, p: Principal = Depends(menu),
                db: Session = Depends(get_db)):
    s, e = _range(start, end)
    return ok(cs_service.attribution(db, s, e))


@router.post("/attribution/report")
def make_report(body: ReportIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    s, e = _range(body.start, body.end)
    period = body.period or (f"{s:%Y-%m}" if s.day == 1 and s.month == e.month else f"{s:%Y%m%d}-{e:%Y%m%d}")
    r = cs_service.generate_report(db, s, e, period, trigger="manual", user_id=p.id)
    db.commit()
    return ok(_report_out(db, r))


def _report_out(db: Session, r: RptReport) -> dict:
    files = [file_brief(db.get(SysFile, fid)) for fid in r.file_ids or []]
    return {"id": r.id, "period": r.period, "title": r.title, "version": r.version, "metrics": r.metrics,
            "trigger": r.trigger, "created_at": r.created_at, "files": [f for f in files if f]}


@router.get("/reports")
def reports(p: Principal = Depends(menu), db: Session = Depends(get_db)):
    rows = db.scalars(select(RptReport).where(RptReport.report_type == "cs_attribution")
                      .order_by(RptReport.created_at.desc(), RptReport.id.desc()).limit(30)).all()
    return ok([_report_out(db, r) for r in rows])
