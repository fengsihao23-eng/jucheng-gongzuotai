"""知识库（乐享集成，PRD FR-06）：全员可用检索/目录/预览；同步仅管理员或 kb:sync 按钮。"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import bad_request, forbidden, ok
from app.services import kb_service

router = APIRouter(prefix="/kb", tags=["kb"])


@router.get("/search")
def search(q: str = Query(..., min_length=1, max_length=200), category: str | None = None,
           limit: int = Query(10, ge=1, le=50), p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not q.strip():
        raise bad_request("请输入检索词")
    items = kb_service.search(db, q, category=category or None, limit=limit)
    return ok({"query": q, "category": category, "items": items, "total": len(items),
               "terms": kb_service.query_terms(q)})


@router.get("/categories")
def categories(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(kb_service.categories(db))


@router.get("/docs")
def docs(category: str | None = None, keyword: str | None = None, p: Principal = Depends(get_principal),
         db: Session = Depends(get_db)):
    return ok(kb_service.list_documents(db, category or None, keyword or None))


@router.get("/docs/{doc_id}")
def doc(doc_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok(kb_service.get_doc(db, doc_id))


@router.get("/sync/status")
def sync_status(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return ok({**kb_service.sync_state(db), "can_sync": p.is_admin or p.can("kb:sync")})


@router.post("/sync")
def sync(request: Request, full: bool = False, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not (p.is_admin or p.can("kb:sync")):
        raise forbidden("仅管理员可触发乐享同步")
    state = kb_service.sync(db, full=full)
    log_op(db, p.id, "kb.sync", "kb_document", None, after={"full": full, "added": state.get("added"),
                                                             "updated": state.get("updated")}, ip=client_ip(request))
    db.commit()
    return ok(state)
