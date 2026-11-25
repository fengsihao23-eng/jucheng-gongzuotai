"""Keep the official WeCom workbook authorization channel in the workbench."""
from alembic import op
import sqlalchemy as sa

revision = "d4a261125b01"
down_revision = "b9d261125f02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("contract_workbook", sa.Column("authorization_url", sa.Text()))
    op.add_column("contract_workbook", sa.Column("authorization_label", sa.String(120)))
    op.add_column(
        "contract_workbook",
        sa.Column("authorization_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("contract_workbook", "authorization_required")
    op.drop_column("contract_workbook", "authorization_label")
    op.drop_column("contract_workbook", "authorization_url")
