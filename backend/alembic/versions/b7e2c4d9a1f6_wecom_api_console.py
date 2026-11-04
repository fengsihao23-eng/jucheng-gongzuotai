"""wecom api console audit

Revision ID: b7e2c4d9a1f6
Revises: 7b4d928e51a0
Create Date: 2026-11-04 15:40:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e2c4d9a1f6"
down_revision: Union[str, Sequence[str], None] = "7b4d928e51a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sys_wecom_api_call",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("doc_path", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("credential", sa.String(length=16), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("errcode", sa.Integer(), nullable=True),
        sa.Column("errmsg", sa.String(length=300), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("request_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_bytes", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sys_wecom_api_call_user_id"), "sys_wecom_api_call", ["user_id"], unique=False)
    op.create_index(op.f("ix_sys_wecom_api_call_doc_path"), "sys_wecom_api_call", ["doc_path"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sys_wecom_api_call_doc_path"), table_name="sys_wecom_api_call")
    op.drop_index(op.f("ix_sys_wecom_api_call_user_id"), table_name="sys_wecom_api_call")
    op.drop_table("sys_wecom_api_call")
