"""业绩统计（FR-05-07）与项目整体运营看板。菜单 performance。"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, ok
from app.core.storage import file_brief
from app.core.timeutil import today, week_no
from app.models import RptReport, SysFile
from app.services import performance_service as svc

router = APIRouter(prefix="/performance", tags=["performance"])
Menu = Depends(require_menu("performance"))
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _period(period: str | None) -> str | None:
    if period and (len(period) != 7 or period[4] != "-"):
        raise bad_request("period 格式应为 yyyy-MM")
    return period or None


@router.get("/overview")
def overview(period: str | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services import business_workbook
    business_workbook.active_source(db, p, "performance")
    return ok(svc.overview(db, _period(period)))


@router.get("/owners")
def owners(period: str | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services import business_workbook
    business_workbook.active_source(db, p, "performance")
    return ok(svc.by_owner(db, _period(period)))


@router.get("/trend")
def trend(period: str | None = None, months: int = 12, p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services import business_workbook
    business_workbook.active_source(db, p, "performance")
    return ok(svc.trend(db, _period(period), max(1, min(months, 36))))


@router.get("/board")
def board(business_line: str = "", p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services import business_workbook
    business_workbook.active_source(db, p, "performance")
    return ok(svc.board(db, business_line))


def _xlsx(data: bytes, name: str) -> Response:
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/export")
def export(request: Request, period: str | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services import business_workbook
    business_workbook.active_source(db, p, "performance")
    data, m = svc.performance_xlsx(db, _period(period))
    log_op(db, p.id, "performance.export", "report", m["period"], ip=client_ip(request))
    db.commit()
    return _xlsx(data, f"业绩统计_{m['period']}.xlsx")


@router.get("/board/export")
def board_export(request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    from app.services import business_workbook
    business_workbook.active_source(db, p, "performance")
    data, _ = svc.board_xlsx(db)
    log_op(db, p.id, "operation_board.export", "report", str(today()), ip=client_ip(request))
    db.commit()
    return _xlsx(data, f"项目运营看板_{today():%Y%m%d}.xlsx")


@router.get("/reports")
def reports(p: Principal = Menu, db: Session = Depends(get_db)):
    """业绩/运营看板历史快照（定时任务生成）。"""
    rows = db.scalars(select(RptReport).where(RptReport.report_type.in_(["performance_monthly", "operation_board"]))
                      .order_by(RptReport.created_at.desc()).limit(30)).all()
    files = {f.id: f for f in db.scalars(select(SysFile).where(SysFile.id.in_([i for r in rows for i in r.file_ids])))}
    return ok([{"id": r.id, "report_type": r.report_type, "period": r.period, "title": r.title, "version": r.version,
                "trigger": r.trigger, "metrics": r.metrics, "created_at": r.created_at,
                "files": [file_brief(files.get(i)) for i in r.file_ids if files.get(i)]} for r in rows])


@router.post("/reports/generate")
def generate(kind: str = "performance_monthly", period: str | None = None, p: Principal = Menu,
             db: Session = Depends(get_db)):
    if kind not in ("performance_monthly", "operation_board"):
        raise bad_request("kind 仅支持 performance_monthly / operation_board")
    period = _period(period) or (f"{today():%Y-%m}" if kind == "performance_monthly" else week_no(today()))
    rpt, f = svc.generate_report(db, kind, period, trigger="manual", user_id=p.id, notify=False)
    db.commit()
    return ok({"id": rpt.id, "title": rpt.title, "version": rpt.version, "file": file_brief(f)})
