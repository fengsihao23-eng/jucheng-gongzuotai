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

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.orm import Session

from app.core.response import BizError, bad_request, conflict, forbidden, not_found
from app.core.storage import file_brief, read_bytes, save_bytes
from app.core.timeutil import today, utcnow
from app.models import CleanJob, ImportBatch, KbDocument, SysFile, SysUser
from app.services import clean_service, kb_service, smart_intake
from app.services.kb_extract import DOC_EXTS, MAX_BYTES, TABLE_EXTS, ext_of, extract_text
from app.services.intake_access import batch_filter, can_view_batch, can_handle_department

STATUS_NAMES = {"processing": "识别中", "pending": "待确认", "pool": "待整理", "done": "已提交", "withdrawn": "已撤回", "dismissed": "已处理"}
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
    from app.core.deps import build_principal
    return [u for u in db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False)))
            if is_handler(build_principal(u))]


def _notify_pool(db: Session, batches: list[ImportBatch], uploader_name: str) -> None:
    from app.services import notify_service
    uploader_ids = {b.uploader_id for b in batches}
    from app.core.deps import build_principal
    users = [u for u in _handlers(db) if u.id not in uploader_ids
             and all(can_view_batch(build_principal(u), b) for b in batches)]
    if not users or not batches:
        return
    b = batches[0]
    if len(batches) == 1:
        reason = b.suggestion.get("reason") or b.note or ""
        title = f"资料待整理：{b.filename}"
        content = (f"**{uploader_name}** 上传了《{b.filename}》{('（' + b.purpose + '）') if b.purpose else ''}，"
                   f"系统未能自动入库：{reason}")
    else:
        title = f"资料待整理：{uploader_name} 的 {len(batches)} 份资料"
        names = "、".join(f"《{x.filename}》" for x in batches[:5]) + (" 等" if len(batches) > 5 else "")
        content = f"**{uploader_name}** 上传的 {names} 需要人工判断去向{('（' + b.purpose + '）') if b.purpose else ''}。"
    notify_service.send(db, users, "import_pool", title, content, link="/imports?tab=pool", ref_type="import_batch",
                        ref_id=str(b.id))


def _notify_uploader(db: Session, b: ImportBatch, actor_id: int | None, text: str) -> None:
    """别人处理了我的资料（整理人分派、导入、标记已处理、撤回）时通知上传人。自己操作不通知。"""
    from app.services import notify_service
    if not b.uploader_id or b.uploader_id == actor_id:
        return
    u = db.get(SysUser, b.uploader_id)
    notify_service.send(db, [u], "import_result", f"资料处理进度：{b.filename}", text,
                        link="/imports", ref_type="import_batch", ref_id=str(b.id))


def _to_pool(b: ImportBatch, reason: str) -> None:
    b.route, b.status = "pool", "pool"
    b.suggestion = {**(b.suggestion or {}), "reason": reason}


def _start_clean(db: Session, p, b: ImportBatch, data: bytes, target: str) -> None:
    job = clean_service.preview(db, p.id, data, b.filename, purpose=b.purpose, target=target)
    job.mapping = {**job.mapping, "import_batch_id": b.id}
    b.route, b.status, b.target, b.clean_job_id = "clean", "pending", target, job.id
    _source_rules(db, b)


def _source_rules(db, b):
    source = (b.suggestion or {}).get("external_source")
    if source and b.clean_job_id:
        job = db.get(CleanJob, b.clean_job_id)
        if job:
            job.mapping = {**job.mapping, "blocked_source_columns": source.get("complex_columns", [])}
            job.stats = {**job.stats, "blocked_source_columns": source.get("complex_columns", [])}


