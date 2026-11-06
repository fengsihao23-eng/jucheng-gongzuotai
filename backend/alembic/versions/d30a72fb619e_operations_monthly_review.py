"""运营月度任务与部门、公司两级审核。"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "d30a72fb619e"
down_revision = "c92fd610b8a4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ops_task", sa.Column("plan_month", sa.String(7)))
    op.execute("UPDATE ops_task SET plan_month = to_char(due_date, 'YYYY-MM')")
    op.alter_column("ops_task", "plan_month", nullable=False)
    op.create_index("ix_ops_task_plan_month", "ops_task", ["plan_month"])
    for name, size, default in (("source", 16, "company"), ("approval_status", 24, "approved")):
        op.add_column("ops_task", sa.Column(name, sa.String(size), nullable=False, server_default=default))
    op.add_column("ops_task", sa.Column("department_reviewer_id", sa.BigInteger(), sa.ForeignKey("sys_user.id")))
    op.add_column("ops_task", sa.Column("company_reviewer_id", sa.BigInteger(), sa.ForeignKey("sys_user.id")))
    op.add_column("ops_task", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("ops_task", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("ops_task", sa.Column("incomplete_reason", sa.Text(), nullable=False, server_default=""))
    op.execute("UPDATE ops_task SET completed_at = COALESCE(last_feedback_at, updated_at) WHERE status = 'done'")
    op.create_table("ops_task_review",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("ops_task.id"), nullable=False),
        sa.Column("reviewer_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False), sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False), sa.Column("task_revision", sa.Integer(), nullable=False),
        sa.Column("task_snapshot", postgresql.JSONB(), nullable=False))
    op.create_index("ix_ops_task_review_task_id", "ops_task_review", ["task_id"])
    op.execute("""UPDATE sys_role SET permissions = jsonb_set(permissions, '{buttons}',
        (permissions->'buttons') || '["operations:approve"]'::jsonb)
        WHERE role_code = 'ZB-01' AND permissions ? 'buttons'
        AND NOT (permissions->'buttons') @> '["operations:approve"]'::jsonb""")
    for code, name, description in [
        ("ops.task_create", "运营月度任务安排", "按当前账号权限布置公司任务或提交本人计划，自动归入负责人月度表；本人计划必须经过部门和公司审核。"),
        ("ops.task_review", "运营任务审核", "仅指定审核人可按部门、公司顺序审核最新任务版本，保留审核意见，禁止自审。"),
    ]:
        op.execute(sa.text("""INSERT INTO mcp_tool(tool_code,name,description,allowed_roles,enabled)
            VALUES (:code,:name,:description,'["*"]'::jsonb,true) ON CONFLICT(tool_code) DO NOTHING""").bindparams(
                code=code,name=name,description=description))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT (SELECT count(*) FROM ops_task_review) + (SELECT count(*) FROM ops_task WHERE source = 'self')")).scalar():
        raise RuntimeError("已有自行安排任务或审核记录，不能丢弃审批信息回退")
    op.execute("DELETE FROM mcp_tool WHERE tool_code IN ('ops.task_create','ops.task_review')")
    op.drop_table("ops_task_review")
    op.drop_index("ix_ops_task_plan_month", table_name="ops_task")
    for name in ("incomplete_reason", "completed_at", "revision", "company_reviewer_id", "department_reviewer_id", "approval_status", "source", "plan_month"):
        op.drop_column("ops_task", name)
    op.execute("""UPDATE sys_role SET permissions=jsonb_set(permissions,'{buttons}',(permissions->'buttons')-'operations:approve')
        WHERE role_code='ZB-01' AND permissions ? 'buttons'""")
