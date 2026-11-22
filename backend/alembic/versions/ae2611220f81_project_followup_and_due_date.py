"""Project feedback on workbook records and contractual collection deadlines."""
from alembic import op
import sqlalchemy as sa

revision = "ae2611220f81"
down_revision = "e9c2a7b401d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workbook_collection_reminder", sa.Column("payment_due_on", sa.Date()))
    op.create_table(
        "workbook_project_feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("workbook_id", sa.BigInteger(), sa.ForeignKey("contract_workbook.id"), nullable=False),
        sa.Column("sheet_id", sa.String(200), nullable=False),
        sa.Column("row_key", sa.String(80), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("problems", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("next_due_on", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_workbook_project_feedback_history", "workbook_project_feedback",
                    ["workbook_id", "sheet_id", "row_key", "id"])


def downgrade():
    op.drop_table("workbook_project_feedback")
    op.drop_column("workbook_collection_reminder", "payment_due_on")
