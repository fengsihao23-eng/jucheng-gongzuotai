"""Source-owned monthly collection plans; reporting progress never books a receipt."""
from datetime import date
from decimal import Decimal
import re

from app.core.response import bad_request
from app.core.timeutil import today
from app.services import business_workbook as source


def periods(ref: date | None = None):
    ref = ref or today()
    following = date(ref.year + (ref.month == 12), ref.month % 12 + 1, 1)
    return [("current", f"{ref:%Y-%m}", "当月预估回款金额"), ("next", f"{following:%Y-%m}", "次月预估回款金额")]


def safe_plan(wb, period: str) -> dict:
    try:
        b = bucket(wb, period)
        return {"period": period, "amount": Decimal(b["amount"]) if b["available"] and b["amount"] is not None else None,
                "count": b["count"] if b["available"] else None, "available": b["available"], "note": b["note"]}
    except Exception:
        return {"period": period, "amount": None, "count": None, "available": False, "note": None}


def _columns(sheet, year):
    result = {}
    for col in sheet.columns:
        header = re.fullmatch(r"(?:(20\d{2})年)?(\d{1,2})月(预计可回款金额|达回款条件,应回款金额)", source.layout.norm(sheet.label(col)))
        if header and 1 <= int(header[2]) <= 12:
            period = f"{int(header[1] or year)}-{int(header[2]):02d}"
            if period in result:
                raise bad_request("月度回款计划列重复，请检查原表")
            result[period] = col
    return result


def bucket(wb, period):
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
        raise bad_request("回款月份无效")
    sheets = source.collection_sheets(wb)
    if not sheets:
        raise bad_request("回款计划的两张来源子表尚未完整同步")
    years = {int(m[1]) for s in sheets if s.profile == "sales" and (m := re.search(r"(20\d{2})年", s.name))}
    if len(years) != 1:
        raise bad_request("无法确定原表回款计划年份")
    year = next(iter(years))
    items, controls, warnings, cols = [], [], [], []
    for sheet in sheets:
        col = _columns(sheet, year).get(period)
        if col is None:
            warnings.append(f"{sheet.name}尚未提供{period}的回款计划")
            continue
        cols.append({"sheet": sheet.name, "label": sheet.label(col)})
        progress_col = next((c for c in sheet.columns if source.layout.norm(sheet.label(c)) in (
            f"{int(period[-2:])}月回款进度", f"{period[:4]}年{int(period[-2:])}月回款进度")), None)
        numerics = []
        for row in sheet.items:
            n = row["row"]
            raw_amount = source.number(sheet.value(n, col))
            combined = sheet.anchors.get((n, col))
            anchor = combined or (n, col)
            inherited = source.number(sheet.value(*anchor))
            note = sheet.display(n, progress_col, inherit=True) if progress_col else None
            plan_text = sheet.display(n, col, inherit=True)
            if plan_text is None and not note:
                continue
            if raw_amount is not None:
                numerics.append(raw_amount)
            address = f"{source.get_column_letter(col)}{anchor[0]}"
            items.append({**{k: v for k, v in row.items() if k != "_search"},
                "plan_amount": source.decimal_text(raw_amount),
                "plan_text": plan_text, "plan_shared": bool(combined),
                "plan_group_amount": source.decimal_text(inherited),
                "plan_cell": address, "source_progress": note,
                "report_path": f"/collections/items/{sheet.id}/{n}/report"})
        calculated = sum(numerics, Decimal(0))
        totals = [(n, source.number(sheet.value(n, col))) for n, row in sheet.rows.items()
                  if n > sheet.header and not row.get("record") and not row.get("parent")
                  and sheet.value(n, sheet.fields["name"]) in (None, "") and source.number(sheet.value(n, col)) is not None]
        if len(totals) != 1 or totals[0][1] != calculated:
            warnings.append(f"{sheet.name}的回款计划合计尚未核对一致")
        else:
            controls.append({"sheet": sheet.name, "cell": f"{source.get_column_letter(col)}{totals[0][0]}",
                             "source": source.decimal_text(totals[0][1]), "calculated": source.decimal_text(calculated),
                             "matches": True})
    available = not warnings
    from app.services.collection_amounts import apply
    items = apply(wb, items)
    return {"period": period, "amount": source.decimal_text(sum((Decimal(r["plan_amount"]) for r in items
              if r["plan_amount"] is not None), Decimal(0))) if available else None,
            "available": available, "count": len(items), "items": items, "controls": controls,
            "columns": cols, "note": "；".join(warnings) if warnings else "原表回款计划；反馈和预计金额不计入实际到账",
            "basis": "planned"}


def cards(wb):
    return [{**{k: v for k, v in bucket(wb, period).items() if k != "items"}, "key": key, "title": title}
            for key, period, title in periods()]


def page(db, wb, p, period, *, page=1, size=30):
    from app.services.collection_workbook import _add_followups
    result = bucket(wb, period)
    rows = result["items"]
    page = min(page, max(1, (len(rows) + size - 1) // size))
    items = rows[(page-1)*size:page*size]
    _add_followups(db, wb, p, items)
    return {**result, "items": items, "total": len(rows), "page": page, "size": size,
            "source": source.source_info(wb, p)}


def for_project(wb, sheet, row):
    result = []
    for key, period, title in periods():
        b = bucket(wb, period)
        item = next((r for r in b["items"] if r["sheet_id"] == sheet and r["row"] == row), None)
        if item:
            result.append({**{k: item[k] for k in ("plan_amount", "plan_text", "plan_shared", "plan_cell", "source_progress")},
                           "period": period, "title": title, "available": b["available"]})
    return result
