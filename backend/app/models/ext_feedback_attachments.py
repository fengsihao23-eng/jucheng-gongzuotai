from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FeedbackAttachment(Base):
    __tablename__ = "feedback_attachment"
    __table_args__ = (Index("ix_feedback_attachment_record", "record_type", "record_id", "file_id"),)

    file_id: Mapped[int] = mapped_column(ForeignKey("sys_file.id", ondelete="CASCADE"), primary_key=True)
    record_type: Mapped[str] = mapped_column(String(40))
    record_id: Mapped[int] = mapped_column(BigInteger)
