"""汇报提交（FR-07-05 / 7.2）：周报/月报/日报提交率（按部门/按人）、未提交名单、近 8 期趋势、一键催交、总结文本。"""
from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, require_menu
from app.core.response import forbidden, ok
from app.services import submission_service as ss

router = APIRouter(prefix="/submissions", tags=["汇报提交"])
Menu = Depends(require_menu("submissions"))


@router.get("")
def overview(type: str = "weekly", period: str | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    data = ss.stats(db, type, period, with_trend=True)
    data["can_remind"] = p.can("submissions:remind")
    data["remind_history"] = ss.remind_history(db, type, data["period"])
    data["periods"] = list(reversed(ss.recent_periods(type, ss.current_period(type), 12)))
    return ok(data)


@router.get("/summary")
def summary(type: str = "weekly", period: str | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    s = ss.stats(db, type, period)
    return ok({"period": s["period"], "summary": s["summary"], "rate": s["rate"], "missing": s["missing"]})


@router.post("/remind")
def remind(type: str = Body("weekly"), period: str | None = Body(None), user_ids: list[int] | None = Body(None),
           p: Principal = Menu, db: Session = Depends(get_db)):
    if not p.can("submissions:remind"):
        raise forbidden("无此操作权限")
    return ok(ss.remind(db, type, period, user_ids, operator_id=p.id))


@router.post("/sync")
def sync(hours: int = Body(24 * 7, embed=True), p: Principal = Menu, db: Session = Depends(get_db)):
    """立即从企微汇报同步（默认近 7 天）。"""
    if not p.can("submissions:remind"):
        raise forbidden("无此操作权限")
    from datetime import timedelta

    from app.core.timeutil import now
    end = now()
    r = ss.sync_journals(db, end - timedelta(hours=max(1, min(hours, 24 * 62))), end)
    try:
        from app.services import board_service
        board_service.invalidate()
    except ImportError:
        pass
    return ok(r)
