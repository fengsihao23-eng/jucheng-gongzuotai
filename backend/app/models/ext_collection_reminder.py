from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class WorkbookCollectionReminder(TimestampMixin, Base):
    __tablename__ = "workbook_collection_reminder"
    __table_args__ = (UniqueConstraint("workbook_id", "sheet_id", "row_key"),)
    workbook_id: Mapped[int] = mapped_column(ForeignKey("contract_workbook.id"))
    sheet_id: Mapped[str] = mapped_column(String(200))
    row_key: Mapped[str] = mapped_column(String(80))
    request_id: Mapped[int] = mapped_column(ForeignKey("workbook_collection_request.id"))
    cadence: Mapped[str] = mapped_column(String(16), default="weekly")
    due_on: Mapped[date] = mapped_column(Date)
    payment_due_on: Mapped[date | None] = mapped_column(Date)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    manager_ids: Mapped[list[int]] = mapped_column(JSON)
    updated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
