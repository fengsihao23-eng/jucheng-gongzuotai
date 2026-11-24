from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class OpsCompetitor(TimestampMixin, Base):
    __tablename__ = "ops_competitor"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    details: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
