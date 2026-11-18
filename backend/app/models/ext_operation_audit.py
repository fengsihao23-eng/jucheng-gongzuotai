"""Administrator-visible request journal and transactionally captured row changes."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base, JSON


class AuditEvent(Base):
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(64), unique=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    actor_name: Mapped[str | None] = mapped_column(String(128))
    account: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(24), default="web")
    module: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(200))
    method: Mapped[str | None] = mapped_column(String(12))
    path: Mapped[str | None] = mapped_column(String(500))
    outcome: Mapped[str] = mapped_column(String(24))
    status_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    __table_args__ = (Index("ix_audit_event_source_time", "source", "created_at"),)


class AuditChange(Base):
    __tablename__ = "audit_change"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(64), index=True)
    table_name: Mapped[str] = mapped_column(String(128), index=True)
    record_key: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(String(16))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    fields: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditSetting(Base):
    __tablename__ = "audit_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
