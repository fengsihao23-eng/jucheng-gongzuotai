"""统一资料导入：同事上传任何资料 → 按文件类型和内容分流，不要求同事知道数据该进哪张表。

- 表格（xlsx/csv）：先按用途描述/文件名识别目标台账，认不出再按表头匹配模板；有台账导入权限的人直接生成清洗预览
  （clean_job），由本人核对映射后导入；没权限或认不出的进入待整理池，由整理人（imports:handle）分派。
- 文档（docx/pdf/pptx/md/txt）：抽取正文并建议知识库分类，上传人确认分类与可见范围后进入知识库（审核规则见 kb_service）。
- 其他文件或解析失败：进入待整理池，附失败原因。
- 每份资料一条 import_batch，保留原件；撤回时按去向回滚：清洗预览取消 / 已导入台账整批删除（被引用则拒绝）/
  知识库未发布删除、已发布下架。
"""
import hashlib
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import ROLE_DEFS
from app.core.response import BizError, bad_request, conflict, forbidden, not_found
from app.core.storage import file_brief, read_bytes, save_bytes
from app.core.timeutil import today, utcnow
from app.models import CleanJob, ImportBatch, KbDocument, SysFile, SysUser
from app.services import clean_service, kb_service
from app.services.kb_extract import DOC_EXTS, MAX_BYTES, TABLE_EXTS, ext_of, extract_text

STATUS_NAMES = {"pending": "待确认", "pool": "待整理", "done": "已入库", "withdrawn": "已撤回", "dismissed": "已处理"}
ROUTE_NAMES = {"clean": "业务台账", "kb": "知识库", "pool": "待整理池"}
HANDLE_BUTTON = "imports:handle"


def is_handler(p) -> bool:
    return p.is_admin or p.can(HANDLE_BUTTON)


def can_clean(p) -> bool:
    return p.has_menu("clean") and p.can("clean:import")


def classify(filename: str) -> str:
    ext = ext_of(filename)
    if ext in TABLE_EXTS or ext == ".xls":
        return "table"
    if ext in DOC_EXTS or ext in (".doc", ".ppt"):
        return "document"
    return "other"


def _handlers(db: Session) -> list[SysUser]:
    from app.services import notify_service
    roles = [c for c, r in ROLE_DEFS.items() if HANDLE_BUTTON in r["buttons"] or "*" in r["buttons"]]
    return notify_service.users_by_roles(db, roles)


def _notify_pool(db: Session, b: ImportBatch, uploader_name: str) -> None:
    from app.services import notify_service
    users = [u for u in _handlers(db) if u.id != b.uploader_id]
    reason = b.suggestion.get("reason") or b.note or ""
    notify_service.send(db, users, "import_pool", f"资料待整理：{b.filename}",
                        f"**{uploader_name}** 上传了《{b.filename}》{('（' + b.purpose + '）') if b.purpose else ''}，"
                        f"系统未能自动入库：{reason}", link="/imports?tab=pool", ref_type="import_batch", ref_id=str(b.id))


def _to_pool(b: ImportBatch, reason: str) -> None:
    b.route, b.status = "pool", "pool"
    b.suggestion = {**(b.suggestion or {}), "reason": reason}


def _start_clean(db: Session, p, b: ImportBatch, data: bytes, target: str) -> None:
    job = clean_service.preview(db, p.id, data, b.filename, purpose=b.purpose, target=target)
    b.route, b.status, b.target, b.clean_job_id = "clean", "pending", target, job.id


def _route_table(db: Session, p, b: ImportBatch, data: bytes) -> None:
    target = clean_service.detect_target(b.purpose, b.filename)
    how = "用途描述/文件名"
    scores = {}
    if not target:
        target, scores = clean_service.guess_target(db, data, b.filename)
        how = "表头匹配"
    sug = {"target": target, "target_name": clean_service.TARGET_NAMES.get(target), "scores": scores}
    if not target:
        b.suggestion = sug
        _to_pool(b, "表格内容不属于现有的合同 / 商机 / 回款 / 项目台账模板，需要整理人判断去向")
        return
    sug["reason"] = f"按{how}识别为「{clean_service.TARGET_NAMES[target]}」"
    b.suggestion, b.target = sug, target
    if not can_clean(p):
        _to_pool(b, sug["reason"] + "；你没有台账导入权限，已交给整理人导入")
        return
    try:
        _start_clean(db, p, b, data, target)
    except BizError as exc:
        _to_pool(b, f"表格解析失败：{exc.message}")


