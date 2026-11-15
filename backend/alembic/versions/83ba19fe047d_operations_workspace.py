"""Scoped work entry and annual outreach, with a reserved daily-income interface."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "83ba19fe047d"
down_revision = "61e8a4cf290d"
branch_labels = None
depends_on = None


def fk(name, target, primary=False):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(target), primary_key=primary, nullable=False)


def stamps():
    return [sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade():
    op.create_table("ops_member_view", fk("project_id", "ops_project.id", True), fk("user_id", "sys_user.id", True),
        sa.Column("sections", postgresql.JSONB(), nullable=False), sa.Column("task_scope", sa.String(12), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), fk("updated_by", "sys_user.id"))
    op.create_table("ops_project_control", fk("project_id", "ops_project.id", True),
        sa.Column("wecom_reminders", sa.Boolean(), nullable=False), sa.Column("business_project_code", sa.String(32)),
        sa.Column("report_audience", sa.String(12), nullable=False), sa.Column("report_user_ids", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), fk("updated_by", "sys_user.id"))
    op.create_table("ops_school_region", fk("school_id", "ops_school.id", True), sa.Column("region", sa.String(32), nullable=False))
    op.create_table("ops_outreach_goal", *stamps(), fk("project_id", "ops_project.id"), fk("school_id", "ops_school.id"),
        sa.Column("year", sa.Integer(), nullable=False), fk("task_id", "ops_task.id"), sa.Column("expected_annual_income", sa.Numeric(14, 2)),
        sa.UniqueConstraint("task_id"), sa.UniqueConstraint("project_id", "school_id", "year", name="uq_ops_school_annual_goal"))
    op.create_index("ix_ops_outreach_goal_project_id", "ops_outreach_goal", ["project_id"])
    op.create_table("ops_income_ingress_batch", *stamps(), fk("project_id", "ops_project.id"),
        sa.Column("source_key", sa.String(64), nullable=False), sa.Column("batch_key", sa.String(128), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False), sa.Column("result", postgresql.JSONB(), nullable=False),
        fk("operator_id", "sys_user.id"), sa.UniqueConstraint("project_id", "source_key", "batch_key", name="uq_ops_income_ingress_batch"))
    op.create_index("ix_ops_income_ingress_batch_project_id", "ops_income_ingress_batch", ["project_id"])


def downgrade():
    for table in ("ops_income_ingress_batch", "ops_outreach_goal", "ops_school_region", "ops_project_control", "ops_member_view"):
        op.drop_table(table)
