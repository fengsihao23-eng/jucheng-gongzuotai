"""Template-driven finance ledgers and confirmation drafts."""
from alembic import op
import sqlalchemy as sa

from app.core.db import JSON, Money

revision = "c4d261123a15"
down_revision = "c26b113bd519"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fin_ledger_import",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("token", sa.String(32), nullable=False, unique=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("draft", JSON, nullable=False),
        sa.Column("result", JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fin_ledger_import_owner_id", "fin_ledger_import", ["owner_id"])
    op.create_table(
        "fin_ledger_entry",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("import_id", sa.BigInteger(), sa.ForeignKey("fin_ledger_import.id"), nullable=False),
        sa.Column("source_sheet", sa.String(160), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("identity", sa.String(64), nullable=False, unique=True),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("ledger_date", sa.Date()),
        sa.Column("period", sa.String(7)),
        sa.Column("account", sa.String(128)),
        sa.Column("project", sa.String(128)),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("inflow", Money),
        sa.Column("outflow", Money),
        sa.Column("fee", Money),
        sa.Column("values", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fin_ledger_entry_kind_period", "fin_ledger_entry", ["kind", "period", "ledger_date"])


def downgrade():
    op.drop_table("fin_ledger_entry")
    op.drop_table("fin_ledger_import")
