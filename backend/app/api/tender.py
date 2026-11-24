"""标书助手（PRD 7.7）：文档库、多轮对话式合规检查、方案生成（带出处）、售前资料库。"""
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deletion import DeletionIn, verify_deletion
from app.core.deps import Principal, require_menu
from app.core.response import bad_request, forbidden, not_found, ok
from app.core.storage import file_brief
from app.models import ProdBidCheck, ProdGeneration, ProdTenderDoc, SysFile
from app.services import tender_service as ts

router = APIRouter(prefix="/tender", tags=["tender"])
menu = require_menu("tender")
MAX_UPLOAD = 50 * 1024 * 1024


def _writer(p: Principal):
    if not p.can("tender:write"):
        raise forbidden("无标书助手操作权限")


class TextDocIn(BaseModel):
    doc_type: str
    title: str = Field(..., max_length=200)
    content: str = Field(..., min_length=20)
    project_code: str | None = None


class CheckIn(BaseModel):
    tender_doc_id: int
    bid_doc_id: int


class ReplyIn(BaseModel):
    answer: str = Field("", max_length=2000)


class GenIn(BaseModel):
    gen_type: str
    tender_doc_id: int | None = None
    material_ids: list[int] = []
    title: str | None = Field(None, max_length=200)


# ---------------- 文档库
@router.post("/docs/upload")
async def upload(file: UploadFile = File(...), doc_type: str = Form(...), title: str | None = Form(None),
                 project_code: str | None = Form(None), p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise bad_request("文件超过 50MB")
    d = ts.upload_document(db, doc_type, file.filename or "upload.txt", data, title, project_code, p.id)
    db.commit()
    return ok(ts.doc_brief(d, with_clauses=True))


@router.post("/docs/text")
def create_text(body: TextDocIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    if body.doc_type not in ts.DOC_TYPES:
        raise bad_request("doc_type 须为 tender / bid / material")
    d = ts.create_document(db, body.doc_type, body.title, body.content, body.project_code, p.id)
    db.commit()
    return ok(ts.doc_brief(d, with_clauses=True))


@router.get("/docs")
def docs(doc_type: str | None = None, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    q = select(ProdTenderDoc).order_by(ProdTenderDoc.created_at.desc(), ProdTenderDoc.id.desc())
    if doc_type:
        q = q.where(ProdTenderDoc.doc_type == doc_type)
    return ok([ts.doc_brief(d) for d in db.scalars(q)])


@router.get("/docs/{doc_id}")
def doc(doc_id: int, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    d = ts.doc_or_404(db, doc_id)
    out = ts.doc_brief(d, with_clauses=True)
    out["file"] = file_brief(db.get(SysFile, d.file_id)) if d.file_id else None
    return ok(out)


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: int, body: DeletionIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    if not p.can_delete("tender:write"):
        raise forbidden("无权删除标书资料")
    verify_deletion(db, body)
    d = ts.doc_or_404(db, doc_id)
    used = db.scalar(select(ProdBidCheck.id).where((ProdBidCheck.tender_doc_id == d.id) | (ProdBidCheck.bid_doc_id == d.id)))
    used = used or db.scalar(select(ProdGeneration.id).where(ProdGeneration.tender_doc_id == d.id))
    if used:
        raise bad_request("该文档已被合规检查或方案生成引用，不能删除")
    db.delete(d)
    db.commit()
    return ok(True)


# ---------------- 合规检查（多轮）
def _check(db: Session, check_id: int) -> ProdBidCheck:
    c = db.get(ProdBidCheck, check_id)
    if not c:
        raise not_found("合规检查")
    return c


@router.post("/checks")
def start_check(body: CheckIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    chk = ts.start_check(db, body.tender_doc_id, body.bid_doc_id, p.id)
    db.commit()
    return ok(ts.check_view(db, chk))


@router.post("/checks/{check_id}/reply")
def reply(check_id: int, body: ReplyIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    chk = ts.reply(db, _check(db, check_id), body.answer)
    db.commit()
    return ok(ts.check_view(db, chk))


@router.get("/checks")
def checks(p: Principal = Depends(menu), db: Session = Depends(get_db)):
    rows = db.scalars(select(ProdBidCheck).order_by(ProdBidCheck.created_at.desc(), ProdBidCheck.id.desc()).limit(50)).all()
    out = []
    for c in rows:
        t = db.get(ProdTenderDoc, c.tender_doc_id)
        b = db.get(ProdTenderDoc, c.bid_doc_id) if c.bid_doc_id else None
        out.append({"id": c.id, "status": c.status, "tender_title": t.title if t else None,
                    "bid_title": b.title if b else None, "rounds": len(c.rounds), "stats": ts.check_stats(c.findings),
                    "created_at": c.created_at})
    return ok(out)


@router.get("/checks/{check_id}")
def check(check_id: int, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    return ok(ts.check_view(db, _check(db, check_id)))


@router.post("/checks/{check_id}/export")
def export_check(check_id: int, fmt: str = Query("xlsx", pattern="^(xlsx|docx)$"), p: Principal = Depends(menu),
                 db: Session = Depends(get_db)):
    f = ts.export_check(db, _check(db, check_id), fmt, p.id)
    db.commit()
    return ok(file_brief(f))


@router.post("/bid-check")
def bid_check(body: CheckIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    """一次性全维度检查（与 MCP kb.bid_check 同一实现）。"""
    _writer(p)
    res = ts.bid_check(db, body.tender_doc_id, body.bid_doc_id, persist=True, user_id=p.id)
    db.commit()
    return ok(res)


# ---------------- 方案生成
def _gen(db: Session, gen_id: int) -> ProdGeneration:
    g = db.get(ProdGeneration, gen_id)
    if not g:
        raise not_found("方案")
    return g


@router.post("/generations")
def generate(body: GenIn, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    _writer(p)
    g = ts.generate(db, body.gen_type, body.tender_doc_id, body.material_ids, body.title, p.id)
    db.commit()
    return ok(ts.generation_view(g))


@router.get("/generations")
def generations(p: Principal = Depends(menu), db: Session = Depends(get_db)):
    rows = db.scalars(select(ProdGeneration).order_by(ProdGeneration.created_at.desc(), ProdGeneration.id.desc()).limit(50))
    return ok([{"id": g.id, "gen_type": g.gen_type, "gen_type_text": ts.GEN_TYPES.get(g.gen_type), "title": g.title,
                "engine": g.engine, "sections": len(g.sections), "created_at": g.created_at} for g in rows])


@router.get("/generations/{gen_id}")
def generation(gen_id: int, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    return ok(ts.generation_view(_gen(db, gen_id)))


@router.post("/generations/{gen_id}/export")
def export_generation(gen_id: int, p: Principal = Depends(menu), db: Session = Depends(get_db)):
    f = ts.export_generation(db, _gen(db, gen_id), p.id)
    db.commit()
    return ok(file_brief(f))


# ---------------- 售前资料库
@router.get("/library")
def library(p: Principal = Depends(menu), db: Session = Depends(get_db)):
    return ok(ts.presales_library(db))


@router.get("/meta")
def meta(p: Principal = Depends(menu)):
    return ok({"doc_types": ts.DOC_TYPES, "gen_types": ts.GEN_TYPES, "dimensions": ts.DIMENSIONS,
               "can_write": p.can("tender:write")})
