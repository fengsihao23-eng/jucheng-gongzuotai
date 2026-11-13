"""Review an old aggregate receipt against a complete dated workbook breakdown.

Dry run by default. The same source adapter and transaction as recurring sync
are used; bank-linked, planned or formally confirmed payments are never edited.
"""
import argparse
import copy
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.core.audit import log_op, snapshot
from app.core.db import SessionLocal
from app.core.timeutil import utcnow
from app.models import BizPayment, ContractWorkbook, FinReceipt
from app.services import contract_workbook_source as source, contract_workbook_sync as sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key")
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--row", type=int, required=True)
    parser.add_argument("--payment-id", type=int, required=True)
    parser.add_argument("--expect-amount", type=Decimal, required=True)
    parser.add_argument("--expect-date", type=date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        sync.lock(db, args.key)
        wb = db.scalar(select(ContractWorkbook).where(ContractWorkbook.source_key == args.key).with_for_update())
        if not wb:
            raise ValueError("固定来源不存在")
        p = sync.reader(db, {**wb.config, "auto_receipts": True})
        revision, sheets = source.fetch_workbook(wb.config, None)
        preview = copy.deepcopy(sheets)
        sync.reconcile(db, wb, preview, p)
        tab = next(s for s in preview if s["id"] == args.sheet)
        row = next(r for r in tab["rows"] if r["row"] == args.row and r.get("record"))
        target = row.get("target") or {}
        assert target.get("kind") == "contract", "原表行尚未唯一对应合同"
        events = row["record"].get("receipt_events", [])
        assert len(events) >= 2, "原表未提供完整的逐笔到账明细"
        assert sum((Decimal(e["amount"]) for e in events), Decimal(0)) == Decimal(row["record"]["received"])
        assert all(date.fromisoformat(e["date"]) <= sync.today() for e in events)
        payments = list(db.scalars(select(BizPayment).where(BizPayment.contract_id == target["id"]).with_for_update()))
        assert len(payments) == 1 and payments[0].id == args.payment_id, "当前回款已变化"
        pay = payments[0]
        assert (pay.amount, pay.payment_date) == (args.expect_amount, args.expect_date), "原回款值与预期不符"
        assert pay.bank_flow_id is None and pay.plan_id is None and "汇总" in (pay.remark or ""), "原回款不是可校正的历史汇总"
        assert not db.scalar(select(FinReceipt.id).where(FinReceipt.payment_id == pay.id)), "回款已确认到账，需走正式更正"
        prefixes, cumulative = [], Decimal(0)
        for number, event in enumerate(events, 1):
            cumulative += Decimal(event["amount"])
            if abs(cumulative - pay.amount) <= Decimal("1.00"):
                prefixes.append((number, cumulative))
        assert len(prefixes) == 1 and prefixes[0][0] > 1, "无法唯一确认历史合计对应的逐笔记录"
        assert events[0]["date"] == pay.payment_date.isoformat(), "最早到账日与历史记录不符"
        plan = {"source_revision": revision, "payment_id": pay.id, "recorded": str(pay.amount),
                "source_subtotal": str(prefixes[0][1]), "cent_difference": str(prefixes[0][1] - pay.amount),
                "retained_event": events[0], "complete_schedule": events}
        if not args.apply:
            print(json.dumps({"dry_run": True, "plan": plan}, ensure_ascii=False))
            return
        before = snapshot(pay, ["amount", "payment_date", "remark"])
        pay.amount, pay.payment_date = Decimal(events[0]["amount"]), date.fromisoformat(events[0]["date"])
        pay.remark = f"企微原表 {args.key} 第{args.row}行：按完整逐笔记录校正历史合计，修正依据见审计。"
        log_op(db, p.id, "contract.workbook.receipt_repair", "payment", pay.id, before,
               {**snapshot(pay, ["amount", "payment_date", "remark"]), "evidence": plan})
        db.flush()
        wb.config = {**wb.config, "auto_receipts": True}
        summary, created, updated = sync.reconcile(db, wb, sheets, p, write=True)
        assert not created, "核对过程中出现了新合同或商机"
        actual = db.scalar(select(func.sum(BizPayment.amount)).where(BizPayment.contract_id == target["id"]))
        assert actual == Decimal(row["record"]["received"]), "校正后回款与原表仍有差异"
        wb.revision, wb.sheets, wb.summary = revision, sheets, summary
        wb.checked_at, wb.synced_at, wb.last_error = utcnow(), utcnow(), None
        log_op(db, p.id, "contract.workbook.sync", "contract_workbook", args.key,
               after={"revision": revision, "summary": summary, "repaired_payment": pay.id})
        db.commit()
        sync.contracts._invalidate()
        print(json.dumps({"applied": True, "plan": plan, "summary": summary, "updated": updated}, ensure_ascii=False))


if __name__ == "__main__":
    main()
