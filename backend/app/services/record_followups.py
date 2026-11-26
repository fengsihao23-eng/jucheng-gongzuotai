"""Shared timeline, assignments, reply, and progress for visible business records."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.orm import aliased

from app.core.audit import log_op
from app.core.deps import build_principal
from app.core.response import BizError, bad_request, conflict, forbidden, not_found
from app.core.timeutil import now
from app.models import (BizProject, BizProjectFeedback, ContractWorkbook, KeyProjectTaskUpdate,
                        RecordFollowup as Entry, RptReport, SysNotify, SysUser, WorkbookProjectFeedback)
from app.services import notify_service, operations_scope, record_followup_targets as targets, workbook_project_feedback as workbook_feedback

NOTICE = "record_followup"


class FollowupIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    client_id: UUID
    action: Literal["note", "request", "reply", "progress"]
    content: str = Field(min_length=1, max_length=6000)
    recipient_id: int | None = Field(None, gt=0)
    reply_to_id: int | None = Field(None, gt=0)
    status: Literal["doing", "blocked", "done"] | None = None
    problems: str = Field("", max_length=6000)
    next_step: str = Field("", max_length=6000)
    next_due_on: date | None = None
    expected_progress: str = Field("", max_length=80)

    @model_validator(mode="after")
    def fields_match_action(self):
        if self.action == "request" and not self.recipient_id:
            raise ValueError("提出要求时请选择回复人")
        if self.action == "reply" and (not self.reply_to_id or self.recipient_id):
            raise ValueError("请选择要回复的要求")
        if self.reply_to_id and self.action != "reply":
            raise ValueError("回复必须关联一条要求")
        if self.recipient_id and self.action not in {"note", "request"}:
            raise ValueError("仅可为留言或要求选择接收人")
        if self.action == "progress":
            if not self.status:
                raise ValueError("请选择完成情况")
            if self.status == "blocked" and not self.problems:
                raise ValueError("请填写遇到的阻碍")
            if self.next_due_on and not self.next_step:
                raise ValueError("请填写下次跟进对应的安排")
        elif self.status or self.problems or self.next_step or self.next_due_on:
            raise ValueError("进展字段仅用于提交进展")
        return self


def _entries(record):
    return select(Entry).where(Entry.target_kind == record.kind, Entry.target_key == record.key)


def _can_request(p, record):
    return p.is_admin or "ZB-01" in p.role_codes or p.can("record:manage") or record.manager or p.id in record.owner_ids


def _assigned(db, p, record):
    return db.scalar(_entries(record).with_only_columns(Entry.id).where(Entry.action == "request", Entry.recipient_id == p.id).limit(1)) is not None


def _can_feedback(db, p, record):
    return record.writer or _can_request(p, record) or _assigned(db, p, record)


def _archive_feedback(db, record):
    if record.archive_key:
        return workbook_feedback.latest_feedback(db, record.workbook, record.sheet, record.archive_key)


def _legacy_rows(db, record):
    if record.legacy is None:
        return []
    kind, ident = record.legacy
    if kind == "workbook":
        q = select(WorkbookProjectFeedback, SysUser.name).outerjoin(SysUser, SysUser.id == WorkbookProjectFeedback.author_id)
        q = q.where(WorkbookProjectFeedback.workbook_id == record.workbook,
                    WorkbookProjectFeedback.sheet_id == record.sheet, WorkbookProjectFeedback.row_key == ident)
        fields = lambda row: (row.status, row.problems, row.next_step, row.next_due_on)
    else:
        q = select(BizProjectFeedback, SysUser.name).outerjoin(SysUser, SysUser.id == BizProjectFeedback.reporter_id)
        if kind == "key-task":
            q = q.join(KeyProjectTaskUpdate, KeyProjectTaskUpdate.feedback_id == BizProjectFeedback.id).where(KeyProjectTaskUpdate.milestone_id == ident)
        else:
            q = q.where(BizProjectFeedback.project_id == ident)
        fields = lambda row: ("blocked" if row.risk_level >= 2 else "doing", "", "", None)
    model = WorkbookProjectFeedback if kind == "workbook" else BizProjectFeedback
    mirrored = {key for key in db.scalars(_entries(record).with_only_columns(Entry.legacy_key).where(Entry.legacy_key.is_not(None)))}
    return [{"key": f"{kind}-{row.id}", "action": "progress", "author": name or "历史成员", "created_at": row.created_at,
             "content": row.content, "status": status, "problems": problems, "next_step": next_step, "next_due_on": due}
            for row, name in db.execute(q.order_by(model.id.desc()).limit(100))
            if f"{kind}-{row.id}" not in mirrored
            for status, problems, next_step, due in [fields(row)]]


def _latest_progress(db, record, legacy):
    row = db.scalar(_entries(record).where(Entry.action == "progress").order_by(Entry.id.desc()).limit(1))
    baseline = legacy[0] if legacy else None
    key = (str(row.id) if row else "") + "/" + (baseline["key"] if baseline else "")
    if row and (not baseline or row.created_at >= baseline["created_at"]):
        return _out(row), key
    return baseline, key


def _out(row, author=None, recipient=None, replied=False, viewer=None):
    return {"id": row.id, "key": f"entry-{row.id}", "author": author, "recipient": recipient,
            "action": row.action, "content": row.content, "status": row.status, "problems": row.problems,
            "next_step": row.next_step, "next_due_on": row.next_due_on, "created_at": row.created_at,
            "reply_to_id": row.reply_to_id, "replied": replied,
            "can_reply": row.action == "request" and row.recipient_id == viewer and not replied}


def view(db, p, record, *, before=None):
    author, recipient, reply = aliased(SysUser), aliased(SysUser), aliased(Entry)
    query = (_entries(record).with_only_columns(Entry, author.name, recipient.name, reply.id)
             .join(author, author.id == Entry.author_id).outerjoin(recipient, recipient.id == Entry.recipient_id)
             .outerjoin(reply, reply.reply_to_id == Entry.id))
    if before:
        query = query.where(Entry.id < before)
    rows = db.execute(query.order_by(Entry.id.desc()).limit(101)).all()
    history = [_out(row, a, r, response_id is not None, p.id) for row, a, r, response_id in rows[:100]]
    legacy = _legacy_rows(db, record)
    latest, revision = _latest_progress(db, record, legacy)
    outstanding = db.scalar(_entries(record).with_only_columns(func.count(Entry.id))
        .outerjoin(reply, reply.reply_to_id == Entry.id)
        .where(Entry.action == "request", reply.id.is_(None)))
    reply_rows = db.execute(query.where(Entry.action == "request", Entry.recipient_id == p.id, reply.id.is_(None))
                            .order_by(Entry.id.desc()).limit(10)).all() if not before else []
    waiting = [_out(row, a, r, False, p.id) for row, a, r, _ in reply_rows]
    owner_names = list(db.scalars(select(SysUser.name).where(SysUser.id.in_(record.owner_ids)))) if record.owner_ids else []
    facts = list(record.facts)
    if owner_names and not any(f["label"] in {"负责人", "责任人"} for f in facts):
        facts.insert(0, {"label": "负责人", "value": "、".join(owner_names)})
    can_feedback = _can_feedback(db, p, record)
    archive = workbook_feedback.archive_state(_archive_feedback(db, record), can_feedback) if record.archive_key else {}
    return {"record": {"label": record.label, "title": record.title, "module": record.menu, "context": record.context,
                       "owner": "、".join(owner_names), "facts": facts, "path": record.path},
            "target": {"kind": record.kind, "id": record.ident, "sheet": record.sheet, "revision": record.revision},
            "items": history, "legacy": legacy if not before else [], "pending": outstanding,
            "my_requests": waiting, "latest": latest, "progress_revision": revision,
            "before": rows[99][0].id if len(rows) > 100 else None,
            "can_request": _can_request(p, record), "can_feedback": can_feedback and not archive.get("archive"),
            "default_note_recipients": [{"id": user.id, "name": user.name} for user in _note_recipients(db, p, record)],
            "discussion_wecom": _wecom_enabled(db, record),
            **archive}


def _recipient(db, actor, record, user_id):
    user = db.get(SysUser, user_id)
    if not user or user.id == actor.id or user.status != 1 or user.is_deleted or not user.workbench_enabled:
        return None
    principal = build_principal(user)
    if not _can_receive(db, principal, record):
        return None
    try:
        target = targets.resolve(db, principal, record.kind, record.reference)
        return user if target.key == record.key else None
    except BizError:
        return None


def _default_owner_ids(record):
    # A workbook cell can match multiple accounts with the same display name.
    return () if record.workbook and len(record.owner_ids) > 1 else record.owner_ids


def _note_recipients(db, actor, record):
    return [user for uid in _default_owner_ids(record) if (user := _recipient(db, actor, record, uid))]


def _wecom_enabled(db, record):
    return record.operation_project_id is None or operations_scope.controls(db, record.operation_project_id)["wecom_reminders"]


def _team_user_ids(record):
    if record.operation_project_id is not None:
        from app.services.operations_policy import team_user_ids
        return team_user_ids(record.operation_project_id)
    if record.biz_project_id is not None:
        from app.services.project_service import team_user_ids
        return team_user_ids(record.biz_project_id)


def _can_receive(db, p, record):
    team = _team_user_ids(record)
    if team is not None:
        return bool(db.scalar(select(SysUser.id).where(SysUser.id == p.id, SysUser.id.in_(team))))
    if record.kind in {"report", "market-report", "general-report"}:
        from app.services.market_service import TEAM_SCOPED_REPORT_TYPES, can_read_project_report
        report = db.get(RptReport, int(record.ident))
        if report and report.report_type in TEAM_SCOPED_REPORT_TYPES:
            return can_read_project_report(db, p, report, team_only=True)
    return True


def recipients(db, p, record, keyword, *, selected_id=None):
    query = select(SysUser.id).where(SysUser.id != p.id, SysUser.status == 1, SysUser.is_deleted.is_(False), SysUser.workbench_enabled.is_(True))
    team = _team_user_ids(record)
    if team is not None:
        query = query.where(SysUser.id.in_(team))
    if keyword:
        query = query.where(SysUser.name.icontains(keyword, autoescape=True))
    elif record.owner_ids:
        query = query.order_by(SysUser.id.in_(record.owner_ids).desc())
    selected = _recipient(db, p, record, selected_id) if selected_id else None
    result = [{"id": selected.id, "name": selected.name}] if selected else []
    for user_id in db.scalars(query.order_by(SysUser.name, SysUser.id).limit(120)):
        if selected and user_id == selected.id:
            continue
        user = _recipient(db, p, record, user_id)
        if user:
            result.append({"id": user.id, "name": user.name})
        if len(result) >= 24:
            break
    return result


def _notify(db, users, entry, title, *, wecom):
    # The link requires the same live visibility as the record. No record details appear in the message.
    if not users:
        return []
    rows = notify_service.send(db, users, NOTICE, title, "请打开工作台查看这条记录的跟进内容。",
                               link=f"/record-followups/{entry.id}", ref_type=NOTICE, ref_id=str(entry.id),
                               wecom=wecom, defer=True)
    return [notice.id for notice in rows if notice.channel == "wecom_app"]


def _progress_requestors(db, record, author_id):
    return set(db.scalars(_entries(record).with_only_columns(Entry.author_id)
                          .where(Entry.action == "request", Entry.recipient_id == author_id)))


def _notice_record(db, p, entry):
    try:
        record = targets.resolve(db, p, entry.target_kind, entry.reference)
    except BizError:
        return None
    return record if record.key == entry.target_key and _can_receive(db, p, record) else None


def visible_notice_refs(db, p):
    refs = select(SysNotify.ref_id).where(SysNotify.user_id == p.id, SysNotify.channel == "inapp", SysNotify.ref_type == NOTICE)
    entries = db.scalars(select(Entry).where(cast(Entry.id, String).in_(refs)))
    visible = {}
    result = []
    for entry in entries:
        key = (entry.target_kind, entry.target_key)
        if key not in visible:
            visible[key] = _notice_record(db, p, entry) is not None
        if visible[key]:
            result.append(str(entry.id))
    return result


def eligible_notice(db, p, notice):
    if notice.ref_type != NOTICE:
        return False
    try:
        entry_id = int(notice.ref_id)
    except (TypeError, ValueError):
        return False
    entry = db.get(Entry, entry_id)
    if not entry or entry.author_id == p.id:
        return False
    record = _notice_record(db, p, entry)
    if not record or not _wecom_enabled(db, record):
        return False
    if entry.action == "progress":
        return p.id in _progress_requestors(db, record, entry.author_id)
    if entry.action == "note" and entry.recipient_id is None:
        return p.id in _default_owner_ids(record)
    if entry.action == "request" and db.scalar(select(Entry.id).where(Entry.reply_to_id == entry.id)):
        return False
    return entry.recipient_id == p.id


def _mirror_progress(db, p, record, body, *, notice_ids=None):
    if record.legacy is None:
        return
    kind, ident = record.legacy
    if kind == "workbook":
        feedback = WorkbookProjectFeedback(workbook_id=record.workbook, sheet_id=record.sheet, row_key=ident,
            author_id=p.id, created_at=now(), status=body.status, content=body.content, problems=body.problems,
            next_step=body.next_step, next_due_on=body.next_due_on)
        db.add(feedback)
        db.flush()
        return f"{kind}-{feedback.id}"
    elif kind == "project":
        from app.services.market_service import add_feedback
        row = db.get(BizProject, ident)
        feedback = add_feedback(db, row, {"content": body.content, "kind": "risk" if body.status == "blocked" else "progress",
                                         "problems": body.problems, "next_step": body.next_step, "next_due_on": body.next_due_on}, p.id,
                                notice_ids=notice_ids)
        return f"{kind}-{feedback.id}"


def _lock(db, value):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": value})


def _lock_workbook(db, record):
    wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.id == record.workbook)
                   .with_for_update().execution_options(populate_existing=True))
    if wb is None or wb.revision != record.revision:
        raise conflict("原表已更新，请重新打开详情")


def archive(db, p, record, body):
    if not record.archive_key:
        raise conflict("此记录缺少可持续跟踪的项目标识，无法归档")
    _lock_workbook(db, record)
    record = targets.resolve(db, p, record.kind, record.reference)
    if not _can_feedback(db, p, record):
        raise forbidden("无权归档此项目")
    workbook_feedback.set_archive(db, p, _archive_feedback(db, record), body, record.title)
    return view(db, p, record)


def write(db, p, record, body, *, notice_ids=None):
    client_id = str(body.client_id)
    _lock(db, f"record-followup-submission:{p.id}:{client_id}")
    _lock(db, f"record-followup:{record.kind}:{record.key}")
    if record.workbook:
        _lock_workbook(db, record)
    record = targets.resolve(db, p, record.kind, record.reference, lock=True)
    duplicate = db.scalar(select(Entry).where(Entry.author_id == p.id, Entry.client_id == client_id))
    if duplicate is not None:
        if duplicate.target_kind != record.kind or duplicate.target_key != record.key:
            raise conflict("这次提交已用于另一条记录")
        return view(db, p, record)
    if body.action == "request" and not _can_request(p, record):
        raise forbidden("无权在这条记录上提出要求")
    if body.action == "progress":
        if not _can_feedback(db, p, record):
            raise forbidden("仅负责人、指定办理人和有管理权限的人员可以提交进展")
        latest_feedback = _archive_feedback(db, record)
        if latest_feedback and latest_feedback.archive_category:
            raise conflict("项目已归档，历史反馈可在过程记录中查看")
        _, revision = _latest_progress(db, record, _legacy_rows(db, record))
        if body.expected_progress != revision:
            raise conflict("已有新进展，请查看过程记录后再提交")
    recipient = _recipient(db, p, record, body.recipient_id) if body.recipient_id else None
    if body.recipient_id and not recipient:
        raise forbidden("接收人无法查看当前记录")
    if body.action == "reply":
        parent = db.scalar(_entries(record).where(Entry.id == body.reply_to_id, Entry.action == "request"))
        if parent is None or parent.recipient_id != p.id:
            raise forbidden("仅指定回复人可以回复这条要求")
        if db.scalar(select(Entry.id).where(Entry.reply_to_id == parent.id)):
            raise conflict("这条要求已回复，可以继续留言")
        recipient = _recipient(db, p, record, parent.author_id)
    row = Entry(target_kind=record.kind, target_key=record.key, reference=record.reference, author_id=p.id,
                action=body.action, recipient_id=recipient.id if recipient else None, reply_to_id=body.reply_to_id,
                content=body.content, status=body.status, problems=body.problems, next_step=body.next_step,
                next_due_on=body.next_due_on, client_id=client_id, created_at=now())
    db.add(row)
    queued = []
    if body.action == "progress":
        # The last entry in the timeline remains the submitted wording even if the domain adds a summary.
        row.legacy_key = _mirror_progress(db, p, record, body, notice_ids=queued)
        row.created_at = now()
    db.flush()
    wecom = _wecom_enabled(db, record)
    if recipient:
        queued.extend(_notify(db, [recipient], row, {"request": "收到新的跟进要求", "reply": "你的跟进要求收到回复",
                                                     "note": "有成员在记录中提醒你"}[body.action], wecom=wecom))
    elif body.action == "note":
        queued.extend(_notify(db, _note_recipients(db, p, record), row, "你负责的记录有新留言", wecom=wecom))
    elif body.action == "progress":
        already_notified = set(db.scalars(select(SysNotify.user_id).where(SysNotify.id.in_(queued)))) if queued else set()
        for user_id in _progress_requestors(db, record, p.id):
            user = _recipient(db, p, record, user_id)
            if user:
                queued.extend(_notify(db, [user], row, "你跟进的记录有新进展", wecom=wecom and user_id not in already_notified))
    if notice_ids is not None:
        notice_ids.extend(queued)
    log_op(db, p.id, f"record_followup.{body.action}", "record_followup", row.id,
           after={"module": record.kind, "recipient_id": row.recipient_id, "status": row.status, "reply_to_id": row.reply_to_id})
    return view(db, p, record)


def from_entry(db, p, ident):
    row = db.get(Entry, ident)
    if row is None:
        raise not_found("跟进记录")
    record = targets.resolve(db, p, row.target_kind, row.reference)
    if record.key != row.target_key:
        raise not_found("业务记录")
    return view(db, p, record)
