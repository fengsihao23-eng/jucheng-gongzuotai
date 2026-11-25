"""The five business pages read one complete workbook, never mixed ledgers.

Source positions are identities within a revision. Replacing a revision replaces
the entire presentation, including removed rows. No inferred business line,
historical payment, ledger match, or default date enters these read models.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import urlsplit

from openpyxl.utils.cell import get_column_letter, range_boundaries
from openpyxl.utils.datetime import from_excel
from sqlalchemy import select

from app.core.response import BizError, bad_request, forbidden, not_found
from app.core.timeutil import utcnow
from app.models import BusinessRecordDeletion, ContractWorkbook
from app.services import contract_workbook_layout as layout, dict_service
from app.services.contract_workbook_source import cell_value, occupied

CONFIG = "business_workbook"
OPPORTUNITY_SHEET = "5、市场商机"
VIEWS = {
    "contracts": {"sales", "older"},
    "collections": {"sales", "older"},
    "performance": {"sales", "older"},
    "opportunities": {"opportunity"},
    "tender": {"tender", "pending"},
    "board": set(layout.PROFILES),
}
MENUS = {"contracts": ("contracts",), "collections": ("contracts", "receivables"),
         "performance": ("performance",), "opportunities": ("opportunities",),
         "tender": ("projects",), "board": ("board",)}
DEFAULTS = {"tender": "tender"}
# Only literal source columns. Amounts in merged cells are counted at their
# anchor once, even when several contracts share the same receipts cell.
FIELDS = {
    "sales": dict(name=2, customer=3, owner=4, party=5, amount=6, date=8, received=9, outstanding=10, region=19, note=15),
    "older": dict(name=2, customer=3, owner=5, party=6, amount=7, date=8, received=9, outstanding=10, note=14),
    "longterm": dict(name=2, customer=3, owner=5, party=6, amount=7, date=8, received=9, outstanding=10, note=11),
    "paid": dict(name=2, customer=3, owner=5, party=6, amount=7, date=8, received=9),
    "pending": dict(name=2, customer=4, owner=6, party=5, amount=7, note=8, progress=3),
    "tender": dict(name=3, customer=8, owner=2, party=9, amount=11, progress=4, date=5, note=7),
    "opportunity": dict(name=7, template=2, contact=3, customer=3, owner=4, collaborator=5, progress=6, date=8, note=9),
    "mobile": dict(name=3, customer=8, party=7, amount=4, note=6),
}
METRICS = ("amount", "received", "outstanding")


def workbook_identity(row):
    keys = dict(((row or {}).get("record") or {}).get("keys", ()))
    return next((f"{kind}:{keys[kind]}" for kind in ("external", "entity", "exact") if kind in keys), "")


def workbook_deletion_key(workbook_id, sheet_id, identity):
    digest = sha256(f"{workbook_id}\n{sheet_id}\n{identity}".encode()).hexdigest()
    return f"{workbook_id}:{digest}"


def number(value):
    """Read a declared amount; retain source precision (e.g. 1,069,803.854).

    Binary floating point noise within 0.0000001 yuan of a cent is normalized.
    A blank, textual price, unknown value, or formula error is never zero.
    """
    if isinstance(value, bool) or value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value).strip())
    if not re.fullmatch(r"[¥￥]?\s*[+-]?(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?:\s*(?:万元|万|元))?", s):
        return None
    try:
        d = Decimal(re.sub(r"[¥￥,\s万元]", "", s)) * (10000 if "万" in s else 1)
        if not d.is_finite() or abs(d) >= Decimal("1000000000000"):
            return None
        cents = d.quantize(Decimal(".01"))
        if abs(d - cents) < Decimal(".0000001"):
            d = cents
        return d
    except (InvalidOperation, ValueError):
        return None


def decimal_text(value):
    return format(value, "f") if value is not None else None


class Sheet:
    def __init__(self, source, profile):
        # Discard every previous ledger match or calculated business record.
        self.source = {**source, "rows": [{"row": r["row"], "cells": r["cells"]} for r in source["rows"]]}
        self.id, self.name, self.profile = source["id"], source["name"], profile
        self.warning = layout.project_sheet(self.source, profile)
        self.rows = {r["row"]: r for r in self.source["rows"]}
        self.header = layout.PROFILES.get(profile, (1, {}))[0]
        self.fields = FIELDS.get(profile, {})
        self.anchors = {}
        for area in source.get("merges", []):
            left, top, right, bottom = range_boundaries(area)
            for row in range(top, bottom + 1):
                for col in range(left, right + 1):
                    if (row, col) != (top, left):
                        self.anchors[row, col] = (top, left)
        width = max((len(r["cells"]) for r in self.rows.values()), default=0)
        self.columns = [c for c in range(1, width + 1)
                        if any(occupied(self.raw(r, c)) for r in self.rows)]
        aliases = {"business_line": {"业务线"}, "contract_status": {"合同状态", "状态"}}
        self.extra_fields = {}
        for field, names in aliases.items():
            matches = [c for c in self.columns if layout.norm(self.label(c)) in names]
            if len(matches) == 1:
                self.extra_fields[field] = matches[0]
        self.items = []
        if not self.warning:
            for row in self.source["rows"]:
                # A second tender lot has its own amounts and progress. It is
                # a separate detail row, belonging to the same project.
                if row.get("record") or (profile == "tender" and row.get("parent")):
                    self.items.append(self.project(row))
        self.source_items = self.items
        self.removed_rows = frozenset()

    def raw(self, row, col):
        cells = self.rows.get(row, {}).get("cells", [])
        return cells[col - 1] if 0 < col <= len(cells) else None

    def value(self, row, col, *, inherit=False):
        if inherit:
            row, col = self.anchors.get((row, col), (row, col))
        return cell_value(self.raw(row, col))

    def label(self, col):
        labels = []
        for r in range(1, self.header + 1):
            value = self.value(r, col, inherit=True)
            if value not in (None, "") and str(value).strip() not in labels:
                labels.append(str(value).strip())
        return " · ".join(labels) or get_column_letter(col)

    def display(self, row, col, *, inherit=False):
        r, c = self.anchors.get((row, col), (row, col)) if inherit else (row, col)
        raw = self.raw(r, c)
        value = cell_value(raw)
        if value in (None, ""):
            if isinstance(raw, dict) and raw.get("error"):
                return str(raw["error"])
            if isinstance(raw, dict) and raw.get("formula"):
                return "公式结果未返回"
            return None
        if isinstance(value, (int, float)) and any(s in self.label(col) for s in ("日期", "时间")):
            return layout.day(value) or str(value)
        return str(value)

    def project(self, row):
        n = row["row"]
        result = {"id": f"{self.id}:{n}", "row": n, "sheet_id": self.id, "sheet_name": self.name,
                  "profile": self.profile, "group_row": row.get("parent") or n, "issues": []}
        for field, col in self.fields.items():
            if field in METRICS:
                result[field] = decimal_text(number(self.value(n, col)))
                result[field + "_text"] = self.display(n, col)
                anchor = self.anchors.get((n, col))
                if anchor and result[field] is None and not result[field + "_text"]:
                    result[field + "_text"] = f"合并金额，见 {get_column_letter(anchor[1])}{anchor[0]}"
                elif result[field] is None:
                    result["issues"].append(f"{self.label(col)}未填写数值，未计入金额合计")
            else:
                result[field] = self.display(n, col, inherit=True)
        source_date = self.value(n, self.fields.get("date", 0), inherit=True)
        result["date_value"] = layout.day(source_date)
        if self.profile == "opportunity" and source_date:
            if isinstance(source_date, (int, float)) and result["date_value"]:
                stamp = from_excel(source_date)
                result["date"] = stamp.isoformat(sep=" ", timespec="milliseconds" if stamp.microsecond else "seconds")
                result["date_value"] = stamp.date().isoformat()
            elif isinstance(source_date, str) and not result["date_value"]:
                try:
                    result["date_value"] = layout.day(datetime.fromisoformat(source_date.strip()))
                except ValueError:
                    pass  # Keep the source text; it cannot enter date statistics.
        for field, col in self.extra_fields.items():
            result[field] = self.display(n, col, inherit=True)
        result["sequence"] = self.display(n, 1, inherit=True)
        if all(result.get(f) is not None for f in METRICS):
            if Decimal(result["amount"]) != Decimal(result["received"]) + Decimal(result["outstanding"]):
                result["issues"].append("原表合同金额与已收、未收之和不同，按原值分别展示")
        result["_search"] = layout.norm(" ".join(self.display(n, c, inherit=True) or "" for c in self.columns))
        return result

    def summary(self, items=None):
        items = self.items if items is None else items
        metrics = {}
        for field in METRICS:
            values = [Decimal(r[field]) for r in items if r.get(field) is not None]
            metrics[field] = {"value": decimal_text(sum(values, Decimal(0))) if values else None,
                              "numeric_count": len(values), "missing_count": len(items) - len(values),
                              "available": field in self.fields,
                              "label": self.label(self.fields[field]) if field in self.fields else "原表无此字段"}
        return {"count": len(items), "groups": len({r["group_row"] for r in items}),
                "review_count": sum(bool(r["issues"]) for r in items), **metrics}

    def controls(self):
        """Compare explicit source total rows without adding them to details."""
        if self.profile == "mobile" or self.warning:
            return []  # Multiple unrelated sections with different column meanings.
        result, summary = [], self.summary(self.source_items)
        for n, row in self.rows.items():
            if n <= self.header or row.get("record") or row.get("parent"):
                continue
            title = self.value(n, self.fields.get("name", 0))
            seq = self.value(n, 1)
            if title not in (None, "") or (seq not in (None, "") and not re.match(r"^(?:合计|总计)", str(seq))):
                continue
            for field in METRICS:
                col = self.fields.get(field, 0)
                source = number(self.value(n, col))
                calculated = summary[field]["value"]
                if source is not None and calculated is not None:
                    difference = Decimal(calculated) - source
                    result.append({"field": field, "label": self.label(col), "cell": f"{get_column_letter(col)}{n}",
                                   "source": decimal_text(source), "calculated": calculated,
                                   "difference": decimal_text(difference), "matches": abs(difference) < Decimal(".0000001")})
        return result

    def cell(self, row, col):
        raw = self.raw(row, col)
        anchor = self.anchors.get((row, col))
        origin = self.raw(*anchor) if anchor else raw
        links = [url for url in layout._extract_links(origin) if urlsplit(url).scheme in {"http", "https"}]
        return {"address": f"{get_column_letter(col)}{row}", "label": self.label(col),
                "value": cell_value(raw), "text": self.display(row, col, inherit=True),
                "formula": raw.get("formula") if isinstance(raw, dict) else None,
                "inherited_from": f"{get_column_letter(anchor[1])}{anchor[0]}" if anchor else None,
                "links": links}

    def brief(self):
        return {"id": self.id, "name": self.name, "profile": self.profile, "warning": self.warning,
                "summary": self.summary(), "controls": self.controls(), "source_rows": len(self.rows)}


def sheets_for(sheets, config):
    from app.services.contract_workbook_sync import profile_for

    profiles = config.get("profiles", {})
    return [Sheet(s, profiles.get(s["id"]) if profiles else profile_for(s["name"])) for s in sheets]


def validate_snapshot(sheets, config):
    """A changed known schema cannot silently become zero business rows."""
    projected = sheets_for(sheets, config)
    for sheet in projected:
        if sheet.profile in layout.PROFILES and sheet.warning:
            raise bad_request(f"「{sheet.name}」表头变化，保留上次完整数据；请核对字段映射")
    return projected


def configured_source(db):
    """Resolve the one source for module queries and the board aggregator."""
    config = dict_service.get(db, CONFIG, "primary")
    if not config or not config.get("enabled"):
        return None
    wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == config.get("source_key")))
    # Fail closed: unavailable/unauthorized source must never fall back to the
    # old mixed-source dashboard, including while the sync itself is paused.
    if not wb or not wb.sheets:
        raise bad_request("固定来源尚无完整数据，请管理员检查同步状态")
    if config.get("docid") and wb.config.get("docid") != config["docid"]:
        raise bad_request("固定来源标识不一致，请管理员检查；未展示其他来源")
    _load_workbook_deletions(db, wb)
    return wb


def _load_workbook_deletions(db, wb):
    removals = list(db.execute(select(BusinessRecordDeletion.resource, BusinessRecordDeletion.record_key).where(
        BusinessRecordDeletion.resource.in_(("workbook-row", "workbook-receipt")),
        BusinessRecordDeletion.record_key.startswith(f"{wb.id}:"))))
    wb._removed_workbook_keys = frozenset(key for resource, key in removals if resource == "workbook-row")
    wb._removed_receipt_keys = frozenset(key for resource, key in removals if resource == "workbook-receipt")


def can_access_view(p, config, view):
    from app.services.contract_workbook_sync import can_view

    if view not in VIEWS or p is None or not any(p.has_menu(m) for m in MENUS[view]):
        return False
    if can_view(p, config):
        return True
    explicit = p.user.permission_override
    # A deliberate full-company module grant is authoritative for that module,
    # while raw workbook/source administration keeps its original bound-reader rules.
    return bool(explicit and p.data_scope == "all" and any(m in explicit.menus for m in MENUS[view]))


def active_source(db, p, view):
    if view not in VIEWS or not any(p.has_menu(m) for m in MENUS[view]):
        raise forbidden()
    wb = configured_source(db)
    if wb is not None and not can_access_view(p, wb.config, view):
        raise forbidden("当前账号尚未获得这份来源数据的查看范围，请管理员核对数据授权")
    return wb


def source_info(wb, p):
    from app.services.contract_workbook_sync import POLL_MINUTES

    age = (utcnow() - wb.checked_at).total_seconds() if wb.checked_at else None
    can_sync = bool(p and p.id == wb.config["reader_id"] and p.can("contracts:write"))
    result = {"key": wb.source_key, "title": wb.config["title"], "url": wb.config.get("url"),
            "revision": wb.revision, "checked_at": wb.checked_at, "synced_at": wb.synced_at,
            "last_error": wb.last_error, "enabled": wb.config.get("enabled", False),
            "stale": bool(wb.last_error or age is None or age > 300 or not wb.config.get("enabled")),
            "poll_minutes": POLL_MINUTES, "can_sync": can_sync,
            "authorization_required": bool(wb.authorization_required)}
    if can_sync and wb.authorization_url:
        result.update(authorization_url=wb.authorization_url, authorization_label=wb.authorization_label)
    return result


def projected_sheets(wb):
    removed = getattr(wb, "_removed_workbook_keys", frozenset())
    cache_key = (wb.revision, id(wb.sheets), tuple(sorted(wb.config.get("profiles", {}).items())), removed)
    cached = getattr(wb, "_business_projection", None)
    if not cached or cached[0] != cache_key:
        sheets = sheets_for(wb.sheets, wb.config)
        if removed:
            for sheet in sheets:
                identities = {number: workbook_identity(row) for number, row in sheet.rows.items() if not row.get("parent")}
                counts = Counter(identities.values())
                roots = {number for number, identity in identities.items() if identity and counts[identity] == 1
                         and workbook_deletion_key(wb.id, sheet.id, identity) in removed}
                sheet.removed_rows = frozenset(number for number, row in sheet.rows.items()
                                               if number in roots or row.get("parent") in roots)
                sheet.items = [row for row in sheet.source_items if row["row"] not in sheet.removed_rows]
        cached = (cache_key, sheets)
        wb._business_projection = cached
    return cached[1]


def module_sheets(wb, view):
    sheets = [s for s in projected_sheets(wb) if s.profile in VIEWS[view]]
    if view in {"contracts", "collections", "performance"}:
        sheets = [s for s in sheets if (s.profile == "sales" and re.search(r"(?:^|、|\.)2026年销售合同$", layout.norm(s.name)))
                  or (s.profile == "older" and "2026年前" in layout.norm(s.name))]
    if view in {"collections", "performance"}:
        sheets = [s for s in sheets if s.profile == "sales" or re.fullmatch(
            r"(?:\d+[、.])?2026年前\(已签单个项目\+未回款\)", layout.norm(s.name))]
    if view == "opportunities":
        sheets = [s for s in sheets if layout.norm(s.name) == layout.norm(OPPORTUNITY_SHEET)]
    return sheets


def opportunity_sheet(wb):
    sheets = module_sheets(wb, "opportunities")
    return sheets[0] if len(sheets) == 1 and not sheets[0].warning else None


def collection_sheets(wb):
    """An incomplete source cannot produce a combined collection total."""
    sheets = module_sheets(wb, "collections")
    if len(sheets) == 2 and {s.profile for s in sheets} == VIEWS["collections"] and not any(s.warning for s in sheets):
        return sorted(sheets, key=lambda s: s.profile != "sales")
    return []


def performance_rows(wb):
    """Year/source routing is enforced before any performance aggregation."""
    return [r for s in module_sheets(wb, "contracts") for r in s.items if not r["date_value"]
            or (s.profile == "sales" and r["date_value"].startswith("2026-"))
            or (s.profile == "older" and r["date_value"] < "2026-01-01")]


def module_rows(wb, view):
    """One read model shared by ledger pages and dashboard calculations."""
    if view == "opportunities":
        sheet = opportunity_sheet(wb)
        return sheet.items if sheet else []
    rows = [r for s in module_sheets(wb, view) for r in s.items]
    if view == "collections":
        from app.services.collection_amounts import apply
        return apply(wb, rows)
    return rows


def page(db, p, view, *, sheet_id="", keyword="", year="", page=1, size=30, raw=False,
         start="", end="", basis="", period="", status="", region="", customer="", owner="",
         business_line="", sign_month="", source_type="", collection_state="", period_kind="", contact="",
         unpaid_only=False, archive_category=""):
    wb = active_source(db, p, view)
    if wb is None:
        return {"active": False}
    if view == "contracts":
        from app.services.performance_workbook import page as performance_page
        from app.services.performance_details import decorate
        result = performance_page(wb, p, sheet_id=sheet_id, keyword=keyword, year=year, page=page, size=size,
                                raw=raw, start=start, end=end, region=region, customer=customer, owner=owner,
                                business_line=business_line, sign_month=sign_month, source_type=source_type, status=status)
        result['items'] = decorate(db, p, wb, result['items'])
        from app.core.timeutil import today, month_range, week_range
        from app.services.workbook_metrics import contract_metrics
        end_date = today()
        result["quick_stats"] = {key: {k: v for k, v in contract_metrics(wb, start_date, end_date).items() if k != "rows"}
                                 for key, start_date in (("month", month_range(ref=end_date)[0]), ("week", week_range(ref=end_date)[0]))}
        return result
    if view == 'collections':
        from app.services.collection_workbook import page as collection_page
        return collection_page(db, wb, p, sheet_id=sheet_id, keyword=keyword, year=year, page=page, size=size,
                               raw=raw, start=start, end=end, region=region, customer=customer, owner=owner,
                               sign_month=sign_month, source_type=source_type, collection_state=collection_state,
                               period_kind=period_kind, basis=basis, period=period, unpaid_only=unpaid_only)
    if view == "opportunities":
        from app.services.opportunity_workbook import page as opportunity_page
        return opportunity_page(db, wb, p, sheet_id=sheet_id, keyword=keyword, year=year, page=page, size=size, raw=raw,
                                start=start, end=end, status=status, owner=owner, contact=contact, archive_category=archive_category)
    sheets = [s for s in projected_sheets(wb) if view == "board" or s.profile in VIEWS[view]]
    result = {"active": True, "source": source_info(wb, p), "sheets": [s.brief() for s in sheets]}
    if view == "board":
        return result
    selected = next((s for s in sheets if s.id == sheet_id), None) if sheet_id and sheet_id != "all" else (
        next((s for s in sheets if s.profile == DEFAULTS[view]), sheets[0] if sheets else None))
    if selected is None:
        if sheet_id:
            raise not_found("此页面的来源子表")
        return {**result, "items": [], "total": 0, "sheet": None, "years": [], "page": 1}
    if year and year != "undated" and not re.fullmatch(r"(?:19|20|21)\d{2}", year):
        raise bad_request("年份筛选无效")
    items = module_rows(wb, view) if sheet_id == "all" else selected.items
    years = sorted({r["date_value"][:4] for r in items if r["date_value"]}, reverse=True)
    if keyword.strip():
        items = [r for r in items if layout.norm(keyword) in r["_search"]]
    if year:
        items = [r for r in items if (not r["date_value"] if year == "undated" else (r["date_value"] or "").startswith(year))]
    if start or end:
        from datetime import date
        try:
            if start: date.fromisoformat(start)
            if end: date.fromisoformat(end)
        except ValueError:
            raise bad_request("日期筛选无效") from None
        items = [r for r in items if r["date_value"] and (not start or r["date_value"] >= start)
                 and (not end or r["date_value"] <= end)]
    if status:
        items = [r for r in items if r.get("progress") == status]
    if view == "tender" and not raw:
        from app.services.workbook_project_feedback import categorize
        items = categorize(db, wb, view, sheets, items, archive_category)
    summary = selected.summary(items)
    if raw and sheet_id == "all":
        raise bad_request("逐格查看请先选择一张具体子表")
    if raw:
        # Raw view contains every occupied row, including totals, marine rebate
        # sections, supplementary documents, and presently unmapped fields.
        items = [{"row": n, "id": f"{selected.id}:{n}", "values": {
            str(c): selected.display(n, c, inherit=True) for c in selected.columns}}
            for n in selected.rows if n not in selected.removed_rows and (not keyword.strip() or any(
                layout.norm(keyword) in layout.norm(selected.display(n, c, inherit=True)) for c in selected.columns))]
        if year:
            raise bad_request("逐格查看保留原表全部内容，请先清除年份筛选")
    total = len(items)
    page = min(page, max(1, (total + size - 1) // size))
    brief = selected.brief() if sheet_id != "all" else {"id": "all", "name": "全部来源子表", "profile": selected.profile,
             "controls": [], "warning": None, "summary": summary}
    return {**result, "sheet": brief, "years": years, "summary": summary,
            "items": [{k: v for k, v in row.items() if k != "_search"} for row in items[(page - 1) * size:page * size]],
            "total": total, "page": page, "size": size,
            "columns": [{"key": str(c), "letter": get_column_letter(c), "label": selected.label(c)} for c in selected.columns]}


def detail(db, p, view, sheet_id, row_number, revision):
    wb = active_source(db, p, view)
    if not wb:
        raise not_found("固定来源")
    if revision != wb.revision:
        raise BizError(40901, "原表已有新版本，请刷新后重新查看对应行")
    if view == "opportunities":
        market = opportunity_sheet(wb)
        if not market or market.id != sheet_id or not market.rows.get(row_number, {}).get("record"):
            raise not_found("市场商机")
        sheets = [market]
    else:
        sheets = module_sheets(wb, view) if view in {"contracts", "collections"} else projected_sheets(wb)
    sheet = next((s for s in sheets if s.id == sheet_id and
                  (view == "board" or s.profile in VIEWS[view])), None)
    if not sheet or row_number not in sheet.rows or row_number in sheet.removed_rows:
        raise not_found("源表行")
    children = [n for n, row in sheet.rows.items() if row.get("parent") == row_number]
    result = {"sheet_name": sheet.name, "row": row_number, "revision": revision,
              "rows": [{"row": n, "cells": [sheet.cell(n, c) for c in sheet.columns]} for n in [row_number, *children]]}
    if view == "opportunities":
        result["item"] = {key: value for key, value in sheet.project(sheet.rows[row_number]).items() if not key.startswith("_")}
    return result
