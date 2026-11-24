"""Metrics owned by the source-backed contract/collection/opportunity modules.

The board consumes these same module records and supplies their filters as
drilldowns. It does not query the old, mixed-source financial/business ledgers.
"""
import re
from datetime import date
from decimal import Decimal

from app.core.response import bad_request
from app.services import business_workbook as source


def between(rows, start, end):
    return [r for r in rows if r.get("date_value") and str(start) <= r["date_value"] <= str(end)]


def amount(rows, field="amount"):
    values = [Decimal(r[field]) for r in rows if r.get(field) is not None]
    return sum(values, Decimal(0)) if values or not rows else None


def contract_metrics(wb, start, end):
    rows = between(source.performance_rows(wb), start, end)
    return {"amount": amount(rows), "count": len(rows), "missing_amount": sum(r.get("amount") is None for r in rows),
            "drill": {"path": "/contracts", "query": {"sheet": "all", "start": str(start), "end": str(end)}},
            "rows": rows}


def opportunity_metrics(wb, start, end):
    sheet = source.opportunity_sheet(wb)
    rows = between(sheet.items, start, end) if sheet else []
    sid = sheet.id if sheet else ""
    return {"amount": None, "count": len(rows),
            "drill": {"path": "/opportunities", "query": {"sheet": sid, "start": str(start), "end": str(end)}}}


def collection_periods(wb):
    """Only explicit monthly receipts from dedicated progress columns.

    A title such as 2026年销售合同 supplies the reporting year. The progress
    column supplies the month, and each actual amount must be explicit (never
    an expected payment). Its sum must match the source's own monthly total.
    Historical totals and last-payment dates are not made into transactions.
    """
    sheets = source.collection_sheets(wb)
    years = {m[1] for s in sheets if s.profile == "sales" and (m := re.search(r"(20\d{2})年", s.name))}
    if len(years) != 1:
        return {}
    year = int(next(iter(years)))
    periods = {}
    for sheet in sheets:
        if sheet.profile not in {"sales", "older"}:
            continue
        for col in sheet.columns:
            header = re.fullmatch(r"(?:(20\d{2})年)?(\d{1,2})月回款进度", source.layout.norm(sheet.label(col)))
            if not header or not 1 <= int(header[2]) <= 12:
                continue
            period = f"{int(header[1] or year)}-{int(header[2]):02d}"
            bucket = periods.setdefault(period, {"rows": [], "controls": [], "issues": [], "available": True})
            totals, rows = [], []
            for n, raw in sheet.rows.items():
                if n <= sheet.header:
                    continue
                value = sheet.value(n, col)
                if not raw.get("record") and not raw.get("parent") and sheet.value(n, sheet.fields["name"]) in (None, ""):
                    total = source.number(value)
                    if total is not None:
                        totals.append((n, total))
            for row in sheet.source_items:
                n = row["row"]
                text = sheet.display(n, col) or ""
                numeric = source.number(sheet.value(n, col))
                events = []
                # Deliberately strict: expected/possible/conditional payments
                # cannot match the explicit completed-payment phrase.
                pattern = r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日(?:已完成本月回款|已回款|已收到|已收款)([\d,.]+)(万元|元)"
                for y, m, d, value, unit in re.findall(pattern, text):
                    try:
                        when = date(int(y or period[:4]), int(m), int(d))
                    except ValueError:
                        continue
                    payment = source.number(value + unit)
                    if when.strftime("%Y-%m") == period and payment is not None:
                        events.append((when.isoformat(), payment))
                if numeric is not None:
                    events = [(None, numeric)]
                identity = source.workbook_identity(sheet.rows.get(row["group_row"]))
                event_counts = {}
                for index, (when, payment) in enumerate(events):
                    signature = (when or "", format(payment.normalize(), "f"))
                    occurrence = event_counts.get(signature, 0) + 1
                    event_counts[signature] = occurrence
                    receipt_key = (source.workbook_deletion_key(wb.id, sheet.id, "\n".join(
                        (identity, period, *signature, str(occurrence)))) if identity else None)
                    rows.append({**row, "id": f"{sheet.id}:{n}:{col}:{index}", "date": when,
                                 "date_value": when, "amount": source.decimal_text(payment),
                                 "received": source.decimal_text(payment), "outstanding": None,
                                 "progress": text, "receipt_cell": f"{source.get_column_letter(col)}{n}",
                                 "deletion_key": receipt_key,
                                 "issues": []})
            calculated = amount(rows)
            if len(totals) != 1 or calculated != totals[0][1]:
                bucket["available"] = False
                bucket["issues"].append(f"{sheet.name}月度回款明细与原表合计尚未核对一致")
            else:
                n, total = totals[0]
                bucket["controls"].append({"field": "received", "cell": f"{sheet.name} · {source.get_column_letter(col)}{n}",
                    "source": str(total), "calculated": str(calculated), "difference": "0.00", "matches": True})
            removed = getattr(wb, "_removed_receipt_keys", frozenset())
            bucket["rows"].extend(row for row in rows
                                  if row["row"] not in sheet.removed_rows and row["deletion_key"] not in removed)
    return periods


def collection_metrics(wb, period, end=None):
    data = collection_periods(wb).get(period)
    if not data or not data["available"]:
        return {"amount": None, "count": None, "rows": [], "available": False,
                "note": "；".join(data["issues"]) if data else "原表未提供本月完整的回款进度合计"}
    rows = [r for r in data["rows"] if not end or not r["date_value"] or r["date_value"] <= str(end)]
    return {**data, "rows": rows, "amount": amount(rows), "count": len(rows),
            "note": "回款情况中的月度回款进度；只统计明确完成并与表内合计一致的金额"}


def collection_page(wb, period, page, size, *, allowed_rows, end=''):
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
        raise bad_request("请选择有效回款月份")
    result = collection_metrics(wb, period, end)
    rows = [r for r in result['rows'] if (r['sheet_id'], r['row']) in allowed_rows]
    value = source.decimal_text(amount(rows)) if result["available"] else None
    summary = {"count": len(rows), "groups": len(rows), "review_count": 0,
               "amount": {"available": False}, "outstanding": {"available": False},
               "received": {"value": value, "available": True, "numeric_count": len(rows), "missing_count": 0,
                            "label": "明确月度回款"}}
    page = min(page, max(1, (len(rows) + size - 1) // size))
    return {"sheet": {"id": "all", "name": f"{period} 月度回款明细", "profile": "monthly_receipts",
                       "summary": summary, "controls": result.get("controls", []),
                       "warning": None if result["available"] else result["note"]},
            "summary": summary, "items": [{k: v for k, v in r.items() if k != "_search"} for r in rows[(page-1)*size:page*size]],
            "total": len(rows), "page": page, "size": size, "years": [], "columns": []}
