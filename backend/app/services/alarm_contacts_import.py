"""Import school contacts into the alarm project's customer maintenance ledger."""
from hashlib import sha256
from io import BytesIO
from re import fullmatch
from unicodedata import normalize
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import select

from app.core.audit import log_op
from app.core.response import bad_request, conflict
from app.models import OpsImportBatch, OpsMaintenanceRecord
from app.services import operations_scope as scope, operations_service as ops

IMPORT_KIND = "alarm_contacts"
CUSTOMERS = "customers"
HEADERS = ("姓名*", "所属单位*", "手机号", "性别", "地址", "职务")


def _text(value):
    return str(value).strip() if value is not None else ""


def _phone(value):
    return "".join(normalize("NFKC", _text(value)).split())


def _contact_key(unit, name, phone):
    return normalize("NFKC", _text(unit)), normalize("NFKC", _text(name)), _phone(phone)


def _read_contacts(payload):
    try:
        workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True, keep_links=False)
    except (BadZipFile, InvalidFileException, OSError) as exc:
        raise bad_request("无法读取学校联系表，请检查 Excel 文件") from exc
    try:
        sheet = workbook.active
        if sheet is None:
            raise bad_request("学校联系表没有工作表")
        rows = iter(sheet.iter_rows(max_col=len(HEADERS), values_only=True))
        if tuple(map(_text, next(rows, ()))) != HEADERS:
            raise bad_request("学校联系表须包含姓名*、所属单位*、手机号、性别、地址、职务六列")
        contacts = []
        for number, row in enumerate(rows, 2):
            name, unit, phone, gender, address, position = map(_text, row)
            if not any((name, unit, phone, gender, address, position)):
                continue
            phone = _phone(phone)
            if not name or len(name) > 64 or not unit or len(unit) > 128:
                raise bad_request(f"学校联系表第 {number} 行姓名或单位无效")
            if not fullmatch(r"1[3-9][0-9]{9}", phone):
                raise bad_request(f"学校联系表第 {number} 行手机号无效")
            if len(address) > 500 or len(position) > 128 or gender not in ("", "男", "女"):
                raise bad_request(f"学校联系表第 {number} 行地址、职务或性别不符合格式")
            contacts.append((number, {"customer_name": unit, "contact_name": name, "phone": phone,
                                      "address": address, "contact_gender": gender, "contact_position": position}))
        if not contacts:
            raise bad_request("学校联系表没有联系人数据")
        return sheet.title, contacts
    finally:
        workbook.close()


def import_workbook(db, p, project_id, source_name, payload):
    project = ops.get_project(db, p, project_id, lock=True)
    if project.name.strip() != "一键报警":
        raise bad_request("学校联系表应导入「一键报警」运营项目")
    ops.manage(db, p, project)
    ops.active(project)
    scope.require(db, p, project, CUSTOMERS)
    ops.assignee(db, project, project.owner_id)
    fingerprint = sha256(payload).hexdigest()
    previous = db.scalar(select(OpsImportBatch).where(
        OpsImportBatch.project_id == project.id, OpsImportBatch.source_sha256 == fingerprint,
    ).execution_options(include_removed_records=True))
    if previous:
        if previous.kind != IMPORT_KIND:
            raise conflict("该文件已用于本项目的其他导入")
        return {"already_imported": True, "batch_id": previous.id,
                **{key: value for key, value in previous.result.items() if key != "created_records"}}

    sheet, contacts = _read_contacts(payload)
    existing = set()
    current = select(OpsMaintenanceRecord).where(
        OpsMaintenanceRecord.project_id == project.id, OpsMaintenanceRecord.kind == CUSTOMERS,
    ).execution_options(include_removed_records=True)
    for record in db.scalars(current):
        existing.add(_contact_key(record.customer_name, record.contact_name, record.phone))

    records = []
    for number, values in contacts:
        key = _contact_key(values["customer_name"], values["contact_name"], values["phone"])
        if key in existing:
            continue
        existing.add(key)
        records.append((number, OpsMaintenanceRecord(project_id=project.id, kind=CUSTOMERS,
            title=values["customer_name"], owner_id=project.owner_id, **values)))

    result = {"sheet": sheet, "rows": len(contacts), "organizations": len({values["customer_name"] for _, values in contacts}),
              "created": len(records), "duplicates": len(contacts) - len(records)}
    db.add_all(row for _, row in records)
    db.flush()
    batch = OpsImportBatch(project_id=project.id, kind=IMPORT_KIND, source_name=source_name,
                           source_sha256=fingerprint, operator_id=p.id,
                           result={**result, "created_records": {str(number): row.id for number, row in records}})
    db.add(batch)
    db.flush()
    log_op(db, p.id, "operations.alarm_contacts.import", "ops_import_batch", batch.id,
           after={"project_id": project.id, "source_name": source_name, **result})
    return {"already_imported": False, "batch_id": batch.id, **result}
