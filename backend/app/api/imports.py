"""统一资料导入（全员）：上传 → 自动分流到台账清洗 / 知识库 / 待整理池；整理人（imports:handle）处理待整理池。"""
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.api.kb import _list
from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import ok
from app.services import import_service as svc

router = APIRouter(prefix="/imports", tags=["imports"])
User = Depends(get_principal)


class WecomSearchIn(BaseModel):
    kind: str = Field(default="doc", pattern="^(doc|disk)$")
    keywords: str = Field(min_length=1, max_length=120)
    cursor: str = Field(default="", max_length=4000)
    space: str = Field(default="", max_length=100)


class WecomPreviewIn(BaseModel):
    url: str = Field(default="", max_length=1000)
    source_id: str = Field(default="", max_length=32)
    table_id: str = Field(default="", max_length=200)


class WecomConfirmIn(BaseModel):
    source_id: str = Field(min_length=32, max_length=32)
    purpose: str = Field(default="", max_length=255)


@router.get("/wecom/status")
def wecom_status(p: Principal = User):
    from app.services import wecom_import_service as external
    return ok(external.status(p))


@router.post("/wecom/search")
def wecom_search(body: WecomSearchIn, p: Principal = User):
    """按本人连接搜索资料；结果不等于已授权读取正文。"""
    from app.services import wecom_import_service as external
    return ok(external.search(p, **body.model_dump()))


@router.post("/wecom/preview")
def wecom_preview(body: WecomPreviewIn, background_tasks: BackgroundTasks, p: Principal = User):
    from app.services import wecom_import_service as external
    source = external.start(p, **body.model_dump())
    background_tasks.add_task(external.prepare, p.id, source["id"])
    return ok(external.view(source))


@router.get("/wecom/sources/{source_id}")
def wecom_source(source_id: str, p: Principal = User):
    from app.services import wecom_import_service as external
    return ok(external.get(p, source_id))


@router.get("/wecom/sources")
def wecom_sources(p: Principal = User):
    from app.services import wecom_import_service as external
    return ok(external.list_sources(p))


@router.post("/wecom/confirm")
def wecom_confirm(body: WecomConfirmIn, request: Request, background_tasks: BackgroundTasks,
                  p: Principal = User, db: Session = Depends(get_db)):
    from app.services import wecom_import_service as external
    batch, reused = external.confirm(db, p, body.source_id, body.purpose)
    log_op(db, p.id, "import.wecom_capture", "import_batch", batch.id,
           after={"source_id": body.source_id, "reused": reused}, ip=client_ip(request))
    db.commit()
    external.completed(p, body.source_id, batch.id)
    if not reused:
        background_tasks.add_task(svc.process_many, p.id, [batch.id])
    return ok({**svc.view(db, batch, p), "reused": reused})


def _done(db: Session, p: Principal, b, action: str, request: Request, after: dict | None = None):
    log_op(db, p.id, f"import.{action}", "import_batch", b.id,
           after={"status": b.status, "route": b.route, **(after or {})}, ip=client_ip(request))
    db.commit()
    return svc.view(db, b, p)


@router.post("")
async def upload(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...),
                 purpose: str = Form(""), background: bool = Form(False), p: Principal = User,
                 db: Session = Depends(get_db)):
    """上传一个文件或一个 zip 压缩包（逐个文件建批次）。返回第一份的视图，并附 batches / skipped。"""
    data = await file.read(svc.MAX_BYTES + 1)
    r = svc.create_many(db, p, data, file.filename or "upload", purpose or None, deferred=background)
    for b in r["batches"]:
        log_op(db, p.id, "import.upload", "import_batch", b.id,
               after={"filename": b.filename, "kind": b.kind, "route": b.route, "status": b.status,
                      "archive": r["archive"]}, ip=client_ip(request))
    db.commit()
    views = [svc.view(db, b, p) for b in r["batches"]]
    queued = [b.id for b in r["batches"] if b.status == "processing"]
    if queued:
        # 扫描件/图片自动转后台逐页识别；普通文档仍在上传请求内完成分流。
        background_tasks.add_task(svc.process_many, p.id, queued)
    return ok({**views[0], "batches": views, "skipped": r["skipped"], "archive": r["archive"]})