def _route_table(db: Session, p, b: ImportBatch, data: bytes) -> None:
    metadata = {}
    try:
        rows = clean_service.read_table(data, b.filename, metadata=metadata)
    except BizError as exc:
        _to_pool(b, exc.message)
        return
    target = clean_service.detect_target(b.purpose, b.filename)
    how = "用途描述/文件名"
    scores = {}
    if not target:
        target, scores = clean_service.guess_target(db, data, b.filename)
        how = "表头匹配"
    ai = None
    if not target:
        ai = _ask_table(db, b, data)
        if ai and ai["target"]:
            target, how = ai["target"], "AI 判断"
    sug = {"target": target, "target_name": clean_service.TARGET_NAMES.get(target), "scores": scores,
           "source": "llm" if ai else "rule", "description": (ai or {}).get("description")}
    header = clean_service.detect_header(rows, clean_service.templates(db)[target or "contract"]) if rows else 0
    sug["table_profile"] = {**metadata, "header_row": rows[header][0] if rows else None,
        "columns": [str(v or "")[:80] for v in rows[header][1][:20]] if rows else [],
        "sample_rows": [[str(v or "")[:120] for v in cells[:20]] for _, cells in rows[header + 1:header + 6]],
        "note": "只展示前 5 行样例；台账导入须核对工作表和完整清洗结果"}
    if not target:
        b.suggestion = sug
        what = f"看起来是「{ai['description']}」，" if ai and ai.get("description") else ""
        _to_pool(b, f"{what}不属于现有的合同 / 商机 / 回款 / 项目台账模板，需要整理人判断去向")
        return
    sug["reason"] = f"按{how}识别为「{clean_service.TARGET_NAMES[target]}」" + (f"（{ai['reason']}）" if ai and ai.get("reason") else "")
    b.suggestion, b.target = sug, target
    if not can_clean(p):
        _to_pool(b, sug["reason"] + "；你没有台账导入权限，已交给整理人导入")
        return
    try:
        _start_clean(db, p, b, data, target)
    except BizError as exc:
        _to_pool(b, f"表格解析失败：{exc.message}")


def _ask_table(db: Session, b: ImportBatch, data: bytes) -> dict | None:
    try:
        rows = clean_service.read_table(data, b.filename)
    except BizError:
        return None
    rows = [r for r in rows if any(not clean_service._is_blank(v) for v in r[1])]
    if not rows:
        return None
    idx = clean_service.detect_header(rows, clean_service.templates(db)["contract"])
    return smart_intake.describe_table(rows[idx][1], [r[1] for r in rows[idx + 1:idx + 6]], b.filename, b.purpose)


def _route_document(db: Session, b: ImportBatch, data: bytes) -> None:
    try:
        text = extract_text(data, b.filename)
    except BizError as exc:
        _to_pool(b, exc.message)
        return
    sug = smart_intake.describe_document(db, b.filename, text, b.purpose)
    b.route, b.status = "kb", "pending"
    b.suggestion = {**sug, "chars": len(text), "preview": text[:300]}


ZIP_MAX_ENTRIES = 100
ZIP_MAX_TOTAL = 200 * 1024 * 1024
ZIP_SKIP = ("__MACOSX/", ".DS_Store", "Thumbs.db", "desktop.ini")


def _dup(db: Session, p, sha: str) -> ImportBatch | None:
    return db.scalar(select(ImportBatch).where(ImportBatch.uploader_id == p.id, ImportBatch.sha256 == sha,
                                               ImportBatch.status.notin_(["withdrawn", "dismissed"])))


def _one(db: Session, p, data: bytes, filename: str, purpose: str | None, channel: str,
         archive_name: str | None = None, deferred: bool = False) -> ImportBatch:
    filename = Path(filename or "upload").name[:200]
    sha = hashlib.sha256(data).hexdigest()
    if db.bind.dialect.name == "postgresql":
        lock_key = int(hashlib.sha256(f"import:{p.id}:{sha}".encode()).hexdigest()[:15], 16)
        db.execute(sql_text("select pg_advisory_xact_lock(:key)"), {"key": lock_key})
    if (dup := _dup(db, p, sha)) is not None:
        raise conflict(f"你已经上传过同一份文件（{dup.filename}，{STATUS_NAMES.get(dup.status)}）", data={"batch_id": dup.id})
    f = save_bytes(db, data=data, filename=filename, category="import",
                   key=f"import/{today():%Y-%m}/{uuid.uuid4().hex[:12]}/{filename}", uploader_id=p.id)
    b = ImportBatch(filename=filename, file_id=f.id, sha256=sha, size=len(data), purpose=(purpose or "").strip()[:255] or None,
                    kind=classify(filename), route="pool", status="pool", suggestion={}, uploader_id=p.id,
                    dept_code=p.dept_code, channel=channel, archive_name=archive_name)
    db.add(b)
    db.flush()
    if deferred:
        b.status = "processing"
        return b
    _classify_batch(db, p, b, data)
    return b


