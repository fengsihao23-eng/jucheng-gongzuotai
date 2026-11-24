from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class SporadicCollectionProject(TimestampMixin, Base):
    __tablename__ = "sporadic_collection_project"
    name: Mapped[str] = mapped_column(String(128))
    customer: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    expected_amount: Mapped[Decimal | None]
    due_on: Mapped[date | None] = mapped_column(Date)
    requirements: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
