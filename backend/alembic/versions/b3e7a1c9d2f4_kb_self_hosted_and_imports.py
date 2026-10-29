"""self-hosted knowledge base (categories, visibility, review) + unified import batch

Revision ID: b3e7a1c9d2f4
Revises: 9c1f2a7d4b10
Create Date: 2026-10-24 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3e7a1c9d2f4"
down_revision: Union[str, Sequence[str], None] = "9c1f2a7d4b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _ts():
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "kb_category",
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("reviewer_roles", JSONB, nullable=False, server_default="[]"),
        *_ts(),
        sa.ForeignKeyConstraint(["parent_id"], ["kb_category.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "name"),
    )
    op.create_index(op.f("ix_kb_category_parent_id"), "kb_category", ["parent_id"], unique=False)

    with op.batch_alter_table("kb_document") as b:
        b.add_column(sa.Column("subcategory", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("tags", JSONB, nullable=False, server_default="[]"))
        b.add_column(sa.Column("summary", sa.String(length=500), nullable=True))
        b.add_column(sa.Column("visibility", sa.String(length=8), nullable=False, server_default="all"))
        b.add_column(sa.Column("visible_depts", JSONB, nullable=False, server_default="[]"))
        b.add_column(sa.Column("visible_roles", JSONB, nullable=False, server_default="[]"))
        b.add_column(sa.Column("status", sa.String(length=12), nullable=False, server_default="published"))
        b.add_column(sa.Column("file_id", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("uploader_id", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("reviewer_id", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("review_note", sa.String(length=255), nullable=True))
        b.create_foreign_key("fk_kb_document_file_id", "sys_file", ["file_id"], ["id"])
        b.create_foreign_key("fk_kb_document_uploader_id", "sys_user", ["uploader_id"], ["id"])
        b.create_foreign_key("fk_kb_document_reviewer_id", "sys_user", ["reviewer_id"], ["id"])
    op.create_index(op.f("ix_kb_document_status"), "kb_document", ["status"], unique=False)
    op.create_index(op.f("ix_kb_document_sha256"), "kb_document", ["sha256"], unique=False)

    op.create_table(
        "import_batch",
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("route", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=True),
        sa.Column("clean_job_id", sa.BigInteger(), nullable=True),
        sa.Column("kb_doc_id", sa.BigInteger(), nullable=True),
        sa.Column("suggestion", JSONB, nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("uploader_id", sa.BigInteger(), nullable=False),
        sa.Column("dept_code", sa.String(length=8), nullable=True),
        sa.Column("handler_id", sa.BigInteger(), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(["file_id"], ["sys_file.id"]),
        sa.ForeignKeyConstraint(["clean_job_id"], ["clean_job.id"]),
        sa.ForeignKeyConstraint(["kb_doc_id"], ["kb_document.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploader_id"], ["sys_user.id"]),
        sa.ForeignKeyConstraint(["handler_id"], ["sys_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_import_batch_sha256"), "import_batch", ["sha256"], unique=False)
    op.create_index(op.f("ix_import_batch_status"), "import_batch", ["status"], unique=False)
    op.create_index(op.f("ix_import_batch_uploader_id"), "import_batch", ["uploader_id"], unique=False)


def downgrade() -> None:
    op.drop_table("import_batch")
    op.drop_index(op.f("ix_kb_document_sha256"), table_name="kb_document")
    op.drop_index(op.f("ix_kb_document_status"), table_name="kb_document")
    with op.batch_alter_table("kb_document") as b:
        for fk in ("fk_kb_document_reviewer_id", "fk_kb_document_uploader_id", "fk_kb_document_file_id"):
            b.drop_constraint(fk, type_="foreignkey")
        for col in ("review_note", "reviewed_at", "reviewer_id", "uploader_id", "sha256", "file_id", "status",
                    "visible_roles", "visible_depts", "visibility", "summary", "tags", "subcategory"):
            b.drop_column(col)
    op.drop_table("kb_category")
