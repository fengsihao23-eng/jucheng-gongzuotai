from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deletion import DeletionIn, verify_deletion
from app.core.deps import Principal, client_ip, get_principal
from app.core.response import ok
from app.services import record_deletion

router = APIRouter(prefix="/record-deletions", tags=["删除记录"])


@router.delete("/{kind}/{ident}")
def remove_record(kind: Annotated[str, Path(max_length=45)], ident: Annotated[str, Path(max_length=200)],
                  body: DeletionIn, request: Request, p: Annotated[Principal, Depends(get_principal)],
                  db: Annotated[Session, Depends(get_db)], sheet: Annotated[str, Query(max_length=200)] = "",
                  revision: Annotated[str, Query(max_length=64)] = ""):
    verify_deletion(db, body)
    record_deletion.delete(db, p, kind, {"id": ident, "sheet": sheet, "revision": revision}, client_ip(request))
    db.commit()
    if kind in {"contract", "contract-payment", "expense", "invoice", "receipt"} or kind.startswith("workbook-"):
        from app.services.board_service import invalidate
        invalidate()
    return ok({"deleted": True})