@router.get("")
def batches(scope: str = Query("mine", pattern="^(mine|pool|all)$"), status: str = "", page: int = Query(1, ge=1),
            page_size: int = Query(20, ge=1, le=100), p: Principal = User, db: Session = Depends(get_db)):
    out = svc.list_batches(db, p, scope, status or None, page, page_size)
    db.commit()  # 同步清洗任务状态
    return ok(out)


@router.get("/{batch_id}")
def batch(batch_id: int, p: Principal = User, db: Session = Depends(get_db)):
    b = svc._get(db, p, batch_id)
    db.commit()
    return ok(svc.view(db, b, p))


@router.get("/{batch_id}/text")
def batch_text(batch_id: int, p: Principal = User, db: Session = Depends(get_db)):
    """已抽取/识别出的正文，供上传人和授权整理人核对扫描件转录是否准确。"""
    b = svc._get(db, p, batch_id)
    return ok({"id": b.id, "filename": b.filename, "chars": len(b.content or ""), "text": b.content or "",
               "extract": (b.suggestion or {}).get("extract")})


@router.post("/{batch_id}/confirm-kb")
def confirm_kb(batch_id: int, request: Request, title: str = Body(""), category: str = Body(""),
               subcategory: str = Body(""), tags: list[str] | str | None = Body(None), summary: str = Body(""),
               visibility: str = Body(""), visible_depts: list[str] | str | None = Body(None),
               visible_roles: list[str] | str | None = Body(None), p: Principal = User, db: Session = Depends(get_db)):
    as_list = lambda v: v if isinstance(v, list) else _list(v)  # noqa: E731
    b = svc.confirm_kb(db, p, batch_id, title=title or None, category=category or None,
                       subcategory=subcategory or None, tags=as_list(tags), summary=summary or None,
                       visibility=visibility or None, visible_depts=as_list(visible_depts),
                       visible_roles=as_list(visible_roles))
    return ok(_done(db, p, b, "confirm_kb", request, {"kb_doc_id": b.kb_doc_id}))


@router.post("/{batch_id}/to-clean")
def to_clean(batch_id: int, request: Request, target: str = Body(..., embed=True), p: Principal = User,
             db: Session = Depends(get_db)):
    b = svc.to_clean(db, p, batch_id, target)
    return ok(_done(db, p, b, "to_clean", request, {"target": target, "clean_job_id": b.clean_job_id}))


@router.post("/{batch_id}/to-kb")
def to_kb(batch_id: int, request: Request, background_tasks: BackgroundTasks, p: Principal = User,
          db: Session = Depends(get_db)):
    b, queued = svc.to_kb(db, p, batch_id)
    uploader_id = b.uploader_id
    result = _done(db, p, b, "to_kb", request, {"queued": queued})
    if queued:
        background_tasks.add_task(svc.process_many, uploader_id, [batch_id])
    return ok(result)


@router.post("/{batch_id}/to-pool")
def to_pool(batch_id: int, request: Request, note: str = Body("", embed=True), p: Principal = User,
            db: Session = Depends(get_db)):
    return ok(_done(db, p, svc.to_pool(db, p, batch_id, note), "to_pool", request))


@router.post("/{batch_id}/dismiss")
def dismiss(batch_id: int, request: Request, note: str = Body(..., embed=True), p: Principal = User,
            db: Session = Depends(get_db)):
    return ok(_done(db, p, svc.dismiss(db, p, batch_id, note), "dismiss", request, {"note": note}))


@router.post("/{batch_id}/withdraw")
def withdraw(batch_id: int, request: Request, p: Principal = User, db: Session = Depends(get_db)):
    b = svc.withdraw(db, p, batch_id)
    return ok(_done(db, p, b, "withdraw", request, {"note": b.note}))


@router.post("/{batch_id}/retry")
def retry(batch_id: int, request: Request, background_tasks: BackgroundTasks, p: Principal = User,
          db: Session = Depends(get_db)):
    b = svc.retry(db, p, batch_id)
    result = _done(db, p, b, "retry", request)
    background_tasks.add_task(svc.process_many, b.uploader_id, [b.id])
    return ok(result)
