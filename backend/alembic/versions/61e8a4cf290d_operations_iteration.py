"""Node evidence, task conversations, school visits and versioned digests.

Revision ID: 61e8a4cf290d
Revises: 14e5d891aff2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "61e8a4cf290d"
down_revision = "14e5d891aff2"
branch_labels = None
depends_on = None


def stamps():
    return [sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def fk(name, target, nullable=False, primary_key=False):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(target), nullable=nullable, primary_key=primary_key)


def upgrade():
    op.create_table("ops_task_node_update", fk("update_id", "ops_task_update.id", primary_key=True), fk("node_id", "ops_task_node.id"))
    op.create_index("ix_ops_task_node_update_node_id", "ops_task_node_update", ["node_id"])
    op.create_table("ops_task_discussion", *stamps(), fk("task_id", "ops_task.id"), fk("node_id", "ops_task_node.id", True),
                    fk("author_id", "sys_user.id"), sa.Column("kind", sa.String(12), nullable=False),
                    sa.Column("content", sa.Text(), nullable=False), fk("recipient_id", "sys_user.id", True),
                    fk("reply_to_id", "ops_task_discussion.id", True), sa.Column("resolved_at", sa.DateTime(timezone=True)))
    for name in ("task_id", "recipient_id"):
        op.create_index(f"ix_ops_task_discussion_{name}", "ops_task_discussion", [name])
    op.create_table("ops_school_visit", *stamps(), fk("school_id", "ops_school.id"), fk("author_id", "sys_user.id"),
                    sa.Column("occurred_on", sa.Date(), nullable=False), sa.Column("content", sa.Text(), nullable=False),
                    sa.Column("next_action", sa.Text(), nullable=False), sa.Column("next_contact_date", sa.Date()))
    op.create_index("ix_ops_school_visit_school_id", "ops_school_visit", ["school_id"])
    op.create_table("ops_digest_preference", fk("project_id", "ops_project.id", primary_key=True),
                    sa.Column("weekly", sa.Boolean(), nullable=False), sa.Column("monthly", sa.Boolean(), nullable=False),
                    fk("updated_by", "sys_user.id"))
    op.create_table("ops_digest_snapshot", *stamps(), fk("project_id", "ops_project.id"), fk("owner_id", "sys_user.id"),
                    sa.Column("audience", sa.String(12), nullable=False), sa.Column("mode", sa.String(8), nullable=False),
                    sa.Column("period", sa.String(12), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
                    fk("generated_by", "sys_user.id"), sa.Column("automatic", sa.Boolean(), nullable=False),
                    sa.Column("facts", postgresql.JSONB(), nullable=False), sa.Column("text", sa.Text(), nullable=False),
                    sa.UniqueConstraint("project_id", "owner_id", "audience", "mode", "period", "version", name="uq_ops_digest_version"))
    for name in ("project_id", "owner_id"):
        op.create_index(f"ix_ops_digest_snapshot_{name}", "ops_digest_snapshot", [name])


def downgrade():
    for table in ("ops_digest_snapshot", "ops_digest_preference", "ops_school_visit", "ops_task_discussion", "ops_task_node_update"):
        op.drop_table(table)
