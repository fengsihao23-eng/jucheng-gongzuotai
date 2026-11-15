"""Read-only comparison of saved source rows and current ledgers."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal

from sqlalchemy import select

from app.core.deps import owner_scope
from app.core.timeutil import now
from app.models import BizContract, BizOpportunity, ContractWorkbook
from app.services import contract_workbook_sync as source


def reconcile(db, p):
    contracts = {r.id: r for r in db.scalars(select(BizContract).where(
        BizContract.is_deleted.is_(False), owner_scope(BizContract.owner_id, p)))}
    opportunities = {r.id: r for r in db.scalars(select(BizOpportunity).where(
        BizOpportunity.is_deleted.is_(False), owner_scope(BizOpportunity.owner_id, p)))}
    items, sources, linked = [], [], set()
    occurrences = Counter()
    for workbook in db.scalars(select(ContractWorkbook).order_by(ContractWorkbook.id)):
        if not source.can_view(p, workbook.config):
            continue
        sources.append({"key": workbook.source_key, "title": workbook.config["title"], "revision": workbook.revision,
                        "synced_at": workbook.synced_at, "checked_at": workbook.checked_at, "last_error": workbook.last_error})
        for sheet in workbook.sheets:
            for row in source._current_receipts(db, deepcopy(sheet.get("rows", []))):
                record = row.get("record")
                if not record:
                    continue
                target = row.get("target") or {}
                kind = target.get("kind")
                entry = (contracts if kind == "contract" else opportunities).get(target.get("id"))
                if target and not entry:
                    continue
                differences = []
                for field, label in (("name", "名称"), ("customer_name", "客户"), ("amount", "金额"), ("sign_date", "签订日期")):
                    if not entry or field not in record or record[field] is None or not hasattr(entry, field):
                        continue
                    actual, expected = getattr(entry, field), record[field]
                    if field == "amount":
                        differs = Decimal(str(actual)) != Decimal(str(expected))
                    else:
                        differs = str(actual or "").strip() != str(expected or "").strip()
                    if differs:
                        differences.append({"field": label, "source": str(expected), "ledger": str(actual)})
                receipts = row.get("receipts") or {}
                if receipts.get("difference") is not None and Decimal(str(receipts["difference"])) != 0:
                    differences.append({"field": "累计回款", "source": receipts.get("source"), "ledger": receipts.get("registered")})
                identity = (kind, target.get("id")) if entry else None
                if identity:
                    occurrences[identity] += 1
                if entry and kind == "contract":
                    linked.add(entry.id)
                items.append({"source_key": workbook.source_key, "source_title": workbook.config["title"],
                    "sheet_id": sheet["id"], "sheet": sheet["name"], "row": row["row"], "name": record.get("name", ""),
                    "target_id": target.get("id"), "kind": kind, "number": target.get("number"),
                    "source_amount": record.get("amount"), "ledger_amount": entry.amount if entry else None,
                    "sign_date": str(entry.sign_date) if entry and kind == "contract" else None,
                    "differences": differences, "notes": row.get("issues", []),
                    "unlinked": not bool(entry), "duplicate_source": False})
    for item in items:
        item["duplicate_source"] = occurrences[(item["kind"], item["target_id"])] > 1 if item["target_id"] else False
    return {"checked_at": now(), "sources": sources, "items": items,
            "summary": {"source_rows": len(items), "matched_contracts": len(linked),
                        "matched_amount": sum((contracts[i].amount for i in linked), Decimal(0)),
                        "difference_rows": sum(bool(r["differences"]) for r in items),
                        "unlinked": sum(r["unlinked"] for r in items),
                        "duplicate_source_rows": sum(r["duplicate_source"] for r in items)},
            "note": "按保存的企微来源版本与当前台账逐行核对，未修改任何金额。关联合同合计按合同 ID 去重，仅供来源核对，不作为当年签约业绩；缺字段和未对应行需由资料负责人确认。"}
