"""保留原始清单的未标价、精确单价和金额，增加导入防重记录。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c92fd610b8a4"
down_revision = "a61e8b92c430"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("ops_product", "model", existing_type=sa.String(128), type_=sa.Text(), existing_nullable=False)
    op.alter_column("ops_product", "reference_price", existing_type=sa.Numeric(14, 2), type_=sa.Numeric(18, 6), nullable=True)
    op.alter_column("ops_equipment", "unit_price", existing_type=sa.Numeric(14, 2), type_=sa.Numeric(18, 6), nullable=True)
    op.add_column("ops_equipment", sa.Column("recorded_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("ops_equipment", sa.Column("review_note", sa.Text(), server_default="", nullable=False))
    op.create_check_constraint("ck_ops_equipment_recorded_amount", "ops_equipment", "recorded_amount IS NULL OR recorded_amount >= 0")
    op.create_table("ops_import_batch",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("ops_project.id"), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("operator_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("project_id", "source_sha256"))
    op.create_index("ix_ops_import_batch_project_id", "ops_import_batch", ["project_id"])
    op.create_table("ops_project_income",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("ops_project.id"), nullable=False),
        sa.Column("income_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("operator_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.UniqueConstraint("project_id", "income_date"), sa.CheckConstraint("amount >= 0"))
    op.create_index("ix_ops_project_income_project_id", "ops_project_income", ["project_id"])


def downgrade():
    # 有精确价格、未标价或超长规格时拒绝有损回退。
    conn = op.get_bind()
    unsupported = conn.execute(sa.text("""SELECT
        (SELECT count(*) FROM ops_product WHERE reference_price IS NULL OR reference_price != round(reference_price, 2) OR length(model) > 128) +
        (SELECT count(*) FROM ops_equipment WHERE unit_price IS NULL OR unit_price != round(unit_price, 2) OR recorded_amount IS NOT NULL OR review_note != '') +
        (SELECT count(*) FROM ops_import_batch) + (SELECT count(*) FROM ops_project_income)""")).scalar()
    if unsupported:
        raise RuntimeError("现有导入资料需要新金额结构，不能执行有损回退")
    op.drop_table("ops_project_income")
    op.drop_table("ops_import_batch")
    op.drop_constraint("ck_ops_equipment_recorded_amount", "ops_equipment", type_="check")
    op.drop_column("ops_equipment", "review_note")
    op.drop_column("ops_equipment", "recorded_amount")
    op.alter_column("ops_equipment", "unit_price", existing_type=sa.Numeric(18, 6), type_=sa.Numeric(14, 2), nullable=False)
    op.alter_column("ops_product", "reference_price", existing_type=sa.Numeric(18, 6), type_=sa.Numeric(14, 2), nullable=False)
    op.alter_column("ops_product", "model", existing_type=sa.Text(), type_=sa.String(128), existing_nullable=False)
