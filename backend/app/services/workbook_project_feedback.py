"""Project feedback and archive classification survive source row moves."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.audit import log_op
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import now, today
from app.models import ContractWorkbook, SysUser, WorkbookProjectFeedback as Feedback
from app.services import business_workbook as source

ArchiveCategory = Literal["competitor", "canceled", "converted"]
ArchiveFilter = ArchiveCategory | Literal["unarchived", ""]


class ProjectArchiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: ArchiveCategory
    expected_latest_id: int = Field(gt=0)


def tracking_key(view, row):
    # Opportunity tickets have permanent numbers. Tender projects use the
    # unique project/client/party identity when there is no external number.
    record = (row or {}).get("record") or {}
    evidence = dict(record.get("keys", ()))
    kind = "external" if view == "opportunities" or record.get("external") else "entity"
    return f"{kind}:{evidence[kind]}" if kind in evidence else None


def project_entries(workbook_id, sheet_id, key):
    return select(Feedback).where(Feedback.workbook_id == workbook_id, Feedback.sheet_id == sheet_id,
                                  Feedback.row_key == key).order_by(Feedback.id.desc())


def latest_feedback(db, workbook_id, sheet_id, key):
    return db.scalar(project_entries(workbook_id, sheet_id, key).limit(1))


def archive_state(latest, can_write):
    archived = bool(latest and latest.archive_category)
    can_archive = bool(can_write and latest and latest.status == "done" and not archived)
    return {"archive": {"category": latest.archive_category, "archived_at": latest.archived_at} if archived else None,
            "can_archive": can_archive, "archive_feedback_id": latest.id if can_archive else None}


def set_archive(db, p, latest, body, title):
    if not latest or latest.id != body.expected_latest_id or latest.status != "done":
        raise conflict("项目反馈已变更，请重新核对完成状态后归档")
    if latest.archive_category:
        if latest.archive_category != body.category:
            raise conflict("项目已归档，请刷新后查看归档分类")
        return
    latest.archive_category = body.category
    latest.archived_at = now()
    log_op(db, p.id, "projects.workbook.archive", "workbook_project_feedback", latest.id,
           after={"project": title, "sheet": latest.sheet_id, "category": body.category})
    db.flush()


def categorize(db, wb, view, sheets, rows, archive_filter=""):
    categories = {(sheet_id, key): category for sheet_id, key, category in db.execute(
        select(Feedback.sheet_id, Feedback.row_key, Feedback.archive_category)
        .where(Feedback.workbook_id == wb.id, Feedback.archive_category.is_not(None)))}
    sheets_by_id = {sheet.id: sheet for sheet in sheets}
    result = []
    for row in rows:
        sheet = sheets_by_id[row["sheet_id"]]
        key = tracking_key(view, sheet.rows[row["group_row"]])
        category = categories.get((sheet.id, key))
        if not archive_filter or (category or "unarchived") == archive_filter:
            result.append({**row, "archive_category": category})
    return result


def history(db, p, view, sheet_id, row_number, revision, *, body=None):
    wb = source.active_source(db, p, view)
    if not wb:
        raise not_found("项目来源")
    if body is not None:
        wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.id == wb.id)
                       .with_for_update().execution_options(populate_existing=True))
        if not wb or not source.can_access_view(p, wb.config, view):
            raise forbidden()
    if wb.revision != revision:
        raise conflict("原表已更新，请重新打开项目后填写反馈")
    sheets = source.module_sheets(wb, view) if view == "tender" else [source.opportunity_sheet(wb)]
    sheet = next((s for s in sheets if s and s.id == sheet_id and not s.warning), None)
    row = sheet.rows.get(row_number) if sheet else None
    if row and row.get("parent"):
        row = sheet.rows.get(row["parent"])
    if not row or not row.get("record") or row["row"] in sheet.removed_rows:
        raise not_found("项目")
    record = row["record"]
    key = tracking_key(view, row)
    can_write = p.can("opp:write") if view == "opportunities" else p.can("feedback:write") or p.can("milestone:write")
    entries = project_entries(wb.id, sheet_id, key).add_columns(SysUser.name).join(SysUser, Feedback.author_id == SysUser.id)
    if body is not None:
        if not can_write:
            raise forbidden("无此类项目的反馈权限")
        if not key:
            raise conflict("原表有相同项目标识，请核对后再反馈")
        last = latest_feedback(db, wb.id, sheet_id, key)
        if last and last.archive_category:
            raise conflict("项目已归档，历史反馈可在过程记录中查看")
        if (last.id if last else None) != body.expected_latest_id:
            raise conflict("项目已有新反馈，请核对过程记录后再提交")
        if body.status == "blocked" and not body.problems:
            raise bad_request("请填写受阻原因")
        if body.next_due_on and not body.next_step:
            raise bad_request("请填写该节点对应的下一步安排")
        entry = Feedback(workbook_id=wb.id, sheet_id=sheet_id, row_key=key, author_id=p.id, created_at=now(),
                         **body.model_dump(exclude={"revision", "expected_latest_id"}))
        db.add(entry)
        db.flush()
        log_op(db, p.id, "projects.workbook.feedback", "workbook_project_feedback", entry.id,
               after={"project": record["name"], "status": entry.status, "sheet": sheet_id})
    rows = db.execute(entries).all()
    state = archive_state(rows[0][0] if rows else None, can_write)
    return {"owner": record.get("owner"), "can_write": bool(key and can_write and not state["archive"]),
            "ambiguous": not key, "today": today(), **state,
            "items": [{"id": r.id, "author": author, "created_at": r.created_at, "status": r.status,
                       "content": r.content, "problems": r.problems, "next_step": r.next_step, "next_due_on": r.next_due_on}
                      for r, author in rows]}
