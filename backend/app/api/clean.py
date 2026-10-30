"""Excel 数据清洗助手（PRD 7.4）。菜单 clean；预览/导入需按钮 clean:import。"""
from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import BizError, forbidden, ok, page_data
from app.models import CleanJob, SysUser
from app.services import clean_service as svc
from app.services.intake_access import clean_filter, get_clean_job

router = APIRouter(prefix="/clean", tags=["clean"])
Menu = Depends(require_menu("clean"))


def _importer(p: Principal) -> None:
    if not p.can("clean:import"):
        raise forbidden("无数据清洗导入权限")


def _job(db: Session, p: Principal, job_id: int, *, lock=False) -> CleanJob:
    return get_clean_job(db, p, job_id, lock=lock)


@router.get("/templates")
def templates(p: Principal = Menu, db: Session = Depends(get_db)):
    tpls = svc.templates(db)
    return ok([{"target": k, "name": svc.TARGET_NAMES[k], "fields": v["fields"]} for k, v in tpls.items()])


@router.get("/detect-target")
def detect_target(purpose: str = "", filename: str = "", p: Principal = Menu):
    t = svc.detect_target(purpose, filename)
    return ok({"target": t, "target_name": svc.TARGET_NAMES.get(t)})


@router.post("/preview")
async def preview(request: Request, file: UploadFile = File(...), purpose: str = Form(""), target: str = Form(""),
                  p: Principal = Menu, db: Session = Depends(get_db)):
    _importer(p)
    data = await file.read(20 * 1024 * 1024 + 1)
    job = svc.preview(db, p.id, data, file.filename or "upload.xlsx", purpose=purpose or None, target=target or None)
    log_op(db, p.id, "clean.preview", "clean_job", job.id, after={"filename": job.filename, "target": job.target,
                                                                    "stats": {k: v for k, v in job.stats.items() if k != "skipped_rows"}},
           ip=client_ip(request))
    db.commit()
    return ok(svc.job_view(db, job))


@router.post("/jobs/{job_id}/remap")
def remap(job_id: int, mapping: dict[str, str | None] | None = Body(default=None, embed=True),
          target: str | None = Body(default=None, embed=True), header_row: int | None = Body(default=None, embed=True),
          sheet_name: str | None = Body(default=None, embed=True), ai: bool = Body(default=False, embed=True),
          p: Principal = Menu, db: Session = Depends(get_db)):
    _importer(p)
    job = svc.remap(db, _job(db, p, job_id, lock=True), mapping, target, header_row, sheet_name, ai=ai)
    db.commit()
    return ok(svc.job_view(db, job))


@router.post("/jobs/{job_id}/import")
def import_(job_id: int, request: Request, p: Principal = Menu, db: Session = Depends(get_db)):
    _importer(p)
    job = _job(db, p, job_id, lock=True)
    svc.import_job(db, job, p.id)
    log_op(db, p.id, "clean.import", "clean_job", job.id,
           after={"target": job.target, "imported": job.stats.get("imported"), "skipped": job.stats.get("skipped")},
           ip=client_ip(request))
    db.commit()
    return ok(svc.job_view(db, job))


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    _importer(p)
    job = _job(db, p, job_id, lock=True)
    if job.status != "preview":
        raise BizError(40901, "仅预览中的任务可取消")
    job.status = "cancelled"
    db.commit()
    return ok(svc.job_view(db, job, include_rows=False))


@router.post("/jobs/{job_id}/report")
def report(job_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    """生成/刷新清洗报告（预览阶段也可先下载核对）。"""
    job = _job(db, p, job_id, lock=True)
    svc.build_report(db, job, p.id)
    db.commit()
    return ok(svc.job_view(db, job, include_rows=False))


@router.get("/jobs")
def jobs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), target: str = "", status: str = "", p: Principal = Menu,
         db: Session = Depends(get_db)):
    q = select(CleanJob).where(clean_filter(p))
    if target:
        q = q.where(CleanJob.target == target)
    if status:
        q = q.where(CleanJob.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(CleanJob.created_at.desc(), CleanJob.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    names = {u.id: u.name for u in db.scalars(select(SysUser).where(SysUser.id.in_({r.created_by for r in rows if r.created_by})))}
    items = []
    for r in rows:
        v = svc.job_view(db, r, include_rows=False)
        v.pop("mapping", None)
        v.pop("fields", None)
        v["stats"] = {k: x for k, x in (r.stats or {}).items() if k != "skipped_rows"}
        v["creator"] = names.get(r.created_by)
        items.append(v)
    return ok(page_data(items, total, page, page_size))


@router.get("/jobs/{job_id}")
def job(job_id: int, p: Principal = Menu, db: Session = Depends(get_db)):
    return ok(svc.job_view(db, _job(db, p, job_id)))
