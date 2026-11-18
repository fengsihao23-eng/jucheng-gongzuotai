"""Operational stock, maintenance history and collection feedback deadlines."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c71d4e9a205b"
down_revision = "b94e1c7a3d20"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def fk(name, target, *, nullable=False, **kw):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(target, **kw), nullable=nullable)


def upgrade():
    op.create_table("ops_asset_movement", *common(), fk("project_id", "ops_project.id"), fk("product_id", "ops_product.id"),
        fk("equipment_id", "ops_equipment.id", nullable=True), fk("school_id", "ops_school.id", nullable=True, ondelete="SET NULL"),
        sa.Column("action", sa.String(24), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False), sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False), sa.Column("note", sa.Text(), nullable=False),
        fk("operator_id", "sys_user.id", nullable=True), sa.UniqueConstraint("source_type", "source_key"), sa.UniqueConstraint("equipment_id"),
        sa.CheckConstraint("quantity > 0"),
        sa.CheckConstraint("action IN ('inbound','outbound','return','scrap_warehouse','scrap_operating')"))
    op.create_index("ix_ops_asset_movement_project_id", "ops_asset_movement", ["project_id"])
    op.create_table("ops_maintenance_record", *common(), fk("project_id", "ops_project.id"),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("title", sa.String(160), nullable=False),
        sa.Column("customer_name", sa.String(128), nullable=False), sa.Column("contact_name", sa.String(64), nullable=False),
        sa.Column("phone", sa.String(64), nullable=False), sa.Column("address", sa.String(500), nullable=False),
        sa.Column("device_name", sa.String(128), nullable=False), sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True), fk("owner_id", "sys_user.id"),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("problems", sa.Text(), nullable=False), sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False))
    op.create_index("ix_ops_maintenance_record_project_id", "ops_maintenance_record", ["project_id"])
    op.create_table("ops_maintenance_update", *common(), fk("record_id", "ops_maintenance_record.id"),
        fk("author_id", "sys_user.id"), sa.Column("values", JSONB(), nullable=False))
    op.create_index("ix_ops_maintenance_update_record_id", "ops_maintenance_update", ["record_id"])
    op.create_table("workbook_collection_reminder", *common(), fk("workbook_id", "contract_workbook.id"),
        sa.Column("sheet_id", sa.String(200), nullable=False), sa.Column("row_key", sa.String(80), nullable=False),
        fk("request_id", "workbook_collection_request.id"), sa.Column("cadence", sa.String(16), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("manager_ids", JSONB(), nullable=False), fk("updated_by", "sys_user.id"),
        sa.Column("revision", sa.Integer(), nullable=False), sa.UniqueConstraint("workbook_id", "sheet_id", "row_key"))
    # Existing school procurement is the opening receipt source. No historical withdrawals are inferred.
    op.execute("""INSERT INTO ops_asset_movement
        (project_id, product_id, equipment_id, school_id, action, quantity, occurred_on, source_type, source_key, note)
        SELECT s.project_id, e.product_id, e.id, e.school_id, 'inbound', e.quantity,
               (e.created_at AT TIME ZONE 'Asia/Shanghai')::date, 'purchase', 'equipment:' || e.id,
               '历史学校采购入库；待按实际出库记录核对'
        FROM ops_equipment e JOIN ops_school s ON s.id=e.school_id""")
    # Previously configured projects retain their selections; procurement gets its matching stock view.
    op.execute("""UPDATE ops_project_features SET sections = sections || '["warehouse"]'::jsonb,
        revision = revision + 1 WHERE sections ? 'procurement' AND NOT sections ? 'warehouse'""")
    op.execute("""UPDATE ops_project_features f SET sections = f.sections ||
        (SELECT COALESCE(jsonb_agg(v), '[]'::jsonb) FROM jsonb_array_elements_text(
        '["device_maintenance","arrears","collection_maintenance","customers","warehouse","scrap","products","reports"]'::jsonb) v
        WHERE NOT f.sections ? v), revision=f.revision+1
        FROM ops_project p WHERE p.id=f.project_id AND btrim(p.name)='一键报警'""")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_proc WHERE proname='jc_audit_install')
        THEN PERFORM public.jc_audit_install(); END IF; END $$;""")


def downgrade():
    op.execute("""UPDATE ops_project_features SET sections=(SELECT COALESCE(jsonb_agg(v),'[]'::jsonb)
        FROM jsonb_array_elements_text(sections) v WHERE v NOT IN
        ('warehouse','scrap','device_maintenance','arrears','collection_maintenance','customers')), revision=revision+1""")
    op.drop_table("workbook_collection_reminder")
    op.drop_table("ops_maintenance_update")
    op.drop_table("ops_maintenance_record")
    op.drop_table("ops_asset_movement")
