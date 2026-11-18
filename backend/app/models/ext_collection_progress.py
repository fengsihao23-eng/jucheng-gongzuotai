"""Append-only human reports on an assigned source contract follow-up."""
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class WorkbookCollectionProgress(TimestampMixin, Base):
    __tablename__ = "workbook_collection_progress"
    request_id: Mapped[int] = mapped_column(ForeignKey("workbook_collection_request.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    stage: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    problems: Mapped[str | None] = mapped_column(Text)
    next_step: Mapped[str | None] = mapped_column(Text)
    expected_on: Mapped[date | None] = mapped_column(Date)