def _route_document(b: ImportBatch, data: bytes) -> None:
    try:
        text = extract_text(data, b.filename)
    except BizError as exc:
        _to_pool(b, exc.message)
        return
    title = Path(b.filename).stem
    sug = kb_service.suggest_category(title, f"{b.purpose or ''}\n{text}")
    b.route, b.status = "kb", "pending"
    b.suggestion = {**sug, "title": title, "chars": len(text), "preview": text[:300]}


def create(db: Session, p, data: bytes, filename: str, purpose: str | None = None) -> ImportBatch:
    if not data:
        raise bad_request("文件为空")
    if len(data) > MAX_BYTES:
        raise bad_request("文件超过 50MB，请拆分后再上传")
    filename = Path(filename or "upload").name[:200]
    sha = hashlib.sha256(data).hexdigest()
    dup = db.scalar(select(ImportBatch).where(ImportBatch.uploader_id == p.id, ImportBatch.sha256 == sha,
                                              ImportBatch.status.notin_(["withdrawn", "dismissed"])))
    if dup is not None:
        raise conflict(f"你已经上传过同一份文件（{dup.filename}，{STATUS_NAMES.get(dup.status)}）", data={"batch_id": dup.id})
    f = save_bytes(db, data=data, filename=filename, category="import",
                   key=f"import/{today():%Y-%m}/{uuid.uuid4().hex[:12]}/{filename}", uploader_id=p.id)
    b = ImportBatch(filename=filename, file_id=f.id, sha256=sha, size=len(data), purpose=(purpose or "").strip()[:255] or None,
                    kind=classify(filename), route="pool", status="pool", suggestion={}, uploader_id=p.id,
                    dept_code=p.dept_code)
    db.add(b)
    db.flush()
    if b.kind == "table":
        _route_table(db, p, b, data)
    elif b.kind == "document":
        _route_document(b, data)
    else:
        _to_pool(b, f"系统不能自动识别 {ext_of(filename) or '无扩展名'} 文件，需要整理人处理")
    db.flush()
    if b.status == "pool":
        _notify_pool(db, b, p.user.name)
    return b


# ---------------------------------------------------------------- 状态同步 / 视图
def _sync(db: Session, b: ImportBatch) -> None:
    """清洗任务的状态在清洗页变化，这里读取后同步到批次。"""
    if b.route == "clean" and b.clean_job_id and b.status == "pending":
        job = db.get(CleanJob, b.clean_job_id)
        if job and job.status == "imported":
            b.status, b.handled_at = "done", job.updated_at
        elif job and job.status in ("cancelled", "rolled_back"):
            b.status = "withdrawn"
            b.note = b.note or "已在数据清洗页取消"


def _get(db: Session, p, batch_id: int, *, manage: bool = False) -> ImportBatch:
    b = db.get(ImportBatch, batch_id)
    if b is None:
        raise not_found("导入记录")
    if b.uploader_id != p.id and not is_handler(p):
        raise not_found("导入记录")
    if manage and b.uploader_id != p.id and not is_handler(p):
        raise forbidden("只能操作自己上传的资料")
    _sync(db, b)
    return b


def _actions(b: ImportBatch, p) -> list[str]:
    owner, handler = b.uploader_id == p.id or is_handler(p), is_handler(p)
    acts = []
    if b.status == "pending" and owner:
        if b.route == "kb":
            acts.append("confirm_kb")
            if b.kind == "table" and can_clean(p):
                acts.append("to_clean")
        if b.route == "clean":
            acts += ["open_clean", "to_kb"]  # 表格也可能只是参考资料（如报价清单）
        acts.append("to_pool")
    if b.status == "pool" and handler:
        if b.kind == "table" and can_clean(p):
            acts.append("to_clean")
        if b.kind in ("table", "document"):
            acts.append("to_kb")
        acts.append("dismiss")
    if b.status in ("pending", "pool", "done") and owner:
        acts.append("withdraw")
    return acts


