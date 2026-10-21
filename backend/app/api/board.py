"""FR-03 经营决策看板：六板块 + 毛利摘要（缓存 ≤5min，refresh=1 强制刷新）、对比查询、回款目标配置、晨报预览。"""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_button, require_menu
from app.core.response import ok
from app.core.storage import file_brief
from app.core.timeutil import month_of, prev_month, today
from app.services import board_service

router = APIRouter(prefix="/board", tags=["经营看板"])
Board = Depends(require_menu("board"))


@router.get("/overview")
def overview(refresh: int = 0, p: Principal = Board, db: Session = Depends(get_db)):
    data = board_service.overview(db, refresh=bool(refresh))
    return ok({**data, "can_config_target": p.can("board:target")})


@router.get("/compare")
def compare(metric: str = "payment_rate", a: str | None = None, b: str | None = None, p: Principal = Board,
            db: Session = Depends(get_db)):
    from app.core.timeutil import week_no, week_range
    from datetime import timedelta
    a = a or month_of(today())
    try:
        if not b:
            b = week_no(week_range(a)[0] - timedelta(days=7)) if "-W" in a else prev_month(a)
        return ok(board_service.compare(db, metric, a, b))
    except ValueError as exc:
        from app.core.response import bad_request
        raise bad_request(f"期间格式错误（月 YYYY-MM / 周 YYYY-Www）：{exc}")


@router.get("/targets")
def get_targets(period: str | None = None, p: Principal = Board, db: Session = Depends(get_db)):
    return ok(board_service.get_targets(db, period or month_of(today())))


@router.put("/targets")
def set_targets(request: Request, period: str = Body(...), values: dict[str, Decimal] = Body(...),
                p: Principal = Depends(require_button("board:target")), db: Session = Depends(get_db)):
    if not p.has_menu("board"):
        from app.core.response import forbidden
        raise forbidden()
    return ok(board_service.set_targets(db, period, values, p.id, client_ip(request)))


@router.get("/morning")
def morning(d: date | None = None, p: Principal = Board, db: Session = Depends(get_db)):
    """晨报预览（与 08:00 推送内容一致）。"""
    r = board_service.build_morning_report(db, d or today(), uploader_id=p.id)
    return ok({"text_markdown": r["text_markdown"], "summary": r["summary"], "image": file_brief(r["image_file"])})
