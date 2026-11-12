"""Add task audiences, reversible archives and management collection history."""

import sqlalchemy as sa
from alembic import op

revision = "0fa1c25c190e"
down_revision = "c1f5a8e3d2b4"
branch_labels = None
depends_on = None


def _timestamps():
    return (sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def upgrade():
    op.create_table(
        "ops_task_policy",
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("ops_task.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("restricted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("kind", sa.String(16), server_default="general", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("archived_by", sa.BigInteger(), sa.ForeignKey("sys_user.id")),
        sa.Column("archive_reason", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint("kind IN ('general', 'outreach')", name="ck_ops_task_policy_kind"),
    )
    op.create_table(
        "ops_task_audience",
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("ops_task.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), primary_key=True),
    )
    op.create_table(
        "biz_collection_followup", *_timestamps(),
        sa.Column("contract_id", sa.BigInteger(), sa.ForeignKey("biz_contract.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_key", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("expected_on", sa.Date()),
        sa.Column("reply_to_id", sa.BigInteger(), sa.ForeignKey("sys_user.id")),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
        sa.Column("last_note", sa.Text(), server_default="", nullable=False),
        sa.UniqueConstraint("contract_id", "plan_key"),
    )
    op.create_index("ix_biz_collection_followup_contract_id", "biz_collection_followup", ["contract_id"])
    op.create_table(
        "biz_collection_entry", *_timestamps(),
        sa.Column("followup_id", sa.BigInteger(), sa.ForeignKey("biz_collection_followup.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("expected_on", sa.Date()),
        sa.Column("recipient_id", sa.BigInteger(), sa.ForeignKey("sys_user.id")),
        sa.CheckConstraint("kind IN ('note', 'request', 'reply')", name="ck_biz_collection_entry_kind"),
    )
    op.create_index("ix_biz_collection_entry_followup_id", "biz_collection_entry", ["followup_id"])
    op.create_table(
        "biz_project_follow_setting",
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("biz_project.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("paused", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade():
    for table in ("biz_project_follow_setting", "biz_collection_entry", "biz_collection_followup",
                  "ops_task_audience", "ops_task_policy"):
        op.drop_table(table)
