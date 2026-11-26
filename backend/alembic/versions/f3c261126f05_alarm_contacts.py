"""Add position and gender to customer maintenance contacts."""
from alembic import op
import sqlalchemy as sa

revision = "f3c261126f05"
down_revision = "e2b261126f04"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ops_maintenance_record", sa.Column("contact_position", sa.String(128), nullable=False, server_default=""))
    op.add_column("ops_maintenance_record", sa.Column("contact_gender", sa.String(16), nullable=False, server_default=""))


def downgrade():
    op.drop_column("ops_maintenance_record", "contact_gender")
    op.drop_column("ops_maintenance_record", "contact_position")
