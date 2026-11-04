"""单一入账服务：审批、人工确认、历史资料使用同一校验和唯一标识。"""
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.audit import log_op
from app.core.response import bad_request, conflict, not_found
from app.core.timeutil import today
from app.models import (BizContract, BizPayment, BizPaymentPlan, FinReceipt,
                        FinReceiptAllocation, FinSalesInvoice, SyncWecomApproval)
from app.services import payment_service

ZERO = Decimal("0.00")


def lock(db):
    # 所有开票、到账、核销和源行操作按事务串行；失败或提交自动释放。
    db.execute(text("select pg_advisory_xact_lock(73112260451)"))


def contract(db, contract_no):
    if not contract_no:
        return None
    c = db.scalar(select(BizContract).where(BizContract.contract_no == contract_no.strip(),
                                            BizContract.is_deleted.is_(False)).with_for_update())
    if not c:
        raise not_found("合同（请核对合同编号）")
    return c


def get(db, model, ident, label):
    row = db.get(model, ident)
    if not row:
        raise not_found(label)
    return row


def _sum(db, statement):
    return db.scalar(statement) or ZERO


def _allocated_query():
    return (select(func.sum(FinReceiptAllocation.amount)).select_from(FinReceiptAllocation)
            .join(FinReceipt, FinReceipt.id == FinReceiptAllocation.receipt_id)
            .where(FinReceipt.state == "confirmed"))


def invoice_allocated(db, ident):
    return _sum(db, _allocated_query().where(FinReceiptAllocation.invoice_id == ident))


def invoice_row(row, allocated=ZERO, contract_no=None):
    net = (row.issued_amount or ZERO) - row.reduced_amount
    return {"id": row.id, "approval_no": row.approval_no, "contract_id": row.contract_id,
            "contract_no": contract_no, "customer": row.customer, "requested_amount": row.requested_amount,
            "invoice_no": row.invoice_no, "issued_on": row.issued_on, "issued_amount": row.issued_amount,
            "reduced_amount": row.reduced_amount, "net": net, "allocated": allocated,
            "outstanding": net - allocated, "state": row.state, "note": row.note,
            "confirmed_by": row.confirmed_by, "updated_at": row.updated_at}


def receipt_row(row, allocated=ZERO, contract_no=None):
    return {"id": row.id, "reference": row.reference, "approval_no": row.approval_no,
            "contract_id": row.contract_id, "contract_no": contract_no, "payment_id": row.payment_id,
            "amount": row.amount, "received_on": row.received_on, "payer": row.payer, "account": row.account,
            "kind": row.kind, "state": row.state, "allocated": allocated,
            "unallocated": row.amount - allocated if row.state == "confirmed" else ZERO,
            "confirmed_by": row.confirmed_by, "note": row.note, "updated_at": row.updated_at}


def request_invoice(db, *, customer, amount, contract_no=None, approval_no=None, note=None, actor=None):
    lock(db)
    c = contract(db, contract_no)
    row = FinSalesInvoice(customer=customer, requested_amount=amount, contract_id=c.id if c else None,
                          approval_no=approval_no, state="pending", note=note)
    db.add(row)
    db.flush()
    log_op(db, actor, "receivables.invoice.request", "sales_invoice", row.id, after=invoice_row(row))
    return row


def issue(db, row, *, invoice_no, issued_on, amount, actor=None):
    lock(db)
    if issued_on > today():
        raise bad_request("开票日期不能晚于今天")
    invoice_no = invoice_no.strip().upper()
    if row.state != "pending":
        if (row.state, row.invoice_no, row.issued_on, row.issued_amount) == ("issued", invoice_no, issued_on, amount):
            return row
        raise conflict("开票已确认或已作废，请按更正流程处理")
    if row.approval_no:
        approval = db.scalar(select(SyncWecomApproval).where(SyncWecomApproval.sp_no == row.approval_no))
        if not approval or approval.status != 2 or approval.dispatch_status != 1:
            raise conflict("来源审批已撤回、变更或待核对，暂不能确认开票")
    if db.scalar(select(FinSalesInvoice.id).where(FinSalesInvoice.invoice_no == invoice_no)):
        raise conflict("发票号码已登记，请核对现有记录")
    if amount > row.requested_amount:
        raise conflict("实际开票金额超过申请金额，请核对后重新申请")
    before = invoice_row(row)
    row.invoice_no, row.issued_on, row.issued_amount = invoice_no, issued_on, amount
    row.state, row.confirmed_by = "issued", actor
    db.flush()
    log_op(db, actor, "receivables.invoice.issue", "sales_invoice", row.id, before, invoice_row(row))
    return row


