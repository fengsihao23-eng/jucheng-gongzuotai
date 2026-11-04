"""contract region (所属区域业绩)

Revision ID: f2a6c8d1b4e7
Revises: d8b4f2a6c9e1
Create Date: 2026-11-02 17:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6c8d1b4e7"
down_revision: Union[str, Sequence[str], None] = "d8b4f2a6c9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("biz_contract", sa.Column("region", sa.String(length=16), nullable=True))
    op.create_index("ix_biz_contract_region", "biz_contract", ["region"])


def downgrade() -> None:
    op.drop_index("ix_biz_contract_region", table_name="biz_contract")
    op.drop_column("biz_contract", "region")
