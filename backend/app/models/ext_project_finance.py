"""历史项目报表与正式凭证之间的来源链；月度汇总和应付快照不冒充实际收支。"""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSON, TimestampMixin


class FinProjectReport(TimestampMixin, Base):
    __tablename__ = "fin_project_report"
    source_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source_name: Mapped[str] = mapped_column(String(255))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    period: Mapped[str] = mapped_column(String(7), index=True)
    primary_project_code: Mapped[str] = mapped_column(ForeignKey("biz_project.project_code"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("sys_file.id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    summary: Mapped[dict] = mapped_column(JSON)
    issues: Mapped[list] = mapped_column(JSON)
    source_cells: Mapped[list] = mapped_column(JSON)


class FinProjectReportContract(Base):
    __tablename__ = "fin_project_report_contract"
    report_id: Mapped[int] = mapped_column(ForeignKey("fin_project_report.id"), primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("biz_contract.id"), primary_key=True)


class FinProjectReportEntry(TimestampMixin, Base):
    __tablename__ = "fin_project_report_entry"
    __table_args__ = (UniqueConstraint("report_id", "sheet", "source_row"),)
    report_id: Mapped[int] = mapped_column(ForeignKey("fin_project_report.id"), index=True)
    sheet: Mapped[str] = mapped_column(String(64))
    source_row: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    cost_id: Mapped[int | None] = mapped_column(ForeignKey("biz_project_cost.id"), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("biz_payment.id"))
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("fin_receipt.id"))
