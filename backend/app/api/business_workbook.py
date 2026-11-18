"""Source-only business pages and their revision-bound source cells."""
from typing import Literal

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import forbidden, not_found, ok
from app.core.timeutil import today
from app.services import business_workbook as svc, collection_followups as followups, contract_workbook_sync as sync

router = APIRouter(prefix="/business-workbook", tags=["固定表格经营数据"])
View = Literal["contracts", "collections", "opportunities", "tender", "board"]
HEADERS = {"Cache-Control": "no-store"}


@router.post("/sync")
def synchronize(view: View = "contracts", p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    wb = svc.active_source(db, p, view)
    if wb is None:
        raise not_found("固定来源")
    if not svc.source_info(wb, p)["can_sync"]:
        raise forbidden("仅绑定账号可主动检查更新")
    return ok(sync.sync(db, wb.source_key), headers=HEADERS)


@router.get("/{view}")
def page(view: View, sheet: str = "", keyword: str = Query("", max_length=100), year: str = "",
         page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100), raw: bool = False,
         start: str = "", end: str = "", basis: str = "", period: str = "", status: str = "",
         region: str = "", customer: str = "", owner: str = "", business_line: str = "", sign_month: str = "", source_type: str = "",
         collection_state: str = '', period_kind: str = '', contact: str = '', unpaid_only: bool = False,
         p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(svc.page(db, p, view, sheet_id=sheet, keyword=keyword, year=year, page=page, size=size, raw=raw,
                       start=start, end=end, basis=basis, period=period, status=status, region=region, customer=customer,
                       owner=owner, business_line=business_line, sign_month=sign_month, source_type=source_type,
                       collection_state=collection_state, period_kind=period_kind, contact=contact, unpaid_only=unpaid_only), headers=HEADERS)


@router.get('/collections/periods/{period}')
def collection_period_projects(period: str, page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.collection_plans import page as planned_page
    wb = svc.active_source(db, p, 'collections')
    if wb is None:
        raise not_found('固定来源')
    return ok(planned_page(db, wb, p, period, page=page, size=size), headers=HEADERS)


@router.get('/collections/items/{sheet}/{row_number}/report')
def collection_project_report(sheet: str, row_number: int, revision: str,
                              p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.performance_details import detail as project_detail
    from app.services.collection_plans import for_project
    result = project_detail(db, p, sheet, row_number, revision, view='collections')
    wb = svc.active_source(db, p, 'collections')
    return ok({**result, 'plans': for_project(wb, sheet, row_number)}, headers=HEADERS)


@router.get('/opportunities/export', response_class=Response)
def export_opportunities(request: Request, revision: str = Query(..., max_length=64), sheet: str = '',
                         keyword: str = Query('', max_length=100), status: str = '', owner: str = '', contact: str = '',
                         start: str = '', end: str = '', year: str = '',
                         p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.opportunity_workbook import export

    wb = svc.active_source(db, p, 'opportunities')
    payload, count = export(wb, sheet_id=sheet, revision=revision, keyword=keyword, status=status,
                            owner=owner, contact=contact, start=start, end=end, year=year)
    log_op(db, p.id, 'opportunities.export_workbook', 'contract_workbook', wb.id,
           after={'revision': revision, 'rows': count}, ip=client_ip(request))
    db.commit()
    name = quote(f'市场商机_{today():%Y%m%d}.xlsx')
    return Response(payload, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={**HEADERS, 'Content-Disposition': f"attachment; filename*=UTF-8''{name}"})


@router.get("/{view}/rows/{sheet}/{row_number}")
def detail(view: View, sheet: str, row_number: int, revision: str,
           p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(svc.detail(db, p, view, sheet, row_number, revision), headers=HEADERS)


@router.get('/contracts/items/{sheet}/{row_number}')
def contract_detail(sheet: str, row_number: int, revision: str = '',
                    p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.performance_details import detail
    return ok(detail(db, p, sheet, row_number, revision), headers=HEADERS)


@router.get('/collections/items/{sheet}/{row_number}')
def collection_detail(sheet: str, row_number: int, revision: str = '',
                      p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.performance_details import detail
    return ok(detail(db, p, sheet, row_number, revision, view='collections'), headers=HEADERS)


class ContractReportQueryIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    question: str = Field(min_length=1, max_length=500)
    year: str = Field(default='', max_length=16)
    start: str = Field(default='', max_length=16)
    end: str = Field(default='', max_length=16)


class CollectionRequestIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    revision: str = Field(min_length=1, max_length=64)
    recipient_id: int = Field(gt=0)
    content: str = Field(min_length=1, max_length=1600)


class CollectionReplyIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    content: str = Field(min_length=1, max_length=1600)
    expected_on: date | None = None
    stage: Literal['contact', 'approval', 'awaiting', 'blocked', 'reported_paid'] = 'contact'
    expected_report_id: int | None = Field(default=None, gt=0)
    problems: str | None = Field(default=None, max_length=2000)
    next_step: str | None = Field(default=None, max_length=1600)


class CollectionExecutionIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    revision: str = Field(min_length=1, max_length=64)
    stage: Literal['contact', 'approval', 'awaiting', 'blocked', 'reported_paid'] = 'contact'
    content: str = Field(min_length=1, max_length=2000)
    has_problem: bool = False
    problems: str | None = Field(default=None, max_length=2000)
    next_step: str | None = Field(default=None, max_length=2000)
    expected_on: date | None = None
    expected_progress_id: int | None = Field(default=None, gt=0)


class CollectionDraftIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    text: str = Field(min_length=1, max_length=2000)


class CollectionSummaryIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    scope: Literal['all', 'current_month', 'next_month', 'tracked', 'problems'] = 'all'
    owner: str = Field(default='', max_length=80)
    focus_prompt: str = Field(default='', max_length=1000)


class CollectionReportSaveIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(default='', max_length=200)
    scope: Literal['all', 'current_month', 'next_month', 'tracked', 'problems'] = 'all'
    owner: str = Field(default='', max_length=80)
    focus_prompt: str = Field(default='', max_length=1000)
    text: str = Field(default='', max_length=50000)


@router.post('/contracts/report-query')
def contract_report_query(body: ContractReportQueryIn, p: Principal = Depends(get_principal),
                          db: Session = Depends(get_db)):
    from app.services.workbook_query_report import query_performance_report
    return ok(query_performance_report(db, p, body.question, year=body.year, start=body.start, end=body.end), headers=HEADERS)


@router.get('/collections/items/{sheet}/{row_number}/execution')
def collection_item_execution(sheet: str, row_number: int, revision: str = '',
                              p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(followups.execution_detail(db, p, sheet, row_number, revision), headers=HEADERS)


@router.post('/collections/items/{sheet}/{row_number}/execution')
def submit_collection_execution(sheet: str, row_number: int, body: CollectionExecutionIn,
                                background: BackgroundTasks, p: Principal = Depends(get_principal),
                                db: Session = Depends(get_db)):
    notices = followups.submit_execution(db, p, sheet, row_number, body)
    db.commit()
    if notices:
        background.add_task(followups.deliver, notices)
    return ok(followups.execution_detail(db, p, sheet, row_number, body.revision), headers=HEADERS)


@router.post('/collections/items/{sheet}/{row_number}/draft')
def draft_collection_execution(sheet: str, row_number: int, body: CollectionDraftIn,
                               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(followups.draft_execution_text(db, p, sheet, row_number, body.text), headers=HEADERS)


@router.post('/collections/summary-report')
def collection_summary_report(body: CollectionSummaryIn, p: Principal = Depends(get_principal),
                              db: Session = Depends(get_db)):
    from app.services.workbook_query_report import build_collection_summary
    return ok(build_collection_summary(db, p, scope=body.scope, owner=body.owner, focus_prompt=body.focus_prompt), headers=HEADERS)


@router.get('/collections/saved-reports')
def list_collection_reports(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.workbook_query_report import list_saved_collection_reports
    return ok(list_saved_collection_reports(db, p), headers=HEADERS)


@router.get('/collections/saved-reports/{report_id}')
def get_collection_report(report_id: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.workbook_query_report import get_saved_collection_report
    return ok(get_saved_collection_report(db, p, report_id), headers=HEADERS)


@router.post('/collections/saved-reports')
def save_collection_report(body: CollectionReportSaveIn, p: Principal = Depends(get_principal),
                           db: Session = Depends(get_db)):
    from app.services.workbook_query_report import save_collection_report as save_report
    result = save_report(db, p, title=body.title, scope=body.scope, owner=body.owner,
                         focus_prompt=body.focus_prompt, text=body.text)
    db.commit()
    return ok(result, headers=HEADERS)


@router.get('/collections/items/{sheet}/{row_number}/followups')
def collection_followup_history(sheet: str, row_number: int, revision: str, p: Principal = Depends(get_principal),
                                db: Session = Depends(get_db)):
    return ok(followups.history(db, p, sheet, row_number, revision), headers=HEADERS)


@router.post('/collections/items/{sheet}/{row_number}/followups')
def request_collection_followup(sheet: str, row_number: int, body: CollectionRequestIn, background: BackgroundTasks,
                                p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    ident, notices = followups.request(db, p, sheet, row_number, body)
    db.commit()
    if notices:
        background.add_task(followups.deliver, notices)
    return ok(followups.thread(db, p, ident), headers=HEADERS)


@router.get('/collection-followups/{followup_id}')
def collection_followup(followup_id: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(followups.thread(db, p, followup_id), headers=HEADERS)


@router.post('/collection-followups/{followup_id}/reply')
def reply_collection_followup(followup_id: int, body: CollectionReplyIn, background: BackgroundTasks,
                              p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    notices = followups.reply(db, p, followup_id, body)
    db.commit()
    if notices:
        background.add_task(followups.deliver, notices)
    return ok(followups.thread(db, p, followup_id), headers=HEADERS)


@router.post('/collection-followups/{followup_id}/cancel')
def cancel_collection_followup(followup_id: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    followups.cancel(db, p, followup_id)
    db.commit()
    return ok(followups.thread(db, p, followup_id), headers=HEADERS)


@router.post('/collection-followups/{followup_id}/progress')
def update_collection_progress(followup_id: int, body: CollectionReplyIn, background: BackgroundTasks,
                               p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    notices = followups.progress(db, p, followup_id, body)
    db.commit()
    if notices:
        background.add_task(followups.deliver, notices)
    return ok(followups.thread(db, p, followup_id), headers=HEADERS)
