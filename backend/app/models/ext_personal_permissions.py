"""Individual menu/action/data-scope configuration, separate from account preferences."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, JSON
from app.models.system import SysUser


class SysUserPermission(Base):
    __tablename__ = "sys_user_permission"
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE"), primary_key=True)
    menus: Mapped[list[str]] = mapped_column(JSON)
    buttons: Mapped[list[str]] = mapped_column(JSON)
    data_scope: Mapped[str] = mapped_column(String(8))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (CheckConstraint("data_scope IN ('self', 'dept', 'all')", name="ck_user_permission_scope"),)


SysUser.permission_override = relationship(SysUserPermission, foreign_keys=[SysUserPermission.user_id],
                                          lazy="selectin", uselist=False, viewonly=True)
