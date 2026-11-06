"""运营项目、跨部门任务及学校经营基础资料。

Revision ID: a61e8b92c430
Revises: b7e2c4d9a1f6
"""
import sqlalchemy as sa
from alembic import op

revision = "a61e8b92c430"
down_revision = "b7e2c4d9a1f6"
branch_labels = None
depends_on = None


def timestamps():
    return (sa.Column("id", sa.BigInteger, primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def fk(name, table, nullable=False):
    return sa.Column(name, sa.BigInteger, sa.ForeignKey(f"{table}.id"), nullable=nullable)


def text(name):
    return sa.Column(name, sa.Text, nullable=False)


def string(name, length):
    return sa.Column(name, sa.String(length), nullable=False)


def money(name):
    return sa.Column(name, sa.Numeric(14, 2), nullable=False)


def upgrade():
    op.create_table("ops_project", *timestamps(), string("name", 128), string("category", 64), fk("owner_id", "sys_user"),
                    text("objective"), sa.Column("target_date", sa.Date), string("status", 16), sa.Column("is_key", sa.Boolean, nullable=False))
    op.create_table("ops_member", *timestamps(), fk("project_id", "ops_project"), fk("user_id", "sys_user"),
                    string("role", 16), sa.UniqueConstraint("project_id", "user_id"))
    op.create_table("ops_school", *timestamps(), fk("project_id", "ops_project"), string("name", 128), string("stage", 24),
                    string("potential", 16), text("next_action"), sa.Column("next_contact_date", sa.Date),
                    money("labor_cost"), money("other_cost"), text("note"), sa.UniqueConstraint("project_id", "name"),
                    sa.CheckConstraint("labor_cost >= 0 AND other_cost >= 0"))
    op.create_table("ops_product", *timestamps(), fk("project_id", "ops_project"), string("name", 128), string("brand", 64),
                    string("model", 128), string("unit", 16), money("reference_price"), text("note"), sa.CheckConstraint("reference_price >= 0"))
    op.create_table("ops_equipment", *timestamps(), fk("school_id", "ops_school"), fk("product_id", "ops_product"),
                    sa.Column("quantity", sa.Integer, nullable=False), money("unit_price"), text("note"),
                    sa.CheckConstraint("quantity > 0 AND unit_price >= 0"))
    op.create_table("ops_income", *timestamps(), fk("school_id", "ops_school"), sa.Column("income_date", sa.Date, nullable=False),
                    money("amount"), string("source", 128), text("note"), fk("operator_id", "sys_user"),
                    sa.UniqueConstraint("school_id", "income_date"), sa.CheckConstraint("amount >= 0"))
    op.create_table("ops_task", *timestamps(), fk("project_id", "ops_project"), fk("school_id", "ops_school", True),
                    string("title", 160), text("description"), fk("owner_id", "sys_user"), fk("creator_id", "sys_user"),
                    sa.Column("due_date", sa.Date, nullable=False), string("priority", 16), string("cadence", 16),
                    string("status", 16), sa.Column("progress", sa.Integer, nullable=False), text("next_step"),
                    sa.Column("last_feedback_at", sa.DateTime(timezone=True)), sa.CheckConstraint("progress >= 0 AND progress <= 100"))
    op.create_table("ops_task_node", *timestamps(), fk("task_id", "ops_task"), string("name", 128),
                    sa.Column("seq", sa.Integer, nullable=False), sa.Column("due_date", sa.Date, nullable=False),
                    sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_table("ops_task_update", *timestamps(), fk("task_id", "ops_task"), fk("author_id", "sys_user"),
                    string("kind", 16), text("content"), text("next_step"), string("status", 16), sa.Column("progress", sa.Integer, nullable=False))
    for table, fields in {
        "ops_member": ["project_id", "user_id"], "ops_school": ["project_id"], "ops_product": ["project_id"],
        "ops_equipment": ["school_id"], "ops_income": ["school_id", "income_date"],
        "ops_task": ["project_id", "owner_id", "due_date"], "ops_task_node": ["task_id"], "ops_task_update": ["task_id"],
    }.items():
        for field in fields:
            op.create_index(f"ix_{table}_{field}", table, [field])
    op.execute("""UPDATE sys_role SET permissions = jsonb_set(COALESCE(permissions, '{}'::jsonb), '{buttons}',
        COALESCE(permissions->'buttons', '[]'::jsonb) || '["operations:manage"]'::jsonb)
        WHERE role_code IN ('ZB-01', 'SC-01') AND permissions ? 'buttons'
        AND NOT COALESCE(permissions->'buttons', '[]'::jsonb) @> '["operations:manage"]'::jsonb""")
    for code, name, description in [
        ("ops.project_status", "运营项目进展", "查询本人有权访问的运营项目、当前待办、负责人和节点反馈。"),
        ("ops.task_feedback", "运营任务反馈", "对本人负责的运营任务提交简短进展，沿用项目成员权限并保留记录。"),
    ]:
        op.execute(sa.text("""INSERT INTO mcp_tool (tool_code, name, description, allowed_roles, enabled)
            VALUES (:code, :name, :description, '["*"]'::jsonb, true) ON CONFLICT (tool_code) DO NOTHING""").bindparams(
                code=code, name=name, description=description))


def downgrade():
    op.execute("DELETE FROM mcp_tool WHERE tool_code IN ('ops.project_status', 'ops.task_feedback')")
    op.execute("""UPDATE sys_role SET permissions = jsonb_set(permissions, '{buttons}', (permissions->'buttons') - 'operations:manage')
        WHERE role_code IN ('ZB-01', 'SC-01') AND permissions ? 'buttons'""")
    for table in ("ops_task_update", "ops_task_node", "ops_task", "ops_income", "ops_equipment", "ops_product", "ops_school", "ops_member", "ops_project"):
        op.drop_table(table)
