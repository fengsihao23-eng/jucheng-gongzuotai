"""Read the entire source using the reader's own, pinned WeCom connection."""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import uuid
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter, range_boundaries

from app.core.response import BizError, bad_request
from app.services import wecom_cli_service as cli, wecom_import_formats as formats
from app.services.xlsx_compat import repair_empty_fills

MAX_SHEETS = 40
MAX_CELLS = 500_000
PAGE_CELLS = 7_000
SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")


def occupied(cell):
    if isinstance(cell, dict):
        return any(v not in (None, "", [], {}) for v in cell.values())
    return cell not in (None, "")


def cell_value(cell):
    if not isinstance(cell, dict):
        return cell
    if cell.get("value") is not None:
        return cell["value"]
    if isinstance(cell.get("rich_string"), list):
        return "".join(str(i.get("value") or i.get("text") or i.get("link_text") or "")
                       for i in cell["rich_string"] if isinstance(i, dict))
    return cell.get("link_text")


def _bounds(area):
    try:
        if not isinstance(area, str):
            raise ValueError()
        left, top, right, bottom = range_boundaries(area)
        if not (1 <= left <= right <= formats.MAX_COLUMNS and 1 <= top <= bottom <= formats.MAX_ROWS):
            raise ValueError()
        return left, top, right, bottom
    except (TypeError, ValueError):
        raise bad_request("子表数据范围缺失或超出同步上限，保留上次完整版本") from None


def _complete(result):
    if (not isinstance(result, dict) or result.get("errcode") or result.get("truncated")
            or result.get("not_viewable_cells") or result.get("has_more")):
        raise bad_request("企微返回了截断或无权查看的数据，保留上次完整版本")
    return result


def _version(fetch, docid):
    version = _complete(fetch("sheet.version.get", {"docid": docid})).get("version")
    if type(version) is not int or version < 0:
        raise bad_request("企微未返回有效表格版本，保留上次完整版本")
    return str(version)


