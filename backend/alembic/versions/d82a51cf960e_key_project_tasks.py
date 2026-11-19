"""Key project task plans and phase feedback on existing milestones."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d82a51cf960e"
down_revision = "c71d4e9a205b"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade():
    op.create_table("key_project_task_plan",
        sa.Column("milestone_id", sa.BigInteger(), sa.ForeignKey("biz_project_milestone.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("preparation_on", sa.Date(), nullable=True), sa.Column("requirements", sa.Text(), nullable=False),
        sa.Column("cadence", sa.String(16), nullable=False), sa.Column("problems", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False), *timestamps())
    op.create_table("key_project_task_node", sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("milestone_id", sa.BigInteger(), sa.ForeignKey("biz_project_milestone.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False), sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("completed_on", sa.Date(), nullable=True), sa.Column("seq", sa.Integer(), nullable=False), *timestamps())
    op.create_index("ix_key_project_task_node_milestone_id", "key_project_task_node", ["milestone_id"])
    op.create_table("key_project_task_update", sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("milestone_id", sa.BigInteger(), sa.ForeignKey("biz_project_milestone.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feedback_id", sa.BigInteger(), sa.ForeignKey("biz_project_feedback.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("values", JSONB(), nullable=False), *timestamps())
    op.create_index("ix_key_project_task_update_milestone_id", "key_project_task_update", ["milestone_id"])
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_proc WHERE proname='jc_audit_install')
        THEN PERFORM public.jc_audit_install(); END IF; END $$;""")


def downgrade():
    op.drop_table("key_project_task_update")
    op.drop_table("key_project_task_node")
    op.drop_table("key_project_task_plan")