def record_invoice(db, *, customer, invoice_no, issued_on, amount, contract_no=None, approval_no=None,
                   note=None, actor=None):
    lock(db)
    c = contract(db, contract_no)
    cid = c.id if c else None
    no = invoice_no.strip().upper()
    existing = db.scalar(select(FinSalesInvoice).where(FinSalesInvoice.invoice_no == no))
    if existing:
        if ((existing.state, existing.customer, existing.contract_id, existing.issued_on, existing.issued_amount)
                == ("issued", customer, cid, issued_on, amount) and
                (not approval_no or existing.approval_no == approval_no)):
            return existing
        raise conflict("该发票号码已使用，内容不一致；请核对原记录")
    if approval_no:
        row = db.scalar(select(FinSalesInvoice).where(FinSalesInvoice.approval_no == approval_no))
        if not row or (row.customer, row.contract_id) != (customer, cid):
            raise conflict("开票申请不存在或与客户、合同不一致")
    else:
        row = request_invoice(db, customer=customer, amount=amount, contract_no=contract_no, note=note, actor=actor)
    return issue(db, row, invoice_no=no, issued_on=issued_on, amount=amount, actor=actor)


def correct_invoice(db, row, *, reduced_total, reason, actor):
    lock(db)
    if row.state == "void" and reduced_total == row.reduced_amount:
        return row
    if row.state != "issued" or not row.reduced_amount <= reduced_total <= row.issued_amount:
        raise conflict("累计红冲金额必须在当前累计红冲与实际开票金额之间")
    allocated = invoice_allocated(db, row.id)
    if row.issued_amount - reduced_total < allocated:
        raise conflict("更正后金额低于已核销金额，请先调整核销")
    if row.reduced_amount == reduced_total:
        return row
    before = invoice_row(row, allocated)
    row.reduced_amount = reduced_total
    if row.issued_amount == reduced_total:
        row.state = "void"
    db.flush()
    log_op(db, actor, "receivables.invoice.correct", "sales_invoice", row.id,
           before, {**invoice_row(row, allocated), "reason": reason})
    return row


def receipt(db, *, reference, received_on, amount, payer, account, kind, contract_no=None,
            payment_id=None, note=None, approval_no=None, actor=None):
    lock(db)
    reference = reference.strip().upper()
    if received_on > today():
        raise bad_request("到账日期不能晚于今天")
    if (kind == "contract") != bool(contract_no):
        raise bad_request("只有合同回款须指定合同编号")
    c = contract(db, contract_no)
    cid = c.id if c else None
    existing = db.scalar(select(FinReceipt).where(FinReceipt.reference == reference))
    if existing:
        same = (existing.state, existing.received_on, existing.amount, existing.payer, existing.account,
                existing.kind, existing.contract_id) == ("confirmed", received_on, amount, payer, account, kind, cid)
        if not same or (payment_id is not None and existing.payment_id != payment_id):
            raise conflict("该到账凭证号已使用，内容不一致；请核对原记录")
        if approval_no and existing.approval_no not in (None, approval_no):
            raise conflict("该到账凭证号已绑定其他审批")
        if approval_no and not existing.approval_no:
            existing.approval_no = approval_no
            log_op(db, actor, "receivables.receipt.link_approval", "receipt", existing.id,
                   after={"approval_no": approval_no})
        return existing
    owns_payment = False
    if payment_id is not None:
        pay = db.scalar(select(BizPayment).where(BizPayment.id == payment_id).with_for_update())
        if not c or not pay or (pay.contract_id, pay.amount, pay.payment_date) != (cid, amount, received_on):
            raise conflict("已有回款的合同、金额、到账日必须完全一致")
        if db.scalar(select(FinReceipt.id).where(FinReceipt.payment_id == payment_id)):
            raise conflict("该已有回款已经关联到账记录")
    elif c:
        candidates = db.scalars(select(BizPayment.id).where(
            BizPayment.contract_id == cid, BizPayment.amount == amount, BizPayment.payment_date == received_on,
            ~select(FinReceipt.id).where(FinReceipt.payment_id == BizPayment.id).exists()).limit(10)).all()
        if candidates:
            raise conflict("发现同合同、同日同金额的旧回款，请核对后填写已有回款 ID",
                           {"payment_ids": candidates})
        pay = payment_service.register_payment(db, c, amount, received_on, user_id=actor, remark=(note or "")[:255],
                                               verified_receipt=True)
        payment_id, owns_payment = pay.id, True
    row = FinReceipt(reference=reference, received_on=received_on, amount=amount, payer=payer, account=account,
                     kind=kind, contract_id=cid, payment_id=payment_id, owns_payment=owns_payment,
                     approval_no=approval_no, confirmed_by=actor, note=note)
    db.add(row)
    db.flush()
    log_op(db, actor, "receivables.receipt.confirm", "receipt", row.id, after=receipt_row(row))
    return row


