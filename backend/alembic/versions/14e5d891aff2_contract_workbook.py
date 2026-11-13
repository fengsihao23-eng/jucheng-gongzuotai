"""Durable source snapshot and business row links for the WeCom contract workbook."""
import sqlalchemy as sa
from alembic import op

from app.core.db import JSON

revision = "14e5d891aff2"
down_revision = "c7e4b1a90d52"
branch_labels = None
depends_on = None


def _timestamps():
    return (sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def upgrade():
    op.create_table(
        "contract_workbook", *_timestamps(),
        sa.Column("source_key", sa.String(48), nullable=False, unique=True),
        sa.Column("config", JSON, nullable=False),
        sa.Column("revision", sa.String(64)),
        sa.Column("sheets", JSON, nullable=False),
        sa.Column("summary", JSON, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
    )
    op.create_table(
        "contract_workbook_link", *_timestamps(),
        sa.Column("workbook_id", sa.BigInteger(), sa.ForeignKey("contract_workbook.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("sheet_id", sa.String(200), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("keys", JSON, nullable=False),
        sa.Column("baseline", JSON, nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("workbook_id", "kind", "target_id"),
    )
    op.create_index("ix_contract_workbook_link_workbook_id", "contract_workbook_link", ["workbook_id"])


def downgrade():
    op.drop_table("contract_workbook_link")
    op.drop_table("contract_workbook")
