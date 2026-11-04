"""Sales invoices, receipts, allocations and source review queue.

Revision ID: 7b4d928e51a0
Revises: f2a6c8d1b4e7
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7b4d928e51a0"
down_revision = "f2a6c8d1b4e7"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade():
    money = sa.Numeric(14, 2)
    op.create_table("fin_sales_invoice", *_timestamps(),
                    sa.Column("approval_no", sa.String(64), unique=True),
                    sa.Column("contract_id", sa.BigInteger, sa.ForeignKey("biz_contract.id")),
                    sa.Column("customer", sa.String(128), nullable=False),
                    sa.Column("requested_amount", money, nullable=False),
                    sa.Column("invoice_no", sa.String(80), unique=True),
                    sa.Column("issued_on", sa.Date), sa.Column("issued_amount", money),
                    sa.Column("reduced_amount", money, nullable=False),
                    sa.Column("state", sa.String(16), nullable=False),
                    sa.Column("confirmed_by", sa.BigInteger, sa.ForeignKey("sys_user.id")),
                    sa.Column("note", sa.Text))
    op.create_table("fin_receipt", *_timestamps(),
                    sa.Column("reference", sa.String(120), nullable=False, unique=True),
                    sa.Column("approval_no", sa.String(64), unique=True),
                    sa.Column("contract_id", sa.BigInteger, sa.ForeignKey("biz_contract.id")),
                    sa.Column("payment_id", sa.BigInteger, sa.ForeignKey("biz_payment.id"), unique=True),
                    sa.Column("owns_payment", sa.Boolean, nullable=False),
                    sa.Column("amount", money, nullable=False), sa.Column("received_on", sa.Date, nullable=False),
                    sa.Column("payer", sa.String(128), nullable=False), sa.Column("account", sa.String(64), nullable=False),
                    sa.Column("kind", sa.String(16), nullable=False), sa.Column("state", sa.String(16), nullable=False),
                    sa.Column("confirmed_by", sa.BigInteger, sa.ForeignKey("sys_user.id")), sa.Column("note", sa.Text))
    op.create_table("fin_receipt_allocation", *_timestamps(),
                    sa.Column("receipt_id", sa.BigInteger, sa.ForeignKey("fin_receipt.id"), nullable=False),
                    sa.Column("invoice_id", sa.BigInteger, sa.ForeignKey("fin_sales_invoice.id"), nullable=False),
                    sa.Column("amount", money, nullable=False),
                    sa.UniqueConstraint("receipt_id", "invoice_id"), sa.CheckConstraint("amount > 0"))
    op.create_table("fin_receivables_source_row", *_timestamps(),
                    sa.Column("source_key", sa.String(80), nullable=False),
                    sa.Column("record_key", sa.String(120), nullable=False),
                    sa.Column("kind", sa.String(16), nullable=False),
                    sa.Column("payload", postgresql.JSONB, nullable=False),
                    sa.Column("version", sa.String(64), nullable=False),
                    sa.Column("state", sa.String(16), nullable=False),
                    sa.Column("applied_version", sa.String(64)), sa.Column("target_id", sa.BigInteger),
                    sa.Column("error", sa.Text), sa.UniqueConstraint("source_key", "record_key"))
    for table, fields in (
        ("fin_sales_invoice", ("contract_id", "issued_on")),
        ("fin_receipt", ("contract_id", "received_on")),
        ("fin_receipt_allocation", ("receipt_id", "invoice_id")),
        ("fin_receivables_source_row", ("state",)),
    ):
        for field in fields:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    for table in ("fin_receivables_source_row", "fin_receipt_allocation", "fin_receipt", "fin_sales_invoice"):
        op.drop_table(table)
