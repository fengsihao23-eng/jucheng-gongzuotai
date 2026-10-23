"""wecom personal item snapshot

Revision ID: 9c1f2a7d4b10
Revises: f840cf0de8bd
Create Date: 2026-10-23 09:50:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c1f2a7d4b10"
down_revision: Union[str, Sequence[str], None] = "f840cf0de8bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sys_wecom_item",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("service", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "service", "external_id"),
    )
    op.create_index(op.f("ix_sys_wecom_item_user_id"), "sys_wecom_item", ["user_id"], unique=False)
    op.create_index(op.f("ix_sys_wecom_item_service"), "sys_wecom_item", ["service"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sys_wecom_item_service"), table_name="sys_wecom_item")
    op.drop_index(op.f("ix_sys_wecom_item_user_id"), table_name="sys_wecom_item")
    op.drop_table("sys_wecom_item")
