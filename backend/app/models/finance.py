"""财务域：银行流水、出纳日记账、柠檬云凭证/余额、税务申报；集成同步记录。"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import JSON, Base, TimestampMixin

FLOW_MATCH_STATUS = {0: "未核对", 1: "已关联报销", 2: "已关联回款", 3: "公户支出", 4: "已关联项目成本", 5: "其他收入"}


class FinBankImport(TimestampMixin, Base):
    """一次流水文件导入批次（含解析报告）。"""
    __tablename__ = "fin_bank_import"
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    filename: Mapped[str] = mapped_column(String(255))
    bank_template: Mapped[str] = mapped_column(String(32))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    inserted_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)  # [{row, field, message}]
    balance_check: Mapped[dict | None] = mapped_column(JSON)
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload|auto_dir|mcp
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class FinBankFlow(TimestampMixin, Base):
    __tablename__ = "fin_bank_flow"
    bank_account: Mapped[str] = mapped_column(String(64), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    trade_time: Mapped[datetime | None] = mapped_column(DateTime)
    direction: Mapped[int] = mapped_column(Integer)  # 1 收 2 支
    amount: Mapped[Decimal]
    counterparty: Mapped[str | None] = mapped_column(String(128))
    counterparty_account: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(String(255))
    balance: Mapped[Decimal | None]
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)  # 账号+日期+金额+摘要(+流水号)指纹
    import_id: Mapped[int | None] = mapped_column(ForeignKey("fin_bank_import.id"))
    source_file: Mapped[str | None] = mapped_column(String(255))
    source_row: Mapped[int | None] = mapped_column(Integer)
    match_status: Mapped[int] = mapped_column(Integer, default=0, index=True)
    biz_ref: Mapped[str | None] = mapped_column(String(64))
    matched_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinCashJournal(TimestampMixin, Base):
    __tablename__ = "fin_cash_journal"
    journal_date: Mapped[date] = mapped_column(Date, index=True)
    account_type: Mapped[str] = mapped_column(String(8))  # cash | bank
    bank_account: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal]
    summary: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(32))  # 回款|报销|采购|工资|税费|其他
    project_code: Mapped[str | None] = mapped_column(String(32), index=True)
    biz_ref: Mapped[str | None] = mapped_column(String(64))
    bank_flow_id: Mapped[int | None] = mapped_column(ForeignKey("fin_bank_flow.id"), unique=True)
    register_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    source_type: Mapped[int] = mapped_column(Integer, default=2)  # 1 流水自动 2 手工


class FinVoucher(TimestampMixin, Base):
    __tablename__ = "fin_voucher"
    __table_args__ = (UniqueConstraint("voucher_no", "line_no", "voucher_date"),)
    voucher_no: Mapped[str] = mapped_column(String(32), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    voucher_date: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal]
    direction: Mapped[int] = mapped_column(Integer)  # 1 借 2 贷
    subject_code: Mapped[str] = mapped_column(String(32), index=True)
    subject_name: Mapped[str] = mapped_column(String(64))
    aux_project: Mapped[str | None] = mapped_column(String(32), index=True)
    sync_batch: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[str] = mapped_column(String(8), default="api")  # api | file


class FinSubjectBalance(TimestampMixin, Base):
    __tablename__ = "fin_subject_balance"
    __table_args__ = (UniqueConstraint("period", "subject_code"),)
    period: Mapped[str] = mapped_column(String(7), index=True)
    subject_code: Mapped[str] = mapped_column(String(32))
    subject_name: Mapped[str] = mapped_column(String(64))
    debit_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))  # 本期借方发生
    credit_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    debit_balance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    credit_balance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    sync_batch: Mapped[str | None] = mapped_column(String(32))


class SyncLemonBatch(TimestampMixin, Base):
    """柠檬云同步批次 + 对账结果（凭证数/金额合计与来源比对）。"""
    __tablename__ = "sync_lemon_batch"
    batch_no: Mapped[str] = mapped_column(String(32), unique=True)
    channel: Mapped[str] = mapped_column(String(8))  # api | file
    period: Mapped[str] = mapped_column(String(7))
    voucher_count: Mapped[int] = mapped_column(Integer, default=0)
    debit_total: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    credit_total: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    source_count: Mapped[int | None] = mapped_column(Integer)
    source_total: Mapped[Decimal | None]
    reconcile_status: Mapped[str] = mapped_column(String(16), default="pending")  # pass|diff|pending
    diffs: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)  # 行级错误定位
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))


class SyncTaxFiling(TimestampMixin, Base):
    __tablename__ = "sync_tax_filing"
    __table_args__ = (UniqueConstraint("period", "tax_type"),)
    period: Mapped[str] = mapped_column(String(7), index=True)
    tax_type: Mapped[str] = mapped_column(String(32))  # 增值税|企业所得税|个人所得税|附加税
    taxable_amount: Mapped[Decimal | None]  # 计税依据（销售额/利润）
    payable_amount: Mapped[Decimal]
    paid_amount: Mapped[Decimal | None]
    filing_date: Mapped[date | None] = mapped_column(Date)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class SyncWecomApproval(TimestampMixin, Base):
    __tablename__ = "sync_wecom_approval"
    sp_no: Mapped[str] = mapped_column(String(64), unique=True)
    template_id: Mapped[str | None] = mapped_column(String(64))
    template_key: Mapped[str] = mapped_column(String(32), index=True)  # contract|expense|opportunity|unknown
    sp_name: Mapped[str | None] = mapped_column(String(64))
    applicant_userid: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[int] = mapped_column(Integer)  # 1 审批中 2 通过 3 驳回 4 撤销
    apply_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict | None] = mapped_column(JSON)
    received_via: Mapped[str] = mapped_column(String(16), default="callback")  # callback|reconcile|mock
    dispatch_status: Mapped[int] = mapped_column(Integer, default=0, index=True)  # 0 未处理 1 已分发 2 失败(待人工) 3 无需处理
    dispatch_target: Mapped[str | None] = mapped_column(String(64))  # 生成的业务单号
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str | None] = mapped_column(Text)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OaSubmission(TimestampMixin, Base):
    """日报/周报/月报提交记录（企微汇报 API）。应提交名单由 sys_dict(report_rule) 决定。"""
    __tablename__ = "oa_submission"
    __table_args__ = (UniqueConstraint("user_id", "report_type", "period"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(8))  # daily|weekly|monthly
    period: Mapped[str] = mapped_column(String(16), index=True)  # 2026-10-20 / 2026-W43 / 2026-10
    submit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wecom_report_id: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[dict | None] = mapped_column(JSON)
    stat_status: Mapped[int] = mapped_column(Integer, default=1)  # 1 已提交 2 迟交


class RptReport(TimestampMixin, Base):
    """报表快照（每日支出/周利润/公司周报/月报/项目运营/归因等）。同 type+period 再生成即新版本。"""
    __tablename__ = "rpt_report"
    report_type: Mapped[str] = mapped_column(String(32), index=True)
    period: Mapped[str] = mapped_column(String(32), index=True)  # 2026-10-19 / 2026-W42 / 2026-10
    title: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    file_ids: Mapped[list] = mapped_column(JSON, default=list)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule|manual|mcp
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)


class RptDailyExpense(TimestampMixin, Base):
    __tablename__ = "rpt_daily_expense"
    stat_date: Mapped[date] = mapped_column(Date, index=True)
    dimension: Mapped[str] = mapped_column(String(8))  # dept|project
    dim_value: Mapped[str] = mapped_column(String(64))
    total_amount: Mapped[Decimal]
    expense_count: Mapped[int] = mapped_column(Integer)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("rpt_report.id", ondelete="CASCADE"))


class RptProjectProfit(TimestampMixin, Base):
    __tablename__ = "rpt_project_profit"
    week_no: Mapped[str] = mapped_column(String(10), index=True)
    project_code: Mapped[str] = mapped_column(String(32))
    income: Mapped[Decimal]
    cost: Mapped[Decimal]
    gross_profit: Mapped[Decimal]
    gross_margin: Mapped[Decimal | None]
    report_id: Mapped[int | None] = mapped_column(ForeignKey("rpt_report.id", ondelete="CASCADE"))
