"""Add an independently revocable, per-account cross-project grant."""
import sqlalchemy as sa
from alembic import op

revision = "f4c261126f06"
down_revision = "f3c261126f05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sys_user", sa.Column("all_project_access", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("""
        UPDATE sys_user
        SET all_project_access = true
        WHERE wecom_userid IN ('SZGLY', 'ken', 'ZhuDanDan', 'FengSiHao')
          AND status = 1 AND NOT is_deleted AND workbench_enabled
    """)
    # Keep the designated project managers' individual grants when moving to the new scope.
    op.execute("""
        UPDATE sys_user_permission AS permission
        SET buttons = (
            SELECT jsonb_agg(button ORDER BY button)
            FROM (
                SELECT jsonb_array_elements_text(permission.buttons) AS button
                UNION
                SELECT unnest(ARRAY['operations:manage', 'operations:archive', 'operations:approve',
                    'project:key', 'project:weekly', 'milestone:write', 'feedback:write', 'review:write'])
            ) AS actions
        ), updated_at = now()
        FROM sys_user AS account
        WHERE permission.user_id = account.id AND account.all_project_access
    """)


def downgrade():
    op.drop_column("sys_user", "all_project_access")
