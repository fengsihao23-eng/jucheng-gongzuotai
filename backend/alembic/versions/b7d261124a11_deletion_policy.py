"""Add the rotatable deletion password and persisted system management grants."""
from alembic import op
import sqlalchemy as sa

from app.core.config import settings

revision = "b7d261124a11"
down_revision = "a6c261124f02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_delete_password",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("password_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "business_record_deletion",
        sa.Column("resource", sa.String(64), primary_key=True),
        sa.Column("record_key", sa.String(255), primary_key=True),
        sa.Column("removed_by", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("""
        INSERT INTO sys_role (role_code, name, dept_code, permissions)
        VALUES ('SYS-MGMT', '系统管理授权', '*', '{}')
        ON CONFLICT (role_code) DO NOTHING
    """)
    accounts = {name.strip() for name in settings.system_management_bootstrap_accounts.split(",") if name.strip()}
    if settings.environment != "prod":
        accounts.add("admin")
    if accounts:
        op.execute(sa.text("""
            INSERT INTO sys_user_role (user_id, role_id)
            SELECT u.id, r.id FROM sys_user u JOIN sys_role r ON r.role_code = 'SYS-MGMT'
            WHERE u.wecom_userid IN :accounts AND NOT u.is_deleted AND u.status = 1 AND u.workbench_enabled
            ON CONFLICT (user_id, role_id) DO NOTHING
        """).bindparams(sa.bindparam("accounts", value=sorted(accounts), expanding=True, type_=sa.String())))


def downgrade():
    op.execute("DELETE FROM sys_role WHERE role_code = 'SYS-MGMT'")
    op.drop_table("business_record_deletion")
    op.drop_table("system_delete_password")
