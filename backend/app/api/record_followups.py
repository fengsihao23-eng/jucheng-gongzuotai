"""Comments, progress updates and process history for business records."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, get_principal
from app.core.response import ok
from app.services import record_followup_targets as targets, record_followups as service

router = APIRouter(prefix="/record-followups", tags=["业务跟进"])
HEADERS = {"Cache-Control": "no-store"}
Actor = Annotated[Principal, Depends(get_principal)]
DB = Annotated[Session, Depends(get_db)]


def target(kind: Annotated[str, Path(max_length=45)], ident: Annotated[str, Path(min_length=1, max_length=200)],
           p: Actor, db: DB, sheet: Annotated[str, Query(max_length=200)] = "",
           revision: Annotated[str, Query(max_length=64)] = ""):
    return targets.resolve(db, p, kind, {"id": ident, "sheet": sheet, "revision": revision})


Record = Annotated[targets.Record, Depends(target)]


@router.get("/entries/{entry_id}")
def linked_record(entry_id: Annotated[int, Path(gt=0, lt=10**18)], p: Actor, db: DB):
    return ok(service.from_entry(db, p, entry_id), headers=HEADERS)


@router.get("/{kind}/{ident}")
def history(record: Record, p: Actor, db: DB, before: Annotated[int | None, Query(gt=0, lt=10**18)] = None):
    return ok(service.view(db, p, record, before=before), headers=HEADERS)


@router.get("/{kind}/{ident}/recipients")
def recipients(record: Record, p: Actor, db: DB, keyword: Annotated[str, Query(max_length=60)] = ""):
    return ok(service.recipients(db, p, record, keyword.strip()), headers=HEADERS)


@router.post("/{kind}/{ident}")
def add(record: Record, body: service.FollowupIn, p: Actor, db: DB):
    result = service.write(db, p, record, body)
    db.commit()
    return ok(result, headers=HEADERS)
