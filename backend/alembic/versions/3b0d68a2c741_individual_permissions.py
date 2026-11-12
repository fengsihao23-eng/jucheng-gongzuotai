"""Individual authorization without changing shared role rules."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "3b0d68a2c741"
down_revision = "0fa1c25c190e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sys_user_permission",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("menus", postgresql.JSONB(), nullable=False),
        sa.Column("buttons", postgresql.JSONB(), nullable=False),
        sa.Column("data_scope", sa.String(8), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("sys_user.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("data_scope IN ('self', 'dept', 'all')", name="ck_user_permission_scope"),
    )


def downgrade():
    op.drop_table("sys_user_permission")
