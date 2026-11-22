from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class WorkbookProjectFeedback(TimestampMixin, Base):
    __tablename__ = "workbook_project_feedback"
    __table_args__ = (Index("ix_workbook_project_feedback_history", "workbook_id", "sheet_id", "row_key", "id"),)
    workbook_id: Mapped[int] = mapped_column(ForeignKey("contract_workbook.id"))
    sheet_id: Mapped[str] = mapped_column(String(200))
    row_key: Mapped[str] = mapped_column(String(80))
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    status: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    problems: Mapped[str] = mapped_column(Text)
    next_step: Mapped[str] = mapped_column(Text)
    next_due_on: Mapped[date | None] = mapped_column(Date)
