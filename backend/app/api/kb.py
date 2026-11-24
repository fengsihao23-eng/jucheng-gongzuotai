"""企业知识库（自建）：全员检索/浏览/上传；分类审核人审核发布；知识库管理员（admin 或 kb:manage）维护分类。"""
import json

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deletion import DeletionIn, verify_deletion
from app.core.deps import Principal, client_ip, get_principal
from app.core.permissions import DEPARTMENTS, ROLE_DEFS
from app.core.response import bad_request, ok
from app.services import kb_service

router = APIRouter(prefix="/kb", tags=["kb"])
User = Depends(get_principal)


def _document_result(db, p, document):
    # 审核成功不自动授予已发布原文的阅读权限；返回回执而非提交后再抛 404。
    if not kb_service.can_view(db, p, document):
        return {"id": document.id, "status": document.status, "access_limited": True}
    return kb_service.get_doc(db, document.id, viewer=p)


def _list(raw: str | None) -> list[str]:
    """表单里的列表字段：接受 JSON 数组或逗号分隔。"""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            return [str(x) for x in json.loads(raw)]
        except ValueError:
            raise bad_request("列表字段格式错误")
    return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]


@router.get("/search")
def search(q: str = Query(..., min_length=1, max_length=200), category: str | None = None,
           subcategory: str | None = None, limit: int = Query(10, ge=1, le=50), p: Principal = User,
           db: Session = Depends(get_db)):
    if not q.strip():
        raise bad_request("请输入检索词")
    items = kb_service.search(db, q, category=category or None, limit=limit, viewer=p,
                              subcategory=subcategory or None)
    return ok({"query": q, "category": category, "items": items, "total": len(items),
               "terms": kb_service.query_terms(q)})


@router.get("/categories")
def categories(p: Principal = User, db: Session = Depends(get_db)):
    """扁平一级分类及可见文档数（兼容旧调用）。"""
    out = kb_service.categories(db, viewer=p)
    db.commit()  # 首次访问会写入默认分类树
    return ok(out)


@router.get("/tree")
def tree(p: Principal = User, db: Session = Depends(get_db)):
    out = kb_service.category_tree(db, viewer=p)
    db.commit()
    return ok(out)


@router.get("/meta")
def meta(p: Principal = User, db: Session = Depends(get_db)):
    """上传表单所需选项与当前用户的知识库统计。"""
    st = kb_service.stats(db, viewer=p)
    return ok({**st, "departments": kb_service.department_options(db),
               "roles": [{"code": c, "name": r["name"]} for c, r in ROLE_DEFS.items()],
               "statuses": kb_service.STATUS_NAMES})


@router.post("/categories")
def create_category(request: Request, name: str = Body(...), parent_id: int | None = Body(None),
                    description: str | None = Body(None), reviewer_roles: list[str] | None = Body(None),
                    p: Principal = User, db: Session = Depends(get_db)):
    row = kb_service.save_category(db, p, name=name, parent_id=parent_id, description=description,
                                   reviewer_roles=reviewer_roles)
    log_op(db, p.id, "kb.category.create", "kb_category", row.id, after={"name": row.name, "parent_id": row.parent_id},
           ip=client_ip(request))
    db.commit()
    return ok({"id": row.id})


@router.put("/categories/{cat_id}")
def update_category(cat_id: int, request: Request, name: str = Body(...), description: str | None = Body(None),
                    reviewer_roles: list[str] | None = Body(None), sort: int | None = Body(None),
                    p: Principal = User, db: Session = Depends(get_db)):
    row = kb_service.save_category(db, p, cat_id=cat_id, name=name, description=description,
                                   reviewer_roles=reviewer_roles, sort=sort)
    log_op(db, p.id, "kb.category.update", "kb_category", row.id,
           after={"name": row.name, "reviewer_roles": row.reviewer_roles}, ip=client_ip(request))
    db.commit()
    return ok({"id": row.id})


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, body: DeletionIn, request: Request, p: Principal = User, db: Session = Depends(get_db)):
    verify_deletion(db, body)
    kb_service.delete_category(db, p, cat_id)
    log_op(db, p.id, "kb.category.delete", "kb_category", cat_id, ip=client_ip(request))
    db.commit()
    return ok()


@router.get("/docs")
def docs(category: str | None = None, subcategory: str | None = None, keyword: str | None = None,
         tag: str | None = None, scope: str = Query("published", pattern="^(published|mine|review|archived)$"),
         p: Principal = User, db: Session = Depends(get_db)):
    return ok(kb_service.list_documents(db, category or None, keyword or None, viewer=p, scope=scope,
                                        subcategory=subcategory or None, tag=tag or None))


