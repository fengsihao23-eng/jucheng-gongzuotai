"""Keep existing projects in the ledger and enroll new General Projects explicitly."""
import sqlalchemy as sa
from alembic import op

revision = "f6e261127f08"
down_revision = "f5d261127f07"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("biz_project", sa.Column("in_general_workspace", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("biz_project", "in_general_workspace")
