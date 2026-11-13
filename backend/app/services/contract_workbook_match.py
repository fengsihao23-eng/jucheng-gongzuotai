"""Reconcile the workbook with existing contract and opportunity IDs."""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from difflib import SequenceMatcher

from app.services.contract_workbook_layout import norm

CONTRACT_NUM = re.compile(r"(?<![a-z0-9])(?:cmgd|cmtt|cmcc)[-a-z0-9]+\d{5,}", re.I)
LEGACY_ROW = re.compile(r"子表(?:[「『])?([1-7]|与中移项目)[^；\n]{0,20}?\s*第\s*(\d+)\s*行")
PENDING_CUSTOMERS = {"", "待定", "待确认", "无", "/"}
SHEET_LABELS = {"sales": "1", "older": "2", "pending": "3", "tender": "4",
                "opportunity": "5", "mobile": "与中移项目", "longterm": "6", "paid": "7"}


def comparable(value):
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal(".01")))
    return value.isoformat() if hasattr(value, "isoformat") else value


def _name(value):
    # Ignore punctuation and spaces, while keeping school numbers and all
    # Chinese characters. In particular, 一中 and 三中 are distinct.
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", norm(value))


def _near(a, b):
    a, b = _name(a), _name(b)
    return min(len(a), len(b)) >= 12 and SequenceMatcher(None, a, b, autojunk=False).ratio() >= .92


def _numbers(value):
    return {x.upper() for x in CONTRACT_NUM.findall(str(value or ""))}


class Candidates:
    def __init__(self, entries, links):
        self.entries = entries
        self.by_id = {(row["kind"], row["id"]): row for row in entries}
        self.by_name, self.by_price_date, self.by_code, self.by_note = (defaultdict(list) for _ in range(4))
        for row in entries:
            kind = row["kind"]
            self.by_name[kind, _name(row["name"])].append(row)
            if kind == "contract":
                self.by_price_date[row["amount"], row["sign_date"]].append(row)
            for identifier in {str(row["number"]).upper(), *_numbers(row["name"])}:
                self.by_code[kind, identifier].append(row)
            if str(row.get("source_approval_no") or "").startswith("S") and kind == "opportunity":
                self.by_code[kind, row["source_approval_no"].upper()].append(row)
            note = row.get("remark") or ""
            if "已签合同台账" in note or "源表" in note or str(row.get("source_approval_no", "")).startswith("wecom:"):
                match = LEGACY_ROW.search(note)
                if match:
                    self.by_note[kind, match[1], int(match[2])].append(row)
        self.links = {l.id: l for l in links}
        self.link_by_key = defaultdict(set)
        for link in links:
            for mode, value in link.keys:
                self.link_by_key[link.sheet_id, mode, value].add(link.id)

    def previous(self, sid, record):
        matches = defaultdict(set)
        for mode, value in record["keys"]:
            matches[mode].update(self.link_by_key[sid, mode, value])
        independent_files = {ident for ident in matches["attachment"] if
                             _name(self.links[ident].baseline.get("name")) == _name(record["name"]) or
                             (record["customer_name"] and
                              norm(self.links[ident].baseline.get("customer_name")) == norm(record["customer_name"]))}
        strong = matches["external"] | matches["exact"] | independent_files
        if strong and matches["entity"] - strong:
            return None, True
        possible = strong or matches["entity"]
        if len(possible) == 1:
            return self.links[next(iter(possible))], False
        return None, bool(possible)

    def initial(self, profile, row_number, rec):
        kind = rec["kind"]
        same_name = self.by_name[kind, _name(rec["name"])]
        same_customer = [o for o in same_name if norm(o["customer_name"]) == norm(rec["customer_name"])]
        money, signed = rec["amount"], rec["sign_date"]
        same_date_and_money = self.by_price_date[money, signed] if signed else []
        by_coord = self.by_note[kind, SHEET_LABELS.get(profile), row_number]
        external = _numbers(rec["name"])
        if rec["external"] and any(k == "external" for k, _ in rec["keys"]):
            external.add(rec["external"].upper())
        exact_codes = {o["id"]: o for no in external for o in self.by_code[kind, no]}
        # Prefer independently corroborated evidence over row coordinates.
        # Previous manual imports stored the row only as a locator.
        verified_coord = []
        for o in by_coord:
            note = o.get("remark") or ""
            exact_name = _name(o["name"]) == _name(rec["name"])
            same_account = norm(o["customer_name"]) == norm(rec["customer_name"])
            file_title = re.sub(r"\.(?:pdf|docx?|xlsx?)$", "", rec.get("attachment_title") or "", flags=re.I)
            if kind == "opportunity":
                if exact_name and (profile != "opportunity" or not rec["external"] or
                                   re.search(r"工单\s*" + re.escape(rec["external"]) + r"\b", note, re.I)):
                    verified_coord.append(o)
            elif money is not None and o["amount"] == money and (
                (exact_name and (same_account or signed == o["sign_date"] or not rec["customer_name"]))
                or (_name(o["name"]).startswith(_name(rec["name"])) and
                    (same_account or signed == o["sign_date"]))
                or (signed and signed == o["sign_date"] and "名称取自原始文件名" in note)
                or (file_title and _name(file_title) == _name(o["name"]) and (same_account or "所属项目行" in note))
            ):
                verified_coord.append(o)
        if kind == "contract":
            exact = [o for o in same_customer if money and o["amount"] == money
                     and (not signed or o["sign_date"] == signed)]
            alternate = [o for o in same_date_and_money if _name(o["name"]) == _name(rec["name"])
                         or (norm(o["customer_name"]) == norm(rec["customer_name"]) and _near(o["name"], rec["name"]))]
        else:
            exact = [o for o in same_customer if norm(rec["customer_name"]) not in PENDING_CUSTOMERS]
            alternate = []
        for choices, method in ((list(exact_codes.values()), "编号"), (verified_coord, "历史来源及业务字段"),
                                (exact, "业务字段"), (alternate, "日期、金额和名称")):
            active = [o for o in choices if not o["is_deleted"]]
            if len(active) == 1:
                return active[0], method, []
            if len(active) > 1:
                return None, None, active
            if choices and method == "编号" and rec["external"]:
                return None, None, choices
        # These are only review hints; never use similarity or the row number
        # alone to join a record or to overwrite a different contract.
        near = [o for o in same_date_and_money if _near(o["name"], rec["name"])]
        if kind == "contract":
            # A long-term contract series can share a title across many schools.
            # A different named customer alone does not make them duplicates.
            close = [o for o in same_name if not rec["customer_name"] or not o["customer_name"] or (
                norm(o["customer_name"]) == norm(rec["customer_name"]))]
        else:
            close = same_name
        possible = {o["id"]: o for o in (*exact_codes.values(), *exact, *by_coord, *alternate, *near, *close)
                    if not o["is_deleted"]}
        return None, None, list(possible.values()) or [o for o in exact if o["is_deleted"]]
