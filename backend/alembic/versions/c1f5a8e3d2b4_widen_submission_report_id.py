"""放宽 oa_submission.wecom_report_id 长度（企微 journaluuid 65 位）。"""
from alembic import op
import sqlalchemy as sa

revision = "c1f5a8e3d2b4"
down_revision = "a7c4e2f9b1d3"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("oa_submission", "wecom_report_id", type_=sa.String(128), existing_nullable=True)


def downgrade():
    op.alter_column("oa_submission", "wecom_report_id", type_=sa.String(64), existing_nullable=True)
