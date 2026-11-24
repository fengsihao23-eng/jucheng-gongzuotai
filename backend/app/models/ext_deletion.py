from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SystemDeletePassword(Base):
    __tablename__ = "system_delete_password"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    password_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    revision: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BusinessRecordDeletion(Base):
    __tablename__ = "business_record_deletion"
    resource: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    removed_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    removed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
