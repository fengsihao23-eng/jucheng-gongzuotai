"""运营项目：跨部门任务闭环与学校经营基础资料。"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin


class OpsProject(TimestampMixin, Base):
    __tablename__ = "ops_project"
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), default="智慧校园")
    owner_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    objective: Mapped[str] = mapped_column(Text, default="")
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_key: Mapped[bool] = mapped_column(Boolean, default=True)
    owner = relationship("SysUser", lazy="joined")


class OpsMember(TimestampMixin, Base):
    __tablename__ = "ops_member"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    user = relationship("SysUser", lazy="joined")


class OpsSchool(TimestampMixin, Base):
    __tablename__ = "ops_school"
    __table_args__ = (UniqueConstraint("project_id", "name"),
                      CheckConstraint("labor_cost >= 0 AND other_cost >= 0"),)
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    stage: Mapped[str] = mapped_column(String(24), default="contact")
    potential: Mapped[str] = mapped_column(String(16), default="medium")
    next_action: Mapped[str] = mapped_column(Text, default="")
    next_contact_date: Mapped[date | None] = mapped_column(Date)
    labor_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    other_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    note: Mapped[str] = mapped_column(Text, default="")


class OpsProduct(TimestampMixin, Base):
    __tablename__ = "ops_product"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    brand: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    unit: Mapped[str] = mapped_column(String(16), default="台")
    reference_price: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (CheckConstraint("reference_price >= 0"),)


class OpsEquipment(TimestampMixin, Base):
    __tablename__ = "ops_equipment"
    school_id: Mapped[int] = mapped_column(ForeignKey("ops_school.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("ops_product.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal]
    note: Mapped[str] = mapped_column(Text, default="")
    product = relationship("OpsProduct", lazy="joined")
    __table_args__ = (CheckConstraint("quantity > 0 AND unit_price >= 0"),)


class OpsIncome(TimestampMixin, Base):
    __tablename__ = "ops_income"
    school_id: Mapped[int] = mapped_column(ForeignKey("ops_school.id"), index=True)
    income_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal]
    source: Mapped[str] = mapped_column(String(128), default="财务收款表")
    note: Mapped[str] = mapped_column(Text, default="")
    operator_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    __table_args__ = (UniqueConstraint("school_id", "income_date"), CheckConstraint("amount >= 0"))


class OpsTask(TimestampMixin, Base):
    __tablename__ = "ops_task"
    project_id: Mapped[int] = mapped_column(ForeignKey("ops_project.id"), index=True)
    school_id: Mapped[int | None] = mapped_column(ForeignKey("ops_school.id"))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    due_date: Mapped[date] = mapped_column(Date, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="key")
    cadence: Mapped[str] = mapped_column(String(16), default="daily")
    status: Mapped[str] = mapped_column(String(16), default="todo")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    next_step: Mapped[str] = mapped_column(Text, default="")
    last_feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner = relationship("SysUser", foreign_keys=[owner_id], lazy="joined")
    school = relationship("OpsSchool", lazy="joined")
    nodes: Mapped[list["OpsTaskNode"]] = relationship(lazy="selectin", order_by="OpsTaskNode.seq")
    __table_args__ = (CheckConstraint("progress >= 0 AND progress <= 100"),)


class OpsTaskNode(TimestampMixin, Base):
    __tablename__ = "ops_task_node"
    task_id: Mapped[int] = mapped_column(ForeignKey("ops_task.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    seq: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpsTaskUpdate(TimestampMixin, Base):
    __tablename__ = "ops_task_update"
    task_id: Mapped[int] = mapped_column(ForeignKey("ops_task.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    kind: Mapped[str] = mapped_column(String(16), default="feedback")
    content: Mapped[str] = mapped_column(Text)
    next_step: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16))
    progress: Mapped[int] = mapped_column(Integer)
    author = relationship("SysUser", lazy="joined")
