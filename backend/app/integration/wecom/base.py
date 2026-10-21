"""企业微信适配器契约（PRD 8.3，Mock 与 Live 实现同一接口）。"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class DeptDto:
    wecom_dept_id: int
    name: str
    parent_id: int | None


@dataclass
class UserDto:
    userid: str
    name: str
    dept_ids: list[int]
    position: str | None = None
    mobile: str | None = None
    email: str | None = None
    status: int = 1  # 1 激活 2 禁用 4 未激活 5 退出


@dataclass
class ApprovalDto:
    sp_no: str
    template_id: str
    sp_name: str
    sp_status: int  # 1 审批中 2 已通过 3 已驳回 4 已撤销
    apply_time: datetime
    applicant_userid: str
    # 归一化后的表单：控件标题 → 值（金额为字符串，日期为 YYYY-MM-DD，选择器为选项文本）
    fields: dict[str, str] = field(default_factory=dict)
    approved_time: datetime | None = None
    raw: dict | None = None


@dataclass
class JournalDto:
    report_id: str
    userid: str
    template_name: str  # 日报/周报/月报
    report_time: datetime
    content: dict | None = None


@dataclass
class MessageDto:
    msg_type: str  # text | markdown | textcard
    title: str
    content: str
    url: str | None = None


@dataclass
class SendResult:
    ok: bool
    msgid: str | None = None
    invalid_users: list[str] = field(default_factory=list)
    error: str | None = None


class WeComProvider(Protocol):
    def get_addressbook(self) -> tuple[list[DeptDto], list[UserDto]]: ...
    def get_approval_list(self, start: datetime, end: datetime) -> list[str]: ...
    def get_approval_detail(self, sp_no: str) -> ApprovalDto: ...
    def send_app_message(self, user_ids: list[str], content: MessageDto) -> SendResult: ...
    def get_journal_records(self, start: datetime, end: datetime) -> list[JournalDto]: ...
    def exchange_user_info(self, code: str) -> str: ...