def _classify_batch(db, p, b, data):
    source = (b.suggestion or {}).get("external_source")
    if b.kind == "table":
        _route_table(db, p, b, data)
    elif b.kind == "document":
        _route_document(db, b, data)
    else:
        _to_pool(b, f"系统不能自动识别 {ext_of(b.filename) or '无扩展名'} 文件，需要整理人处理")
    if source:
        b.suggestion = {**(b.suggestion or {}), "external_source": source}
        _source_rules(db, b)
    db.flush()


def process_many(user_id, batch_ids):
    """上传原件已持久化后后台识别。中断的记录保留，可由本人重试；不自动写台账。"""
    from app.core.db import SessionLocal
    from app.core.deps import build_principal
    for batch_id in batch_ids:
        with SessionLocal() as db:
            batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update())
            if not batch or batch.status != "processing" or batch.uploader_id != user_id:
                continue
            user = db.get(SysUser, user_id)
            if not user or user.status != 1 or user.is_deleted:
                _to_pool(batch, "上传人账号已停用，需要管理员核对资料归属后处理")
                db.commit()
                continue
            p = build_principal(user)
            try:
                with db.begin_nested():
                    _classify_batch(db, p, batch, read_bytes(db.get(SysFile, batch.file_id)))
            except Exception:
                _to_pool(batch, "自动识别未完成，原件已保留。可重新识别或交给整理人核对，尚未写入台账或知识库")
            if batch.status == "pool":
                _notify_pool(db, [batch], user.name)
            db.commit()


def _locked_clean(db, job_id):
    return db.scalar(select(CleanJob).where(CleanJob.id == job_id).with_for_update()
                     .execution_options(populate_existing=True))


def _zip_name(info) -> str:
    """Windows 压缩的中文文件名多为 GBK 且未置 UTF-8 标志位，zipfile 会按 cp437 解码成乱码，这里还原。"""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("gb18030")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _unzip(data: bytes) -> tuple[list[tuple[str, bytes]], list[dict]]:
    import io
    import zipfile
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise bad_request("压缩包已损坏或不是 zip 格式")
    files, skipped, total = [], [], 0
    infos = [i for i in zf.infolist() if not i.is_dir()]
    for info in infos:
        name = _zip_name(info)
        base = Path(name).name
        if not base or any(x in name for x in ZIP_SKIP) or base.startswith(("._", "~$")):
            continue
        if info.flag_bits & 0x1:
            skipped.append({"filename": base, "reason": "加密文件，无法读取"})
            continue
        if ext_of(base) in (".zip", ".rar", ".7z"):
            skipped.append({"filename": base, "reason": "压缩包里的压缩包请先解开再上传"})
            continue
        if info.file_size > MAX_BYTES:
            skipped.append({"filename": base, "reason": "单个文件超过 50MB"})
            continue
        total += info.file_size
        if total > ZIP_MAX_TOTAL or len(files) >= ZIP_MAX_ENTRIES:
            raise bad_request(f"压缩包内容过多（最多 {ZIP_MAX_ENTRIES} 个文件、解压后 200MB），请拆分后上传")
        try:
            files.append((base, zf.read(info)))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
            skipped.append({"filename": base, "reason": "压缩条目损坏或编码不支持，请单独重新上传"})
    if not files and not skipped:
        raise bad_request("压缩包里没有可导入的文件")
    return files, skipped


