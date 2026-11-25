"""Classify completed workbook projects without changing their source rows."""
from alembic import op
import sqlalchemy as sa

revision = "a8c261125f01"
down_revision = "e1ac261124b2"
branch_labels = None
depends_on = None


def upgrade():
    table = "workbook_project_feedback"
    op.add_column(table, sa.Column("archive_category", sa.String(16)))
    op.add_column(table, sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_workbook_project_feedback_archive", table,
        "(archive_category IS NULL AND archived_at IS NULL) OR "
        "(archive_category IS NOT NULL AND archive_category IN ('competitor', 'canceled', 'converted') "
        "AND archived_at IS NOT NULL AND status = 'done')")
    op.create_index("uq_workbook_project_feedback_archive", table, ["workbook_id", "sheet_id", "row_key"],
                    unique=True, postgresql_where=sa.text("archive_category IS NOT NULL"))


def downgrade():
    table = "workbook_project_feedback"
    op.drop_index("uq_workbook_project_feedback_archive", table_name=table)
    op.drop_constraint("ck_workbook_project_feedback_archive", table, type_="check")
    op.drop_column(table, "archived_at")
    op.drop_column(table, "archive_category")
