"""Link new project progress entries to trackable plan tasks."""
from alembic import op
import sqlalchemy as sa

revision = "42db3ac9a061"
down_revision = "bc2e84a51f09"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ops_project_progress", sa.Column("task_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_ops_project_progress_task_id", "ops_project_progress", "ops_task", ["task_id"], ["id"])
    op.create_unique_constraint("uq_ops_project_progress_task_id", "ops_project_progress", ["task_id"])


def downgrade():
    if op.get_bind().execute(sa.text("select 1 from ops_project_progress where task_id is not null limit 1")).scalar():
        raise RuntimeError("已有项目计划关联，不能删除关联后回退")
    op.drop_constraint("uq_ops_project_progress_task_id", "ops_project_progress", type_="unique")
    op.drop_constraint("fk_ops_project_progress_task_id", "ops_project_progress", type_="foreignkey")
    op.drop_column("ops_project_progress", "task_id")