def create_many(db: Session, p, data: bytes, filename: str, purpose: str | None = None,
                channel: str = "web", deferred: bool = False) -> dict:
    """上传一个文件；zip 压缩包逐个解开分别建批次。返回 {batches, skipped, archive}。待整理的资料合并成一条通知。"""
    if not data:
        raise bad_request("文件为空")
    if len(data) > MAX_BYTES:
        raise bad_request("文件超过 50MB，请拆分后再上传")
    filename = Path(filename or "upload").name[:200]
    if ext_of(filename) in (".rar", ".7z"):
        raise bad_request("暂不支持 rar / 7z，请改成 zip 压缩包或直接上传文件")
    if ext_of(filename) != ".zip":
        b = _one(db, p, data, filename, purpose, channel, deferred=deferred)
        if b.status == "pool":
            _notify_pool(db, [b], p.user.name)
        return {"batches": [b], "skipped": [], "archive": None}
    if (dup := _dup(db, p, hashlib.sha256(data).hexdigest())) is not None:
        raise conflict(f"你已经上传过同一个压缩包里的文件（{dup.filename}）", data={"batch_id": dup.id})
    files, skipped = _unzip(data)
    batches = []
    for name, content in files:
        if not content:
            skipped.append({"filename": name, "reason": "空文件"})
            continue
        try:
            with db.begin_nested():
                batches.append(_one(db, p, content, name, purpose, channel, archive_name=filename, deferred=deferred))
        except BizError as exc:
            skipped.append({"filename": name, "reason": exc.message})
    if not batches:
        raise bad_request("压缩包里的文件都没能导入：" + "；".join(f"{x['filename']}（{x['reason']}）" for x in skipped[:5]))
    pooled = [b for b in batches if b.status == "pool"]
    if pooled:
        _notify_pool(db, pooled, p.user.name)
    return {"batches": batches, "skipped": skipped, "archive": filename}


def create(db: Session, p, data: bytes, filename: str, purpose: str | None = None, channel: str = "web") -> ImportBatch:
    """单文件上传（兼容旧调用）；zip 请用 create_many。"""
    return create_many(db, p, data, filename, purpose, channel)["batches"][0]


# ---------------------------------------------------------------- 状态同步 / 视图
def _sync(db: Session, b: ImportBatch) -> None:
    """清洗任务的状态在清洗页变化，这里读取后同步到批次。"""
    if b.route == "clean" and b.clean_job_id and b.status == "pending":
        job = db.get(CleanJob, b.clean_job_id)
        if job and job.status == "imported":
            b.status, b.handled_at = "done", job.updated_at
            _notify_uploader(db, b, job.created_by,
                             f"《{b.filename}》已导入{clean_service.TARGET_NAMES.get(b.target, '台账')} "
                             f"{(job.stats or {}).get('imported', 0)} 行。")
        elif job and job.status in ("cancelled", "rolled_back"):
            b.status = "withdrawn"
            b.note = b.note or "已在数据清洗页取消"


def _get(db: Session, p, batch_id: int, *, manage: bool = False) -> ImportBatch:
    query = select(ImportBatch).where(ImportBatch.id == batch_id, batch_filter(p))
    b = db.scalar(query.with_for_update() if manage else query)
    if b is None:
        raise not_found("导入记录")
    _sync(db, b)
    return b


def _actions(b: ImportBatch, p) -> list[str]:
    owner, handler = can_view_batch(p, b), can_handle_department(p, b.dept_code)
    acts = []
    if b.status == "pending" and owner:
        if b.route == "kb":
            acts.append("confirm_kb")
            if b.kind == "table" and can_clean(p):
                acts.append("to_clean")
        if b.route == "clean":
            if can_clean(p):
                acts.append("open_clean")
            acts.append("to_kb")  # 表格也可能只是参考资料（如报价清单）
        acts.append("to_pool")
    if b.status == "pool" and owner:
        if b.kind == "table" and can_clean(p):
            acts.append("to_clean")
        if b.kind in ("table", "document"):
            acts.append("to_kb")
        acts.append("retry")
        if handler:
            acts.append("dismiss")
    if b.status == "processing" and owner:
        acts.append("retry")
    if b.status in ("processing", "pending", "pool", "done") and owner:
        acts.append("withdraw")
    return acts


