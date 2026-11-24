"""回款登记（共享）：流水核对确认回款、合同归档自动关联、人工登记都走这里。

- 回款金额按计划阶段顺序冲抵（预付→进度→验收→质保），更新阶段状态
- 一条流水只能生成一笔回款（bank_flow_id 唯一）
- 回款日期 = 流水到账日（PRD 3.4 口径）
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.response import conflict
from app.core.timeutil import today, utcnow
from app.models import BizContract, BizPayment, FinBankFlow, FinCashJournal, FinReceipt


def refresh_plan_status(contract: BizContract, ref: date | None = None) -> None:
    ref = ref or today()
    for p in contract.plans:
        if p.received_amount >= p.amount:
            p.status = 4
        elif p.received_amount > 0:
            p.status = 3
        elif p.due_date and p.due_date < ref:
            p.status = 5
        elif p.due_date and (p.due_date - ref).days <= 30:
            p.status = 2
        else:
            p.status = 1


def apply_to_plans(contract: BizContract, amount: Decimal) -> int | None:
    """把一笔回款冲抵到计划阶段，返回首个冲抵的 plan_id。"""
    first = None
    left = amount
    for p in contract.plans:
        due = p.amount - p.received_amount
        if due <= 0 or left <= 0:
            continue
        take = min(due, left)
        p.received_amount += take
        left -= take
        first = first or p.id
    refresh_plan_status(contract)
    return first


def has_confirmed_receipt(db, contract_id, amount, payment_date, *payers):
    who = {name.strip().lower() for name in payers if name and name.strip()}
    related = FinReceipt.contract_id == contract_id
    if who:
        related = or_(related, (FinReceipt.kind == "unmatched") & func.lower(FinReceipt.payer).in_(who))
    return db.scalar(select(FinReceipt.id).where(
        related, FinReceipt.received_on == payment_date,
        FinReceipt.amount == amount, FinReceipt.state == "confirmed").limit(1)) is not None


def register_payment(db: Session, contract: BizContract, amount: Decimal, payment_date: date, *,
                     flow: FinBankFlow | None = None, source_type: int = 2, user_id: int | None = None,
                     remark: str | None = None, write_journal: bool = True, verified_receipt: bool = False) -> BizPayment:
    db.execute(select(BizContract.id).where(BizContract.id == contract.id).with_for_update())
    if flow is not None:
        if db.scalar(select(BizPayment.id).where(BizPayment.bank_flow_id == flow.id)):
            raise conflict("该流水已登记过回款")
        if flow.direction != 1:
            raise conflict("只有收入流水可登记为回款")
        payment_date, amount = flow.trade_date, flow.amount
    if not verified_receipt and has_confirmed_receipt(db, contract.id, amount, payment_date,
                                                     contract.customer_name, flow.counterparty if flow else None):
        raise conflict("开票与收款台账已有同日同金额的相关到账，请先核对凭证，避免重复登记")
    pay = BizPayment(contract_id=contract.id, amount=amount, payment_date=payment_date,
                     bank_flow_id=flow.id if flow else None, register_by=user_id, source_type=source_type, remark=remark)
    pay.plan_id = apply_to_plans(contract, amount)
    db.add(pay)
    if flow is not None:
        flow.match_status, flow.biz_ref = 2, contract.contract_no
        flow.matched_by, flow.matched_at = user_id, utcnow()
        if write_journal and not db.scalar(select(FinCashJournal.id).where(FinCashJournal.bank_flow_id == flow.id)):
            db.add(FinCashJournal(journal_date=flow.trade_date, account_type="bank", bank_account=flow.bank_account,
                                  direction=1, amount=flow.amount, summary=flow.summary or f"{contract.customer_name} 回款",
                                  category="回款", project_code=contract.project_code, biz_ref=contract.contract_no,
                                  bank_flow_id=flow.id, register_by=user_id, source_type=1))
    db.flush()
    return pay


def contract_received(contract: BizContract) -> Decimal:
    return sum((p.amount for p in contract.payments), Decimal("0"))


def rebuild_plan_receipts(db: Session, contract: BizContract) -> None:
    db.expire(contract, ["payments", "plans"])
    for plan in contract.plans:
        plan.received_amount = Decimal("0")
    for payment in sorted(contract.payments, key=lambda row: (row.payment_date, row.id)):
        payment.plan_id = apply_to_plans(contract, payment.amount)
    refresh_plan_status(contract)


def plan_rows(contract: BizContract) -> list[dict]:
    names = {1: "未到期", 2: "待回款", 3: "部分回款", 4: "已回款", 5: "逾期"}
    return [{"id": p.id, "seq": p.seq, "stage_name": p.stage_name, "ratio": p.ratio, "amount": p.amount,
             "due_date": p.due_date, "received_amount": p.received_amount, "status": p.status,
             "status_name": names.get(p.status), "condition_text": p.condition_text,
             "overdue": bool(p.due_date and p.due_date < today() and p.amount > p.received_amount)} for p in contract.plans]