def view(db: Session, b: ImportBatch, p, names: dict | None = None) -> dict:
    names = names if names is not None else dict(db.execute(select(SysUser.id, SysUser.name).where(
        SysUser.id.in_({b.uploader_id, b.handler_id} - {None}))).all())
    job = db.get(CleanJob, b.clean_job_id) if b.clean_job_id else None
    doc = db.get(KbDocument, b.kb_doc_id) if b.kb_doc_id else None
    return {"id": b.id, "filename": b.filename, "size": b.size, "purpose": b.purpose, "kind": b.kind,
            "route": b.route, "route_name": ROUTE_NAMES.get(b.route), "status": b.status,
            "status_name": STATUS_NAMES.get(b.status, b.status), "target": b.target,
            "target_name": clean_service.TARGET_NAMES.get(b.target), "suggestion": b.suggestion or {}, "note": b.note,
            "uploader_id": b.uploader_id, "uploader": names.get(b.uploader_id), "dept_code": b.dept_code,
            "handler": names.get(b.handler_id), "handled_at": b.handled_at, "created_at": b.created_at,
            "file": file_brief(db.get(SysFile, b.file_id)),
            "clean_job": {"id": job.id, "status": job.status, "stats": {k: v for k, v in (job.stats or {}).items()
                                                                         if k in ("total", "importable", "error", "duplicate",
                                                                                  "imported", "rolled_back")}}
            if job else None,
            "kb_doc": {"id": doc.id, "title": doc.title, "status": doc.status,
                       "status_name": kb_service.STATUS_NAMES.get(doc.status), "category": doc.category} if doc else None,
            "actions": _actions(b, p)}


def list_batches(db: Session, p, scope: str = "mine", status: str | None = None, page: int = 1,
                 page_size: int = 20) -> dict:
    q = select(ImportBatch)
    if scope in ("pool", "all"):
        if not is_handler(p):
            raise forbidden("只有资料整理人可以查看")
        if scope == "pool":
            q = q.where(ImportBatch.status == "pool")
    else:
        q = q.where(ImportBatch.uploader_id == p.id)
    if status:
        q = q.where(ImportBatch.status == status)
    pending = list(db.scalars(q.where(ImportBatch.route == "clean", ImportBatch.status == "pending")))
    for b in pending:
        _sync(db, b)
    db.flush()
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = list(db.scalars(q.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
                           .offset((page - 1) * page_size).limit(page_size)))
    ids = {x for b in rows for x in (b.uploader_id, b.handler_id) if x}
    names = dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(ids))).all()) if ids else {}
    counts = {}
    if is_handler(p):
        counts["pool"] = db.scalar(select(func.count(ImportBatch.id)).where(ImportBatch.status == "pool")) or 0
    counts["mine_pending"] = db.scalar(select(func.count(ImportBatch.id)).where(
        ImportBatch.uploader_id == p.id, ImportBatch.status == "pending")) or 0
    return {"items": [view(db, b, p, names) for b in rows], "total": total, "page": page, "page_size": page_size,
            "counts": counts, "is_handler": is_handler(p), "can_clean": can_clean(p)}


# ---------------------------------------------------------------- 操作
def _mark(b: ImportBatch, p, status: str, note: str | None = None) -> None:
    b.status = status
    if note:
        b.note = note[:500]
    if b.uploader_id != p.id or status in ("done", "dismissed"):
        b.handler_id = p.id
    b.handled_at = utcnow()


def confirm_kb(db: Session, p, batch_id: int, **meta) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if b.route != "kb" or b.status != "pending":
        raise conflict("这份资料当前不在「待确认入知识库」状态")
    data = read_bytes(db.get(SysFile, b.file_id))
    d = kb_service.upload_document(db, p, data=data, filename=b.filename, **meta)
    b.kb_doc_id = d.id
    _mark(b, p, "done", "已发布到知识库" if d.status == "published" else "已提交知识库，等待分类审核人审核")
    db.flush()
    return b


