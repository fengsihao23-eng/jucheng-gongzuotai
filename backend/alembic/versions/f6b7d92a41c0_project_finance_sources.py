"""历史项目分析表的原件、合同关联、明细与对账。"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "f6b7d92a41c0"
down_revision = "d30a72fb619e"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade():
    op.create_table("fin_project_report", *timestamps(),
        sa.Column("source_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("period", sa.String(7), nullable=False, index=True),
        sa.Column("primary_project_code", sa.String(32), sa.ForeignKey("biz_project.project_code"), nullable=False, index=True),
        sa.Column("file_id", sa.BigInteger(), sa.ForeignKey("sys_file.id"), nullable=False),
        sa.Column("operator_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("issues", postgresql.JSONB(), nullable=False),
        sa.Column("source_cells", postgresql.JSONB(), nullable=False))
    op.create_table("fin_project_report_contract",
        sa.Column("report_id", sa.BigInteger(), sa.ForeignKey("fin_project_report.id"), primary_key=True),
        sa.Column("contract_id", sa.BigInteger(), sa.ForeignKey("biz_contract.id"), primary_key=True))
    op.create_table("fin_project_report_entry", *timestamps(),
        sa.Column("report_id", sa.BigInteger(), sa.ForeignKey("fin_project_report.id"), nullable=False, index=True),
        sa.Column("sheet", sa.String(64), nullable=False), sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, index=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, index=True),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("cost_id", sa.BigInteger(), sa.ForeignKey("biz_project_cost.id"), index=True),
        sa.Column("payment_id", sa.BigInteger(), sa.ForeignKey("biz_payment.id")),
        sa.Column("receipt_id", sa.BigInteger(), sa.ForeignKey("fin_receipt.id")),
        sa.UniqueConstraint("report_id", "sheet", "source_row"))


def downgrade():
    if op.get_bind().execute(sa.text("select count(*) from fin_project_report")).scalar():
        raise RuntimeError("已有历史项目报表，不能丢失来源链回退")
    op.drop_table("fin_project_report_entry")
    op.drop_table("fin_project_report_contract")
    op.drop_table("fin_project_report")