def assign_receipt(db, row, contract_no, actor):
    lock(db)
    if row.state != "confirmed" or row.kind != "unmatched":
        raise conflict("只能为已确认的待匹配收款关联合同")
    c = contract(db, contract_no)
    if db.scalar(select(BizPayment.id).where(BizPayment.contract_id == c.id, BizPayment.amount == row.amount,
                                             BizPayment.payment_date == row.received_on).limit(1)):
        raise conflict("已有同日同金额回款，请先核对现有记录")
    before = receipt_row(row)
    pay = payment_service.register_payment(db, c, row.amount, row.received_on, user_id=actor, remark=(row.note or "")[:255],
                                           verified_receipt=True)
    row.contract_id, row.payment_id, row.kind, row.owns_payment = c.id, pay.id, "contract", True
    db.flush()
    log_op(db, actor, "receivables.receipt.assign", "receipt", row.id, before, receipt_row(row))
    return row


def allocate(db, row, items, actor):
    lock(db)
    if row.state != "confirmed" or row.kind == "unmatched":
        raise conflict("待匹配或已撤销的收款不能核销发票")
    amounts = {i.invoice_id: i.amount for i in items}
    if len(amounts) != len(items) or sum(amounts.values(), ZERO) > row.amount:
        raise bad_request("发票不能重复，核销总额不能超过本笔实际到账")
    existing = {a.invoice_id: a for a in db.scalars(select(FinReceiptAllocation).where(
        FinReceiptAllocation.receipt_id == row.id))}
    before = {str(i): a.amount for i, a in existing.items()}
    for ident, amount in amounts.items():
        invoice = get(db, FinSalesInvoice, ident, "发票")
        if (invoice.state != "issued" or invoice.contract_id != row.contract_id
                or (row.contract_id is None and invoice.customer != row.payer)):
            raise conflict("只能核销同合同的有效发票；非合同收款须与发票客户一致")
        prev = existing[ident].amount if ident in existing else ZERO
        if invoice_allocated(db, ident) - prev + amount > invoice.issued_amount - invoice.reduced_amount:
            raise conflict(f"发票 {invoice.invoice_no} 核销后会超过有效开票金额")
    for ident, allocation in existing.items():
        if ident in amounts:
            allocation.amount = amounts.pop(ident)
        else:
            db.delete(allocation)
    for ident, amount in amounts.items():
        db.add(FinReceiptAllocation(receipt_id=row.id, invoice_id=ident, amount=amount))
    db.flush()
    log_op(db, actor, "receivables.receipt.allocate", "receipt", row.id, before,
           {str(i.invoice_id): i.amount for i in items})


def void_receipt(db, row, reason, actor):
    lock(db)
    if row.state == "void":
        return row
    before = receipt_row(row)
    if row.payment_id is not None:
        pay = get(db, BizPayment, row.payment_id, "合同回款")
        row.payment_id = None
        db.flush()
        if row.owns_payment:
            c = db.scalar(select(BizContract).where(BizContract.id == pay.contract_id).with_for_update())
            db.delete(pay)
            db.flush()
            db.expire(c, ["payments", "plans"])
            for plan in c.plans:
                plan.received_amount = ZERO
            for remaining in sorted(c.payments, key=lambda p: (p.payment_date, p.id)):
                remaining.plan_id = payment_service.apply_to_plans(c, remaining.amount)
            payment_service.refresh_plan_status(c)
    row.state = "void"
    db.flush()
    log_op(db, actor, "receivables.receipt.void", "receipt", row.id,
           before, {**receipt_row(row), "reason": reason})
    return row


def summary(db, start, end):
    from app.services.board_service import payment_sum

    net = FinSalesInvoice.issued_amount - FinSalesInvoice.reduced_amount
    issued = _sum(db, select(func.sum(net)).where(FinSalesInvoice.issued_on.between(start, end)))
    total_net = _sum(db, select(func.sum(net)).where(FinSalesInvoice.state == "issued"))
    allocated = _sum(db, _allocated_query().join(FinSalesInvoice, FinSalesInvoice.id == FinReceiptAllocation.invoice_id)
                     .where(FinSalesInvoice.state == "issued"))
    other = _sum(db, select(func.sum(FinReceipt.amount)).where(
        FinReceipt.state == "confirmed", FinReceipt.kind != "contract", FinReceipt.received_on.between(start, end)))
    received = payment_sum(db, start, end)
    remaining = BizPaymentPlan.amount - BizPaymentPlan.received_amount
    overdue = _sum(db, select(func.sum(remaining)).join(BizContract, BizContract.id == BizPaymentPlan.contract_id)
                   .where(BizContract.is_deleted.is_(False), BizContract.status != 3,
                          BizPaymentPlan.due_date < today(), remaining > 0))
    return {"net_issued": issued, "contract_received": received, "other_received": other,
            "total_received": received + other, "invoice_outstanding_now": total_net - allocated,
            "plan_overdue_now": overdue}


def invalidate():
    from app.services.board_service import invalidate as board_invalidate

    board_invalidate()
