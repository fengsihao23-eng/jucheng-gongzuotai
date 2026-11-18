"""Business rows in the signed-contract workbook; all other cells stay in the snapshot."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from openpyxl.utils.cell import range_boundaries

from app.services import line_rules, region_rules
from app.services.contract_workbook_source import cell_value, occupied

CENT = Decimal(".01")
EMPTY = {"", "/", "-", "—", "无", "无日期", "待定", "未知", "未填写", "暂无"}
TOTAL = re.compile(r"^(?:合计|小计|总计|汇总|更新时间|备注[：:]|序号)")
# Column numbers are 1 based. Header checks stop business projection if the
# template moves; the complete new source remains available for review.
PROFILES = {
    "sales": (2, {2: "项目/合同名称", 3: "对接单位", 4: "实际跟进人", 5: "我方签约主体", 6: "合同总金额",
                  8: "签订时间", 14: "已收款时间", 19: "所属区域业绩"}),
    "older": (2, {2: "项目名称", 3: "对接单位", 5: "实际跟进人", 6: "我方签约主体", 7: "合同总金额", 8: "签订时间"}),
    "pending": (1, {2: "项目名称", 4: "对接单位", 5: "我方签约主体", 6: "实际跟进人", 7: "合同金额"}),
    "tender": (1, {2: "责任人", 3: "招标项目", 9: "公司主体", 10: "中标金额预估", 11: "我方合同金额预估（减5个点）"}),
    "opportunity": (2, {1: "工单编号", 2: "工单模板", 3: "联系人", 4: "责任人", 5: "协同人",
                        6: "工单状态", 7: "工单主题", 8: "发起时间", 9: "描述"}),
    "mobile": (1, {2: "与中移合作项目类别", 3: "项目名称", 4: "合同金额（含税）", 7: "上游业主单位", 8: "上游签约主体"}),
    "longterm": (2, {2: "项目名称", 3: "挂靠单位", 5: "实际跟进人", 6: "我方签约主体", 7: "合同总金额", 8: "签订时间"}),
    "paid": (1, {2: "项目名称", 3: "挂靠单位", 5: "实际跟进人", 6: "我方签约主体", 7: "合同金额", 8: "签订日期", 9: "已收", 10: "收款日期"}),
}
CONTRACTS = {"sales", "older", "mobile", "longterm", "paid"}
STAGE = {"pending": "negotiation", "tender": "quote", "opportunity": "contact"}


def text(value):
    return str(value).strip() if value is not None else ""


def norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text(value))).casefold()


def present(value):
    s = text(value)
    return s if s not in EMPTY else None


def amount(value, *, estimate=False):
    if isinstance(value, bool) or not present(value):
        return None
    try:
        # Only accept amounts with a declared or unambiguous unit. A note with
        # several amounts, a range or an annual/monthly rate is not a posting.
        s = unicodedata.normalize("NFKC", text(value))
        if not re.fullmatch(r"[¥￥]?\s*[+-]?(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?:\s*(?:万元|万|元))?", s):
            return None
        multi = "万" in s
        d = Decimal(re.sub(r"[¥￥,\s万元]", "", s)) * (10000 if multi else 1)
        if not d.is_finite() or not 0 <= d < Decimal("1000000000000"):
            return None
        cents = d.quantize(CENT)
        return str(cents) if estimate or abs(d - cents) < Decimal("0.0000001") else None
    except (InvalidOperation, ValueError):
        return None


def day(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 36_526 <= value <= 73_050:
            d = date(1899, 12, 30) + timedelta(days=int(value))
        elif isinstance(value, (datetime, date)):
            d = value.date() if isinstance(value, datetime) else value
        else:
            m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|T\d{2}:\d{2}:\d{2}(?:\.\d+)?)?", text(value))
            if not m:
                return None
            d = date(*map(int, m.groups()))
        return d.isoformat() if 2000 <= d.year < 2100 else None
    except (TypeError, ValueError, OverflowError):
        return None


def key(*values):
    return hashlib.sha256(json.dumps([norm(v) for v in values], ensure_ascii=False).encode()).hexdigest()


def receipt_events(received, paid_on, note):
    if received is None or Decimal(received) <= 0:
        return []
    # A complete, explicitly dated breakdown takes precedence over a date
    # column that may mean "last receipt date".
    pattern = (r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*"
               r"(?:(?:已)?回款|已收到|已收款|已收)\s*"
               r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万元|元)")
    events = []
    for year, month, day_, number, unit in re.findall(pattern, text(note)):
        when = day(f"{year}-{month}-{day_}")
        total = amount(number + unit)
        if not when or not total or Decimal(total) <= 0:
            return []
        events.append({"date": when, "amount": total})
    if events:
        unique = {(e["date"], e["amount"]) for e in events}
        if len(unique) != len(events) or sum((Decimal(e["amount"]) for e in events), Decimal(0)) != Decimal(received):
            return []
        return sorted(events, key=lambda e: e["date"])
    return [{"date": paid_on, "amount": received}] if paid_on else []


def _extract_links(cell):
    if not isinstance(cell, dict):
        return []
    items = [cell, *(cell.get("rich_string") or [])] if isinstance(cell.get("rich_string"), list) else [cell]
    return [item["link"] for item in items if isinstance(item, dict) and isinstance(item.get("link"), str)]


def project_sheet(sheet, profile):
    if profile not in PROFILES:
        return "该子表尚未配置业务映射，全部内容仍可查看"
    header, checks = PROFILES[profile]
    by_row = {r["row"]: r["cells"] for r in sheet["rows"]}

    def raw(row, col):
        cells = by_row.get(row, [])
        return cells[col - 1] if col and col <= len(cells) else None

    def value(row, col):
        return cell_value(raw(row, col))

    def label(row, col):
        return norm(value(row, col))

    if any(label(header, col) != norm(title) and label(1, col) != norm(title) for col, title in checks.items()):
        return "子表表头已改变，已同步原表，请核对列的位置再更新业务台账"
    if profile in {"sales", "older", "longterm"} and (label(2, 9), label(2, 10)) != ("已收", "未收"):
        return "已收/未收列已改变，暂缓更新此子表的业务台账"

    merges = {}
    for area in sheet["merges"]:
        left, top, right, bottom = range_boundaries(area)
        if left == right and bottom > top and top > header:
            for row in range(top + 1, bottom + 1):
                merges[row, left] = top

    def merged(row, col):
        return value(merges.get((row, col), row), col) if col else None

    code_columns = [col for col in range(1, max(map(len, by_row.values()), default=0) + 1)
                    if {label(1, col), label(header, col)} & {"合同编号", "合同号", "同步编号"}]
    code_col = code_columns[0] if len(code_columns) == 1 else None
    if profile == "opportunity":
        code_col = 1
    mobile_end = next((n for n in sorted(by_row) if n > 1 and norm(value(n, 1)) == "序号"), None)
    if profile == "mobile" and mobile_end is None:
        return "与中移项目的分区标题缺失，已保留原表"
    records = []
    for row in sheet["rows"]:
        n = row["row"]
        if n <= header or (profile == "mobile" and n >= mobile_end):
            continue
        seq = value(n, 1)
        if profile in {"tender", "mobile"}:
            title_col, customer_col, owner_col, party_col, amount_col = (3, 8, 2, 9, 11) if profile == "tender" else (3, 8, 0, 7, 4)
        elif profile == "opportunity":
            title_col, customer_col, owner_col, party_col, amount_col = 7, 3, 4, 0, 0
        elif profile == "pending":
            title_col, customer_col, owner_col, party_col, amount_col = 2, 4, 6, 5, 7
        elif profile == "sales":
            title_col, customer_col, owner_col, party_col, amount_col = 2, 3, 4, 5, 6
        else:
            title_col, customer_col, owner_col, party_col, amount_col = 2, 3, 5, 6, 7
        title = present(merged(n, title_col))
        if not title or TOTAL.match(text(seq)) or TOTAL.match(title):
            continue
        if profile in {"sales", "older", "longterm", "paid"} and not present(value(n, title_col)):
            if not occupied(raw(n, amount_col)) or not present(value(n, amount_col)):
                # E.g. a second attachment or another receipt date under one
                # merged amount. Keep it in the source, without counting twice.
                row["parent"] = merges.get((n, title_col))
                continue
        if profile == "tender" and not present(value(n, title_col)):
            row["parent"] = merges.get((n, title_col))
            continue
        if profile not in {"opportunity", "tender"} and not present(merged(n, 1)):
            # Appended business rows may have no display sequence number.
            if not any(present(value(n, col)) for col in (customer_col, party_col, amount_col)):
                continue
        if profile == "mobile" and not re.fullmatch(r"\d+(?:\.0)?", text(seq)):
            continue
        if profile == "opportunity" and not present(value(n, 1)):
            continue
        customer, party, owner = (present(merged(n, col)) for col in (customer_col, party_col, owner_col))
        if profile == "opportunity" and customer in {"业主", "待确认"}:
            customer = None
        source_amount = value(n, amount_col)
        total = amount(source_amount, estimate=profile in STAGE)
        sign_raw = merged(n, 8) if profile in CONTRACTS - {"mobile"} else None
        sign_date = day(sign_raw)
        warnings = []
        if profile in CONTRACTS:
            if total is None or Decimal(total) <= 0:
                warnings.append("原表没有明确的正数合同金额")
            if not sign_date:
                warnings.append("原表缺少完整签订日期")
            if not customer:
                warnings.append("原表未填写客户")
        elif profile in {"pending", "tender"} and total is None:
            warnings.append("原表未填写明确的我方预计金额")
        received = amount(value(n, 9)) if profile in {"sales", "older", "longterm", "paid"} else None
        outstanding = amount(value(n, 10)) if profile in {"sales", "older", "longterm"} else None
        paid_on_raw = value(n, 14 if profile == "sales" else 10) if profile in {"sales", "paid"} else None
        paid_on = day(paid_on_raw)
        attachment_col = {"sales": 16, "older": 15, "longterm": 12, "paid": 11}.get(profile, 0)
        external = present(value(n, code_col))
        line, line_basis = line_rules.classify(title)
        region = present(value(n, 19)) if profile == "sales" else None
        if region and region not in region_rules.REGIONS:
            warnings.append("原表区域未在工作台字典中")
            region = None
        external = external or (str(seq).strip() if profile == "opportunity" else None)
        record = {
            "kind": "contract" if profile in CONTRACTS else "opportunity",
            "name": title, "customer_name": customer, "party": party, "owner": owner,
            "amount": total, "sign_date": sign_date, "business_line": line, "line_basis": line_basis,
            "region": region, "external": external, "received": received, "outstanding": outstanding,
            "paid_on": paid_on, "paid_on_text": present(paid_on_raw) if paid_on is None else None,
            "stage": STAGE.get(profile), "issues": warnings,
        }
        note_col = {"sales": 15, "older": 14, "longterm": 11, "paid": 10}.get(profile)
        record["receipt_events"] = receipt_events(received, paid_on, value(n, note_col)) if note_col else []
        if profile in CONTRACTS and received is not None and outstanding is not None and total is not None:
            if Decimal(received) + Decimal(outstanding) != Decimal(total):
                warnings.append("原表合同金额与已收、未收之和不同，已分别保留")
        record["exact"] = key(record["kind"], title, customer, party, total, sign_date)
        record["entity"] = key(record["kind"], title, customer, party)
        attachment = raw(n, attachment_col)
        record["attachment_keys"] = [key("file", link) for link in _extract_links(attachment)]
        record["attachment_title"] = present(cell_value(attachment))
        records.append(record)
        row["record"] = record
    # A shared file, work-order number or merged identity is not unique by
    # itself. Mark the keys that may safely follow a moved or edited row.
    keys = []
    for r in records:
        r["keys"] = candidates = [("exact", r["exact"]), ("entity", r["entity"])]
        if r["external"]:
            candidates.append(("external", key("external", r["external"])))
        candidates.extend(("attachment", item) for item in set(r["attachment_keys"]))
        keys.extend(candidates)
    counts = Counter(keys)
    for r in records:
        r["keys"] = [[kind, val] for kind, val in r["keys"] if counts[kind, val] == 1]
        del r["attachment_keys"]
    return None
