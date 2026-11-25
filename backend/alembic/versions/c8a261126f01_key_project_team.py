"""Add project-local membership for key projects."""
from alembic import op
import sqlalchemy as sa

revision = "c8a261126f01"
down_revision = "d4a261125b01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "key_project_member",
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("biz_project.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_key_project_member_user_id", "key_project_member", ["user_id"])


def downgrade():
    op.drop_table("key_project_member")
