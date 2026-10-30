"""企微正文与表格读取适配。保留类型和原始回包，不把截断或受限数据当完整数据。"""
import io
import json
import os
import re
import stat
from pathlib import Path

from app.core.response import bad_request
from app.services import wecom_cli_service as cli

MAX_ROWS = 20000
MAX_COLUMNS = 200
MAX_PAGES = 100
MAX_BYTES = 50 * 1024 * 1024
COMPLEX_TYPES = {"attachment", "image", "reference", "two_way_link_records", "twowaylinkrecords", "lookup", "wwgroup", "formula"}


def read_output(user_id, request_id, filename, *, limit=MAX_BYTES):
    root = cli.home(user_id) / "work" / "imports" / request_id
    supplied = Path(str(filename))
    target = supplied if supplied.is_absolute() else root / supplied
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise bad_request("企微返回的文件不在本次本人读取目录内，已停止处理")
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise bad_request("企微资料过大或文件类型异常，请拆分资料")
            content = stream.read(limit + 1)
    except OSError:
        raise bad_request("企微返回的临时文件无法读取，请重新预览") from None
    if len(content) > limit:
        raise bad_request("企微资料过大，请拆分后接入")
    return content


def text_content(result, user_id, request_id, inline="content"):
    if result.get("file_path"):
        try:
            return read_output(user_id, request_id, result["file_path"]).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise bad_request("企微正文编码无法识别，未将乱码入库") from None
    value = result.get(inline)
    if not isinstance(value, str):
        raise bad_request("企微没有返回可读取的正文，尚未完成内容读取")
    return value


def paginate(fetch, method, params, key, *, maximum=MAX_ROWS, page_size=100):
    rows, cursor, seen, total = [], None, set(), None
    for _ in range(250):
        payload = {**params, "limit": page_size}
        if cursor:
            payload["cursor"] = cursor
        result = fetch(method, payload)
        if result.get("truncated"):
            raise bad_request("企微返回了截断数据，请缩小源表后重新读取")
        values = result.get(key)
        if not isinstance(values, list):
            raise bad_request(f"企微没有返回完整的{key}列表，未将空响应当成读取成功")
        rows.extend(values)
        if len(rows) > maximum:
            raise bad_request("企微源表超出接入上限，请拆分；未保存部分数据")
        if key == "records" and isinstance(result.get("total"), int):
            if total is not None and total != result["total"]:
                raise bad_request("源表在分页过程中发生变化，请停止编辑后重新读取")
            total = result["total"]
        if not result.get("has_more"):
            if total is not None and total != len(rows):
                raise bad_request("企微记录总数与实际读取数量不一致，请重新预览")
            return rows
        cursor = result.get("next_cursor")
        if not cursor or cursor in seen or not values:
            raise bad_request("企微分页游标异常，尚未完整读取资料")
        seen.add(cursor)
    raise bad_request("企微分页次数过多，请拆分资料后接入")


def _value(field, value):
    kind = str(field.get("field_type") or "text").lower().removeprefix("field_type_")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value, kind in COMPLEX_TYPES
    if isinstance(value, list) and kind in {"single_select", "select", "user", "url", "text"}:
        labels = []
        for item in value:
            if isinstance(item, str):
                labels.append(item)
            elif isinstance(item, dict):
                label = next((item[k] for k in ("userName", "name", "text", "value", "link") if isinstance(item.get(k), (str, int, float))), None)
                if label is None:
                    return json.dumps(value, ensure_ascii=False), True
                labels.append(str(label))
            else:
                return json.dumps(value, ensure_ascii=False), True
        return "、".join(labels), kind == "user" and len(labels) > 1
    return json.dumps(value, ensure_ascii=False), True


def _label(value, index):
    """子表名可能为空或在接口里缺失；给可核对的占位名，不能把 null 当成表名。"""
    text = re.sub(r"[\r\n\t]+", " ", str(value)).strip() if value is not None else ""
    return text[:100] or f"未命名子表 {index}"


