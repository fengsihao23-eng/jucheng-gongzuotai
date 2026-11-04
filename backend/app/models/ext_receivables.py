"""经营台账：开票确认、实际到账、核销和外部资料的待处理记录。"""
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class FinSalesInvoice(TimestampMixin, Base):
    __tablename__ = "fin_sales_invoice"

    approval_no: Mapped[str | None] = mapped_column(String(64), unique=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("biz_contract.id"), index=True)
    customer: Mapped[str] = mapped_column(String(128))
    requested_amount: Mapped[Decimal]
    invoice_no: Mapped[str | None] = mapped_column(String(80), unique=True)
    issued_on: Mapped[date | None] = mapped_column(index=True)
    issued_amount: Mapped[Decimal | None]
    reduced_amount: Mapped[Decimal] = mapped_column(default=Decimal(0))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    note: Mapped[str | None] = mapped_column(Text)


class FinReceipt(TimestampMixin, Base):
    __tablename__ = "fin_receipt"

    reference: Mapped[str] = mapped_column(String(120), unique=True)
    approval_no: Mapped[str | None] = mapped_column(String(64), unique=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("biz_contract.id"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("biz_payment.id"), unique=True)
    owns_payment: Mapped[bool] = mapped_column(default=False)
    amount: Mapped[Decimal]
    received_on: Mapped[date] = mapped_column(index=True)
    payer: Mapped[str] = mapped_column(String(128))
    account: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # contract | unmatched | other
    state: Mapped[str] = mapped_column(String(16), default="confirmed")
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    note: Mapped[str | None] = mapped_column(Text)


class FinReceiptAllocation(TimestampMixin, Base):
    __tablename__ = "fin_receipt_allocation"
    __table_args__ = (UniqueConstraint("receipt_id", "invoice_id"), CheckConstraint("amount > 0"))

    receipt_id: Mapped[int] = mapped_column(ForeignKey("fin_receipt.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("fin_sales_invoice.id"), index=True)
    amount: Mapped[Decimal]


class FinReceivablesSourceRow(TimestampMixin, Base):
    __tablename__ = "fin_receivables_source_row"
    __table_args__ = (UniqueConstraint("source_key", "record_key"),)

    source_key: Mapped[str] = mapped_column(String(80))
    record_key: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    applied_version: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
