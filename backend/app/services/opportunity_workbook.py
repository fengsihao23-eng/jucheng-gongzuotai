"""The market opportunity list, filters and export are views of one source tab."""
from collections import Counter
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.response import bad_request, conflict, not_found
from app.core.timeutil import month_range, today, week_range
from app.services import business_workbook as source, workbook_metrics as metrics

COLUMNS = (
    ("sequence", "工单编号", 24), ("template", "工单模板", 18), ("contact", "联系人", 20),
    ("owner", "责任人", 20), ("collaborator", "协同人", 30), ("progress", "工单状态", 18),
    ("name", "工单主题", 56), ("date", "发起时间", 24), ("note", "描述", 58),
)


def _sheet(wb, sheet_id):
    sheet = source.opportunity_sheet(wb)
    if sheet_id and sheet_id != "all" and (not sheet or sheet_id != sheet.id):
        raise not_found("市场商机子表")
    return sheet


def _date(value):
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise bad_request("发起时间筛选无效") from None


def _filtered(rows, *, keyword="", year="", start="", end="", status="", owner="", contact=""):
    start, end = _date(start), _date(end)
    if start and end and start > end:
        raise bad_request("发起时间的结束日期不能早于开始日期")
    if year and year != "undated" and not (year.isascii() and year.isdigit() and len(year) == 4):
        raise bad_request("年份筛选无效")
    key = source.layout.norm(keyword)
    exact = {f: val for f, val in (("progress", status), ("owner", owner), ("contact", contact)) if val}

    def accepts(row):
        day = row["date_value"] or ""
        return ((not key or key in row["_search"])
                and all(source.layout.text(row.get(f)) == val for f, val in exact.items())
                and (not year or (not day if year == "undated" else day.startswith(year + "-")))
                and (not (start or end) or (day and (not start or start <= day) and (not end or day <= end))))

    return sorted((r for r in rows if accepts(r)),
                  key=lambda r: (r["date_value"] or "", r["date"] or "", r["row"]), reverse=True)


def status_counts(rows):
    counts = Counter(status for r in rows if (status := source.layout.text(r.get("progress"))))
    return [{"value": state, "count": count} for state, count in sorted(
        counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def page(wb, p, *, sheet_id="", page=1, size=30, raw=False, **filters):
    if raw:
        raise bad_request("商机信息仅展示工单明细")
    sheet = _sheet(wb, sheet_id)
    rows = sheet.items if sheet else []
    displayed = _filtered(rows, **filters)
    total = len(displayed)
    page = min(page, max(1, (total + size - 1) // size))

    end = today()
    status = status_counts(rows)
    brief = sheet.brief() if sheet else None
    return {"active": True, "source": source.source_info(wb, p), "sheet": brief, "sheets": [brief] if brief else [],
            "warning": None if sheet else f"未找到可用的「{source.OPPORTUNITY_SHEET}」，请联系管理员检查表格。",
            "summary": sheet.summary(displayed) if sheet else None,
            "years": sorted({r["date_value"][:4] for r in rows if r["date_value"]}, reverse=True),
            "stats": {"total": len(rows), "week": metrics.opportunity_metrics(wb, week_range(ref=end)[0], end),
                      "month": metrics.opportunity_metrics(wb, month_range(ref=end)[0], end)},
            "statuses": status,
            "options": {"status": [o["value"] for o in status], **{
                option: sorted({value for r in rows if (value := source.layout.text(r.get(option)))})
                for option in ("owner", "contact")}},
            "items": [{k: v for k, v in r.items() if not k.startswith("_")}
                      for r in displayed[(page - 1) * size:page * size]],
            "total": total, "page": page, "size": size}


def export(wb, *, sheet_id="", revision="", **filters):
    if not wb:
        raise not_found("市场商机子表")
    if revision != wb.revision:
        raise conflict("原表已更新，请刷新商机列表后重新导出")
    sheet = _sheet(wb, sheet_id)
    if not sheet:
        raise not_found("市场商机子表")
    rows = _filtered(sheet.items, **filters)
    book = Workbook()
    out = book.active
    out.title = source.OPPORTUNITY_SHEET
    out.append([label for _, label, _ in COLUMNS])
    for j, (_, _, width) in enumerate(COLUMNS, 1):
        out.column_dimensions[get_column_letter(j)].width = width
        cell = out.cell(1, j)
        cell.fill = PatternFill("solid", fgColor="1B3275")
        cell.font = Font(bold=True, color="FFFFFF")
    for index, row in enumerate(rows, 2):
        for col, (field, _, _) in enumerate(COLUMNS, 1):
            if row.get(field):
                cell = out.cell(index, col, row[field])
                cell.data_type = "s"  # Source text starting with '=' remains text.
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    out.freeze_panes = "A2"
    out.auto_filter.ref = out.dimensions
    notes = book.create_sheet("口径说明")
    notes.append(["数据来源", sheet.name])
    notes.append(["表格版本", wb.revision])
    notes.append(["导出范围", f"当前筛选，共 {len(rows)} 条"])
    notes.append(["统计日期", "发起时间取原表"])
    notes.column_dimensions["A"].width = 16
    notes.column_dimensions["B"].width = 36
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue(), len(rows)
