"""Access and follow-up records added by the management workbench."""
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class OpsTaskPolicy(Base):
    __tablename__ = "ops_task_policy"
    task_id: Mapped[int] = mapped_column(ForeignKey("ops_task.id", ondelete="CASCADE"), primary_key=True)
    restricted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    kind: Mapped[str] = mapped_column(String(16), default="general", server_default="general")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    archive_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (CheckConstraint("kind IN ('general', 'outreach')", name="ck_ops_task_policy_kind"),)


class OpsTaskAudience(Base):
    __tablename__ = "ops_task_audience"
    task_id: Mapped[int] = mapped_column(ForeignKey("ops_task.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), primary_key=True)


class BizCollectionFollowup(TimestampMixin, Base):
    __tablename__ = "biz_collection_followup"
    contract_id: Mapped[int] = mapped_column(ForeignKey("biz_contract.id", ondelete="CASCADE"), index=True)
    # Payment plans can be replaced; keep the former plan identifier and the label for history.
    plan_key: Mapped[int] = mapped_column(BigInteger)
    label: Mapped[str] = mapped_column(String(64))
    expected_on: Mapped[date | None] = mapped_column(Date)
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (UniqueConstraint("contract_id", "plan_key"),)


class BizCollectionEntry(TimestampMixin, Base):
    __tablename__ = "biz_collection_entry"
    followup_id: Mapped[int] = mapped_column(ForeignKey("biz_collection_followup.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    kind: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    expected_on: Mapped[date | None] = mapped_column(Date)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    __table_args__ = (CheckConstraint("kind IN ('note', 'request', 'reply')", name="ck_biz_collection_entry_kind"),)


class BizProjectFollowSetting(Base):
    __tablename__ = "biz_project_follow_setting"
    project_id: Mapped[int] = mapped_column(ForeignKey("biz_project.id", ondelete="CASCADE"), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
