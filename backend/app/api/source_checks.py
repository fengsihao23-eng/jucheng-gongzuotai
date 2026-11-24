"""Controlled finance-source reads and named WeCom renewal ownership."""
import hashlib
from datetime import date
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_op
from app.core.db import get_db
from app.core.deps import Principal, require_menu
from app.core.response import bad_request, conflict, forbidden, not_found, ok
from app.core.timeutil import today
from app.models import SysUser
from app.services import dict_service as cfg, wecom_import_service as reader

router = APIRouter(prefix="/source-checks", tags=["数据源核对"])
Finance = Depends(require_menu("finance_data", "admin"))
Admin = Depends(require_menu("admin"))
FINANCE = "workbench_finance_source"
RESULT = "workbench_finance_read"
ROBOT = "workbench_robot"


class FinanceSource(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=100)
    kind: Literal["journal", "receipt", "invoice", "school_income", "other"]
    url: str = Field(min_length=12, max_length=1000)
    table_id: str = Field(default="", max_length=140)
    mapping: str = Field(default="", max_length=1500)
    scheduled: bool = False


def can_manage(p):
    return p.is_admin or p.can("finance:sync")


def signature(source):
    return hashlib.sha256((source["url"] + "\n" + source.get("table_id", "")).encode()).hexdigest()


def _source(db, p, key):
    source = cfg.get(db, FINANCE, key)
    if not source:
        raise not_found("数据源")
    if source["owner_id"] != p.id or not can_manage(p):
        raise forbidden("仅资料负责人可读取或设置该资料")
    return source


@router.get("/finance")
def finance_sources(p: Principal = Finance, db: Session = Depends(get_db)):
    sources = cfg.items(db, FINANCE)
    stats = cfg.items(db, RESULT)
    ids = {x["owner_id"] for x in sources.values()}
    names = dict(db.execute(select(SysUser.id, SysUser.name).where(SysUser.id.in_(ids))).all()) if ids else {}
    return ok({"items": [
        {**{k: v for k, v in source.items() if k != "url" or source["owner_id"] == p.id},
         "key": key, "owner_name": names.get(source["owner_id"]), "own": source["owner_id"] == p.id and can_manage(p),
         "last_check": stats.get(key)}
        for key, source in sources.items() if source["owner_id"] == p.id or p.data_scope == "all"],
        "can_manage": can_manage(p)})


@router.put("/finance/{key}")
def save_source(key: str, body: FinanceSource, p: Principal = Finance, db: Session = Depends(get_db)):
    if not can_manage(p):
        raise forbidden("无财务数据源配置权限")
    before = None if key == "new" else _source(db, p, key)
    if not before:
        key = uuid4().hex
    source = body.model_dump()
    parsed = reader.parse_link(body.url)
    if not parsed.get("disk") and parsed["kind"] not in {"sheet", "smartsheet"}:
        raise bad_request("请选择收付款、开票或收入的表格链接")
    source.update(url=parsed["url"], owner_id=p.id)
    if before and signature(before) == signature(source):
        source["verified_on"] = before.get("verified_on")
    if body.scheduled and not source.get("verified_on"):
        raise conflict("请先试读并确认当前源表及子表，再启用定时读取")
    cfg.put(db, FINANCE, key, source, "财务源表检查，读后保留样例，需人工核对写入")
    log_op(db, p.id, "finance.source.save", FINANCE, key, after={
        "title": source["title"], "scheduled": source["scheduled"], "kind": source["kind"]})
    db.commit()
    return ok({"key": key})


@router.post("/finance/{key}/preview")
def start_read(key: str, tasks: BackgroundTasks, p: Principal = Finance, db: Session = Depends(get_db)):
    source = _source(db, p, key)
    record = reader.start(p, url=source["url"], table_id=source.get("table_id", ""))
    record["workbench_source"] = key
    record["workbench_signature"] = signature(source)
    reader._save(record)
    tasks.add_task(reader.prepare, p.id, record["id"])
    return ok(reader.view(record))


def _record(db, p, key, preview_id):
    source = _source(db, p, key)
    record = reader._load(p.id, preview_id)
    reader._check(record)
    if record.get("workbench_source") != key or record.get("workbench_signature") != signature(source):
        raise conflict("源表配置已经变化，请重新试读")
    return source, spreadsheet_view(record)


