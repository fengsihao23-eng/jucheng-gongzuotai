"""固定企微子表与历史 CSV 使用同一个可核对的暂存队列。"""
import csv
import hashlib
import io
import json
import re
import shutil
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.core.deps import build_principal
from app.core.response import BizError, bad_request, conflict, forbidden
from app.models import FinReceivablesSourceRow, SysUser
from app.services import (receivables_fields as fields, receivables_service as ledger,
                          wecom_cli_service as cli, wecom_import_formats as formats)

MODELS = {"invoice": fields.InvoiceRecord, "receipt": fields.Receipt}
TEMPLATE = {
    "invoice": ["invoice_no", "issued_on", "amount", "customer", "contract_no", "approval_no", "note"],
    "receipt": ["reference", "received_on", "amount", "payer", "account", "kind", "contract_no", "payment_id", "note"],
}
KEY = {"invoice": "invoice_no", "receipt": "reference"}
LIMIT = 1000


def _version(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def stage(db, source, kind, records, *, snapshot=False):
    ledger.lock(db)
    old = {r.record_key: r for r in db.scalars(select(FinReceivablesSourceRow).where(
        FinReceivablesSourceRow.source_key == source).execution_options(include_removed_records=True))}
    ids = set()
    changes = 0
    for key, payload in records:
        if not isinstance(key, str) or not 1 <= len(key) <= 120 or key in ids:
            raise bad_request("源行标识为空、过长或重复，未更新暂存队列")
        ids.add(key)
        row = old.get(key)
        if row and row.kind != kind:
            raise conflict("已使用的数据源不能切换类型，请新建数据源配置")
        try:
            value = fields.parse(MODELS[kind], payload).model_dump(mode="json")
            value[KEY[kind]] = value[KEY[kind]].upper()
            value["amount"] = str(Decimal(value["amount"]).quantize(Decimal("0.01")))
            error = None
        except BizError as exc:
            value = payload
            error = str(exc)
        version = _version(value)
        if row and row.version == version and row.state != "missing":
            continue
        if not row:
            row = FinReceivablesSourceRow(source_key=source, record_key=key, kind=kind, payload=value, version=version)
            db.add(row)
        row.payload, row.version, row.error = value, version, error
        if row.applied_version:
            row.state = "applied" if version == row.applied_version else "changed"
            if row.state == "changed":
                row.error = "已入账的源记录有变化，请核对并在正式台账办理更正。"
        else:
            row.state = "error" if error else "pending"
        changes += 1
    if snapshot:
        for key in old.keys() - ids:
            if old[key].state != "missing":
                old[key].state = "missing"
                old[key].error = "源表中未找到此行，正式台账未变更。"
                changes += 1
    db.flush()
    return changes


def stage_csv(db, kind, content):
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        headers = reader.fieldnames or []
        if not headers or len(set(headers)) != len(headers) or set(headers) - set(TEMPLATE[kind]) or KEY[kind] not in headers:
            raise bad_request("CSV 列名与模板不匹配，请使用本页下载的模板")
        records = []
        for i, row in enumerate(reader, 2):
            if i > LIMIT + 1 or None in row or any(v is None or len(v) > 2000 for v in row.values()):
                raise bad_request(f"第 {i} 行列数、长度或行数超出限制（每批最多 {LIMIT} 行）")
            key = row[KEY[kind]].strip().upper()
            if not key:
                raise bad_request(f"第 {i} 行缺少发票号码或到账凭证号")
            records.append((key, row))
    except (UnicodeError, csv.Error):
        raise bad_request("请上传 UTF-8 编码、格式正确的 CSV") from None
    if not records:
        raise bad_request("CSV 中没有业务记录")
    return stage(db, f"csv:{kind}", kind, records)


def apply_row(db, row, version, actor):
    if row.version != version or row.state not in {"pending", "error", "applied"}:
        raise conflict("源记录状态或版本已变化，请刷新后重新核对")
    if row.state == "applied":
        return row
    try:
        with db.begin_nested():
            value = fields.parse(MODELS[row.kind], row.payload)
            handler = ledger.record_invoice if row.kind == "invoice" else ledger.receipt
            target = handler(db, **value.model_dump(), actor=actor)
            row.target_id, row.applied_version, row.state, row.error = target.id, version, "applied", None
            db.flush()
    except BizError as exc:
        row.state, row.error = "error", str(exc)
        if isinstance(exc.data, dict) and exc.data.get("payment_ids"):
            row.error += "：" + ", ".join(map(str, exc.data["payment_ids"]))
    return row


def fetch_source(db, config):
    kind = config["kind"]
    user = db.get(SysUser, config["reader_id"])
    principal = build_principal(user) if user and not user.is_deleted and user.status == 1 else None
    if not principal or principal.data_scope != "all" or not principal.can("receivables:source"):
        raise forbidden("同步账号不可用或未授予财务资料接入权限")
    user_id = user.id
    binding_id = cli._owned(user_id)["binding_id"]
    docid, sheet_id = config["docid"], config["sheet_id"]
    if not all(isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", i) for i in (docid, sheet_id)):
        raise bad_request("固定来源的文档或子表标识无效")
    mapping = config["fields"]
    required = {key for key, field in MODELS[kind].model_fields.items() if field.is_required()}
    if not required <= mapping.keys() or not mapping.keys() <= MODELS[kind].model_fields.keys():
        raise bad_request("固定来源配置未完整映射必填字段，或包含未知字段")
    request_id = uuid.uuid4().hex

    def fetch(method, params):
        result = cli.import_read(user_id, method, params, binding_id, request_id)
        if result.get("file_path"):
            return json.loads(formats.read_output(user_id, request_id, result["file_path"]))
        return result

    try:
        if config.get("format", "smartsheet") == "sheet":
            from openpyxl import load_workbook

            meta = {"docid": docid, "title": "财务来源"}
            content, _ = formats.online_table(fetch, meta, sheet_id)
            if meta["row_count"] > LIMIT:
                raise bad_request(f"固定来源最多 {LIMIT} 行，请拆分子表")
            headers = meta["columns"]
            if not all(headers.count(title) == 1 and headers.index(title) not in meta["complex_columns"]
                       for title in mapping.values()):
                raise bad_request("固定来源字段缺失、重名或包含公式，请使用普通数据列")
            indices = {key: headers.index(title) for key, title in mapping.items()}
            book = load_workbook(io.BytesIO(content), read_only=True)
            try:
                rows = []
                for row in book.active.iter_rows(min_row=2, values_only=True):
                    payload = {key: row[index] for key, index in indices.items()}
                    if all(value in (None, "") for value in payload.values()):
                        continue
                    ref = payload.get(KEY[kind])
                    if not isinstance(ref, str) or not ref.strip():
                        raise bad_request("在线表格的发票号码或到账凭证号必须为非空文本")
                    rows.append((ref.strip().upper(), payload))
                return rows
            finally:
                book.close()
        if config.get("format", "smartsheet") != "smartsheet":
            raise bad_request("固定来源仅支持智能表格或在线表格")
        params = {"docid": docid, "sheet_id": sheet_id}
        source_fields = formats.paginate(fetch, "smartsheet.fields.list", params, "fields", maximum=200)
        titles = [f.get("field_title") or f.get("name") for f in source_fields]
        if not all(isinstance(title, str) and titles.count(title) == 1 for title in mapping.values()):
            raise bad_request("固定来源的字段缺失或重名，未更改台账")
        records = formats.paginate(fetch, "smartsheet.records.list", {**params, "key_type": "field_title"}, "records",
                                   maximum=LIMIT, page_size=min(100, 9000 // len(titles)))
        return [(row.get("record_id"), {key: row.get("values", row.get("fields", {})).get(title)
                                       for key, title in mapping.items()}) for row in records]
    finally:
        temp = cli.home(user_id) / "work" / "imports" / request_id
        if not temp.is_symlink():
            shutil.rmtree(temp, ignore_errors=True)
