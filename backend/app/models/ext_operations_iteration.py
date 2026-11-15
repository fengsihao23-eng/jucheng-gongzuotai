"""Additive meeting iteration: node evidence, discussions, visits and frozen reports."""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class OpsTaskNodeUpdate(Base):
    __tablename__ = "ops_task_node_update"
    update_id: Mapped[int] = mapped_column(ForeignKey("ops_task_update.id"), primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("ops_task_node.id"), index=True)


class OpsTaskDiscussion(TimestampMixin, Base):
    __tablename__ = "ops_task_discussion"
    task_id: Mapped[int] = mapped_column(ForeignKey("ops_task.id"), index=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("ops_task_node.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    kind: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), index=True)
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("ops_task_discussion.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpsSchoolVisit(TimestampMixin, Base):
    __tablename__ = "ops_school_visit"
    school_id: Mapped[int] = mapped_column(ForeignKey("ops_school.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    occurred_on: Mapped[date] = mapped_column(Date)
    content: Mapped[str] = mapped_column(Text)
    next_action: Mapped[str] = mapped_column(Text, default="")
    next_contact_date: Mapped[date | None] = mapped_column(Date)


class OpsDigestPreference(Base):
    __tablename__ = "ops_digest_preference"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), primary_key=True)
    weekly: Mapped[bool] = mapped_column(Boolean, default=True)
    monthly: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))


class OpsDigestSnapshot(TimestampMixin, Base):
    __tablename__ = "ops_digest_snapshot"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    audience: Mapped[str] = mapped_column(String(12))
    mode: Mapped[str] = mapped_column(String(8))
    period: Mapped[str] = mapped_column(String(12))
    version: Mapped[int] = mapped_column(Integer)
    generated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    facts: Mapped[dict] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("project_id", "owner_id", "audience", "mode", "period", "version",
                                     name="uq_ops_digest_version"),)
