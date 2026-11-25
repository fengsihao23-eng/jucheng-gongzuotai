"""Keep structured workbook fields on alarm maintenance records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c9a261126f02"
down_revision = "c8a261126f01"
branch_labels = None
depends_on = None


def upgrade():
    workbook_data = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column("ops_maintenance_record", sa.Column("workbook_row", workbook_data, nullable=True))


def downgrade():
    op.drop_column("ops_maintenance_record", "workbook_row")