def read_online(fetch, docid, previous=None):
    """Return (revision, sheets or None). Never publish a mix of revisions."""
    version = _version(fetch, docid)
    if version == previous:
        return version, None
    sheets = _complete(fetch("sheet.workbook.get", {"docid": docid})).get("sheets")
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= MAX_SHEETS:
        raise bad_request("企微没有返回完整的子表清单")
    snapshots, ids, total_cells = [], set(), 0
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise bad_request("企微返回的子表格式异常")
        sid, title, area = sheet.get("sheet_id"), sheet.get("name"), sheet.get("range")
        if not isinstance(sid, str) or not SOURCE_ID.fullmatch(sid) or sid in ids or not isinstance(title, str):
            raise bad_request("企微返回的子表标识异常")
        ids.add(sid)
        left, top, right, bottom = _bounds(area)
        width = right - left + 1
        total_cells += width * (bottom - top + 1)
        if total_cells > MAX_CELLS:
            raise bad_request("整份表格超出单次同步上限，保留上次完整版本")
        args = {"docid": docid, "sheet": sid}
        merges = _complete(fetch("sheet.merge.get", {**args, "range": area})).get("merges")
        if not isinstance(merges, list):
            raise bad_request("企微未返回完整的合并单元格信息")
        # Merges are part of the source: validate their geometry before
        # interpreting a blank as inherited data.
        for merge in merges:
            mleft, mtop, mright, mbottom = _bounds(merge)
            if mleft < left or mright > right or mtop < top or mbottom > bottom:
                raise bad_request("企微返回的合并区超出了子表范围")
        rows = []
        step = max(1, PAGE_CELLS // width)
        for start in range(top, bottom + 1, step):
            end = min(bottom, start + step - 1)
            page = f"{get_column_letter(left)}{start}:{get_column_letter(right)}{end}"
            data = _complete(fetch("sheet.cells.get", {**args, "range": page, "include": ["formula"]}))
            cells = data.get("cells")
            if (data.get("actual_range") != page or not isinstance(cells, list)
                    or len(cells) != end - start + 1
                    or any(not isinstance(row, list) or len(row) != width for row in cells)):
                raise bad_request(f"企微未完整返回子表「{title}」{page}，保留上次完整版本")
            for number, row in enumerate(cells, start):
                if any(occupied(c) for c in row):
                    rows.append({"row": number, "cells": [None] * (left - 1) + row})
        snapshots.append({"id": sid, "name": title, "range": area, "merges": merges, "rows": rows})
    if _version(fetch, docid) != version:
        raise bad_request("表格在读取期间被编辑，本次未更新，稍后自动重试")
    return version, snapshots


def read_xlsx(content):
    """Read all tabs of an actual WeDrive XLSX, including cached formulas and links."""
    try:
        with ZipFile(io.BytesIO(content)) as zip_file:
            entries = zip_file.infolist()
            if len(entries) > 2000 or sum(i.file_size for i in entries) > 4 * formats.MAX_BYTES:
                raise bad_request("XLSX 解压后超出同步上限")
            content = repair_empty_fills(zip_file, content)
        with_formulas = load_workbook(io.BytesIO(content), keep_links=False)
        try:
            with_values = load_workbook(io.BytesIO(content), data_only=True, keep_links=False)
            try:
                return _xlsx_tabs(with_formulas, with_values)
            finally:
                with_values.close()
        finally:
            with_formulas.close()
    except BizError:
        raise
    except Exception:
        raise bad_request("企微文件不是可完整读取的 XLSX，保留上次完整版本") from None


def _xlsx_tabs(with_formulas, with_values):
    from datetime import date, time, timedelta

    def scalar(value):
        if isinstance(value, (date, time)):
            return value.isoformat()
        if isinstance(value, timedelta):
            return str(value)
        return value

    if not 1 <= len(with_formulas.worksheets) <= MAX_SHEETS:
        raise bad_request("XLSX 子表数量超出同步上限")
    result, total_cells = [], 0
    for sheet in with_formulas.worksheets:
        cached = with_values[sheet.title]
        area = sheet.calculate_dimension()
        left, top, right, bottom = _bounds(area)
        total_cells += (right - left + 1) * (bottom - top + 1)
        if total_cells > MAX_CELLS:
            raise bad_request("整份 XLSX 超出单次同步上限")
        rows = []
        for number, cells in enumerate(sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right), top):
            row = [None] * (left - 1)
            for c in cells:
                if c.data_type == "f":
                    row.append({"formula": str(c.value), "value": scalar(cached[c.coordinate].value)})
                elif c.hyperlink:
                    row.append({"value": scalar(c.value), "link": c.hyperlink.target or c.hyperlink.location})
                elif c.data_type == "e":
                    row.append({"error": str(c.value)})
                else:
                    row.append(scalar(c.value))
            if any(occupied(c) for c in row):
                rows.append({"row": number, "cells": row})
        result.append({"id": sheet.title, "name": sheet.title, "range": area,
                       "merges": sorted(str(m) for m in sheet.merged_cells.ranges), "rows": rows})
    return result


def fetch_workbook(config, previous):
    reader_id = config["reader_id"]
    binding = cli._owned(reader_id, config["binding_id"])
    request_id = uuid.uuid4().hex
    work = cli.home(reader_id) / "work" / "imports" / request_id

    def fetch(method, payload):
        result = cli.import_read(reader_id, method, payload, binding["binding_id"], request_id)
        if result.get("file_path") and method != "disk.files.download":
            return json.loads(formats.read_output(reader_id, request_id, result["file_path"]))
        return result

    try:
        if config["format"] == "workbook":
            return read_online(fetch, config["docid"], previous)
        result = _complete(fetch("disk.files.download", {"file_id": config["file_id"]}))
        if not result.get("file_path"):
            raise bad_request("企微未返回 XLSX 原文件")
        content = formats.read_output(reader_id, request_id, result["file_path"])
        digest = hashlib.sha256(content).hexdigest()
        return digest, None if digest == previous else read_xlsx(content)
    finally:
        if not work.is_symlink():
            shutil.rmtree(work, ignore_errors=True)
