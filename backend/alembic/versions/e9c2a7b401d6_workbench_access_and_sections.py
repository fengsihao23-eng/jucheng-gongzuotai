"""Explicit workbench access and individual module sections."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e9c2a7b401d6"
down_revision = "d82a51cf960e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sys_user", sa.Column("workbench_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("sys_user", "workbench_enabled", server_default=sa.false())
    op.add_column("sys_user_permission", sa.Column("sections", JSONB(), nullable=True))


def downgrade():
    op.drop_column("sys_user_permission", "sections")
    op.drop_column("sys_user", "workbench_enabled")
