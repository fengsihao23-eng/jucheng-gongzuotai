"""Add workbook collection follow-up requests without changing source or ledger rows."""
from alembic import op
import sqlalchemy as sa

revision = "db4e82a761cf"
down_revision = "83ba19fe047d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workbook_collection_request",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("workbook_id", sa.BigInteger(), sa.ForeignKey("contract_workbook.id"), nullable=False),
        sa.Column("sheet_id", sa.String(200), nullable=False),
        sa.Column("row_key", sa.String(80), nullable=False),
        sa.Column("contract_name", sa.Text(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply", sa.Text()),
        sa.Column("expected_on", sa.Date()),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    )
    columns = ["workbook_id", "sheet_id", "row_key"]
    op.create_index("ix_workbook_collection_row", "workbook_collection_request", columns)
    op.create_index("uq_workbook_collection_pending", "workbook_collection_request", columns, unique=True,
                    postgresql_where=sa.text("replied_at IS NULL AND cancelled_at IS NULL"))


def downgrade():
    op.drop_table("workbook_collection_request")
