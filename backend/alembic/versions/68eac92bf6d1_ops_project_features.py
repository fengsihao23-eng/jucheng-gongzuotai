"""Store each project's standard feature selection without converting old projects."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "68eac92bf6d1"
down_revision = "a721b0c59e64"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ops_project_features",
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("ops_project.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sections", JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("legacy_campus_income", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute(sa.text("SELECT public.jc_audit_install()"))


def downgrade():
    op.drop_table("ops_project_features")
