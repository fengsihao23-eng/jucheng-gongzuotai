"""The template fields drive previews, validation and visible ledger columns."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from app.core.timeutil import today

MAX_ROWS = 5000
MAX_COLUMNS = 100
MAX_BYTES = 20 * 1024 * 1024
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"
    aliases: tuple[str, ...] = ()
    required: bool = False
    size: int = 128
    visible: bool = True

    def headers(self):
        return {header(v) for v in (self.key, self.label, *self.aliases)}

    def meta(self):
        return {"key": self.key, "label": self.label, "type": self.kind,
                "required": self.required, "size": self.size, "visible": self.visible}


@dataclass(frozen=True)
class Ledger:
    label: str
    menu: str
    button: str
    fields: tuple[Field, ...]
    totals: tuple[tuple[str, str], ...]

    def meta(self, writable):
        return {"label": self.label, "menu": self.menu, "can_import": writable,
                "fields": [f.meta() for f in self.fields], "totals": dict(self.totals)}


SEQ = Field("sequence", "序号", aliases=("序", "行号", "NO", "index"), size=32)
RECEIVED = Field("date", "收款日期", "date", ("日期", "到账日期", "入账日期", "交易日期", "付款日", "收款时间", "入账时间"), True)
DAY = Field("date", "日期", "date", ("支付日期", "付款日期", "交易日期", "支出日期", "记账日期", "发生时间", "记账日"), True)
SUMMARY = Field("summary", "摘要", aliases=("事由", "用途", "说明", "支出用途", "交易摘要", "业务摘要"), size=500)
PROJECT = Field("project", "项目", aliases=("归属项目", "所属项目", "项目名称", "项目编码", "费用归属"))
NOTE = Field("note", "备注", aliases=("附言", "其他说明", "备注说明"), size=500)
REF = Field("reference", "流水号", aliases=("交易流水号", "银行流水号", "交易单号", "订单号", "凭证号", "商户单号", "交易编号"), visible=False)
APPROVAL = Field("approval_no", "审批编号", aliases=("申请编号", "审批单号", "报销单号", "申请单号"), size=64)
CASH_APPROVAL = Field("approval_no", "申请编号", aliases=("审批编号", "审批单号", "报销单号", "申请单号"), size=64)

LEDGERS = {
    "bank_receipt": Ledger("收款记录明细", "receivables", "receivables:receipt", (
        SEQ, RECEIVED,
        Field("account", "收款银行", aliases=("收款账户", "收款账号", "到账银行", "收款行", "银行账户", "账户", "收款户名"), required=True),
        Field("payer", "汇款公司", aliases=("付款方", "付款人", "打款方", "付款单位", "汇款单位", "对方户名", "付款户名"), required=True),
        Field("net", "收入金额", "money", ("收款金额", "到账金额", "入账金额", "实收金额", "收款", "收入", "金额"), True),
        SUMMARY, PROJECT, REF, NOTE,
    ), (("inflow", "收入金额"),)),
    "qr_receipt": Ledger("二维码收款明细", "receivables", "receivables:receipt", (
        SEQ, RECEIVED,
        Field("account", "收款账户", aliases=("收款银行", "账户", "渠道", "收款渠道", "账号", "账户名称", "收款账号"), required=True),
        Field("gross", "交易金额", "money", ("订单金额", "应收金额", "交易原金额", "支付金额")),
        Field("fee", "手续费", "money", ("交易手续费", "渠道费", "费率金额", "服务费", "扣费")),
        Field("net", "实收金额", "money", ("净收金额", "实际到账", "入账金额", "实收", "到账金额", "收款金额", "收入金额", "金额")),
        PROJECT, REF, SUMMARY, NOTE,
    ), (("inflow", "实收金额"), ("fee", "手续费"))),
    "bank_expense": Ledger("银行支出日记账", "expenses", "journal:write", (
        SEQ, DAY,
        Field("account", "汇款公司", aliases=("汇款单位", "付款单位", "付款银行", "汇出银行", "付款账户", "汇款账户", "付款方", "账户"), required=True),
        SUMMARY,
        Field("payee", "收款方资料", aliases=("收款方", "收款单位", "收款人", "收款户名", "收款公司", "对方单位", "对方户名"), required=True),
        Field("payment", "金额", "money", ("付款金额", "支付金额", "支出金额", "汇款金额", "实付金额", "借方发生额"), True),
        Field("applicant", "申请人", aliases=("申请", "报销人", "经办人", "申请人员"), size=64),
        APPROVAL, NOTE, PROJECT, REF,
    ), (("outflow", "支出金额"),)),
    "cash_journal": Ledger("现金日记账", "expenses", "journal:write", (
        SEQ, Field("date", "日期", "date", DAY.aliases),
        Field("account", "现金账户", aliases=("账户", "账套", "账户名称", "账号", "现金账号"), required=True),
        Field("counterparty", "对方科目", aliases=("对方户名", "对方单位", "对方", "往来科目", "对方账户", "科目")),
        CASH_APPROVAL, SUMMARY,
        Field("income", "收入金额", "money", ("收入", "收款金额", "收方金额", "借方金额", "进账")),
        Field("payment", "支付金额", "money", ("支付", "支出金额", "付款金额", "付方金额", "贷方金额", "支出")),
        Field("balance", "结余金额", "money", ("余额", "结余", "期末余额", "本次余额", "现金余额")),
        NOTE, PROJECT, REF,
        Field("period", "所属月份", "month", ("归属月份", "月份", "账期"), visible=False),
    ), (("inflow", "本期收入"), ("outflow", "本期支付"))),
    "invoice": Ledger("开票台账", "receivables", "receivables:invoice", (
        SEQ,
        Field("date", "开票日期", "date", ("开具日期", "出票日期", "开票时间", "日期"), True),
        Field("invoice_no", "发票号码", aliases=("发票号", "票号", "数电票号码", "发票编号"), required=True, size=80),
        Field("customer", "购方名称", aliases=("客户", "客户名称", "发票抬头", "购买方名称", "开票抬头"), required=True),
        Field("net", "开票金额", "money", ("价税合计", "价税合计小写", "含税金额", "发票金额", "票面金额", "金额"), True),
        Field("contract_no", "合同编号", aliases=("合同号",), size=80), APPROVAL, NOTE,
    ), (("inflow", "开票金额"),)),
}


def can_read(p, kind):
    return p.data_scope == "all" and p.has_menu(LEDGERS[kind].menu)


def can_import(p, kind):
    ledger = LEDGERS[kind]
    return can_read(p, kind) and (p.can(ledger.button) or (ledger.menu == "expenses" and p.can("expense:allocate")))


def header(value):
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"\((?:人民币|元|cny|rmb|必填|选填)\)", "", text)
    return re.sub(r"[\W_]+", "", text)


def parse_month(text):
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})月?", text)
    if not match:
        raise ValueError("请填写有效月份，例如 2026-06")
    return date(int(match[1]), int(match[2]), 1).strftime("%Y-%m")


def parse_date(text):
    if re.fullmatch(r"[3-7]\d{4}(?:\.0+)?", text):
        return date(1899, 12, 30) + timedelta(days=int(Decimal(text)))
    part = re.split(r"[ T]", text, maxsplit=1)[0]
    if re.fullmatch(r"\d{8}", part):
        return datetime.strptime(part, "%Y%m%d").date()
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", part)
    if not match:
        raise ValueError("请填写完整日期，例如 2026-06-02")
    return date(*map(int, match.groups()))


def parse_money(text):
    text = re.sub(r"(?i)人民币|CNY|RMB|[¥元\s]", "", text)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
        raise ValueError("请核对金额格式")
    amount = Decimal(text.replace(",", ""))
    if abs(amount) >= Decimal("1000000000000") or amount.quantize(Decimal("0.01")) != amount:
        raise ValueError("金额须小于一万亿元，且精确到分")
    return format(amount.quantize(Decimal("0.01")) if amount else ZERO, "f")


def clean_value(field, value):
    raw = str(value or "").strip()
    if raw in {"", "—", "--", "-"}:
        return ""
    text = unicodedata.normalize("NFKC", raw) if field.kind != "text" else raw
    if field.kind == "date":
        parsed = parse_date(text)
        if parsed > today():
            raise ValueError("日期不能晚于今天")
        return parsed.isoformat()
    if field.kind == "month":
        return parse_month(text)
    if field.kind == "money":
        return parse_money(text)
    if len(text) > field.size:
        raise ValueError(f"最多 {field.size} 字")
    return text.upper() if field.key == "invoice_no" else text


def is_opening(values):
    return header(values.get("summary")) in {"上月结转", "期初余额", "期初结余", "期初", "上期结转", "期初结转", "上年结转", "承前页", "上日结转", "承上页"}


def normalize(kind, raw):
    values, errors, warnings = {}, {}, []
    for field in LEDGERS[kind].fields:
        try:
            value = clean_value(field, raw.get(field.key))
        except (ValueError, InvalidOperation) as exc:
            value = str(raw.get(field.key) or "")
            errors[field.key] = str(exc) if field.kind in {"money", "text"} else f"请核对{field.label}"
        values[field.key] = value
        if field.required and not value:
            errors[field.key] = "请补充"
    if errors:
        return values, errors, warnings
    if kind == "qr_receipt":
        gross, fee, net = (Decimal(values[k]) if values[k] else None for k in ("gross", "fee", "net"))
        if net is None and (gross is None or fee is None):
            errors["net"] = "请填写实收金额，或同时填写交易金额与手续费"
        elif gross is not None and fee is not None:
            if net is None:
                try:
                    values["net"] = parse_money(format(gross - fee, "f"))
                    warnings.append("实收金额按交易金额减手续费计算")
                except ValueError as exc:
                    errors["net"] = str(exc)
            elif gross - fee != net:
                errors["net"] = "与交易金额减手续费不一致，请核对"
    if kind == "cash_journal":
        has_income, has_payment = (bool(Decimal(values[k] or "0")) for k in ("income", "payment"))
        if is_opening(values):
            if has_income or has_payment:
                errors["income"] = "结转行应只填写结余金额"
            if not values["balance"]:
                errors["balance"] = "请填写结转余额"
            if not values["date"] and not values["period"]:
                errors["period"] = "请填写结转所属月份"
        else:
            if not values["date"]:
                errors["date"] = "请补充日期"
            if not (has_income or has_payment):
                errors["income"] = "请填写收入或支付金额"
        if values["date"] and values["period"] and not values["date"].startswith(values["period"]):
            errors["period"] = "所属月份与日期不一致"
    if kind == "invoice" and Decimal(values["net"]) <= ZERO:
        errors["net"] = "开票金额须大于零"
    if kind in {"bank_receipt", "bank_expense"} and Decimal(values.get("net") or values.get("payment")) == ZERO:
        errors["net" if kind == "bank_receipt" else "payment"] = "金额不能为零"
    return values, errors, warnings


def amounts(kind, values):
    def monetary(key):
        value = values.get(key)
        return Decimal(value) if value else None

    if kind == "cash_journal":
        return monetary("income"), monetary("payment"), None
    if kind == "bank_expense":
        return None, monetary("payment"), None
    return monetary("net"), None, monetary("fee")
