"""Add structured follow-up feedback while keeping existing visit records."""
from alembic import op
import sqlalchemy as sa

revision = "e2b261126f04"
down_revision = "d0a261126f03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ops_school_visit", sa.Column("status", sa.String(12), nullable=True))
    op.add_column("ops_school_visit", sa.Column("incomplete_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("ops_school_visit", sa.Column("client_id", sa.String(36), nullable=True))
    op.create_unique_constraint("uq_ops_school_visit_submission", "ops_school_visit", ["school_id", "author_id", "client_id"])


def downgrade():
    op.drop_constraint("uq_ops_school_visit_submission", "ops_school_visit", type_="unique")
    op.drop_column("ops_school_visit", "client_id")
    op.drop_column("ops_school_visit", "incomplete_reason")
    op.drop_column("ops_school_visit", "status")