def to_clean(db: Session, p, batch_id: int, target: str) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if b.kind != "table":
        raise bad_request("只有表格可以导入业务台账")
    if target not in clean_service.TARGET_NAMES:
        raise bad_request("请选择目标台账")
    if b.status not in ("pending", "pool") or (b.route == "clean" and b.clean_job_id):
        raise conflict("这份资料已经在台账导入流程中")
    if not can_clean(p):
        raise forbidden("你没有台账导入权限")
    _start_clean(db, p, b, read_bytes(db.get(SysFile, b.file_id)), target)
    if b.uploader_id != p.id:
        b.handler_id, b.handled_at = p.id, utcnow()
    db.flush()
    return b


def to_kb(db: Session, p, batch_id: int) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if b.status not in ("pending", "pool"):
        raise conflict("这份资料已处理")
    if b.status == "pool" and not is_handler(p):
        raise forbidden("待整理池中的资料由整理人处理")
    if b.route == "clean" and b.clean_job_id:
        job = db.get(CleanJob, b.clean_job_id)
        if job and job.status == "imported":
            raise conflict("已导入台账，请先撤回")
        if job and job.status == "preview":
            job.status = "cancelled"
        b.clean_job_id = None
    data = read_bytes(db.get(SysFile, b.file_id))
    _route_document(b, data)
    if b.status == "pool":
        raise bad_request(b.suggestion.get("reason") or "文件无法放入知识库")
    db.flush()
    return b


def to_pool(db: Session, p, batch_id: int, note: str | None = None) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if b.status != "pending":
        raise conflict("只有待确认的资料可以交给整理人")
    if b.route == "clean" and b.clean_job_id:
        job = db.get(CleanJob, b.clean_job_id)
        if job and job.status == "imported":
            raise conflict("已导入台账，不能再转交")
        if job and job.status == "preview":
            job.status = "cancelled"
        b.clean_job_id = None
    _to_pool(b, (note or "").strip()[:200] or "上传人请整理人协助处理")
    db.flush()
    _notify_pool(db, b, p.user.name)
    return b


def dismiss(db: Session, p, batch_id: int, note: str) -> ImportBatch:
    b = _get(db, p, batch_id)
    if not is_handler(p):
        raise forbidden("只有资料整理人可以处理待整理池")
    if b.status != "pool":
        raise conflict("只有待整理的资料可以标记已处理")
    if not (note or "").strip():
        raise bad_request("请写明处理方式，例如「已线下录入」「无需入库」")
    _mark(b, p, "dismissed", note.strip())
    db.flush()
    return b


def withdraw(db: Session, p, batch_id: int) -> ImportBatch:
    """撤回：按去向回滚已产生的数据。台账记录被后续业务引用时整体拒绝（错误里列出原因）。"""
    b = _get(db, p, batch_id, manage=True)
    if b.status in ("withdrawn", "dismissed"):
        raise conflict("这份资料已撤回或已处理")
    note = "已撤回"
    if b.clean_job_id:
        job = db.get(CleanJob, b.clean_job_id)
        if job and job.status == "preview":
            job.status = "cancelled"
            note = "已撤回（清洗预览已取消）"
        elif job and job.status == "imported":
            if not can_clean(p):
                raise forbidden("已导入台账的资料需由有台账导入权限的人撤回")
            r = clean_service.rollback_job(db, job)
            note = f"已撤回，删除台账记录 {r['removed']} 条"
    if b.kb_doc_id:
        d = db.get(KbDocument, b.kb_doc_id)
        if d and d.status in ("pending", "rejected"):
            kb_service.delete_document(db, p, d.id)
            note = "已撤回（知识库文档已删除）"
        elif d and d.status == "published":
            kb_service.set_archived(db, p, d.id, True)
            note = "已撤回（知识库文档已下架）"
    _mark(b, p, "withdrawn", note)
    db.flush()
    return b