def view(db: Session, b: ImportBatch, p, names: dict | None = None) -> dict:
    names = names if names is not None else dict(db.execute(select(SysUser.id, SysUser.name).where(
        SysUser.id.in_({b.uploader_id, b.handler_id} - {None}))).all())
    job = db.get(CleanJob, b.clean_job_id) if b.clean_job_id else None
    doc = db.get(KbDocument, b.kb_doc_id) if b.kb_doc_id else None
    readiness = {"processing": ("processing", "识别中", "原件已保存，正在识别。长时间未完成可重新识别"),
                 "pending": ("confirmation", "待核对", "核对分类、可见范围或表格字段后确认"),
                 "pool": ("needs_help", "待整理", "尚未入库，可选择知识库、台账或重新识别"),
                 "withdrawn": ("withdrawn", "已撤回", "资料不再参与检索或统计"),
                 "dismissed": ("dismissed", "已处理（未入库）", "查看整理人的处理说明")}.get(b.status, ("ready", "已入库", ""))
    if b.status == "done" and doc:
        readiness = {"pending": ("review", "待审核", "分类审核通过后，小聚和授权同事才能检索"),
                     "rejected": ("rejected", "已驳回", doc.review_note or "请到知识库修改后重新提交审核"),
                     "archived": ("archived", "已下架", "已从知识检索中移除"),
                     "published": ("ready", "已发布", "已建立检索索引，按可见范围供同事和小聚使用")}[doc.status]
    elif b.status == "done" and job:
        stats = job.stats or {}
        issues = stats.get("error", 0) + stats.get("import_failed", 0)
        readiness = ("failed", "未导入有效记录", "请下载清洗报告，修正异常后重新提交") if not stats.get("imported") else (
            ("partial", "部分入库", f"已导入 {stats['imported']} 行，另有 {issues} 行异常；请核对清洗报告") if issues else
            ("ready", "已入台账", f"已导入 {stats['imported']} 行，使用台账原有权限与统计口径"))
    elif b.status == "done":
        readiness = ("removed", "入库记录已移除", "原件仍保留；如需重新入库，请撤回本次记录后重新上传")
    defaults = {"visibility": "dept" if b.dept_code else "private", "visible_depts": [b.dept_code] if b.dept_code else [], "visible_roles": []}
    source = (b.suggestion or {}).get("external_source")
    if source:
        defaults = {"visibility": "private", "visible_depts": [], "visible_roles": []}
    return {"id": b.id, "filename": b.filename, "size": b.size, "purpose": b.purpose, "kind": b.kind,
            "channel": b.channel, "archive_name": b.archive_name,
            "route": b.route, "route_name": "待识别" if b.status == "processing" else ROUTE_NAMES.get(b.route), "status": b.status,
            "status_name": readiness[1], "stage": readiness[0], "next_step": readiness[2], "ready": readiness[0] == "ready",
            "default_visibility": defaults, "target": b.target,
            "source_snapshot": file_brief(db.get(SysFile, source["raw_file_id"])) if source and source.get("raw_file_id") else None,
            "target_name": clean_service.TARGET_NAMES.get(b.target), "suggestion": b.suggestion or {}, "note": b.note,
            "uploader_id": b.uploader_id, "uploader": names.get(b.uploader_id), "dept_code": b.dept_code,
            "handler": names.get(b.handler_id), "handled_at": b.handled_at, "created_at": b.created_at, "updated_at": b.updated_at,
            "file": file_brief(db.get(SysFile, b.file_id)),
            "clean_job": {"id": job.id, "status": job.status, "stats": {k: v for k, v in (job.stats or {}).items()
                                                                         if k in ("total", "importable", "error", "duplicate",
                                                                                  "imported", "import_failed", "skipped", "rolled_back")}}
            if job else None,
            "kb_doc": {"id": doc.id, "title": doc.title, "status": doc.status,
                       "status_name": kb_service.STATUS_NAMES.get(doc.status), "category": doc.category,
                       "visibility": doc.visibility, "visible_depts": doc.visible_depts, "visible_roles": doc.visible_roles} if doc else None,
            "actions": _actions(b, p)}


