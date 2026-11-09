"""Preserve the first effective deadline and record delay decisions."""
from alembic import op
import sqlalchemy as sa

revision = "bc2e84a51f09"
down_revision = "a7c4e2f9b1d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ops_task", sa.Column("original_due_date", sa.Date(), nullable=True))
    op.execute("""
        WITH assigned AS (
            SELECT DISTINCT ON (target_id) target_id, "after"->>'due_date' AS due
            FROM log_operation
            WHERE action = 'operations.task.create' AND target_type = 'ops_task'
            ORDER BY target_id, id
        )
        UPDATE ops_task AS t SET original_due_date = assigned.due::date
        FROM assigned
        WHERE t.id::text = assigned.target_id AND t.source = 'company'
          AND assigned.due ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND pg_input_is_valid(assigned.due, 'date')
    """)
    op.execute("""
        WITH approved AS (
            SELECT DISTINCT ON (task_id) task_id, task_snapshot->>'due_date' AS due
            FROM ops_task_review WHERE stage = 'company' AND decision = 'approve'
            ORDER BY task_id, id
        )
        UPDATE ops_task AS t SET original_due_date = approved.due::date
        FROM approved
        WHERE t.id = approved.task_id AND t.source = 'self'
          AND approved.due ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND pg_input_is_valid(approved.due, 'date')
    """)
    # Where no original record survives, freeze the currently recorded date as the baseline.
    op.execute("UPDATE ops_task SET original_due_date = due_date WHERE original_due_date IS NULL AND approval_status = 'approved'")
    op.create_table(
        "ops_task_delay",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("ops_task.id"), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("original_due_date", sa.Date(), nullable=False),
        sa.Column("reviewer_id", sa.BigInteger(), sa.ForeignKey("sys_user.id")),
        sa.Column("decision", sa.String(16)),
        sa.Column("review_comment", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_ops_task_delay_reason"),
        sa.CheckConstraint(
            "(decision IS NULL AND reviewer_id IS NULL AND reviewed_at IS NULL) OR "
            "(decision IS NOT NULL AND decision IN ('accepted', 'rejected') AND reviewer_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND reviewer_id <> author_id)", name="ck_ops_task_delay_decision",
        ),
    )
    op.create_index("ix_ops_task_delay_task_id_id", "ops_task_delay", ["task_id", "id"])


def downgrade():
    if op.get_bind().execute(sa.text("select 1 from ops_task_delay limit 1")).scalar():
        raise RuntimeError("已有延期原因或复核记录，不能直接删除历史记录回退")
    op.drop_table("ops_task_delay")
    op.drop_column("ops_task", "original_due_date")
