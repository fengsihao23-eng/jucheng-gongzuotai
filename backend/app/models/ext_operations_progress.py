"""项目级进度记录；与成员提交的任务反馈独立保存。"""
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin


class OpsProjectProgress(TimestampMixin, Base):
    __tablename__ = "ops_project_progress"
    __table_args__ = (
        Index("ix_ops_project_progress_project_id_id", "project_id", "id"),
        UniqueConstraint("task_id", name="uq_ops_project_progress_task_id"),
        CheckConstraint("length(trim(progress)) > 0", name="ck_ops_project_progress_content"),
        CheckConstraint("(next_due_date IS NULL AND next_plan = '') OR (next_due_date IS NOT NULL AND next_plan <> '')",
                        name="ck_ops_project_progress_plan_date"),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("ops_task.id", name="fk_ops_project_progress_task_id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    progress: Mapped[str] = mapped_column(Text)
    problems: Mapped[str] = mapped_column(Text, default="")
    next_plan: Mapped[str] = mapped_column(Text, default="")
    next_due_date: Mapped[date | None] = mapped_column(Date)
    author = relationship("SysUser", lazy="joined")
