"""Link uploaded files to the feedback record that was submitted with them."""
import sqlalchemy as sa
from alembic import op

revision = "f5d261127f07"
down_revision = "f4c261126f06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feedback_attachment",
        sa.Column("file_id", sa.BigInteger(), sa.ForeignKey("sys_file.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_feedback_attachment_record", "feedback_attachment", ["record_type", "record_id", "file_id"])


def downgrade():
    op.drop_table("feedback_attachment")
