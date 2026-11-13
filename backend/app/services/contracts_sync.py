"""合同台账固定来源同步：企业微信在线表格 → 工作台合同台账。

与 receivables_sync 同一套思路，但合同台账只有一张主档表，不做暂存队列，
直接「读表 → 逐行校验 → 与台账比对 → 生成差异 → 确认后落库」：

- 唯一键是合同编号（contract_no）；编号为空、重复、格式异常的行整行挂起
- 只增、只改，不删；源表删掉的行只标记「源表已移除」，台账记录保留
- 金额、签订日期、业务线、区域、销售负责人逐字段校验，任一项对不上整行挂起
- 不碰回款记录、付款计划、归档原件，只同步合同主档字段
- 每行写入都走 contract_service，保留 log_operation 前后值

配置存在 sys_dict（dict_type=contract_source），键为来源名，值为：

    {"enabled": true, "format": "sheet", "docid": "...", "sheet_id": "...",
     "reader_id": 12, "auto_apply": false,
     "fields": {"contract_no": "合同编号", "customer_name": "客户名称", ...}}
"""
from __future__ import annotations

import io
import json
import re
import shutil
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import build_principal
from app.core.response import BizError, bad_request, forbidden
from app.core.timeutil import today
from app.models import BizContract, SysUser
from app.services import (contract_service as cs, wecom_cli_service as cli,
                          wecom_import_formats as formats)

DICT_TYPE = "contract_source"
SOURCE_TYPE_WECOM = 4
REQUIRED = ("contract_no", "customer_name", "amount", "sign_date", "business_line")
SYNC_FIELDS = ("name", "customer_name", "amount", "sign_date", "business_line", "region", "owner_id")
LINES_BY_LABEL = {v: k for k, v in cs.LINES.items()}
REGIONS = {"市直", "江城", "阳春", "阳东", "阳西", "海陵湾", "待定"}
LIMIT = 2000

_DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),
)


