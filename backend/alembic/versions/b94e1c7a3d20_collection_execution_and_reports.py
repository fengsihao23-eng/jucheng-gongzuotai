"""Add collection execution problem field and saved collection stage reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "b94e1c7a3d20"
down_revision = "68eac92bf6d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workbook_collection_progress", sa.Column("problems", sa.Text(), nullable=True))
    op.create_table(
        "workbook_collection_report",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("workbook_id", sa.BigInteger(), sa.ForeignKey("contract_workbook.id"), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False, server_default="all"),
        sa.Column("owner_filter", sa.String(80), nullable=True),
        sa.Column("focus_prompt", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("facts", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    )
    op.create_index("ix_workbook_collection_report_workbook_id", "workbook_collection_report", ["workbook_id"])
    op.create_index("ix_workbook_collection_report_author_id", "workbook_collection_report", ["author_id"])
    op.execute(sa.text("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'jc_audit_install') THEN
            PERFORM public.jc_audit_install();
        END IF;
    END $$;
    """))


def downgrade():
    op.drop_index("ix_workbook_collection_report_author_id", table_name="workbook_collection_report")
    op.drop_index("ix_workbook_collection_report_workbook_id", table_name="workbook_collection_report")
    op.drop_table("workbook_collection_report")
    op.drop_column("workbook_collection_progress", "problems")
