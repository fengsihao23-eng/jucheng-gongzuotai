"""Key project work plans extend the existing project nodes and report feedback."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class KeyProjectMember(Base):
    __tablename__ = "key_project_member"
    project_id: Mapped[int] = mapped_column(ForeignKey("biz_project.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KeyProjectTaskPlan(Base):
    __tablename__ = "key_project_task_plan"
    milestone_id: Mapped[int] = mapped_column(ForeignKey("biz_project_milestone.id", ondelete="CASCADE"), primary_key=True)
    preparation_on: Mapped[date | None] = mapped_column(Date)
    requirements: Mapped[str] = mapped_column(Text, default="")
    cadence: Mapped[str] = mapped_column(String(16), default="weekly")
    feedback_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    problems: Mapped[str] = mapped_column(Text, default="")
    next_step: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KeyProjectTaskNode(TimestampMixin, Base):
    __tablename__ = "key_project_task_node"
    milestone_id: Mapped[int] = mapped_column(ForeignKey("biz_project_milestone.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    due_on: Mapped[date] = mapped_column(Date)
    completed_on: Mapped[date | None] = mapped_column(Date)
    seq: Mapped[int] = mapped_column(Integer)


class KeyProjectTaskUpdate(TimestampMixin, Base):
    __tablename__ = "key_project_task_update"
    milestone_id: Mapped[int] = mapped_column(ForeignKey("biz_project_milestone.id", ondelete="CASCADE"), index=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("biz_project_feedback.id", ondelete="CASCADE"), unique=True)
    values: Mapped[dict] = mapped_column(JSON)
