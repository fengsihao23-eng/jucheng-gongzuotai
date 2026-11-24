"""Add sporadic collection projects and operations competitor records."""
from alembic import op
import sqlalchemy as sa

revision = "a6c261124f02"
down_revision = "f0d261123b62"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sporadic_collection_project",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("customer", sa.String(128), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("expected_amount", sa.Numeric(14, 2)),
        sa.Column("due_on", sa.Date()),
        sa.Column("requirements", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sporadic_collection_project_owner_id", "sporadic_collection_project", ["owner_id"])
    op.create_table(
        "ops_competitor",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("ops_project.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ops_competitor_project_id", "ops_competitor", ["project_id"])


def downgrade():
    op.drop_table("ops_competitor")
    # Remove timeline rows for the project IDs that this rollback drops.
    op.execute("DELETE FROM record_followup WHERE target_kind = 'sporadic-collection'")
    op.drop_table("sporadic_collection_project")
