"""Retain removed finance records while allowing corrected files to be imported."""
from alembic import op
import sqlalchemy as sa

revision = "f0d261123b62"
down_revision = "c4d261123a15"
branch_labels = None
depends_on = None

TABLE = "fin_ledger_entry"


def upgrade():
    op.add_column(TABLE, sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column(TABLE, sa.Column("deleted_by", sa.BigInteger()))
    op.add_column(TABLE, sa.Column("delete_reason", sa.String(200)))
    op.create_foreign_key("fk_fin_ledger_entry_deleted_by", TABLE, "sys_user", ["deleted_by"], ["id"])
    op.create_index("ix_fin_ledger_entry_import_id", TABLE, ["import_id"])
    op.create_index("uq_fin_ledger_entry_active_identity", TABLE, ["identity"], unique=True,
                    postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_constraint("fin_ledger_entry_identity_key", TABLE, type_="unique")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM fin_ledger_entry WHERE deleted_at IS NOT NULL)")).scalar():
        raise RuntimeError("Cannot remove the deletion fields while the ledger retains deleted records")
    op.create_unique_constraint("fin_ledger_entry_identity_key", TABLE, ["identity"])
    op.drop_index("uq_fin_ledger_entry_active_identity", table_name=TABLE)
    op.drop_index("ix_fin_ledger_entry_import_id", table_name=TABLE)
    op.drop_constraint("fk_fin_ledger_entry_deleted_by", TABLE, type_="foreignkey")
    op.drop_column(TABLE, "delete_reason")
    op.drop_column(TABLE, "deleted_by")
    op.drop_column(TABLE, "deleted_at")