@router.get("/finance/{key}/preview/{preview_id}")
def read_status(key: str, preview_id: str, p: Principal = Finance, db: Session = Depends(get_db)):
    _, record = _record(db, p, key, preview_id)
    return ok(reader.view(record))


@router.post("/finance/{key}/verify/{preview_id}")
def verify_source(key: str, preview_id: str, p: Principal = Finance, db: Session = Depends(get_db)):
    source, record = _record(db, p, key, preview_id)
    if record["state"] != "ready" or not record.get("columns"):
        raise conflict("尚未读取到可核对的表格列")
    source["verified_on"] = today().isoformat()
    cfg.put(db, FINANCE, key, source)
    cfg.put(db, RESULT, key, _read_result(record))
    log_op(db, p.id, "finance.source.verify", FINANCE, key, after={"rows": record.get("row_count")})
    db.commit()
    return ok({"verified_on": source["verified_on"]})


def spreadsheet_view(record):
    """Use the owner's private snapshot to show an XLSX file's columns and sample."""
    if record.get("state") != "ready" or record.get("kind") != "file":
        return record
    if not str(record.get("filename", "")).lower().endswith(".xlsx"):
        return {**record, "state": "failed", "message": "请使用 XLSX 源文件，或改为企业微信在线表格。"}
    import io
    from zipfile import ZipFile
    from openpyxl import load_workbook
    try:
        payload = reader._path(record["user_id"], record["id"]).with_suffix(".payload").read_bytes()
        with ZipFile(io.BytesIO(payload)) as zipfile:
            if len(zipfile.infolist()) > 500 or sum(x.file_size for x in zipfile.infolist()) > 80 * 1024**2:
                raise ValueError("Workbook too large")
        book = load_workbook(io.BytesIO(payload), read_only=True, data_only=True, keep_links=False)
        try:
            names = book.sheetnames
            key = record.get("requested_table") or (names[0] if len(names) == 1 else "")
            if key not in names:
                return {**record, "state": "choose_table", "tables": [{"id": name, "name": name} for name in names]}
            sheet = book[key]
            rows = list(sheet.iter_rows(min_row=1, max_row=6, max_col=min(sheet.max_column or 25, 50), values_only=True))
            if not rows or not any(rows[0]):
                raise ValueError("Workbook has no headers")
            return {**record, "table_name": key, "columns": [str(x or "") for x in rows[0]],
                    "sample_rows": [[str(x) if x is not None else "" for x in row] for row in rows[1:]],
                    "row_count": sheet.max_row - 1 if sheet.max_row else None}
        finally:
            book.close()
    except Exception:
        return {**record, "state": "failed", "message": "文件无法解析或过大，请先将所需子表另存为有效 XLSX。"}


def _read_result(record):
    return {"checked_on": today().isoformat(), "state": record["state"], "rows": record.get("row_count"),
            "table_name": record.get("table_name"), "columns": len(record.get("columns") or [])}


class RobotRenewal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    feature: str = Field(min_length=1, max_length=100)
    owner_id: int = Field(gt=0)
    backup_id: int | None = Field(default=None, gt=0)
    expires_on: date
    advance_days: int = Field(default=1, ge=0, le=30)


@router.get("/robot")
def robot_state(p: Principal = Admin, db: Session = Depends(get_db)):
    config = cfg.get(db, ROBOT, "renewal")
    q = select(SysUser.id, SysUser.name).where(SysUser.status == 1, SysUser.is_deleted.is_(False)).order_by(SysUser.name)
    return ok({"config": config, "users": [{"id": uid, "name": name} for uid, name in db.execute(q)], "today": today()})


@router.put("/robot")
def save_robot(body: RobotRenewal, p: Principal = Admin, db: Session = Depends(get_db)):
    ids = {x for x in (body.owner_id, body.backup_id) if x}
    if len(ids) != (2 if body.backup_id else 1) or len(list(db.scalars(select(SysUser.id).where(
        SysUser.id.in_(ids), SysUser.status == 1, SysUser.is_deleted.is_(False))))) != len(ids):
        raise bad_request("请选择有效且不同的负责人及替补")
    before = cfg.get(db, ROBOT, "renewal")
    updated = body.model_dump(mode="json")
    cfg.put(db, ROBOT, "renewal", updated, "机器人功能授权责任和已核实有效期")
    log_op(db, p.id, "robot.renewal.configure", "sys_dict", "renewal", before=before, after=updated)
    db.commit()
    return ok(updated)