def table_xlsx(title, fields, records):
    from openpyxl import Workbook
    if not fields or len(fields) > MAX_COLUMNS or len(records) > MAX_ROWS:
        raise bad_request("表结构为空或数据超过 20000 行、200 列，请核对源表")
    titles = [str(f.get("field_title") or f.get("name") or "") for f in fields]
    if not all(titles) or len(set(titles)) != len(titles):
        raise bad_request("源表存在空字段名或重名字段，无法可靠映射台账")
    rows, complex_columns, ids = [], set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise bad_request("企微记录结构不符合接口定义")
        record_id = record.get("record_id")
        if record_id:
            if record_id in ids:
                raise bad_request("分页返回重复记录，未将不完整数据入库")
            ids.add(record_id)
        values = record.get("values", record.get("fields", record))
        if not isinstance(values, dict):
            raise bad_request("企微记录没有字段映射，无法转换")
        row = []
        for index, (field, field_title) in enumerate(zip(fields, titles)):
            raw = values.get(field_title, values.get(field.get("field_id")))
            value, complex_ = _value(field, raw)
            if complex_:
                complex_columns.add(index)
            if isinstance(value, str) and len(value) > 32767:
                raise bad_request("源表单元格内容过长，不能无损转为台账预览")
            row.append(value)
        rows.append(row)
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\\/*?:\[\]]", "", str(title or "数据"))[:31] or "数据"
    for row in [titles, *rows]:
        ws.append(row)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # 源文本不执行为 Excel 公式。
    output = io.BytesIO()
    wb.save(output)
    if len(output.getvalue()) > 20 * 1024 * 1024:
        raise bad_request("转换结果超过台账导入的 20MB 上限，请拆分源表")
    return output.getvalue(), rows, sorted(complex_columns), titles


def smart_table(fetch, source, table_id):
    catalog = fetch("smartsheet.sheets.list", {"docid": source["docid"]})
    sheets = catalog.get("sheets")
    if not isinstance(sheets, list):
        raise bad_request("没有读取到智能表格的子表清单")
    choices = []
    for index, sheet in enumerate(sheets, 1):
        if sheet.get("type") not in {"smartsheet", "sheet"}:
            continue
        if not isinstance(sheet.get("sheet_id"), str) or not sheet["sheet_id"].strip():
            raise bad_request("智能表格子表清单缺少标识，无法可靠选择要读取的子表")
        choices.append({"id": sheet["sheet_id"], "name": _label(sheet.get("title") or sheet.get("sheet_title"), index),
                        "rows": sheet.get("record_count"), "columns": sheet.get("field_count")})
    source.update(title=catalog.get("name") or source["title"], tables=choices)
    if not table_id:
        return None
    selected = next((s for s in choices if s["id"] == table_id), None)
    if not selected:
        raise bad_request("请选择接口实际返回的数据子表，仪表盘不能直接当台账")
    args = {"docid": source["docid"], "sheet_id": table_id}
    fields = paginate(fetch, "smartsheet.fields.list", args, "fields", maximum=MAX_COLUMNS)
    records = paginate(fetch, "smartsheet.records.list", {**args, "key_type": "field_title"}, "records",
                       page_size=min(100, 9000 // max(1, len(fields))))
    result = table_xlsx(selected["name"], fields, records)
    source.update(table_id=table_id, table_name=selected["name"], row_count=len(records),
                  fields=fields, complex_columns=result[2], columns=result[3], sample_rows=result[1][:5])
    return result[0], {"fields": fields, "records": records, "table": selected}


def online_table(fetch, source, table_id):
    from openpyxl.utils.cell import range_boundaries, get_column_letter
    catalog = fetch("sheet.workbook.get", {"docid": source["docid"]})
    sheets = catalog.get("sheets")
    if not isinstance(sheets, list):
        raise bad_request("没有读取到在线表格的子表清单")
    choices = []
    for index, sheet in enumerate(sheets, 1):
        if sheet.get("type", "sheet") != "sheet":
            continue
        if not isinstance(sheet.get("sheet_id"), str) or not sheet["sheet_id"].strip():
            raise bad_request("在线表格子表清单缺少标识，无法可靠选择要读取的工作表")
        choices.append({"id": sheet["sheet_id"], "name": _label(sheet.get("name"), index), "range": sheet.get("range")})
    source["tables"] = choices
    if not table_id:
        return None
    selected = next((s for s in choices if s["id"] == table_id), None)
    if not selected:
        raise bad_request("请选择实际存在的工作表")
    try:
        left, top, right, bottom = range_boundaries(selected["range"])
        if None in (left, top, right, bottom):
            raise ValueError()
    except (ValueError, TypeError):
        raise bad_request("企微未返回明确的数据范围，无法保证完整读取") from None
    width, height = right - left + 1, bottom - top + 1
    if width > MAX_COLUMNS or height > MAX_ROWS or width * height > 1000000:
        raise bad_request("工作表超过接入上限，请拆分后导入")
    matrix, raw_pages, complex_columns = [], [], set()
    for start in range(top, bottom + 1, max(1, 7000 // width)):
        end = min(bottom, start + max(1, 7000 // width) - 1)
        area = f"{get_column_letter(left)}{start}:{get_column_letter(right)}{end}"
        data = fetch("sheet.cells.get", {"docid": source["docid"], "sheet": table_id, "range": area, "include": ["formula"]})
        if data.get("truncated") or data.get("not_viewable_cells"):
            raise bad_request("源表包含截断或无权查看的区域，尚未完成读取")
        cells = data.get("cells")
        if not isinstance(cells, list) or len(cells) != end - start + 1 or any(len(r) != width for r in cells):
            raise bad_request("源表返回范围不完整，请核对区域授权后重试")
        raw_pages.append(data)
        for row in cells:
            normalized = []
            for i, cell in enumerate(row):
                if isinstance(cell, dict):
                    if cell.get("formula") or cell.get("image"):
                        complex_columns.add(i)
                    cell = cell.get("value", cell.get("link_text", cell.get("rich_string")))
                value, complex_ = _value({"field_type": "text"}, cell)
                if complex_:
                    complex_columns.add(i)
                normalized.append(value)
            matrix.append(normalized)
    # 保留原表头位置，让现有清洗服务识别多行表头，不能假定第一行。
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\\/*?:\[\]]", "", selected["name"] or "数据")[:31]
    for row in matrix:
        ws.append(row)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str): cell.data_type = "s"
    output = io.BytesIO()
    wb.save(output)
    source.update(table_id=table_id, table_name=selected["name"], row_count=max(0, len(matrix) - 1),
                  columns=[str(c or "") for c in matrix[0]] if matrix else [], sample_rows=matrix[1:6],
                  complex_columns=sorted(complex_columns))
    return output.getvalue(), {"table": selected, "pages": raw_pages}
