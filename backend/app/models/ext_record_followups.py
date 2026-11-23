"""Follow-up discussion and progress attached to business records."""
from datetime import date

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class RecordFollowup(TimestampMixin, Base):
    __tablename__ = "record_followup"
    __table_args__ = (
        Index("ix_record_followup_history", "target_kind", "target_key", "id"),
        Index("ix_record_followup_recipient", "recipient_id", "id"),
        UniqueConstraint("author_id", "client_id", name="uq_record_followup_submission"),
    )

    target_kind: Mapped[str] = mapped_column(String(45))
    target_key: Mapped[str] = mapped_column(String(64))
    reference: Mapped[dict] = mapped_column(JSON)
    action: Mapped[str] = mapped_column(String(12))
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("record_followup.id"), unique=True)
    legacy_key: Mapped[str | None] = mapped_column(String(45))
    client_id: Mapped[str] = mapped_column(String(36))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(12))
    problems: Mapped[str] = mapped_column(Text, default="")
    next_step: Mapped[str] = mapped_column(Text, default="")
    next_due_on: Mapped[date | None]
