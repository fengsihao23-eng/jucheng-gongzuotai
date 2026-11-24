"""Give configured system operators with individual permissions a deletion-only grant."""
from alembic import op
import sqlalchemy as sa

from app.core.config import settings

revision = "e1ac261124b2"
down_revision = "b7d261124a11"
branch_labels = None
depends_on = None


def upgrade():
    accounts = sorted({account.strip() for account in settings.system_management_bootstrap_accounts.split(",") if account.strip()})
    if not accounts:
        return
    op.execute(sa.text("""
        UPDATE sys_user_permission AS permission
        SET buttons = permission.buttons || '["record:delete"]'::jsonb, updated_at = now()
        FROM sys_user AS person
        WHERE permission.user_id = person.id AND person.wecom_userid IN :accounts
          AND person.status = 1 AND person.workbench_enabled AND NOT person.is_deleted
          AND NOT jsonb_exists(permission.buttons, 'record:delete')
          AND EXISTS (
              SELECT 1 FROM sys_user_role AS assigned JOIN sys_role AS role ON role.id = assigned.role_id
              WHERE assigned.user_id = person.id AND role.role_code = 'SYS-MGMT'
          )
          AND NOT EXISTS (
              SELECT 1 FROM sys_user_role AS assigned JOIN sys_role AS role ON role.id = assigned.role_id
              WHERE assigned.user_id = person.id AND role.role_code = 'ADMIN-01'
          )
    """).bindparams(sa.bindparam("accounts", value=accounts, expanding=True, type_=sa.String())))


def downgrade():
    op.execute("""
        UPDATE sys_user_permission SET buttons = buttons - 'record:delete', updated_at = now()
        WHERE jsonb_exists(buttons, 'record:delete')
    """)