def list_batches(db: Session, p, scope: str = "mine", status: str | None = None, page: int = 1,
                 page_size: int = 20) -> dict:
    q = select(ImportBatch).where(batch_filter(p))
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
        counts["pool"] = db.scalar(select(func.count(ImportBatch.id)).where(ImportBatch.status == "pool", batch_filter(p))) or 0
    counts["mine_pending"] = db.scalar(select(func.count(ImportBatch.id)).where(
        ImportBatch.uploader_id == p.id, ImportBatch.status == "pending")) or 0
    processing = select(func.count(ImportBatch.id)).where(ImportBatch.status == "processing", batch_filter(p))
    if scope == "mine":
        processing = processing.where(ImportBatch.uploader_id == p.id)
    counts["processing"] = db.scalar(processing) or 0
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
    from app.core.deps import build_principal
    owner = build_principal(db.get(SysUser, b.uploader_id))
    if (b.suggestion or {}).get("external_source") and not meta.get("visibility"):
        meta["visibility"] = "private"
    d = kb_service.upload_document(db, p, data=data, filename=b.filename, owner=owner, **meta)
    b.kb_doc_id = d.id
    _mark(b, p, "done", "已发布到知识库" if d.status == "published" else "已提交知识库，等待分类审核人审核")
    db.flush()
    _notify_uploader(db, b, p.id, f"《{b.filename}》{b.note}（{d.category}）。")
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
    _notify_uploader(db, b, p.id, f"{p.user.name} 正在把《{b.filename}》导入{clean_service.TARGET_NAMES[target]}。")
    return b


def to_kb(db: Session, p, batch_id: int) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if b.status not in ("pending", "pool"):
        raise conflict("这份资料已处理")
    if b.route == "clean" and b.clean_job_id:
        job = _locked_clean(db, b.clean_job_id)
        if job and job.status == "imported":
            raise conflict("已导入台账，请先撤回")
        if job and job.status == "preview":
            job.status = "cancelled"
        b.clean_job_id = None
    data = read_bytes(db.get(SysFile, b.file_id))
    _route_document(db, b, data)
    if b.status == "pool":
        raise bad_request(b.suggestion.get("reason") or "文件无法放入知识库")
    if b.uploader_id != p.id:
        b.handler_id, b.handled_at = p.id, utcnow()
    db.flush()
    return b


def to_pool(db: Session, p, batch_id: int, note: str | None = None) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if b.status != "pending":
        raise conflict("只有待确认的资料可以交给整理人")
    if b.route == "clean" and b.clean_job_id:
        job = _locked_clean(db, b.clean_job_id)
        if job and job.status == "imported":
            raise conflict("已导入台账，不能再转交")
        if job and job.status == "preview":
            job.status = "cancelled"
        b.clean_job_id = None
    _to_pool(b, (note or "").strip()[:200] or "上传人请整理人协助处理")
    db.flush()
    _notify_pool(db, [b], p.user.name)
    return b


def dismiss(db: Session, p, batch_id: int, note: str) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if not is_handler(p):
        raise forbidden("只有资料整理人可以处理待整理池")
    if b.status != "pool":
        raise conflict("只有待整理的资料可以标记已处理")
    if not (note or "").strip():
        raise bad_request("请写明处理方式，例如「已线下录入」「无需入库」")
    _mark(b, p, "dismissed", note.strip())
    db.flush()
    _notify_uploader(db, b, p.id, f"{p.user.name} 已处理《{b.filename}》：{b.note}")
    return b


def retry(db: Session, p, batch_id: int) -> ImportBatch:
    b = _get(db, p, batch_id, manage=True)
    if b.status not in ("pool", "processing") or b.clean_job_id or b.kb_doc_id:
        raise conflict("只能重新识别尚未进入知识库或台账的资料")
    if b.status == "processing" and (utcnow() - b.updated_at).total_seconds() < 300:
        raise conflict("资料仍在识别中，请稍后刷新；超过 5 分钟未完成可重试")
    b.status, b.note = "processing", None
    if b.uploader_id != p.id:
        b.handler_id, b.handled_at = p.id, utcnow()
    b.updated_at = utcnow()
    db.flush()
    return b


def withdraw(db: Session, p, batch_id: int) -> ImportBatch:
    """撤回：按去向回滚已产生的数据。台账记录被后续业务引用时整体拒绝（错误里列出原因）。"""
    b = _get(db, p, batch_id, manage=True)
    if b.status in ("withdrawn", "dismissed"):
        raise conflict("这份资料已撤回或已处理")
    note = "已撤回"
    if b.clean_job_id:
        job = _locked_clean(db, b.clean_job_id)
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
    _notify_uploader(db, b, p.id, f"{p.user.name} 撤回了《{b.filename}》：{note}")
    return b
