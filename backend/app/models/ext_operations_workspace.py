"""Personal work entry, scoped project content, annual outreach and income ingress."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class OpsMemberView(Base):
    __tablename__ = "ops_member_view"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), primary_key=True)
    sections: Mapped[list] = mapped_column(JSON)
    task_scope: Mapped[str] = mapped_column(String(12), default="team")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))


class OpsProjectControl(Base):
    __tablename__ = "ops_project_control"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), primary_key=True)
    wecom_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    business_project_code: Mapped[str | None] = mapped_column(String(32))
    report_audience: Mapped[str] = mapped_column(String(12), default="all")
    report_user_ids: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))


class OpsSchoolRegion(Base):
    __tablename__ = "ops_school_region"
    school_id: Mapped[int] = mapped_column(ForeignKey("ops_school.id"), primary_key=True)
    region: Mapped[str] = mapped_column(String(32), default="待定")


class OpsOutreachGoal(TimestampMixin, Base):
    __tablename__ = "ops_outreach_goal"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("ops_school.id"))
    year: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[int] = mapped_column(ForeignKey("ops_task.id"), unique=True)
    expected_annual_income: Mapped[Decimal | None]
    __table_args__ = (UniqueConstraint("project_id", "school_id", "year", name="uq_ops_school_annual_goal"),)


class OpsIncomeIngressBatch(TimestampMixin, Base):
    __tablename__ = "ops_income_ingress_batch"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(64))
    batch_key: Mapped[str] = mapped_column(String(128))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    operator_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    __table_args__ = (UniqueConstraint("project_id", "source_key", "batch_key", name="uq_ops_income_ingress_batch"),)
