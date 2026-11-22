"""Business collection view with authorization independent of cash receipt entry."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, require_menu
from app.core.response import ok
from app.core.timeutil import today
from app.services import collection_service as service

router = APIRouter(prefix="/collections", tags=["回款台账"])
Menu = Depends(require_menu("contracts", "receivables"))


@router.get("/monthly-receipts")
def monthly_receipts(page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                     p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services import business_workbook, collection_workbook
    wb = business_workbook.active_source(db, p, "collections")
    if wb:
        result = collection_workbook.page(db, wb, p, basis="current", page=page, size=size)
        return ok({"period": today().strftime("%Y-%m"), "count": result["total"], "amount": result["summary"]["received"]["value"],
                   "items": result["items"], "warning": result["sheet"]["warning"]})
    return ok(service.monthly_receipts(db, p, page, size))


@router.get("")
def collection_board(year: int | None = Query(None, ge=2000, le=2099), basis: Literal["due", "sign"] = "due",
                     bucket: Literal["", "all", "before", "current", "future", "undated"] = "",
                     keyword: str = Query("", max_length=120), owner_id: int | None = Query(None, gt=0),
                     state: Literal["open", "settled", "pending", "unreported", "all"] = "open",
                     page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                     p: Principal = Menu, db: Session = Depends(get_db)):
    from app.core.timeutil import month_of
    from app.services import business_workbook, collection_workbook, workbook_metrics
    wb = business_workbook.active_source(db, p, "collections")
    if wb is not None:
        T = today()
        cur_period = month_of(T)
        data = collection_workbook.page(db, wb, p, keyword=keyword, page=page, size=size, unpaid_only=(state == "open"))
        overall = data.get("overall_summary") or data.get("summary") or {}
        m_pay = workbook_metrics.collection_metrics(wb, cur_period, T)
        cards = data.get("collection_cards") or []
        return ok({
            "source_mode": "workbook",
            "total": data["total"],
            "page": data["page"],
            "size": data["size"],
            "overall_summary": overall,
            "month_received": {"period": cur_period, "title": "本月已回款", "amount": m_pay["amount"], "count": m_pay["count"], "available": m_pay["available"]},
            "collection_cards": cards,
            "totals": [
                {"key": "all_contract_amount", "label": "全部合同金额", "amount": overall.get("amount", {}).get("value"), "count": overall.get("count")},
                {"key": "outstanding", "label": "未收金额", "amount": overall.get("outstanding", {}).get("value"), "count": data["total"]},
                {"key": "month_received", "label": "本月已回款", "amount": m_pay["amount"], "count": m_pay["count"]},
                *[{"key": c["key"], "label": c["title"], "period": c["period"], "amount": c["amount"], "count": c["count"]} for c in cards],
            ],
            "owners": [{"id": idx, "name": name} for idx, name in enumerate(data.get("filter_options", {}).get("owners", []), start=1)],
            "items": data["items"],
            "note": "固定来源口径：合并「2026年销售合同」与「2026年前（已签单个项目+未回款）」；区分本月已回款与当月/次月预估回款金额。",
        })
    return ok(service.board(db, p, year=year or today().year, basis=basis, bucket=bucket, keyword=keyword,
                            owner_id=owner_id, state=state, page=page, size=size))


class FollowupIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    kind: Literal["note", "request", "reply"]
    content: str = Field(min_length=1, max_length=1600)
    expected_on: date | None = None
    recipient_id: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def recipient_matches_action(self):
        if (self.kind == "request") != (self.recipient_id is not None):
            raise ValueError("发起催办须指定一位回复人")
        return self


@router.get("/{contract_id}/{plan_key}")
def item(contract_id: int, plan_key: int, p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(service.detail(db, p, contract_id, plan_key))


@router.post("/{contract_id}/{plan_key}")
def follow_up(contract_id: int, plan_key: int, body: FollowupIn, p: Principal = Menu, db: Session = Depends(get_db)):
    out = service.record(db, p, contract_id, plan_key, body)
    db.commit()
    return ok(out)
