"""商机关联合同编号：赢单后能回溯到具体合同，打通商机→合同链路。"""
from alembic import op
import sqlalchemy as sa

revision = "c7e4b1a90d52"
down_revision = "3b0d68a2c741"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("biz_opportunity", sa.Column("contract_no", sa.String(32), nullable=True))
    op.create_index("ix_biz_opportunity_contract_no", "biz_opportunity", ["contract_no"])


def downgrade():
    op.drop_index("ix_biz_opportunity_contract_no", table_name="biz_opportunity")
    op.drop_column("biz_opportunity", "contract_no")
