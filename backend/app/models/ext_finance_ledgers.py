"""Confirmed template ledger rows and private upload drafts."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import JSON, Base, TimestampMixin


class FinLedgerImport(TimestampMixin, Base):
    __tablename__ = "fin_ledger_import"

    token: Mapped[str] = mapped_column(String(32), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    draft: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)


class FinLedgerEntry(TimestampMixin, Base):
    __tablename__ = "fin_ledger_entry"
    __table_args__ = (
        Index("ix_fin_ledger_entry_kind_period", "kind", "period", "ledger_date"),
        Index("uq_fin_ledger_entry_active_identity", "identity", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    import_id: Mapped[int] = mapped_column(ForeignKey("fin_ledger_import.id"), index=True)
    source_sheet: Mapped[str] = mapped_column(String(160))
    source_row: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    identity: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(64))
    ledger_date: Mapped[date | None] = mapped_column(Date)
    period: Mapped[str | None] = mapped_column(String(7))
    account: Mapped[str | None] = mapped_column(String(128))
    project: Mapped[str | None] = mapped_column(String(128))
    search_text: Mapped[str] = mapped_column(Text)
    inflow: Mapped[Decimal | None]
    outflow: Mapped[Decimal | None]
    fee: Mapped[Decimal | None]
    values: Mapped[dict] = mapped_column(JSON)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", name="fk_fin_ledger_entry_deleted_by"))
    delete_reason: Mapped[str | None] = mapped_column(String(200))
