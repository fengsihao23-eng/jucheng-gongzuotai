"""Per-project standard features, independent of a member's data permissions."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON


class OpsProjectFeatures(Base):
    __tablename__ = "ops_project_features"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id", ondelete="CASCADE"), primary_key=True)
    sections: Mapped[list[str]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    legacy_campus_income: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
