"""站内通知（FR-12-02）：右上角铃铛。"""
from fastapi import APIRouter, Body, Depends
from sqlalchemy import String, cast, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, get_principal
from app.core.response import ok, page_data
from app.models import BizCollectionEntry, BizCollectionFollowup, BizContract, OpsProject, OpsTask, OpsTaskDiscussion, OpsDigestSnapshot, SysNotify

router = APIRouter(prefix="/notifications", tags=["通知"])


@router.get("")
def my_notifications(page: int = 1, page_size: int = 20, unread: bool = False,
                     p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.services.collection_service import scoped
    from app.services.operations_notifications import project_assignment_access
    from app.services.operations_policy import task_access
    from app.services import operations_scope as scope, operations_reports as reports
    from app.services import key_projects, market_service, record_followups
    from app.models import BizProject, BizProjectMilestone, RptReport

    task_notice = ("ops_task", "ops_task_reminder")
    accessible_keys = key_projects.visible_projects(p).where(BizProject.is_key.is_(True)).with_only_columns(BizProject.id)
    accessible_general = key_projects.visible_projects(p, is_key=False).where(BizProject.is_key.is_(False)).with_only_columns(BizProject.id)
    visible_project_task = exists(select(BizProjectMilestone.id).where(
        cast(BizProjectMilestone.id, String) == func.split_part(SysNotify.ref_id, ":", 1),
        or_((SysNotify.ref_type == key_projects.REF) & BizProjectMilestone.project_id.in_(accessible_keys),
            (SysNotify.ref_type == key_projects.GENERAL_REF) & BizProjectMilestone.project_id.in_(accessible_general))))
    visible_team_notice = exists(select(BizProject.id).where(cast(BizProject.id, String) == SysNotify.ref_id,
        BizProject.is_deleted.is_(False), key_projects.membership_scope(p, is_key=None)))
    visible_milestone = exists(select(BizProjectMilestone.id).where(cast(BizProjectMilestone.id, String) == SysNotify.ref_id,
        BizProjectMilestone.project_id.in_(accessible_general)))
    report_refs = select(SysNotify.ref_id).where(SysNotify.user_id == p.id, SysNotify.channel == "inapp", SysNotify.ref_type == "rpt_report")
    hidden_reports = [str(row.id) for row in db.scalars(select(RptReport).where(
        RptReport.report_type.in_(market_service.TEAM_SCOPED_REPORT_TYPES), cast(RptReport.id, String).in_(report_refs))
        .execution_options(include_removed_records=True)) if not market_service.can_read_project_report(db, p, row)]
    scoped_refs = ("ops_project", *task_notice, "ops_discussion", "ops_request_reminder", "ops_digest", "collection_entry",
        key_projects.REF, key_projects.GENERAL_REF, key_projects.TEAM_REF, "biz_project", "milestone",
        "project_weekly", "morning_report", record_followups.NOTICE)
    visible_project = exists(select(OpsProject.id).where(
        cast(OpsProject.id, String) == SysNotify.ref_id, project_assignment_access(p)))
    visible_task = exists(select(OpsTask.id).where(
        cast(OpsTask.id, String) == func.split_part(SysNotify.ref_id, ":", 1), task_access(p))) & p.has_menu("operations")
    discussion_types = ("ops_discussion", "ops_request_reminder")
    visible_discussion = exists(select(OpsTaskDiscussion.id).join(OpsTask, OpsTask.id == OpsTaskDiscussion.task_id).where(
        cast(OpsTaskDiscussion.id, String) == func.split_part(SysNotify.ref_id, ":", 1), task_access(p))) & p.has_menu("operations")
    report_ids = []
    if p.has_menu("operations"):
        own_report_notices = select(SysNotify.ref_id).where(SysNotify.user_id == p.id, SysNotify.ref_type == "ops_digest")
        for report in db.scalars(select(OpsDigestSnapshot).where(cast(OpsDigestSnapshot.id, String).in_(own_report_notices),
                OpsDigestSnapshot.project_id.in_(scope.content_project_ids(db, p, "reports")))):
            if reports.can_read(db, p, report):
                report_ids.append(str(report.id))
    visible_collection = exists(select(BizCollectionEntry.id).join(BizCollectionFollowup).where(
        cast(BizCollectionEntry.id, String) == SysNotify.ref_id,
        BizCollectionFollowup.contract_id.in_(scoped(p).with_only_columns(BizContract.id))))
    q = select(SysNotify).where(SysNotify.user_id == p.id, SysNotify.channel == "inapp",
        or_(SysNotify.ref_type.is_(None) & (SysNotify.msg_type != "project_weekly"), ~SysNotify.ref_type.in_(scoped_refs),
            visible_project_task,
            SysNotify.ref_type.in_((key_projects.TEAM_REF, "biz_project", "project_weekly")) & visible_team_notice,
            (SysNotify.ref_type == "milestone") & visible_milestone,
            (SysNotify.ref_type == record_followups.NOTICE) & SysNotify.ref_id.in_(record_followups.visible_notice_refs(db, p)),
            (SysNotify.ref_type == "ops_project") & visible_project,
            SysNotify.ref_type.in_(task_notice) & visible_task,
            SysNotify.ref_type.in_(discussion_types) & visible_discussion,
            (SysNotify.ref_type == "ops_digest") & SysNotify.ref_id.in_(report_ids),
            (SysNotify.ref_type == "collection_entry") & visible_collection & (p.has_menu("contracts") or p.has_menu("receivables"))))
    if hidden_reports:
        q = q.where(or_(SysNotify.ref_type.is_distinct_from("rpt_report"), SysNotify.ref_id.not_in(hidden_reports)))
    unread_n = db.scalar(select(func.count()).select_from(q.where(SysNotify.read_status.is_(False)).subquery()))
    if unread:
        q = q.where(SysNotify.read_status.is_(False))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(SysNotify.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": r.id, "type": r.msg_type, "title": r.title, "content": r.content, "link": r.link,
              "read": r.read_status, "created_at": r.created_at} for r in rows]
    return ok(page_data(items, total, page, page_size, unread=unread_n))


@router.post("/read")
def mark_read(ids: list[int] | None = Body(default=None, embed=True), p: Principal = Depends(get_principal),
              db: Session = Depends(get_db)):
    q = update(SysNotify).where(SysNotify.user_id == p.id, SysNotify.channel == "inapp")
    if ids:
        q = q.where(SysNotify.id.in_(ids))
    db.execute(q.values(read_status=True))
    db.commit()
    return ok()


account = APIRouter(prefix="/account", tags=["个人中心"])


@account.get("/approvals")
def my_approvals(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """我提交的企微审批与自动登记结果（与 MCP oa.pending_approvals 同源）。"""
    from app.mcp_server.server import t_pending_approvals
    return ok(t_pending_approvals(db, p, type="submitted")["data"])


@account.get("/expenses")
def my_expenses(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.models import BizExpenseReport
    rows = db.scalars(select(BizExpenseReport).where(BizExpenseReport.applicant_id == p.id)
                      .order_by(BizExpenseReport.expense_date.desc()).limit(30)).all()
    return ok([{"approval_no": r.approval_no, "amount": r.amount, "category": r.category, "expense_date": r.expense_date,
                "project_code": r.project_code, "status": r.status, "pay_status": r.pay_status} for r in rows])
