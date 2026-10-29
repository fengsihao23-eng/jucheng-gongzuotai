"""wecom inbound messages + import batch channel / archive

Revision ID: c5d8e2f1a7b3
Revises: b3e7a1c9d2f4
Create Date: 2026-10-30 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5d8e2f1a7b3"
down_revision: Union[str, Sequence[str], None] = "b3e7a1c9d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sys_wecom_inbound",
        sa.Column("msg_id", sa.String(length=96), nullable=False),
        sa.Column("wecom_userid", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("msg_type", sa.String(length=16), nullable=False),
        sa.Column("preview", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batch.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("msg_id"),
    )
    op.create_index(op.f("ix_sys_wecom_inbound_user_id"), "sys_wecom_inbound", ["user_id"], unique=False)
    op.create_index(op.f("ix_sys_wecom_inbound_wecom_userid"), "sys_wecom_inbound", ["wecom_userid"], unique=False)
    with op.batch_alter_table("import_batch") as b:
        b.add_column(sa.Column("channel", sa.String(length=8), nullable=False, server_default="web"))
        b.add_column(sa.Column("archive_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("import_batch") as b:
        b.drop_column("archive_name")
        b.drop_column("channel")
    op.drop_table("sys_wecom_inbound")
