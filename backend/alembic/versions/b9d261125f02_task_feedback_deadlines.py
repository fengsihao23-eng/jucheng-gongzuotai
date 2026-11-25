"""Add optional feedback deadlines to existing project tasks."""
from alembic import op
import sqlalchemy as sa

revision = "b9d261125f02"
down_revision = "a8c261125f01"
branch_labels = None
depends_on = None

TABLES = ("ops_task", "key_project_task_plan")


def upgrade():
    for table in TABLES:
        op.add_column(table, sa.Column("feedback_due_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("feedback_requested_at", sa.DateTime(timezone=True)))


def downgrade():
    for table in TABLES:
        op.drop_column(table, "feedback_requested_at")
        op.drop_column(table, "feedback_due_at")
