"""延期原因的版本与复核决定，与任务完成状态分开保存。"""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin


class OpsTaskDelay(TimestampMixin, Base):
    __tablename__ = "ops_task_delay"
    __table_args__ = (
        Index("ix_ops_task_delay_task_id_id", "task_id", "id"),
        CheckConstraint("length(trim(reason)) > 0", name="ck_ops_task_delay_reason"),
        CheckConstraint(
            "(decision IS NULL AND reviewer_id IS NULL AND reviewed_at IS NULL) OR "
            "(decision IS NOT NULL AND decision IN ('accepted', 'rejected') AND reviewer_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND reviewer_id <> author_id)", name="ck_ops_task_delay_decision",
        ),
    )

    task_id: Mapped[int] = mapped_column(ForeignKey("ops_task.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    reason: Mapped[str] = mapped_column(Text)
    original_due_date: Mapped[date] = mapped_column(Date)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    decision: Mapped[str | None] = mapped_column(String(16))
    review_comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author = relationship("SysUser", foreign_keys=[author_id], lazy="joined")
    reviewer = relationship("SysUser", foreign_keys=[reviewer_id], lazy="joined")
