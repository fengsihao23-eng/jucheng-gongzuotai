"""Preserve human follow-up progress separately from source receipts."""
from alembic import op
import sqlalchemy as sa

revision = "e8920fc14ad6"
down_revision = "db4e82a761cf"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workbook_collection_progress",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("request_id", sa.BigInteger(), sa.ForeignKey("workbook_collection_request.id"), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text()),
        sa.Column("expected_on", sa.Date()))
    op.create_index("ix_workbook_collection_progress_request_id", "workbook_collection_progress", ["request_id"])


def downgrade():
    op.drop_table("workbook_collection_progress")
