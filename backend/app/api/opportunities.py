"""商机信息表（FR-05-02）。菜单 opportunities；写操作按钮 opp:write。"""
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db, get_or_404
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import forbidden, ok
from app.core.timeutil import today
from app.models import BizOpportunity
from app.services import opportunity_service as svc

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
Menu = Depends(require_menu("opportunities"))


class OppIn(BaseModel):
    name: str | None = None
    customer_name: str | None = None
    business_line: str | None = None
    stage: str | None = None
    amount: Decimal | None = None
    expected_sign_date: date | None = None
    owner_id: int | None = None
    project_code: str | None = None
    remark: str | None = None


class AdvanceIn(BaseModel):
    to_stage: str | None = None
    note: str | None = None
    lost_reason: str | None = None


def _filters(stage: str = "", business_line: str = "", owner_id: int | None = None, customer: str = "",
             start: date | None = None, end: date | None = None, source: int | None = None, keyword: str = "",
             status: str = "") -> dict:
    return dict(stage=stage, business_line=business_line, owner_id=owner_id, customer=customer, start=start, end=end,
                source=source, keyword=keyword, status=status)


def _get(db: Session, p: Principal, opp_id: int) -> BizOpportunity:
    o = get_or_404(db, BizOpportunity, opp_id, "商机")
    if not db.scalar(svc._filtered(p).with_only_columns(BizOpportunity.id).where(BizOpportunity.id == o.id)):
        raise forbidden()
    return o


def _writer(p: Principal) -> None:
    if not p.can("opp:write"):
        raise forbidden("无商机维护权限")


@router.get("/options")
def options(p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(svc.options(db))


@router.get("/funnel")
def funnel(f: dict = Depends(_filters), p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(svc.funnel(db, p, **f))


@router.get("")
def list_(page: int = 1, page_size: int = 20, sort: str = "-created_at", f: dict = Depends(_filters),
          p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(svc.list_page(db, p, page=page, page_size=min(page_size, 200), sort=sort, **f))


@router.get("/export")
def export(request: Request, f: dict = Depends(_filters), p: Principal = Menu, db: Session = Depends(get_db)):
    data = svc.export_xlsx(db, p, **f)
    log_op(db, p.id, "opportunity.export", "opportunity", None, after={k: str(v) for k, v in f.items() if v},
           ip=client_ip(request))
    db.commit()
    name = f"商机信息表_{today():%Y%m%d}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/{opp_id}")
def get_one(opp_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services.contract_workbook_sync import linked_sources

    result = svc.detail(db, _get(db, p, opp_id))
    result["workbook_sources"] = linked_sources(db, p, "opportunity", opp_id)
    return ok(result)


@router.post("")
def create(body: OppIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _writer(p)
    o = svc.create(db, p, body.model_dump(exclude_unset=True), ip=client_ip(request))
    db.commit()
    return ok(svc.detail(db, o))


@router.put("/{opp_id}")
def update(opp_id: int, body: OppIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _writer(p)
    o = svc.update(db, p, _get(db, p, opp_id), body.model_dump(exclude_unset=True), ip=client_ip(request))
    db.commit()
    return ok(svc.detail(db, o))


@router.post("/{opp_id}/advance")
def advance(opp_id: int, body: AdvanceIn, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _writer(p)
    o = svc.advance(db, p, _get(db, p, opp_id), body.to_stage, body.note, body.lost_reason, ip=client_ip(request))
    db.commit()
    return ok(svc.detail(db, o))


@router.get("/{opp_id}/history")
def history(opp_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(svc.history(db, _get(db, p, opp_id)))


@router.post("/{opp_id}/contract-draft")
def contract_draft(opp_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    _writer(p)
    return ok(svc.contract_draft(db, _get(db, p, opp_id)))
