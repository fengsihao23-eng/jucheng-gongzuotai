"""Inventory movements and operational maintenance, independent of finance postings."""
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class OpsAssetMovement(TimestampMixin, Base):
    __tablename__ = "ops_asset_movement"
    __table_args__ = (UniqueConstraint("source_type", "source_key"),
                      CheckConstraint("quantity > 0"),
                      CheckConstraint("action IN ('inbound','outbound','return','scrap_warehouse','scrap_operating')"),)
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("ops_product.id"))
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("ops_equipment.id"), unique=True)
    school_id: Mapped[int | None] = mapped_column(ForeignKey("ops_school.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(24))
    quantity: Mapped[int] = mapped_column(Integer)
    occurred_on: Mapped[date] = mapped_column(Date)
    source_type: Mapped[str] = mapped_column(String(16), default="manual")
    source_key: Mapped[str] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text, default="")
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class OpsMaintenanceRecord(TimestampMixin, Base):
    __tablename__ = "ops_maintenance_record"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(160))
    customer_name: Mapped[str] = mapped_column(String(128), default="")
    contact_name: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    contact_position: Mapped[str] = mapped_column(String(128), default="", server_default="")
    contact_gender: Mapped[str] = mapped_column(String(16), default="", server_default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    device_name: Mapped[str] = mapped_column(String(128), default="")
    amount: Mapped[Decimal | None]
    due_on: Mapped[date | None] = mapped_column(Date)
    owner_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    status: Mapped[str] = mapped_column(String(16), default="open")
    content: Mapped[str] = mapped_column(Text, default="")
    problems: Mapped[str] = mapped_column(Text, default="")
    next_step: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    workbook_row: Mapped[dict | None] = mapped_column(JSON)


class OpsMaintenanceUpdate(TimestampMixin, Base):
    __tablename__ = "ops_maintenance_update"
    record_id: Mapped[int] = mapped_column(ForeignKey("ops_maintenance_record.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    values: Mapped[dict] = mapped_column(JSON)