@router.get("/docs/{doc_id}")
def doc(doc_id: str, p: Principal = User, db: Session = Depends(get_db)):
    return ok(kb_service.get_doc(db, doc_id, viewer=p))


@router.get("/docs/{doc_id}/chunks")
def doc_chunks(doc_id: str, offset: int = Query(0, ge=0), limit: int = Query(5, ge=1, le=10),
               p: Principal = User, db: Session = Depends(get_db)):
    """分页读取本人有权访问的知识正文，每页返回继续位置；不能用摘要代替全文。"""
    return ok(kb_service.document_chunks(db, p, doc_id, offset, limit))


@router.post("/suggest")
async def suggest(file: UploadFile = File(...), p: Principal = User, db: Session = Depends(get_db)):
    """上传前预读：返回建议的标题、摘要、标签与分类（AI 优先，规则兜底），不落库。"""
    from app.services import smart_intake
    from app.services.kb_extract import extract_text
    data = await file.read(50 * 1024 * 1024 + 1)
    name = file.filename or "upload"
    text = extract_text(data, name)
    out = smart_intake.describe_document(db, name, text)
    db.commit()
    return ok({**out, "chars": len(text), "preview": text[:300]})


@router.post("/docs")
async def upload(request: Request, file: UploadFile = File(...), title: str = Form(""), category: str = Form(""),
                 subcategory: str = Form(""), tags: str = Form(""), summary: str = Form(""),
                 visibility: str = Form(""), visible_depts: str = Form(""), visible_roles: str = Form(""),
                 p: Principal = User, db: Session = Depends(get_db)):
    data = await file.read(50 * 1024 * 1024 + 1)
    d = kb_service.upload_document(db, p, data=data, filename=file.filename or "upload", title=title or None,
                                   category=category or None, subcategory=subcategory or None, tags=_list(tags),
                                   summary=summary or None, visibility=visibility or None,
                                   visible_depts=_list(visible_depts), visible_roles=_list(visible_roles))
    log_op(db, p.id, "kb.upload", "kb_document", d.id, after={"title": d.title, "category": d.category,
                                                              "status": d.status, "visibility": d.visibility},
           ip=client_ip(request))
    db.commit()
    return ok(_document_result(db, p, d))


@router.post("/docs/{doc_id}/version")
async def new_version(doc_id: int, request: Request, file: UploadFile = File(...), p: Principal = User,
                      db: Session = Depends(get_db)):
    data = await file.read(50 * 1024 * 1024 + 1)
    d = kb_service.upload_document(db, p, data=data, filename=file.filename or "upload", replace_doc_id=doc_id,
                                   title=None, visibility=None)
    log_op(db, p.id, "kb.version", "kb_document", d.id, after={"version": d.version, "status": d.status},
           ip=client_ip(request))
    db.commit()
    return ok(_document_result(db, p, d))


@router.put("/docs/{doc_id}")
def update_doc(doc_id: int, request: Request, body: dict = Body(...), p: Principal = User,
               db: Session = Depends(get_db)):
    allowed = {"title", "category", "subcategory", "tags", "summary", "visibility", "visible_depts", "visible_roles"}
    fields = {k: v for k, v in body.items() if k in allowed}
    d = kb_service.update_meta(db, p, doc_id, **fields)
    log_op(db, p.id, "kb.update", "kb_document", d.id, after={k: fields[k] for k in fields if k != "summary"},
           ip=client_ip(request))
    db.commit()
    return ok(_document_result(db, p, d))


@router.post("/docs/{doc_id}/review")
def review(doc_id: int, request: Request, approve: bool = Body(...), note: str | None = Body(None),
           p: Principal = User, db: Session = Depends(get_db)):
    d = kb_service.review(db, p, doc_id, approve, note)
    log_op(db, p.id, "kb.review", "kb_document", d.id, after={"status": d.status, "note": d.review_note},
           ip=client_ip(request))
    db.commit()
    return ok(_document_result(db, p, d))


@router.post("/docs/{doc_id}/archive")
def archive(doc_id: int, request: Request, archived: bool = Body(True, embed=True), p: Principal = User,
            db: Session = Depends(get_db)):
    d = kb_service.set_archived(db, p, doc_id, archived)
    log_op(db, p.id, "kb.archive" if archived else "kb.restore", "kb_document", d.id, ip=client_ip(request))
    db.commit()
    return ok(_document_result(db, p, d))


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: int, body: DeletionIn, request: Request, p: Principal = User, db: Session = Depends(get_db)):
    verify_deletion(db, body)
    kb_service.remove_document(db, p, doc_id, client_ip(request))
    db.commit()
    return ok()
