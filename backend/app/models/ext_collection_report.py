"""Saved stage summaries of collection progress and issues."""
from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class WorkbookCollectionReport(TimestampMixin, Base):
    __tablename__ = "workbook_collection_report"
    workbook_id: Mapped[int] = mapped_column(ForeignKey("contract_workbook.id"), index=True)
    revision: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    scope: Mapped[str] = mapped_column(String(40), default="all")
    owner_filter: Mapped[str | None] = mapped_column(String(80))
    focus_prompt: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict)
