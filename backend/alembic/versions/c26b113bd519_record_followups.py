"""Add the shared business record timeline."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c26b113bd519"
down_revision = "ae2611220f81"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "record_followup",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("target_kind", sa.String(45), nullable=False),
        sa.Column("target_key", sa.String(64), nullable=False),
        sa.Column("reference", JSONB(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), sa.ForeignKey("sys_user.id")),
        sa.Column("reply_to_id", sa.BigInteger(), sa.ForeignKey("record_followup.id"), unique=True),
        sa.Column("legacy_key", sa.String(45)),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12)),
        sa.Column("problems", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("next_due_on", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("author_id", "client_id", name="uq_record_followup_submission"),
    )
    op.create_index("ix_record_followup_history", "record_followup", ["target_kind", "target_key", "id"])
    op.create_index("ix_record_followup_recipient", "record_followup", ["recipient_id", "id"])


def downgrade():
    op.drop_table("record_followup")
