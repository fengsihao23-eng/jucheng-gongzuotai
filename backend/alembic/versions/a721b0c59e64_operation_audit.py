"""Add request and row-level audit without rewriting existing business data."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a721b0c59e64"
down_revision = "e8920fc14ad6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("audit_event",
        sa.Column("id",sa.BigInteger(),primary_key=True),
        sa.Column("event_key",sa.String(64),nullable=False,unique=True),
        sa.Column("actor_id",sa.BigInteger()),sa.Column("actor_name",sa.String(128)),sa.Column("account",sa.String(128)),
        sa.Column("source",sa.String(24),nullable=False),sa.Column("module",sa.String(64),nullable=False),
        sa.Column("action",sa.String(200),nullable=False),sa.Column("method",sa.String(12)),sa.Column("path",sa.String(500)),
        sa.Column("outcome",sa.String(24),nullable=False),sa.Column("status_code",sa.Integer()),
        sa.Column("duration_ms",sa.Integer()),sa.Column("client_ip",sa.String(64)),sa.Column("detail",JSONB()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    for name,cols in (("ix_audit_event_actor_id",["actor_id"]),("ix_audit_event_created_at",["created_at"]),
                      ("ix_audit_event_source_time",["source","created_at"])):
        op.create_index(name,"audit_event",cols)
    op.create_table("audit_change",
        sa.Column("id",sa.BigInteger(),primary_key=True),sa.Column("event_key",sa.String(64),nullable=False),
        sa.Column("table_name",sa.String(128),nullable=False),sa.Column("record_key",sa.Text(),nullable=False),
        sa.Column("operation",sa.String(16),nullable=False),sa.Column("before",JSONB()),sa.Column("after",JSONB()),
        sa.Column("fields",JSONB(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_index("ix_audit_change_event_key","audit_change",["event_key"])
    op.create_index("ix_audit_change_table_name","audit_change",["table_name"])
    op.create_table("audit_setting",sa.Column("key",sa.String(64),primary_key=True),sa.Column("value",JSONB(),nullable=False))
    op.execute("INSERT INTO audit_setting(key,value) VALUES('enabled',jsonb_build_object('since',clock_timestamp()))")
    from app.core.audit_sql import install
    install(op.get_bind())


def downgrade():
    connection=op.get_bind()
    connection.execute(sa.text("""
DO $body$ DECLARE r record; BEGIN
 FOR r IN SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
  JOIN pg_namespace n ON n.oid=c.relnamespace WHERE t.tgname='jc_operation_audit' AND n.nspname='public' LOOP
  EXECUTE format('DROP TRIGGER jc_operation_audit ON public.%I',r.relname);
 END LOOP;
END $body$;
DROP FUNCTION IF EXISTS public.jc_audit_install();
DROP FUNCTION IF EXISTS public.jc_audit_capture();
DROP FUNCTION IF EXISTS public.jc_audit_redact(jsonb,text);
"""))
    op.drop_table("audit_change")
    op.drop_table("audit_event")
    op.drop_table("audit_setting")
