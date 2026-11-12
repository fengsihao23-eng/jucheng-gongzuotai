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


@router.get("")
def collection_board(year: int | None = Query(None, ge=2000, le=2099), basis: Literal["due", "sign"] = "due",
                     bucket: Literal["", "all", "before", "current", "future", "undated"] = "",
                     keyword: str = Query("", max_length=120), owner_id: int | None = Query(None, gt=0),
                     state: Literal["open", "settled", "pending", "unreported", "all"] = "open",
                     page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                     p: Principal = Menu, db: Session = Depends(get_db)):
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
