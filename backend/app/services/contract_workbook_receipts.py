"""Register only an explicit, balanced source receipt schedule; never infer a delta date."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.response import BizError
from app.core.timeutil import today
from app.models import BizContract, BizPayment, FinReceipt
from app.services import contract_workbook_layout as layout, receivables_service as ledger


def apply(db, workbook, row, p):
    rec = row["record"]
    events = rec.get("receipt_events") or []
    if not events or row.get("target", {}).get("kind") != "contract":
        return {}
    if any(date.fromisoformat(e["date"]) > today() for e in events):
        row["issues"].append("原表到账日晚于今天，暂未登记")
        return {}
    contract_id = row["target"]["id"]
    try:
        with db.begin_nested():
            ledger.lock(db)
            contract = db.get(BizContract, contract_id, with_for_update=True, populate_existing=True)
            if not contract or contract.is_deleted:
                row["issues"].append("合同已停用，暂未同步到账")
                return {}
            payments = list(db.scalars(select(BizPayment).where(BizPayment.contract_id == contract_id)))
            desired = Counter((e["date"], Decimal(e["amount"])) for e in events)
            current = Counter((pay.payment_date.isoformat(), pay.amount) for pay in payments)
            if current - desired:
                row["issues"].append("原表到账明细与已有回款的日期或金额不同，暂未自动补账")
                return {}
            if sum((Decimal(e["amount"]) for e in events), Decimal(0)) > contract.amount:
                row["issues"].append("原表已收超过合同金额，暂未自动登记")
                return {}
            existing = defaultdict(list)
            for pay in payments:
                existing[pay.payment_date.isoformat(), pay.amount].append(pay)
            by_payment = {r.payment_id: r for r in db.scalars(select(FinReceipt).where(
                FinReceipt.payment_id.in_([pay.id for pay in payments])))} if payments else {}
            for pay in payments:
                if pay.id in by_payment and by_payment[pay.id].state != "confirmed":
                    row["issues"].append("已有到账凭证已撤销，暂未自动登记")
                    return {}
            result = {"new_payments": 0, "linked_payments": 0}
            for event in events:
                amount = Decimal(event["amount"])
                matches = existing[event["date"], amount]
                pay = matches.pop() if matches else None
                if pay and pay.id in by_payment:
                    continue
                reference = "WECOM-" + layout.key(workbook.id, contract_id, event["date"], event["amount"])[:32].upper()
                note = (f"来自企微固定台账「{workbook.config['title']}」来源 {workbook.source_key}，"
                        f"原表第{row['row']}行。日期及金额来自明确到账字段；付款方和收款账户未注明。"
                        "来源编号用于防重，不是银行回单号。")
                ledger.receipt(db, reference=reference, received_on=date.fromisoformat(event["date"]),
                               amount=amount, payer="原表未注明", account="原表未注明", kind="contract",
                               contract_no=contract.contract_no, payment_id=pay.id if pay else None, note=note,
                               actor=p.id)
                result["linked_payments" if pay else "new_payments"] += 1
            return result
    except BizError as exc:
        row["issues"].append("到账同步待核对：" + exc.message)
        return {}
