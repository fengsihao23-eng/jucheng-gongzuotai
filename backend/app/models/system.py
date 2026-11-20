"""系统与权限域：部门、用户、角色、字典口径、通知、文件、任务日志、审计。"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import JSON, Base, SoftDeleteMixin, TimestampMixin
from app.core.config import settings


class SysDepartment(TimestampMixin, Base):
    __tablename__ = "sys_department"
    dept_code: Mapped[str] = mapped_column(String(8), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    wecom_dept_id: Mapped[int | None] = mapped_column(BigInteger)
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class SysRole(TimestampMixin, Base):
    __tablename__ = "sys_role"
    role_code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    dept_code: Mapped[str] = mapped_column(String(8))
    # {"menus": [...], "buttons": [...], "data_scope": "self|dept|all", "portal": "exec|finance|..."}
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)


class SysUserRole(Base):
    __tablename__ = "sys_user_role"
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True)


class SysUser(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "sys_user"
    wecom_userid: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("sys_department.id"))
    position: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(128))
    mobile: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1 在职 2 离职
    # 生产新账号默认等待单独授权；迁移保留旧账号，再由一次性名单脚本收回授权。
    workbench_enabled: Mapped[bool] = mapped_column(
        Boolean, default=lambda: settings.environment != "prod",
        server_default="false")
    password_hash: Mapped[str | None] = mapped_column(String(256))
    access_token: Mapped[str | None] = mapped_column(String(128), unique=True)  # MCP 个人令牌（存 sha256）
    access_token_hint: Mapped[str | None] = mapped_column(String(16))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)  # 门户卡片顺序等个人偏好

    department: Mapped["SysDepartment | None"] = relationship(lazy="joined")
    roles: Mapped[list["SysRole"]] = relationship(secondary="sys_user_role", lazy="selectin")

    @property
    def dept_code(self) -> str | None:
        return self.department.dept_code if self.department else None


class SysWecomItem(TimestampMixin, Base):
    """企业微信个人数据快照：待办、日程等按账号隔离保存，只本人可见。"""
    __tablename__ = "sys_wecom_item"
    __table_args__ = (UniqueConstraint("user_id", "service", "external_id"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE"), index=True)
    service: Mapped[str] = mapped_column(String(16), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(24))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    link: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SysWecomInbound(TimestampMixin, Base):
    """成员在企业微信「聚成工作台」应用里发来的消息。msg_id 唯一，用于去重企微的回调重试；只存摘要不存全文。"""
    __tablename__ = "sys_wecom_inbound"
    msg_id: Mapped[str] = mapped_column(String(96), unique=True)
    wecom_userid: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL"), index=True)
    msg_type: Mapped[str] = mapped_column(String(16))
    preview: Mapped[str | None] = mapped_column(String(200))  # 文字前 200 字 / 文件名 / 菜单键
    # received 已收到 | done 已回复 | ignored 无需处理 | failed 处理失败
    status: Mapped[str] = mapped_column(String(12), default="received")
    task_id: Mapped[str | None] = mapped_column(String(64))  # 小聚任务
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(String(300))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SysDict(TimestampMixin, Base):
    """口径/配置字典：payment_target、business_line、opportunity_stage、approval_template、portal_cards、
    attendance_rule、alert_lead_days、bank_template、clean_template…评审变更只改配置不改代码。"""
    __tablename__ = "sys_dict"
    __table_args__ = (UniqueConstraint("dict_type", "dict_key"),)
    dict_type: Mapped[str] = mapped_column(String(32), index=True)
    dict_key: Mapped[str] = mapped_column(String(64))
    dict_value: Mapped[dict | list | str | int | float | None] = mapped_column(JSON)
    remark: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer, default=1)


class SysNotify(TimestampMixin, Base):
    __tablename__ = "sys_notify"
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # wecom_app | inapp
    msg_type: Mapped[str] = mapped_column(String(32))  # morning_report|daily_expense|reminder|alert|overdue|job_failed|approval
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(255))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    send_status: Mapped[int] = mapped_column(Integer, default=0)  # 0 待发 1 成功 2 失败
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_status: Mapped[bool] = mapped_column(Boolean, default=False)


class SysFile(TimestampMixin, Base):
    """文件归档（本地目录 / COS 适配器）。key 形如 report/daily_expense/2026-10/xxx.xlsx；同 key 多版本。"""
    __tablename__ = "sys_file"
    storage_key: Mapped[str] = mapped_column(String(255), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))  # report|contract|bankflow|tax|tender|attendance|clean|hr_doc|lemoncloud
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))


class SyncJobLog(TimestampMixin, Base):
    __tablename__ = "sync_job_log"
    job_name: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule | manual
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # running|success|failed|skipped
    rows_affected: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    message: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)


class LogOperation(Base):
    """台账人工改动、报表下载、配置变更等操作审计（不做逻辑删除）。"""
    __tablename__ = "log_operation"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    action: Mapped[str] = mapped_column(String(64), index=True)  # contract.update / report.download / dict.update …
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class McpTool(TimestampMixin, Base):
    __tablename__ = "mcp_tool"
    tool_code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    allowed_roles: Mapped[list] = mapped_column(JSON, default=list)  # 角色码或部门码，["*"] 全员
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class LogMcpAudit(Base):
    __tablename__ = "log_mcp_audit"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    tool_code: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[dict | None] = mapped_column(JSON)  # 脱敏后
    outcome: Mapped[str] = mapped_column(String(16))  # ok|denied|error|rate_limited|unauthorized
    result_summary: Mapped[str | None] = mapped_column(Text)
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SysWecomApiCall(TimestampMixin, Base):
    """企业微信接口中心的调用审计：记录谁调了哪个接口、结果与耗时，不存凭证和报文原文。"""
    __tablename__ = "sys_wecom_api_call"
    user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id", ondelete="SET NULL"), index=True)
    doc_path: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    credential: Mapped[str] = mapped_column(String(16))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    errcode: Mapped[int | None] = mapped_column(Integer)
    errmsg: Mapped[str | None] = mapped_column(String(300))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_summary: Mapped[dict | None] = mapped_column(JSON)
    response_bytes: Mapped[int] = mapped_column(Integer, default=0)
