"""Import the alarm workbook's site and receipt detail into existing maintenance records."""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from io import BytesIO
from math import isfinite
from re import sub

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string
from sqlalchemy import select

from app.core.audit import log_op
from app.core.response import bad_request, conflict
from app.models import OpsImportBatch, OpsMaintenanceRecord
from app.services import operations_scope as scope, operations_service as ops

IMPORT_KIND = "alarm_maintenance"
DEVICE = "device_maintenance"
ARREARS = "arrears"
RECEIPT = "collection_maintenance"
CENT = Decimal("0.01")

SITE_COLUMNS = {
    "A": ("group", "(返回顶部)"),
    "B": ("sequence", "序"),
    "C": ("area", "区域"),
    "D": ("school_name", "学校名称(全市全量)"),
    "E": ("billing_count", "计费个数"),
    "F": ("central_school", "学校名称(中心校)(隐藏"),
}
DEVICE_COLUMNS = {
    "G": ("alternative_name", "其他名称"),
    "H": ("closure_status", "撤校情况"),
    "I": ("installation_status", "结果判定"),
    "J": ("removal_date", "拆机日期"),
    "K": ("camera_ip", "摄像头IP"),
    "L": ("recorder_ip", "录像机IP"),
    "M": ("alarm_box_ip", "报警盒IP"),
    "N": ("equipment_status", "配套"),
    "O": ("camera_status", "摄像头状态"),
    "P": ("recorder_status", "录像机状态"),
    "Q": ("alarm_box_status", "报警盒状态"),
    "R": ("offline_count", "离线数量"),
}
ARREARS_COLUMNS = {
    "AD": ("billing_start", "计费月份(移动合同次月)"),
    "AE": ("writeoff_month", "销账月份"),
    "AF": ("monthly_due", "每月应交额"),
    "AG": ("due_months", "应缴月份"),
    "AH": ("writeoff_amount", "销账金额"),
    "AI": ("waived_amount", "减免金额"),
    "AJ": ("cumulative_amount", "累计金额"),
    "AK": ("paid_months", "已缴月份"),
    "AL": ("arrears_months", "应补缴月份"),
    "AM": ("unwritten_paid", "补缴未销账"),
    "BA": ("amount", "应补缴金额"),
}
RECEIPT_COLUMNS = {
    "G": ("receipt_date", "日期"),
    "H": ("receipt_month", "月份"),
    "I": ("counterparty", "对方"),
    "J": ("summary", "摘要"),
    "K": ("amount", "借方"),
    "L": ("carrier", "所属"),
}


def _rows(sheet, header_row, columns):
    indexes = {letter: column_index_from_string(letter) - 1 for letter in columns}
    rows = enumerate(sheet.iter_rows(min_row=header_row, max_col=max(indexes.values()) + 1,
                                    values_only=True), header_row)
    _, header = next(rows, (None, ()))
    for letter, (_, label) in columns.items():
        if indexes[letter] >= len(header):
            raise bad_request(f"「{sheet.title}」缺少第 {header_row} 行表头")
        actual = sub(r"\s+", "", str(header[indexes[letter]])).replace("（", "(").replace("）", ")")
        if actual != label:
            raise bad_request(f"「{sheet.title}」{letter}{header_row} 应为「{label}」")
    for row_number, cells in rows:
        values = {key: cells[indexes[letter]] for letter, (key, _) in columns.items()}
        if any(value is not None for value in values.values()):
            yield row_number, values


