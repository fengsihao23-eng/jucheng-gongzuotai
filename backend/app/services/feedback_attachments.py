"""Feedback files are saved with the feedback transaction and inherit its read access."""
from collections import defaultdict
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.core.db import get_or_404
from app.core.response import BizError, bad_request, not_found
from app.core.storage import file_brief, save_bytes
from app.models import (BizCollectionEntry, BizProjectFeedback, FeedbackAttachment,
                        OpsProjectProgress, OpsSchool, OpsSchoolVisit, OpsTaskUpdate, RecordFollowup, SysFile,
                        WorkbookCollectionProgress, WorkbookProjectFeedback)

CATEGORY = "feedback_attachment"
MAX_FILES = 10
MAX_BYTES = 50 * 1024 * 1024
RECORDS = {model.__tablename__: model for model in (
    RecordFollowup, BizProjectFeedback, OpsTaskUpdate, OpsSchoolVisit, OpsProjectProgress,
    BizCollectionEntry, WorkbookCollectionProgress, WorkbookProjectFeedback,
)}


def filename_of(upload):
    name = Path((upload.filename or "").replace("\\", "/")).name
    name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)
    if name in {"", ".", ".."} or len(name.encode("utf-8")) > 255:
        raise bad_request("附件文件名无效或过长")
    return name


class Uploads:
    def __init__(self, groups=None):
        self.groups = groups or {}
        self.pending_keys = []

    def save(self, db, principal, record, *, group=0):
        uploads = self.groups.get(group, ())
        if not uploads:
            return []
        db.flush()
        files = []
        for upload, name in uploads:
            data = upload.file.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise bad_request("单个附件不能超过 50 MB")
            key = f"{CATEGORY}/{uuid4().hex}"
            self.pending_keys.append(key)
            file = save_bytes(db, data=data, filename=name, category=CATEGORY, key=key,
                              uploader_id=principal.id, content_type="application/octet-stream")
            db.add(FeedbackAttachment(file_id=file.id, record_type=record.__tablename__, record_id=record.id))
            files.append(file_brief(file))
        db.flush()
        return files

    def commit(self, db):
        db.commit()
        self.pending_keys.clear()

    def discard(self):
        for key in self.pending_keys:
            rmtree(Path(settings.storage_dir) / key, ignore_errors=True)


def by_record(db, model, record_ids):
    files = defaultdict(list)
    ids = set(record_ids)
    if ids:
        query = (select(FeedbackAttachment.record_id, SysFile).join(SysFile, SysFile.id == FeedbackAttachment.file_id)
                 .where(FeedbackAttachment.record_type == model.__tablename__, FeedbackAttachment.record_id.in_(ids))
                 .order_by(FeedbackAttachment.file_id))
        for ident, file in db.execute(query):
            files[ident].append(file_brief(file))
    return files


def require_access(db, p, file):
    link = db.get(FeedbackAttachment, file.id)
    model = RECORDS.get(link.record_type) if link else None
    if model is None:
        raise not_found("文件")
    try:
        record = get_or_404(db, model, link.record_id)
        _require_record(db, p, record)
    except BizError as exc:
        if exc.http_status in (403, 404, 409):
            raise not_found("文件") from None
        raise


def _require_record(db, p, record):
    from app.services import (collection_followups, collection_service, key_projects, operations_followup,
                              operations_service, record_followups, workbook_project_feedback)

    if isinstance(record, RecordFollowup):
        record_followups.resolve_entry(db, p, record)
    elif isinstance(record, BizProjectFeedback):
        key_projects.project(db, p, record.project_id, is_key=None)
    elif isinstance(record, OpsTaskUpdate):
        operations_service.read_task(db, p, record.task_id)
    elif isinstance(record, OpsSchoolVisit):
        school = get_or_404(db, OpsSchool, record.school_id)
        operations_followup.school_access(db, p, school.project_id, school.id)
    elif isinstance(record, OpsProjectProgress):
        operations_service.read_progress(db, p, record.project_id, entry_id=record.id)
    elif isinstance(record, BizCollectionEntry):
        collection_service.read_entry(db, p, record)
    elif isinstance(record, WorkbookCollectionProgress):
        collection_followups.read_progress(db, p, record)
    elif isinstance(record, WorkbookProjectFeedback):
        workbook_project_feedback.read_progress(db, p, record)
    else:
        raise not_found("文件")
