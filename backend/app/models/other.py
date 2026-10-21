"""人事、客服、产品中心（标书）、知识库镜像、Excel 清洗等 P1 域。"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import JSON, Base, TimestampMixin


# ---------------- 人事 ----------------
class HrEmployee(TimestampMixin, Base):
    """员工人事档案（与 sys_user 一对一；编制/流失/合同到期分析数据源）。"""
    __tablename__ = "hr_employee"
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), unique=True)
    employee_no: Mapped[str] = mapped_column(String(16), unique=True)
    gender: Mapped[str | None] = mapped_column(String(4))
    hire_date: Mapped[date] = mapped_column(Date)
    probation_end_date: Mapped[date | None] = mapped_column(Date)
    contract_start_date: Mapped[date | None] = mapped_column(Date)
    contract_end_date: Mapped[date | None] = mapped_column(Date)
    leave_date: Mapped[date | None] = mapped_column(Date)
    leave_reason: Mapped[str | None] = mapped_column(String(128))
    education: Mapped[str | None] = mapped_column(String(16))
    employ_type: Mapped[str] = mapped_column(String(16), default="正式")  # 正式|试用|实习|外包
    annual_leave_days: Mapped[Decimal] = mapped_column(default=Decimal("5"))
    user = relationship("SysUser", lazy="joined")


class HrHeadcountPlan(TimestampMixin, Base):
    __tablename__ = "hr_headcount_plan"
    __table_args__ = (UniqueConstraint("year", "dept_code"),)
    year: Mapped[int] = mapped_column(Integer)
    dept_code: Mapped[str] = mapped_column(String(8))
    planned: Mapped[int] = mapped_column(Integer)


class HrAttendanceImport(TimestampMixin, Base):
    __tablename__ = "hr_attendance_import"
    period: Mapped[str] = mapped_column(String(7), index=True)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    filename: Mapped[str] = mapped_column(String(255))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    exception_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class HrAttendanceRecord(TimestampMixin, Base):
    __tablename__ = "hr_attendance_record"
    __table_args__ = (UniqueConstraint("user_id", "work_date"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    check_in: Mapped[datetime | None] = mapped_column(DateTime)
    check_out: Mapped[datetime | None] = mapped_column(DateTime)
    exception_type: Mapped[str | None] = mapped_column(String(32))  # 逗号分隔：迟到,早退,缺卡,旷工,请假,加班
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    early_minutes: Mapped[int] = mapped_column(Integer, default=0)
    overtime_hours: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    leave_type: Mapped[str | None] = mapped_column(String(16))
    leave_hours: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    import_id: Mapped[int | None] = mapped_column(ForeignKey("hr_attendance_import.id"))
    source_file: Mapped[str | None] = mapped_column(String(255))
    source_row: Mapped[int | None] = mapped_column(Integer)


class HrAttendanceSummary(TimestampMixin, Base):
    __tablename__ = "hr_attendance_summary"
    __table_args__ = (UniqueConstraint("user_id", "period"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    work_days: Mapped[int] = mapped_column(Integer, default=0)
    late_times: Mapped[int] = mapped_column(Integer, default=0)
    early_times: Mapped[int] = mapped_column(Integer, default=0)
    missing_times: Mapped[int] = mapped_column(Integer, default=0)
    absent_times: Mapped[int] = mapped_column(Integer, default=0)
    overtime_hours: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    leave_days: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    report_file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))


class HrContractAlert(TimestampMixin, Base):
    __tablename__ = "hr_contract_alert"
    __table_args__ = (UniqueConstraint("user_id", "alert_type", "due_date"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    alert_type: Mapped[str] = mapped_column(String(16))  # contract | probation
    due_date: Mapped[date] = mapped_column(Date)
    days_left: Mapped[int] = mapped_column(Integer)
    alert_sent: Mapped[bool] = mapped_column(default=False)
    handled: Mapped[bool] = mapped_column(default=False)
    handle_note: Mapped[str | None] = mapped_column(String(255))


class HrDocument(TimestampMixin, Base):
    """生成的人事文书（入职资料包/离职证明/通知/制度/会议纪要…）。"""
    __tablename__ = "hr_document"
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128))
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_hints: Mapped[list] = mapped_column(JSON, default=list)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class HrCandidate(TimestampMixin, Base):
    __tablename__ = "hr_candidate"
    name: Mapped[str] = mapped_column(String(32))
    position: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str | None] = mapped_column(String(32))
    education: Mapped[str | None] = mapped_column(String(16))
    years: Mapped[int | None] = mapped_column(Integer)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String(16), default="resume")  # resume|screen_pass|interview|offer|hired|rejected
    screen_score: Mapped[int | None] = mapped_column(Integer)
    screen_reason: Mapped[str | None] = mapped_column(Text)
    interview_eval: Mapped[dict | None] = mapped_column(JSON)


# ---------------- 客服 ----------------
class CsTicket(TimestampMixin, Base):
    __tablename__ = "cs_ticket"
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True)
    customer_name: Mapped[str] = mapped_column(String(128), index=True)
    contact: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(8))  # 电话|微信|邮件|企微
    device: Mapped[str | None] = mapped_column(String(64))  # 涉及系统/设备
    problem_desc: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), index=True)
    dedupe_hash: Mapped[str] = mapped_column(String(64), index=True)
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("cs_ticket.id"))
    related_ticket_ids: Mapped[list] = mapped_column(JSON, default=list)
    suggestion: Mapped[dict | None] = mapped_column(JSON)  # 排查建议（来源出处）
    root_cause: Mapped[str | None] = mapped_column(String(255))
    root_cause_type: Mapped[str | None] = mapped_column(String(32))  # 配置|硬件|网络|软件缺陷|操作|第三方
    solution: Mapped[str | None] = mapped_column(Text)
    handler_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1 登记 2 排查中 3 已解决 4 已归因
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------- 产品中心 ----------------
class ProdTenderDoc(TimestampMixin, Base):
    """招标文件/需求书/投标文件/投喂资料（解析后条款化）。"""
    __tablename__ = "prod_tender_doc"
    doc_type: Mapped[str] = mapped_column(String(16))  # tender(招标/需求书)|bid(投标文件)|material(投喂资料)
    title: Mapped[str] = mapped_column(String(200))
    project_code: Mapped[str | None] = mapped_column(String(32))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    content_text: Mapped[str] = mapped_column(Text)
    clauses: Mapped[list] = mapped_column(JSON, default=list)  # [{no, text, kind(资质/参数/格式/商务/★), required}]
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class ProdBidCheck(TimestampMixin, Base):
    """合规检查会话（多轮）：每轮按一个维度推进，累积整改清单。"""
    __tablename__ = "prod_bid_check"
    tender_doc_id: Mapped[int] = mapped_column(ForeignKey("prod_tender_doc.id"))
    bid_doc_id: Mapped[int | None] = mapped_column(ForeignKey("prod_tender_doc.id"))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done
    rounds: Mapped[list] = mapped_column(JSON, default=list)  # [{dimension, question, answer, findings:[...]}]
    findings: Mapped[list] = mapped_column(JSON, default=list)  # [{clause_no, clause_text, dimension, severity, deviation, suggestion, evidence:[{title,url,excerpt}]}]
    report_file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class ProdGeneration(TimestampMixin, Base):
    """实施方案/安装调试方案/售前方案生成任务（带出处标注）。"""
    __tablename__ = "prod_generation"
    gen_type: Mapped[str] = mapped_column(String(32))  # implementation|installation|presales
    title: Mapped[str] = mapped_column(String(200))
    tender_doc_id: Mapped[int | None] = mapped_column(ForeignKey("prod_tender_doc.id"))
    material_ids: Mapped[list] = mapped_column(JSON, default=list)
    sections: Mapped[list] = mapped_column(JSON, default=list)  # [{heading, body, citations:[{n,title,url,excerpt}]}]
    engine: Mapped[str] = mapped_column(String(16), default="rule")  # llm|rule
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


# ---------------- 知识库（乐享镜像，检索在本地进行，出处链接指回乐享） ----------------
class KbDocument(TimestampMixin, Base):
    __tablename__ = "kb_document"
    source: Mapped[str] = mapped_column(String(16), default="lexiang")
    source_doc_id: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(16), index=True)  # 制度|售前|方案模板|案例|资质|客服|产品
    url: Mapped[str] = mapped_column(String(500))
    content_text: Mapped[str] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(32))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KbChunk(TimestampMixin, Base):
    __tablename__ = "kb_chunk"
    doc_id: Mapped[int] = mapped_column(ForeignKey("kb_document.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(200))
    chunk_text: Mapped[str] = mapped_column(Text)
    terms: Mapped[list] = mapped_column(JSON, default=list)  # 分词结果（bigram）用于打分
    document = relationship("KbDocument", lazy="joined")


# ---------------- Excel 清洗 ----------------
class CleanJob(TimestampMixin, Base):
    __tablename__ = "clean_job"
    target: Mapped[str] = mapped_column(String(32))  # contract|opportunity|payment|project
    filename: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    status: Mapped[str] = mapped_column(String(16), default="preview")  # preview|imported|cancelled
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)  # 源列 → 标准字段
    rows: Mapped[list] = mapped_column(JSON, default=list)  # 清洗后行（含 _row, _status, _issues, _changes）
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    report_file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
