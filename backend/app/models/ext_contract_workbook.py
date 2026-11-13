"""Restricted source snapshot and durable links to the business ledgers."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class ContractWorkbook(TimestampMixin, Base):
    __tablename__ = "contract_workbook"
    source_key: Mapped[str] = mapped_column(String(48), unique=True)
    config: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[str | None] = mapped_column(String(64))
    sheets: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ContractWorkbookLink(TimestampMixin, Base):
    __tablename__ = "contract_workbook_link"
    __table_args__ = (UniqueConstraint("workbook_id", "kind", "target_id"),)
    workbook_id: Mapped[int] = mapped_column(ForeignKey("contract_workbook.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(BigInteger)
    sheet_id: Mapped[str] = mapped_column(String(200))
    row_number: Mapped[int] = mapped_column(Integer)
    keys: Mapped[list] = mapped_column(JSON)
    baseline: Mapped[dict] = mapped_column(JSON)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