def _date(value, location, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise bad_request(f"{location}的日期无效")


def _amount(value, location):
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise bad_request(f"{location}的金额无效")
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _text(value, location):
    if not isinstance(value, str) or not value.strip():
        raise bad_request(f"{location}缺少必填文本")
    return value.strip()


def _record(kind, sheet, row_number, values, name, *, title=None, amount=None):
    return OpsMaintenanceRecord(
        kind=kind, title=title or name, customer_name=name, amount=amount,
        status="done" if kind == RECEIPT else "open",
        workbook_row={"sheet": sheet, "row_number": row_number, "values": values},
    )


def _read_records(payload):
    workbook = load_workbook(BytesIO(payload), read_only=True, data_only=True, keep_links=False)
    try:
        if not {"全量", "年度取数"}.issubset(workbook.sheetnames):
            raise bad_request("工作簿须包含「全量」和「年度取数」")
        records = {kind: [] for kind in (DEVICE, ARREARS, RECEIPT)}
        sequences = set()
        columns = SITE_COLUMNS | DEVICE_COLUMNS | ARREARS_COLUMNS
        for number, values in _rows(workbook["全量"], 1, columns):
            location = f"「全量」第 {number} 行"
            name = _text(values["school_name"], location)
            sequence = values["sequence"]
            if type(sequence) is not int or sequence < 1 or sequence in sequences:
                raise bad_request(f"{location}的序号无效或重复")
            sequences.add(sequence)
            values["removal_date"] = _date(values["removal_date"], location, optional=True)
            values["billing_start"] = _date(values["billing_start"], location, optional=True)
            values["writeoff_month"] = _date(values["writeoff_month"], location)[:7]
            amount = _amount(values["amount"], location)
            values["amount"] = str(amount)
            for key in ("monthly_due", "writeoff_amount", "waived_amount", "cumulative_amount", "unwritten_paid"):
                value = values[key]
                if value is not None and (key, value) != ("cumulative_amount", "#VALUE!"):
                    values[key] = str(_amount(value, location))
            site = {key: values[key] for key, _ in SITE_COLUMNS.values()}
            device = site | {key: values[key] for key, _ in DEVICE_COLUMNS.values()}
            arrears = site | {key: values[key] for key, _ in ARREARS_COLUMNS.values()}
            records[DEVICE].append(_record(DEVICE, "全量", number, device, name))
            records[ARREARS].append(_record(ARREARS, "全量", number, arrears, name, amount=amount))

        annual = workbook["年度取数"]
        for number, values in _rows(annual, 2, RECEIPT_COLUMNS):
            location = f"「年度取数」第 {number} 行"
            name = _text(values["counterparty"], location)
            paid_on = _date(values["receipt_date"], location)
            if values["receipt_month"] != f"{int(paid_on[5:7])}月":
                raise bad_request(f"{location}的月份与到账日期不一致")
            amount = _amount(values["amount"], location)
            values.update(receipt_date=paid_on, receipt_month=paid_on[:7], amount=str(amount))
            records[RECEIPT].append(_record(RECEIPT, "年度取数", number, values, name,
                                            title=values["summary"] or f"{paid_on} 到账", amount=amount))

        if not records[DEVICE] or not records[RECEIPT]:
            raise bad_request("工作簿缺少点位或回款明细")
        if sum(row.amount for row in records[RECEIPT]) != _amount(annual["H1"].value, "「年度取数」H1"):
            raise bad_request("回款明细合计与「年度取数」本年实际到账不一致")
        return records
    finally:
        workbook.close()


def import_workbook(db, p, project_id, source_name, payload):
    """Caller commits once after all three tabs and the import audit pass validation."""
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    ops.active(project)
    if project.name.strip() != "一键报警":
        raise bad_request("仅可导入到「一键报警」运营项目")
    for kind in (DEVICE, ARREARS, RECEIPT):
        scope.require(db, p, project, kind)
    ops.user(db, project.owner_id)

    digest = sha256(payload).hexdigest()
    previous = db.scalar(select(OpsImportBatch).where(
        OpsImportBatch.project_id == project_id, OpsImportBatch.kind == IMPORT_KIND,
    ))
    if previous:
        if previous.source_sha256 != digest:
            raise conflict("项目已有一键报警台账，请核对原导入批次")
        return {"already_imported": True, "batch_id": previous.id, **previous.result}

    records = _read_records(payload)
    result = {
        "rows": {kind: len(items) for kind, items in records.items()},
        "arrears_due": str(sum(row.amount for row in records[ARREARS])),
        "receipts": str(sum(row.amount for row in records[RECEIPT])),
    }
    for items in records.values():
        for row in items:
            row.project_id = project_id
            row.owner_id = project.owner_id
        db.add_all(items)
    batch = OpsImportBatch(project_id=project_id, kind=IMPORT_KIND, source_name=source_name,
                           source_sha256=digest, operator_id=p.id, result=result)
    db.add(batch)
    db.flush()
    log_op(db, p.id, "operations.alarm_maintenance.import", "ops_import_batch", batch.id,
           after={"project_id": project_id, "source_name": source_name, **result})
    return {"already_imported": False, "batch_id": batch.id, **result}
