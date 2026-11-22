"""Append-only feedback belongs to a source project, including all of its tender lots."""
from sqlalchemy import select

from app.core.audit import log_op
from app.core.response import bad_request, conflict, forbidden, not_found
from app.core.timeutil import now, today
from app.models import ContractWorkbook, SysUser, WorkbookProjectFeedback as Feedback
from app.services import business_workbook as source


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
    if not row or not row.get("record"):
        raise not_found("项目")
    record = row["record"]
    evidence = dict(record["keys"])
    # Work orders have permanent numbers. For tender projects use the unique
    # project/client/party identity so moving a row or updating its price is safe.
    kind = "external" if view == "opportunities" or record.get("external") else "entity"
    key = f"{kind}:{evidence[kind]}" if kind in evidence else None
    can_write = p.can("opp:write") if view == "opportunities" else p.can("feedback:write") or p.can("milestone:write")
    entries = select(Feedback, SysUser.name).join(SysUser, Feedback.author_id == SysUser.id).where(
        Feedback.workbook_id == wb.id, Feedback.sheet_id == sheet_id, Feedback.row_key == key).order_by(Feedback.id.desc())
    if body is not None:
        if not can_write:
            raise forbidden("无此类项目的反馈权限")
        if not key:
            raise conflict("原表有相同项目标识，请核对后再反馈")
        last = db.execute(entries.limit(1)).first()
        if (last[0].id if last else None) != body.expected_latest_id:
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
    return {"owner": record.get("owner"), "can_write": bool(key and can_write), "ambiguous": not key, "today": today(),
            "items": [{"id": r.id, "author": author, "created_at": r.created_at, "status": r.status,
                       "content": r.content, "problems": r.problems, "next_step": r.next_step, "next_due_on": r.next_due_on}
                      for r, author in db.execute(entries)]}
