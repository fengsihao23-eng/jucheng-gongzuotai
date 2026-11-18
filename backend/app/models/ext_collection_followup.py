"""Follow-up requests for the fixed workbook, separate from financial data."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class WorkbookCollectionRequest(TimestampMixin, Base):
    __tablename__ = "workbook_collection_request"
    workbook_id: Mapped[int] = mapped_column(ForeignKey("contract_workbook.id"))
    sheet_id: Mapped[str] = mapped_column(String(200))
    row_key: Mapped[str] = mapped_column(String(80))
    contract_name: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    content: Mapped[str] = mapped_column(Text)
    reply: Mapped[str | None] = mapped_column(Text)
    expected_on: Mapped[date | None] = mapped_column(Date)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_workbook_collection_row", "workbook_id", "sheet_id", "row_key"),
        Index("uq_workbook_collection_pending", "workbook_id", "sheet_id", "row_key", unique=True,
              postgresql_where=text("replied_at IS NULL AND cancelled_at IS NULL"),
              sqlite_where=text("replied_at IS NULL AND cancelled_at IS NULL")),
    )
