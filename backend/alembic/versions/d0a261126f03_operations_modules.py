"""Make operations feature selections independent and add project user lists."""
from alembic import op
import sqlalchemy as sa

revision = "d0a261126f03"
down_revision = "c9a261126f02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ops_project_user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("ops_project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(12), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("contact", sa.String(80), nullable=False),
        sa.Column("contact_details", sa.String(128), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("category in ('education', 'other')", name="ck_ops_project_user_category"),
    )
    op.create_index("ix_ops_project_user_project_category", "ops_project_user", ["project_id", "category"])
    # Preserve the sections that the previous release always enabled.
    op.execute("""
        WITH additions AS (
            SELECT project_id, jsonb_agg(feature ORDER BY position) AS features
            FROM ops_project_features
            CROSS JOIN unnest(ARRAY['tasks', 'team', 'competitors', 'settings'])
                WITH ORDINALITY AS defaults(feature, position)
            WHERE NOT jsonb_exists(sections, feature)
            GROUP BY project_id
        )
        UPDATE ops_project_features AS project
        SET sections = project.sections || additions.features, revision = project.revision + 1
        FROM additions
        WHERE project.project_id = additions.project_id
    """)
    op.execute("SELECT public.jc_audit_install()")


def downgrade():
    op.execute("UPDATE ops_project_features SET sections = sections - 'settings' - 'user_list'")
    op.drop_table("ops_project_user")
