"""Preview a reviewed school-day income file without creating or replacing records."""
import csv
import hashlib
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select

from app.core.response import bad_request, conflict
from app.core.timeutil import today
from app.models import OpsImportBatch, OpsIncome, OpsProjectIncome, OpsSchool
from app.services import operations_service as ops
from app.services.operations_import import IncomeRow, Snapshot, apply_snapshot

MAX_BYTES = 3 * 1024 * 1024
MAX_ROWS = 1500
HEADER = ("学校", "实际收款日期", "收入金额", "备注")


def read_rows(raw, filename):
    try:
        if filename.lower().endswith(".csv"):
            rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        elif filename.lower().endswith(".xlsx"):
            from zipfile import ZipFile
            from openpyxl import load_workbook
            with ZipFile(io.BytesIO(raw)) as zipped:
                if len(zipped.infolist()) > 100 or sum(i.file_size for i in zipped.infolist()) > 20 * MAX_BYTES:
                    raise ValueError("workbook exceeds limits")
            book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
            try:
                if len(book.sheetnames) != 1 or book.active.max_row and book.active.max_row > MAX_ROWS + 1:
                    raise ValueError("expected a single worksheet")
                rows = list(book.active.iter_rows(min_row=1, max_row=MAX_ROWS + 2, max_col=5, values_only=True))
            finally:
                book.close()
        else:
            raise bad_request("仅支持 UTF-8 CSV 或单页 XLSX")
    except (OSError, ValueError, UnicodeError, csv.Error) as exc:
        raise bad_request("无法读取文件，请核对编码、单个工作表和行数") from exc
    except Exception as exc:  # malformed ZIP/XML from XLSX
        from app.core.response import BizError
        if isinstance(exc, BizError):
            raise
        raise bad_request("工作簿损坏，无法预览") from exc
    rows = [tuple(row) for row in rows if any(x is not None and str(x).strip() for x in row)]
    if not rows or tuple(str(v or "").strip() for v in rows[0][:4]) != HEADER or len(rows) > MAX_ROWS + 1 or len(rows) < 2:
        raise bad_request("表头须为「学校,实际收款日期,收入金额,备注」，数据行数须为 1 至 1500")
    if any(any(v is not None and str(v).strip() for v in row[4:]) for row in rows):
        raise bad_request("文件只能包含四个指定列")
    return rows[1:]


def prepare(db, p, project_id, raw, filename):
    project = ops.get_project(db, p, project_id)
    ops.manage(db, p, project)
    ops.content_scope.require(db, p, project, "income")
    if not raw or len(raw) > MAX_BYTES:
        raise bad_request("请选择小于 3MB 的收入明细文件")
    sha = hashlib.sha256(raw).hexdigest()
    schools = dict(db.execute(select(OpsSchool.name, OpsSchool.id).where(OpsSchool.project_id == project_id)).all())
    existing = {(r.school_id, r.income_date): r.amount for r in db.scalars(select(OpsIncome).where(OpsIncome.school_id.in_(schools.values())))}
    existing.update({(None, r.income_date): r.amount for r in db.scalars(
        select(OpsProjectIncome).where(OpsProjectIncome.project_id == project_id))})
    filename = Path(filename.replace("\\", "/")).name[:125]
    source = "文件：" + filename
    errors, normalized, new_rows, keys, unchanged = [], [], [], set(), 0
    for i, raw_row in enumerate(read_rows(raw, filename), 2):
        cells = [*raw_row[:4], "", "", "", ""]
        school, received, amount, note = cells[:4]
        school = str(school or "").strip()
        uid = schools.get(school)
        if school and uid is None:
            errors.append({"line": i, "message": "学校未建档或名称不一致"}); continue
        try:
            received = received.date() if isinstance(received, datetime) else date.fromisoformat(str(received))
            value = IncomeRow(school=school or None, income_date=received, amount=Decimal(str(amount)), source=source, note=str(note or ""))
        except (ValidationError, ValueError, InvalidOperation):
            errors.append({"line": i, "message": "日期或金额无效，金额须为非负且最多两位小数"}); continue
        if received > today():
            errors.append({"line": i, "message": "实际收款日期晚于今天"}); continue
        key = (uid, received)
        if key in keys:
            errors.append({"line": i, "message": "同一学校同一天出现多次，请在源表合计"}); continue
        keys.add(key)
        normalized.append(value)
        if key in existing:
            if existing[key] != value.amount:
                errors.append({"line": i, "message": "同一天已登记不同金额，请在原记录对账更正"})
            else:
                unchanged += 1
        else:
            new_rows.append(value)
    amount = sum((x.amount for x in normalized), Decimal("0"))
    new_amount = sum((x.amount for x in new_rows), Decimal("0"))
    report = {"sha256": sha, "rows": len(normalized), "amount": amount, "new_rows": len(new_rows), "new_amount": new_amount,
              "unchanged": unchanged, "errors": errors[:30], "error_count": len(errors), "sample": normalized[:8]}
    batch = db.scalar(select(OpsImportBatch.id).where(OpsImportBatch.project_id == project_id, OpsImportBatch.source_sha256 == sha))
    return report, new_rows, batch


def confirm(db, p, project_id, raw, filename, expected_sha, expected_rows, expected_amount):
    # Hold the same lock used by manual income writes and by reviewed imports.
    project = ops.get_project(db, p, project_id, lock=True)
    ops.manage(db, p, project)
    report, rows, batch = prepare(db, p, project_id, raw, filename)
    if report["sha256"] != expected_sha or report["error_count"]:
        raise conflict("文件已更改或含需对账的行，请重新预览")
    if batch:
        return {"already_imported": True, "batch_id": batch, "rows": 0}
    if report["new_rows"] != expected_rows or report["new_amount"] != expected_amount:
        raise conflict("预览后登记情况有变化，请重新核对")
    if not rows:
        return {"already_imported": True, "rows": 0}
    return apply_snapshot(db, p, project_id, Snapshot(source_name=Path(filename).name[:255], source_sha256=expected_sha,
        kind="income", incomes=rows, expected_rows=expected_rows, expected_total=expected_amount,
        notes=["学校每日收款；空学校名为项目公共收入。"]))
