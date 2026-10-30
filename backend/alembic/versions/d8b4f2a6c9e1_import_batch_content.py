"""import batch extracted content (vision transcription cache)

Revision ID: d8b4f2a6c9e1
Revises: c5d8e2f1a7b3
Create Date: 2026-10-30 16:10:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8b4f2a6c9e1"
down_revision: Union[str, Sequence[str], None] = "c5d8e2f1a7b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 扫描件由大模型逐页识别的正文缓存：确认入库时直接复用，避免同一份 PDF 重复调用模型。
    with op.batch_alter_table("import_batch") as b:
        b.add_column(sa.Column("content", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("import_batch") as b:
        b.drop_column("content")
