"""柠檬云适配器契约（PRD 8.3 LemonCloudProvider，Mock 与 Live 实现同一接口）。"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass
class DateRange:
    start: date
    end: date


@dataclass
class VoucherDto:
    voucher_no: str  # 记-001
    line_no: int
    voucher_date: date
    summary: str
    subject_code: str
    subject_name: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    aux_project: str | None = None

    @property
    def direction(self) -> int:
        return 1 if self.debit else 2

    @property
    def amount(self) -> Decimal:
        return self.debit or self.credit


@dataclass
class SubjectBalanceDto:
    period: str
    subject_code: str
    subject_name: str
    opening_debit: Decimal = Decimal("0")
    opening_credit: Decimal = Decimal("0")
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    debit_balance: Decimal = Decimal("0")
    credit_balance: Decimal = Decimal("0")


@dataclass
class CashJournalDto:
    journal_date: date
    account: str
    direction: int
    amount: Decimal
    summary: str
    voucher_no: str | None = None


@dataclass
class ProjectAuxDto:
    project_code: str
    income: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    subjects: list[dict] = field(default_factory=list)


class LemonCloudProvider(Protocol):
    def fetch_vouchers(self, date_range: DateRange) -> list[VoucherDto]: ...
    def fetch_subject_balances(self, period: str) -> list[SubjectBalanceDto]: ...
    def fetch_cash_journal(self, date_range: DateRange) -> list[CashJournalDto]: ...
    def fetch_project_aux(self, project_code: str) -> ProjectAuxDto: ...


# 小企业会计准则常用科目（mock 生成与文件解析的科目名兜底）
SUBJECTS = {
    "1001": "库存现金", "1002": "银行存款", "1122": "应收账款", "1221": "其他应收款",
    "2202": "应付账款", "2211": "应付职工薪酬", "2221": "应交税费", "3001": "实收资本",
    "5001": "主营业务收入", "5401": "主营业务成本", "5403": "合同履约成本", "5601": "销售费用",
    "5602": "管理费用", "5603": "财务费用", "5301": "营业外收入",
}
