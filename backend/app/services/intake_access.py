"""资料原件与清洗任务的服务端访问边界；不能只靠前端隐藏入口。"""
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.core.response import not_found
from app.models import CleanJob, ImportBatch, KbDocument, SysFile, SysUser

PROTECTED_FILE_CATEGORIES = {"import", "kb", "clean", "clean_report"}


def can_handle_department(p, dept_code):
    return p.is_admin or (p.can("imports:handle") and (
        p.data_scope == "all" or p.data_scope == "dept" and bool(p.dept_code) and p.dept_code == dept_code))


def can_view_batch(p, batch):
    return p.is_admin or batch.uploader_id == p.id or (
        can_handle_department(p, batch.dept_code) and (batch.status == "pool" or batch.handler_id == p.id))


def batch_filter(p):
    if p.is_admin:
        return True
    own = ImportBatch.uploader_id == p.id
    assigned = or_(ImportBatch.status == "pool", ImportBatch.handler_id == p.id)
    if p.can("imports:handle") and p.data_scope == "all":
        return or_(own, assigned)
    if p.can("imports:handle") and p.data_scope == "dept" and p.dept_code:
        return or_(own, and_(assigned, ImportBatch.dept_code == p.dept_code))
    return own


def clean_filter(p):
    if p.is_admin:
        return True
    allowed = [CleanJob.created_by == p.id]
    if p.can("imports:handle"):
        allowed.append(CleanJob.id.in_(select(ImportBatch.clean_job_id).where(batch_filter(p))))
        origin = CleanJob.mapping["import_batch_id"].as_integer()
        allowed.append(origin.in_(select(ImportBatch.id).where(batch_filter(p))))
        # 自动生成的清洗副本不能成为绕过「未转交原件」隔离的旁路。
        standalone = and_(origin.is_(None), ~CleanJob.id.in_(select(ImportBatch.clean_job_id).where(
            ImportBatch.clean_job_id.is_not(None))))
        if p.data_scope == "all":
            allowed.append(standalone)
        if p.data_scope == "dept" and p.user.department_id:
            allowed.append(and_(standalone, CleanJob.created_by.in_(select(SysUser.id).where(
                SysUser.department_id == p.user.department_id))))
    return or_(*allowed)


def get_clean_job(db, p, job_id, *, lock=False):
    query = select(CleanJob).where(CleanJob.id == job_id, clean_filter(p))
    if lock:
        query = query.with_for_update()
    job = db.scalar(query)
    if job is None:
        raise not_found("清洗任务")
    return job


def require_file_access(db, p, file):
    """旧版本原件沿用当前文档权限；撤销共享后旧签名也不能绕过权限。"""
    if file.category not in PROTECTED_FILE_CATEGORIES:
        return
    allowed = False
    if file.category == "kb":
        from app.services.kb_service import can_view
        current_file = aliased(SysFile)
        doc = db.scalar(select(KbDocument).join(current_file, KbDocument.file_id == current_file.id)
                        .where(current_file.storage_key == file.storage_key))
        allowed = can_view(db, p, doc) if doc else p.is_admin or file.uploader_id == p.id
    elif file.category == "import":
        batch = db.scalar(select(ImportBatch).where(or_(ImportBatch.file_id == file.id,
            ImportBatch.suggestion["external_source"]["raw_file_id"].as_integer() == file.id)))
        allowed = can_view_batch(p, batch) if batch else p.is_admin or file.uploader_id == p.id
    elif file.category in {"clean", "clean_report"}:
        allowed = bool(db.scalar(select(CleanJob.id).where(
            or_(CleanJob.file_id == file.id, CleanJob.report_file_id == file.id), clean_filter(p))))
        # 原上传人可取回自己委托整理的文件，不因此获得台账导入权限。
        if not allowed:
            allowed = bool(db.scalar(select(ImportBatch.id).join(CleanJob, ImportBatch.clean_job_id == CleanJob.id)
                .where(ImportBatch.uploader_id == p.id,
                       or_(CleanJob.file_id == file.id, CleanJob.report_file_id == file.id))))
    if not allowed:
        raise not_found("文件")