# ------------------------------------------------------------------ 取数
def fetch_rows(db: Session, config: dict) -> list[dict]:
    """按配置读取在线表格，返回 [{列名: 值}]。凭证来自 reader 本人的企业微信绑定。"""
    user = db.get(SysUser, config["reader_id"]) if isinstance(config.get("reader_id"), int) else None
    principal = build_principal(user) if user and not user.is_deleted and user.status == 1 else None
    if not principal or principal.data_scope != "all" or not principal.has_menu("contracts") or not principal.can("contracts:write"):
        raise forbidden("同步账号不可用，或未授予合同台账的数据范围")

    docid, sheet_id = config.get("docid"), config.get("sheet_id")
    if not all(isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", i) for i in (docid, sheet_id)):
        raise bad_request("固定来源的文档或子表标识无效")
    if config.get("format", "sheet") != "sheet":
        raise bad_request("合同台账固定来源目前只支持企业微信在线表格")

    mapping = config.get("fields") or {}
    missing = [f for f in REQUIRED if not isinstance(mapping.get(f), str) or not mapping[f].strip()]
    if missing:
        raise bad_request("固定来源未映射必填字段：" + "、".join(missing))
    unknown = set(mapping) - set(REQUIRED) - set(SYNC_FIELDS)
    if unknown:
        raise bad_request("固定来源包含未知字段：" + "、".join(sorted(unknown)))

    binding_id = cli._owned(user.id)["binding_id"]
    request_id = uuid.uuid4().hex

    def fetch(method, params):
        result = cli.import_read(user.id, method, params, binding_id, request_id)
        if result.get("file_path"):
            return json.loads(formats.read_output(user.id, request_id, result["file_path"]))
        return result

    try:
        meta = {"docid": docid, "title": "合同台账来源"}
        content, _ = formats.online_table(fetch, meta, sheet_id)
        if meta["row_count"] > LIMIT:
            raise bad_request(f"固定来源最多 {LIMIT} 行，请拆分工作表")
        headers = meta["columns"]
        for field in mapping.values():
            if headers.count(field) != 1 or headers.index(field) in meta["complex_columns"]:
                raise bad_request(f"源表列「{field}」缺失、重名或含公式，未更改台账")
        indices = {f: headers.index(title) for f, title in mapping.items() if isinstance(title, str) and title}
        book = load_workbook(io.BytesIO(content), read_only=True)
        try:
            rows = []
            for row in book.active.iter_rows(min_row=2, values_only=True):
                payload = {f: row[i] if i < len(row) else None for f, i in indices.items()}
                if all(v in (None, "") for v in payload.values()):
                    continue
                rows.append(payload)
            return rows
        finally:
            book.close()
    finally:
        temp = cli.home(user.id) / "work" / "imports" / request_id
        if not temp.is_symlink():
            shutil.rmtree(temp, ignore_errors=True)


# ------------------------------------------------------------------ 校验
def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_amount(value) -> Decimal:
    if isinstance(value, (int, float, Decimal)):
        raw = str(value)
    else:
        raw = re.sub(r"[¥￥,\s元]", "", str(value or ""))
    try:
        amount = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise bad_request("合同金额无法识别") from None
    if amount <= 0:
        raise bad_request("合同金额须大于 0")
    return amount.quantize(Decimal("0.01"))


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in _DATE_PATTERNS:
        matched = pattern.match(text)
        if matched:
            try:
                return date(*map(int, matched.groups()))
            except ValueError:
                break
    raise bad_request("签订日期无法识别")


def _cached_today() -> date:
    try:
        return today()
    except Exception:  # 无应用上下文时退回系统日期
        return date.today()


def normalize(db: Session, payload: dict, owners: dict[str, list[int]]) -> tuple[dict, list[str]]:
    """把一行源数据转成台账字段，返回 (值, 错误列表)；有错误时不落库。"""
    errors: list[str] = []
    values: dict = {}

    no = _text(payload.get("contract_no"))
    if not no:
        errors.append("合同编号为空")
    elif len(no) > 32:
        errors.append("合同编号超过 32 位")
    else:
        values["contract_no"] = no.upper()

    customer = _text(payload.get("customer_name"))
    if not customer:
        errors.append("客户名称为空")
    else:
        values["customer_name"] = customer

    name = _text(payload.get("name"))
    if name:
        values["name"] = name[:128]

    try:
        values["amount"] = parse_amount(payload.get("amount"))
    except BizError as exc:
        errors.append(exc.message)

    try:
        sign = parse_date(payload.get("sign_date"))
        if sign < date(2000, 1, 1) or sign > _cached_today() + timedelta(days=365):
            errors.append("签订日期超出合理范围")
        else:
            values["sign_date"] = sign
    except BizError as exc:
        errors.append(exc.message)

    line_raw = _text(payload.get("business_line"))
    if not line_raw:
        errors.append("业务线为空")
    else:
        code = LINES_BY_LABEL.get(line_raw, line_raw.upper() if line_raw.upper() in cs.LINES else None)
        if code:
            values["business_line"] = code
        else:
            errors.append(f"业务线「{line_raw}」不在四条业务线内")

    region = _text(payload.get("region"))
    if region:
        if region in REGIONS:
            values["region"] = region
        else:
            errors.append(f"区域「{region}」不在 {('/'.join(sorted(REGIONS)))} 内")
    else:
        # 源表没有区域时按客户名/合同名推断，推不出归待定，不让区域空着导致业绩统计断档
        from app.services import region_rules

        values["region"] = region_rules.classify(values.get("customer_name"), values.get("name"))[0]

    if "owner_id" in payload:
        owner_name = _text(payload.get("owner_id"))
        if owner_name:
            candidates = owners.get(owner_name, [])
            if len(candidates) == 1:
                values["owner_id"] = candidates[0]
            elif not candidates:
                errors.append(f"销售负责人「{owner_name}」在系统里查不到")
            else:
                errors.append(f"销售负责人「{owner_name}」有 {len(candidates)} 个同名账号")
        else:
            errors.append("销售负责人为空")
    return values, errors


def _owner_index(db: Session) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for user in db.scalars(select(SysUser).where(SysUser.status == 1, SysUser.is_deleted.is_(False))):
        index.setdefault(user.name.strip(), []).append(user.id)
    return index


# ------------------------------------------------------------------ 比对
def _comparable(field: str, value):
    if field == "amount":
        return Decimal(value).quantize(Decimal("0.01"))
    if field == "sign_date":
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return value


def _json_safe(field: str, value):
    if value is None:
        return None
    if isinstance(value, (Decimal, date)):
        return str(value)
    return value


def preview(db: Session, config: dict, key: str | None = None) -> dict:
    """读取来源并生成差异，不写库。"""
    rows = fetch_rows(db, config)
    owners = _owner_index(db)
    seen: dict[str, int] = {}
    items: list[dict] = []
    counts = {"create": 0, "update": 0, "unchanged": 0, "conflict": 0}

    numbers = [v for v in (_text(r.get("contract_no")) for r in rows) if v]
    existing = {c.contract_no: c for c in db.scalars(
        select(BizContract).where(BizContract.contract_no.in_([n.upper() for n in numbers]),
                                  BizContract.is_deleted.is_(False)))} if numbers else {}

    for row_no, payload in enumerate(rows, start=2):
        values, errors = normalize(db, payload, owners)
        contract_no = values.get("contract_no")
        if contract_no:
            if contract_no in seen:
                errors.append(f"合同编号与第 {seen[contract_no]} 行重复")
            else:
                seen[contract_no] = row_no

        item = {"row": row_no, "contract_no": contract_no or _text(payload.get("contract_no")),
                "customer_name": values.get("customer_name"), "amount": _json_safe("amount", values.get("amount")),
                "sign_date": _json_safe("sign_date", values.get("sign_date")), "changes": {}, "errors": errors}

        if errors:
            item["action"] = "conflict"
        elif contract_no not in existing:
            item["action"] = "create"
            item["changes"] = {f: {"from": None, "to": _json_safe(f, v)} for f, v in values.items() if f != "contract_no"}
        else:
            contract = existing[contract_no]
            for field in SYNC_FIELDS:
                if field not in values:
                    continue
                old, new = _comparable(field, getattr(contract, field)), _comparable(field, values[field])
                if old != new:
                    item["changes"][field] = {"from": _json_safe(field, old), "to": _json_safe(field, new)}
            item["action"] = "update" if item["changes"] else "unchanged"

        counts[item["action"]] += 1
        items.append(item)

    removed = []
    if key:
        synced = db.scalars(select(BizContract).where(
            BizContract.source_type == SOURCE_TYPE_WECOM,
            BizContract.source_approval_no == f"wecom:{key}",
            BizContract.is_deleted.is_(False))).all()
        removed = [c for c in synced if c.contract_no not in seen]
    return {"source": {"key": key, "docid": config.get("docid"), "sheet_id": config.get("sheet_id"),
                       "auto_apply": config.get("auto_apply") is True},
            "summary": {"rows": len(rows), **counts, "removed": len(removed)},
            "removed": [{"contract_no": c.contract_no, "customer_name": c.customer_name} for c in removed],
            "items": items}


# ------------------------------------------------------------------ 落库
def apply(db: Session, p, config: dict, preview_data: dict | None = None,
          only: list[str] | None = None, ip: str | None = None, key: str | None = None) -> dict:
    """把差异里 create/update 的部分写入台账；冲突行跳过，等人工处理。"""
    data = preview_data or preview(db, config, key)
    wanted = {n.upper() for n in only} if only else None
    created = updated = skipped = 0
    failed: list[dict] = []

    for item in data["items"]:
        no = (item.get("contract_no") or "").upper()
        if wanted is not None and no not in wanted:
            continue
        if item["action"] == "conflict":
            skipped += 1
            failed.append({"contract_no": item.get("contract_no"), "errors": item["errors"]})
            continue
        if item["action"] == "unchanged":
            continue
        values = {f: v["to"] for f, v in item["changes"].items()}
        values["contract_no"] = no
        try:
            if item["action"] == "create":
                created_contract = cs.create(db, p, values, ip, source_type=SOURCE_TYPE_WECOM)
                if key:
                    created_contract.source_approval_no = f"wecom:{key}"
                    db.commit()
                created += 1
            else:
                contract = db.scalar(select(BizContract).where(BizContract.contract_no == no))
                if not contract:
                    raise bad_request("台账里已找不到该合同，请重新预览")
                cs.update(db, p, contract, values, ip)
                updated += 1
        except BizError as exc:
            db.rollback()
            failed.append({"contract_no": no, "errors": [exc.message]})

    return {"created": created, "updated": updated, "skipped": skipped, "failed": failed,
            "summary": data["summary"]}


def run(db: Session, key: str, config: dict, actor: SysUser | None = None) -> dict:
    """定时任务入口：预览，配置允许时才自动落库。"""
    data = preview(db, config, key)
    result = {"source": key, "summary": data["summary"], "applied": None}
    if config.get("auto_apply") is True and (data["summary"]["create"] or data["summary"]["update"]):
        user = actor or (db.get(SysUser, config["reader_id"]) if isinstance(config.get("reader_id"), int) else None)
        if not user:
            raise bad_request("自动落库需要可用的同步账号")
        principal = build_principal(user)
        result["applied"] = apply(db, principal, config, data, ip="scheduler", key=key)
    return result
