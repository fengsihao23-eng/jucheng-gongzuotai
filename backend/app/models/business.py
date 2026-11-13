"""项目、合同、回款、商机、报销、市场情报等业务域。金额统一 Numeric(14,2)。"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import JSON, Base, SoftDeleteMixin, TimestampMixin

PROJECT_STATUS = {1: "招标中", 2: "在建", 3: "已验收", 4: "运维", 5: "结项"}
MILESTONE_STATUS = {1: "未开始", 2: "进行中", 3: "已完成", 4: "延期"}
CONTRACT_STATUS = {1: "履行中", 2: "已归档", 3: "已终止"}
EXPENSE_STATUS = {1: "审批中", 2: "已通过", 3: "已驳回", 4: "已归集"}


class BizProject(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "biz_project"
    project_code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    business_line: Mapped[str] = mapped_column(String(8), index=True)  # ZY/XT/YW/RJ
    customer_name: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    market_owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))  # 市场跟进人
    status: Mapped[int] = mapped_column(Integer, default=1)
    is_key: Mapped[bool] = mapped_column(Boolean, default=False)  # 重点项目
    budget_amount: Mapped[Decimal | None]  # 预算/投标额
    contract_amount: Mapped[Decimal | None]
    bid_deadline: Mapped[date | None] = mapped_column(Date)  # 招标截止
    start_date: Mapped[date | None] = mapped_column(Date)
    plan_end_date: Mapped[date | None] = mapped_column(Date)
    milestone_template: Mapped[str | None] = mapped_column(String(32))
    remark: Mapped[str | None] = mapped_column(Text)

    owner = relationship("SysUser", foreign_keys=[owner_id], lazy="joined")
    milestones: Mapped[list["BizProjectMilestone"]] = relationship(
        back_populates="project", order_by="BizProjectMilestone.seq", cascade="all, delete-orphan")


class BizProjectMilestone(TimestampMixin, Base):
    __tablename__ = "biz_project_milestone"
    project_id: Mapped[int] = mapped_column(ForeignKey("biz_project.id", ondelete="CASCADE"), index=True)
    node_name: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    duty: Mapped[str | None] = mapped_column(String(255))  # 岗位职责要求
    plan_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[int] = mapped_column(Integer, default=1)
    alert_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    progress_note: Mapped[str | None] = mapped_column(Text)

    project: Mapped[BizProject] = relationship(back_populates="milestones")
    owner = relationship("SysUser", lazy="joined")


class BizMilestoneLog(Base):
    """节点变更留痕（操作人/时间/前后值）。"""
    __tablename__ = "biz_milestone_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("biz_project.id", ondelete="CASCADE"), index=True)
    milestone_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32))  # create|update|delete|reorder|complete
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    source: Mapped[str] = mapped_column(String(16), default="web")  # web|mcp|system
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BizProjectFeedback(TimestampMixin, Base):
    """重点项目/重点事件每日进展反馈（总办看板 ②、市场跟进看板）。"""
    __tablename__ = "biz_project_feedback"
    project_id: Mapped[int] = mapped_column(ForeignKey("biz_project.id", ondelete="CASCADE"), index=True)
    feedback_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="progress")  # progress|event|risk
    content: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)  # 0 正常 1 关注 2 风险
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    reporter = relationship("SysUser", lazy="joined")


class BizProjectReview(TimestampMixin, Base):
    """项目复盘（结项自动生成初稿 → 人工定稿 → 沉淀经验库）。"""
    __tablename__ = "biz_project_review"
    project_id: Mapped[int] = mapped_column(ForeignKey("biz_project.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    goal: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    lessons: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1 初稿 2 已定稿
    author_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))


class BizContract(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "biz_contract"
    contract_no: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str | None] = mapped_column(String(128))
    project_code: Mapped[str | None] = mapped_column(String(32), index=True)
    customer_name: Mapped[str] = mapped_column(String(128), index=True)
    amount: Mapped[Decimal]
    sign_date: Mapped[date] = mapped_column(Date, index=True)
    business_line: Mapped[str] = mapped_column(String(8), index=True)
    region: Mapped[str | None] = mapped_column(String(16), index=True)  # 所属区域业绩：市直/江城/阳春/阳东/阳西/海陵湾
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))  # 销售负责人
    source_approval_no: Mapped[str | None] = mapped_column(String(64), index=True)
    source_type: Mapped[int] = mapped_column(Integer, default=2)  # 1 企微审批自动登记 2 人工录入 3 Excel 清洗导入
    status: Mapped[int] = mapped_column(Integer, default=1)
    archive_file_id: Mapped[int | None] = mapped_column(ForeignKey("sys_file.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remark: Mapped[str | None] = mapped_column(Text)

    payments: Mapped[list["BizPayment"]] = relationship(back_populates="contract", order_by="BizPayment.payment_date")
    plans: Mapped[list["BizPaymentPlan"]] = relationship(back_populates="contract", order_by="BizPaymentPlan.seq",
                                                          cascade="all, delete-orphan")


class BizPaymentPlan(TimestampMixin, Base):
    """合同回款计划：合同归档后从原件中自动读取付款条款生成。"""
    __tablename__ = "biz_payment_plan"
    contract_id: Mapped[int] = mapped_column(ForeignKey("biz_contract.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    stage_name: Mapped[str] = mapped_column(String(64))  # 预付款/进度款/验收款/质保金
    ratio: Mapped[Decimal | None]
    amount: Mapped[Decimal]
    due_date: Mapped[date | None] = mapped_column(Date)
    condition_text: Mapped[str | None] = mapped_column(Text)  # 原文条款
    received_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1 未到期 2 待回款 3 部分回款 4 已回款 5 逾期
    contract: Mapped[BizContract] = relationship(back_populates="plans")


class BizPayment(TimestampMixin, Base):
    __tablename__ = "biz_payment"
    contract_id: Mapped[int] = mapped_column(ForeignKey("biz_contract.id"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("biz_payment_plan.id"))
    amount: Mapped[Decimal]
    payment_date: Mapped[date] = mapped_column(Date, index=True)  # = 流水到账日
    bank_flow_id: Mapped[int | None] = mapped_column(ForeignKey("fin_bank_flow.id"), unique=True)
    register_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    source_type: Mapped[int] = mapped_column(Integer, default=2)  # 1 流水核对生成 2 人工 3 归档自动关联
    remark: Mapped[str | None] = mapped_column(String(255))
    contract: Mapped[BizContract] = relationship(back_populates="payments")


class BizOpportunity(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "biz_opportunity"
    opp_no: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    customer_name: Mapped[str] = mapped_column(String(128))
    business_line: Mapped[str] = mapped_column(String(8))
    stage: Mapped[str] = mapped_column(String(16), index=True)  # lead|contact|quote|negotiation|won|lost
    amount: Mapped[Decimal]
    expected_sign_date: Mapped[date | None] = mapped_column(Date)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    source_approval_no: Mapped[str | None] = mapped_column(String(64), index=True)
    source_type: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open|won|lost
    project_code: Mapped[str | None] = mapped_column(String(32))
    contract_no: Mapped[str | None] = mapped_column(String(32), index=True)  # 赢单后关联的合同
    remark: Mapped[str | None] = mapped_column(Text)
    owner = relationship("SysUser", lazy="joined")


class BizExpenseReport(TimestampMixin, Base):
    __tablename__ = "biz_expense_report"
    approval_no: Mapped[str] = mapped_column(String(64), unique=True)
    applicant_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"), index=True)
    dept_code: Mapped[str | None] = mapped_column(String(8), index=True)
    project_code: Mapped[str | None] = mapped_column(String(32), index=True)  # 空 = 公共费用
    amount: Mapped[Decimal]
    expense_date: Mapped[date] = mapped_column(Date, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    category: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer, default=1)
    pay_status: Mapped[int] = mapped_column(Integer, default=0)  # 0 未付 1 已付（流水核对）
    bank_flow_id: Mapped[int | None] = mapped_column(ForeignKey("fin_bank_flow.id"))
    allocated_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))  # 人工补归集人
    detail: Mapped[dict | None] = mapped_column(JSON)
    applicant = relationship("SysUser", foreign_keys=[applicant_id], lazy="joined")
    items: Mapped[list["BizExpenseItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class BizExpenseItem(TimestampMixin, Base):
    __tablename__ = "biz_expense_item"
    report_id: Mapped[int] = mapped_column(ForeignKey("biz_expense_report.id", ondelete="CASCADE"), index=True)
    item_name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal]
    invoice_count: Mapped[int] = mapped_column(Integer, default=1)


class BizProjectCost(TimestampMixin, Base):
    """人工标记的采购成本等（项目成本构成之一）。"""
    __tablename__ = "biz_project_cost"
    project_code: Mapped[str] = mapped_column(String(32), index=True)
    cost_date: Mapped[date] = mapped_column(Date, index=True)
    cost_type: Mapped[str] = mapped_column(String(32))  # 采购|分包|人工|其他
    amount: Mapped[Decimal]
    summary: Mapped[str | None] = mapped_column(String(255))
    bank_flow_id: Mapped[int | None] = mapped_column(ForeignKey("fin_bank_flow.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class MktIntel(TimestampMixin, Base):
    """竞品与行业情报（结构化沉淀）。"""
    __tablename__ = "mkt_intel"
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # competitor|industry|policy|bid
    competitor: Mapped[str | None] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_name: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    importance: Mapped[int] = mapped_column(Integer, default=1)  # 1 一般 2 重要 3 紧急
    published_on: Mapped[date | None] = mapped_column(Date)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
