"""Add append-only operating project progress reports."""
from alembic import op
import sqlalchemy as sa

revision = "ef42c1b08d7a"
down_revision = "f6b7d92a41c0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ops_project_progress",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("ops_project.id"), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("progress", sa.Text(), nullable=False),
        sa.Column("problems", sa.Text(), nullable=False),
        sa.Column("next_plan", sa.Text(), nullable=False),
        sa.Column("next_due_date", sa.Date()),
        sa.CheckConstraint("length(trim(progress)) > 0", name="ck_ops_project_progress_content"),
        sa.CheckConstraint("(next_due_date IS NULL AND next_plan = '') OR (next_due_date IS NOT NULL AND next_plan <> '')",
                           name="ck_ops_project_progress_plan_date"),
    )
    op.create_index("ix_ops_project_progress_project_id_id", "ops_project_progress", ["project_id", "id"])


def downgrade():
    if op.get_bind().execute(sa.text("select 1 from ops_project_progress limit 1")).scalar():
        raise RuntimeError("已有项目进度记录，不能直接删除历史记录回退")
    op.drop_table("ops_project_progress")
