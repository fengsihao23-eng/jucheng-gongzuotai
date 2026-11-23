"""Standalone finance ledger upload, human approval and reading endpoints."""
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Principal, client_ip, require_menu
from app.core.response import bad_request, ok
from app.services import finance_ledger_fields as f, finance_ledgers as service

router = APIRouter(prefix="/finance-ledgers", tags=["财务账表"])
PrincipalDep = Annotated[Principal, Depends(require_menu("receivables", "expenses"))]
DB = Annotated[Session, Depends(get_db)]
Kind = Literal["bank_receipt", "qr_receipt", "bank_expense", "cash_journal", "invoice"]
RowID = Annotated[str, Field(pattern=r"^\d{1,4}:\d{1,8}$")]
GroupID = Annotated[str, Field(pattern=r"^\d{1,4}$")]
Version = Annotated[int, Field(ge=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RowUpdate(Strict):
    id: RowID
    values: dict[Annotated[str, Field(max_length=32)], Annotated[str, Field(max_length=2000)]] = Field(max_length=20)


class GroupConfig(Strict):
    id: GroupID
    kind: Kind
    header: int = Field(ge=0)
    mapping: dict[Annotated[str, Field(pattern=r"^(0|[1-9]\d?)$")], Annotated[str, Field(max_length=32)]] = Field(max_length=f.MAX_COLUMNS)
    account: str = Field("", max_length=128)
    period: str = Field("", pattern=r"^$|^20\d\d-(0[1-9]|1[0-2])$")


class Review(Strict):
    version: Version
    groups: list[GroupConfig] = Field(default_factory=list, max_length=f.MAX_ROWS)
    rows: list[RowUpdate] = Field(default_factory=list, max_length=f.MAX_ROWS)


class Confirm(Strict):
    version: Version
    row_ids: list[RowID] = Field(min_length=1, max_length=f.MAX_ROWS)


class Allocate(Strict):
    project: str = Field(max_length=128)


@router.get("/schema")
def schema(p: PrincipalDep):
    return ok({"ledgers": {kind: ledger.meta(f.can_import(p, kind)) for kind, ledger in f.LEDGERS.items() if f.can_read(p, kind)},
               "limits": {"bytes": f.MAX_BYTES, "rows": f.MAX_ROWS, "columns": f.MAX_COLUMNS},
               "can_allocate": p.has_menu("expenses") and p.can("expense:allocate") and f.can_read(p, "bank_expense")})


@router.get("/entries/{kind}")
def entries(kind: Kind, p: PrincipalDep, db: DB, period: str = Query("", pattern=r"^$|^20\d\d-(0[1-9]|1[0-2])$"),
            account: str = Query("", max_length=128), keyword: str = Query("", max_length=120), project: str = Query("", max_length=128),
            page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100)):
    service.require_access(p, kind)
    return ok(service.list_entries(db, kind, period, account, keyword.strip(), project, page, size))


@router.patch("/entries/{ident}/project")
def allocate(ident: int, body: Allocate, request: Request, p: PrincipalDep, db: DB):
    data = service.assign_project(db, p, ident, body.project.strip(), client_ip(request))
    db.commit()
    return ok(data)


@router.post("/preview")
def preview(request: Request, p: PrincipalDep, db: DB, kind: Annotated[Kind, Form()], file: Annotated[UploadFile, File()],
            use_xiaoju: Annotated[bool, Form()] = True):
    service.require_access(p, kind, write=True)
    content = file.file.read(f.MAX_BYTES + 1)
    if not content or len(content) > f.MAX_BYTES:
        raise bad_request("请选择大小在 20 MB 以内的非空文件")
    filename = Path((file.filename or "导入.txt").replace("\\", "/")).name.replace("\x00", "")
    if len(filename) > 255:
        raise bad_request("文件名过长，请缩短后上传")
    data = service.build_draft(db, p, filename, content, kind, use_xiaoju, client_ip(request))
    db.commit()
    return ok(data)


@router.get("/imports/{token}")
def draft(token: str, p: PrincipalDep, db: DB):
    batch = service.get_batch(db, p, token)
    if batch.result is not None:
        for kind in batch.result["selection"]:
            service.require_access(p, kind, write=True)
        return ok({"id": batch.token, "version": batch.version, "filename": batch.filename, "result": batch.result})
    return ok(service.report(db, batch, p))


@router.post("/imports/{token}/review")
def review(token: str, body: Review, p: PrincipalDep, db: DB):
    batch = service.get_batch(db, p, token, lock=True)
    data = service.review(db, batch, p, body.version, body.groups, body.rows)
    db.commit()
    return ok(data)


@router.post("/imports/{token}/confirm")
def confirm(token: str, body: Confirm, request: Request, p: PrincipalDep, db: DB):
    batch = service.get_batch(db, p, token, lock=True)
    data = service.write_rows(db, batch, p, body.version, body.row_ids, client_ip(request))
    db.commit()
    return ok(data)
