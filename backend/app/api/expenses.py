"""报销归集（PRD 3.3 / FR-05-04 补充归集）：列表、聚合统计、待归集池补充归集（留痕）、详情溯源、导出。"""
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db, get_or_404
from app.core.deps import Principal, client_ip, require_button, require_menu
from app.core.response import forbidden, ok, page_data
from app.core.timeutil import month_range, today
from app.models import BizExpenseReport, BizProject, SysDepartment
from app.services import dict_service, expense_service

router = APIRouter(prefix="/expenses", tags=["expenses"])
Menu = Depends(require_menu("expenses"))


def xlsx_response(data: bytes, filename: str) -> Response:
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


def _query(status, project, dept, category, start, end, applicant, keyword, pay_status=None):
    return expense_service.build_query(status=status, project=project or None, dept=dept or None,
                                       category=category or None, start=start, end=end, applicant=applicant or None,
                                       keyword=keyword or None, pay_status=pay_status)


@router.get("")
def list_expenses(status: str = "approved", project: str = "", dept: str = "", category: str = "",
                  start: date | None = None, end: date | None = None, applicant: str = "", keyword: str = "",
                  pay_status: int | None = None, page: int = 1, page_size: int = 20, p: Principal = Menu,
                  db: Session = Depends(get_db)):
    q = _query(status, project, dept, category, start, end, applicant, keyword, pay_status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(BizExpenseReport.expense_date.desc(), BizExpenseReport.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    projects, depts = expense_service._names(db)
    return ok(page_data([expense_service.row(r, projects, depts) for r in rows], total, page, page_size,
                        summary=expense_service.summary(db, q)))


@router.get("/stats")
def stats(start: date | None = None, end: date | None = None, period: str = "", p: Principal = Menu,
          db: Session = Depends(get_db)):
    if not start or not end:
        start, end = month_range(period) if period else month_range(ref=today())
    return ok(expense_service.stats(db, start, end))


@router.get("/options")
def options(p: Principal = Menu, db: Session = Depends(get_db)):
    projects = db.scalars(select(BizProject).where(BizProject.is_deleted.is_(False)).order_by(BizProject.project_code)).all()
    return ok({"projects": [{"code": x.project_code, "name": x.name, "business_line": x.business_line, "status": x.status}
                            for x in projects],
               "depts": [{"code": d.dept_code, "name": d.name} for d in db.scalars(select(SysDepartment).order_by(SysDepartment.sort))],
               "categories": dict_service.get(db, "expense_category", "list", []),
               "can_allocate": p.can("expense:allocate")})


class AllocateBody(BaseModel):
    ids: list[int] = Field(min_length=1)
    project_code: str | None = None  # 空 = 公共费用
    remark: str | None = None


@router.post("/allocate")
def allocate(body: AllocateBody, request: Request, p: Principal = Depends(require_button("expense:allocate")),
             db: Session = Depends(get_db)):
    if not p.has_menu("expenses"):
        raise forbidden()
    r = expense_service.allocate(db, body.ids, body.project_code or None, p.id, remark=body.remark, ip=client_ip(request))
    db.commit()
    return ok(r, message=f"已归集 {r['count']} 张报销单")


@router.get("/export")
def export(request: Request, status: str = "approved", project: str = "", dept: str = "", category: str = "",
           start: date | None = None, end: date | None = None, applicant: str = "", keyword: str = "",
           pay_status: int | None = None, p: Principal = Menu, db: Session = Depends(get_db)):
    q = _query(status, project, dept, category, start, end, applicant, keyword, pay_status)
    title = f"报销归集明细（{start or '起'} ~ {end or '今'}）"
    data = expense_service.export_xlsx(db, q, title)
    log_op(db, p.id, "expense.export", "expense", None, after={"status": status, "project": project, "dept": dept,
                                                               "start": start, "end": end, "keyword": keyword},
           ip=client_ip(request))
    db.commit()
    return xlsx_response(data, f"报销归集_{today():%Y%m%d}.xlsx")


@router.get("/{report_id}")
def detail(report_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    r = get_or_404(db, BizExpenseReport, report_id, "报销单")
    return ok(expense_service.detail(db, r))
